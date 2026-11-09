"""Store policy documents as immutable revisions, exactly as sent: no text is ever rewritten.

Each revision's Markdown is one S3 object (services/document_store.py); MongoDB holds its metadata and the object's
reference, never the text. A record written before S3 storage still holds its text until the migration
(expense_api.migrate_documents) moves it: it is read, and checked, exactly as before.

A revision is written in two steps, S3 then MongoDB, made safe as follows:
- The S3 write is create-only: a revision's object is never replaced, and of two uploads racing for one revision
  number at most one writes it. The other is a 409, as is a second record for it (the unique index).
- A failed S3 write leaves no record. A failed MongoDB write removes the object just written, unless a record may
  reference it: when MongoDB cannot say, the object is kept and noted for reconciliation, never deleted.
- An object left by an earlier failed attempt is reused when it holds exactly this content, so a retry succeeds.
"""

import logging
from collections.abc import Callable

from bson import ObjectId
from expense_validator import PolicyDocumentInput
from pydantic import ValidationError
from pymongo.errors import PyMongoError

from expense_api.config import Settings
from expense_api.errors import ApiError
from expense_api.repositories import DocumentApprovalRepository, DocumentProfileRepository, DocumentRepository
from expense_api.schemas.documents import ApprovalAction, DocumentCreate, ProfileUpdate, RevisionCreate
from expense_api.services.document_store import DocumentStore, ObjectExists, StoredObject
from expense_api.services.rulesets import now, sha256

log = logging.getLogger(__name__)


class DocumentService:
    def __init__(self, settings: Settings, repository: DocumentRepository, store: Callable[[], DocumentStore], profiles: DocumentProfileRepository | None = None, approvals: DocumentApprovalRepository | None = None):
        # The store is asked for only when S3 is needed, so a record not yet migrated reads without it.
        self.settings, self.repository, self.store, self.profiles, self.approvals = settings, repository, store, profiles, approvals

    def create(self, request: DocumentCreate) -> dict:
        record = self._store(request, f"doc_{ObjectId()}", request.filename, 1, None)
        given = {name: getattr(request, name) for name in ("category", "description", "scope") if name in request.model_fields_set}
        if given and self.profiles is not None:
            self.profiles.set(record["document_id"], _profile_fields(given), None, record["created_at"])
        return self._with_profiles([record])[0]

    def update_profile(self, document_id: str, request: ProfileUpdate) -> dict:
        """Change a document's business description. Its revisions, their text and hashes, never change."""
        latest = self._metadata(document_id)
        given = {name: getattr(request, name) for name in ("category", "description", "scope", "duplicate_of") if name in request.model_fields_set}
        if self.profiles is None:
            raise ApiError(503, "profiles_unavailable", "document profiles are not available here")
        if given.get("duplicate_of") is not None:
            self._check_duplicate(latest, given["duplicate_of"])
        self.profiles.set(document_id, _profile_fields(given), request.actor, now())
        return self._with_profiles([latest])[0]

    def add_revision(self, document_id: str, request: RevisionCreate) -> dict:
        latest = self._metadata(document_id)
        if request.based_on_revision is not None and request.based_on_revision != latest["revision"]:
            raise ApiError(409, "revision_conflict", f"revision {request.based_on_revision} is not the latest revision, {latest['revision']}: start again from the latest")
        if self._digest(request.content) == latest["content_sha256"]:
            raise ApiError(409, "revision_unchanged", "the content is the same as the latest revision", {"revision": latest["revision"]})
        return self._with_profiles([self._store(request, document_id, latest["filename"], latest["revision"] + 1, latest["revision"])])[0]

    def get(self, document_id: str, revision: int | None = None) -> dict:
        """A revision's record with its text, read from S3 and checked against its recorded hash. Nothing is stored."""
        record = self.repository.get(document_id, revision)
        if record is None:
            raise self._not_found(document_id, revision)
        if "storage" in record:
            return self._with_profiles([{**record, "content": self.store().read(record["storage"], record["content_sha256"], record["size_bytes"])}])[0]
        if "content" in record:  # written before S3 storage, and not yet migrated
            if sha256(record["content"]) != record["content_sha256"]:
                raise ApiError(500, "integrity_error", f"{document_id} revision {record['revision']} does not match its stored hash")
            return self._with_profiles([record])[0]
        raise ApiError(500, "integrity_error", f"{document_id} revision {record['revision']} has no stored content")

    def page(self, filename: str | None, limit: int, offset: int) -> tuple[list[dict], int]:
        items, total = self.repository.page(filename, limit, offset)
        return self._with_profiles(items), total

    def _with_profiles(self, records: list[dict]) -> list[dict]:
        """Each record with its document's profile, and the approval of its own exact revision: one never described
        stays unclassified, and one never acted on is a draft, whatever another revision's approval is."""
        if not records:
            return records
        profiles = self.profiles.of_each(list({record["document_id"] for record in records})) if self.profiles is not None else {}
        approvals = self.approvals.of_each([(record["document_id"], record["revision"]) for record in records]) if self.approvals is not None else {}
        out = []
        for record in records:
            approval = approvals.get((record["document_id"], record["revision"]))
            state = {"status": "draft"} if approval is None else {name: approval.get(name) for name in ("status", "content_sha256", "updated_at", "updated_by")}
            out.append(record | profiles.get(record["document_id"], {}) | {"approval": state})
        return out

    # --- Approval of an exact revision: draft, needs review, approved. Never ruleset publication. ---

    TRANSITIONS = {"submit": ("draft", "needs_review"), "approve": ("needs_review", "approved")}

    def approval(self, document_id: str, revision: int) -> dict:
        record = self.repository.metadata(document_id, revision)
        if record is None:
            raise self._not_found(document_id, revision)
        stored = self.approvals.get(document_id, revision) if self.approvals is not None else None
        return {"document_id": document_id, "revision": revision, "status": "draft", "content_sha256": record["content_sha256"], "history": []} | ({} if stored is None else {
            name: stored[name] for name in ("status", "content_sha256", "updated_at", "updated_by", "history")})

    def act_on_approval(self, document_id: str, revision: int, request: ApprovalAction) -> dict:
        """Move one exact revision's approval. Its text is read from storage and checked against its hash first, and
        the request must name that same hash: approval is of these exact bytes, never of a document ID or a filename."""
        if self.approvals is None:
            raise ApiError(503, "approvals_unavailable", "document approval is not available here")
        verified = self.get(document_id, revision)  # reads the immutable text and refuses it unless it matches its hash
        if request.content_sha256 != verified["content_sha256"]:
            raise ApiError(409, "revision_mismatch", f"revision {revision} of {document_id} holds other text than the one named: reload it", {"content_sha256": verified["content_sha256"]})
        current = self.approval(document_id, revision)["status"]
        if request.action == "return_to_draft":
            if current == "draft":
                raise ApiError(409, "approval_conflict", "this revision is already a draft")
            if not (request.note and request.note.strip()):
                raise ApiError(422, "note_required", "returning a revision to draft needs a note saying why")
            expected, target = current, "draft"
        else:
            expected, target = self.TRANSITIONS[request.action]
            if current != expected:
                raise ApiError(409, "approval_conflict", f"this revision is {current.replace('_', ' ')}, so it cannot be {'submitted' if request.action == 'submit' else 'approved'}: it must be {expected.replace('_', ' ')}", {"status": current})
        event = {"action": request.action, "from_status": expected, "to_status": target, "actor": {"claimed": request.actor, "verified": False}, "note": request.note, "content_sha256": verified["content_sha256"], "at": now()}
        if not self.approvals.move(document_id, revision, expected, event):
            raise ApiError(409, "approval_conflict", "another approval action on this revision came first: reload it")
        return self.approval(document_id, revision)

    def _check_duplicate(self, latest: dict, kept_id: str) -> None:
        """A document is a duplicate only of another whose latest revision has exactly its content, and that is not
        itself a duplicate: one level, so restoring one never reveals another hidden behind it."""
        if kept_id == latest["document_id"]:
            raise ApiError(422, "invalid_duplicate", "a document cannot be a duplicate of itself")
        kept = self.repository.metadata(kept_id)
        if kept is None:
            raise ApiError(422, "invalid_duplicate", f"{kept_id} is not a document")
        if kept["content_sha256"] != latest["content_sha256"]:
            raise ApiError(422, "not_identical", "only a document with identical content can be marked a duplicate: compare them, and revise one, instead",
                           {"content_sha256": latest["content_sha256"], "kept_content_sha256": kept["content_sha256"]})
        if self.profiles.of_each([kept_id]).get(kept_id, {}).get("duplicate_of"):
            raise ApiError(409, "duplicate_chain", f"{kept_id} is itself a duplicate: mark this one a duplicate of the document it is kept as")
        if self.profiles.duplicates_of(latest["document_id"]):
            raise ApiError(409, "duplicate_chain", f"{latest['document_id']} has duplicates of its own: it is the copy kept")

    def _metadata(self, document_id: str) -> dict:
        record = self.repository.metadata(document_id)
        if record is None:
            raise self._not_found(document_id, None)
        return record

    def _store(self, request: DocumentCreate | RevisionCreate, document_id: str, filename: str, revision: int, based_on: int | None) -> dict:
        self._check(filename, request.content)
        if self.repository.metadata(document_id, revision) is not None:  # already taken: fail before touching S3
            raise self._taken(document_id, revision)
        data, digest = request.content.encode("utf-8"), self._digest(request.content)
        store = self.store()
        key = store.key(document_id, revision)
        try:
            stored, reused = store.put(key, data, digest), False
        except ObjectExists:
            stored, reused = self._reuse(store, key, digest, document_id, revision), True
        record = {
            "_id": f"{document_id}:{revision}",
            "document_id": document_id,
            "revision": revision,
            "based_on_revision": based_on,
            "filename": filename,
            "title": request.title,
            "content_sha256": digest,
            "size_bytes": len(data),
            "storage": stored.reference(),
            "source": request.source.model_dump(),
            "created_at": now(),
        }
        try:
            self.repository.insert(record)
        except ApiError:  # another record took this revision: it cannot reference an object this request wrote
            if not reused:
                self._discard(store, stored, document_id, revision, "revision_taken")
            raise
        except PyMongoError:
            written = self._after_failed_insert(store, stored, reused, record)
            if written is None:
                raise
            record = written
        return {**record, "content": request.content}

    def _reuse(self, store: DocumentStore, key: str, digest: str, document_id: str, revision: int) -> StoredObject:
        """An object already at the key, with no record: an earlier attempt's, reused only if it holds this exact text."""
        existing = store.existing(key)
        if existing is None:  # removed since the write was refused: never write over a race
            raise self._taken(document_id, revision)
        try:
            store.read(existing.reference(), digest, self.settings.max_document_bytes)
        except ApiError as error:
            if error.code == "integrity_error":  # another upload's text: it may be on its way to its record
                raise self._taken(document_id, revision) from None
            raise
        log.info("reusing the object an earlier attempt left for %s revision %s", document_id, revision)
        return existing

    def _after_failed_insert(self, store: DocumentStore, stored: StoredObject, reused: bool, record: dict) -> dict | None:
        """The record may or may not have been written. Returns it when it was, as this request's own; else deletes the
        object, once MongoDB says nothing references it, and returns None."""
        document_id, revision = record["document_id"], record["revision"]
        try:
            existing = self.repository.metadata(document_id, revision)
        except PyMongoError:
            log.error("kept %s: whether a record references it is unknown, so it is left for reconciliation", stored.key)
            return None
        if existing is not None:
            # Written after all, despite the error: this request's record when it names this object, else another's.
            return existing if existing.get("storage", {}).get("key") == stored.key and existing["content_sha256"] == record["content_sha256"] else None
        if not reused:
            self._discard(store, stored, document_id, revision, "record_not_written")
        return None

    def _discard(self, store: DocumentStore, stored: StoredObject, document_id: str, revision: int, reason: str) -> None:
        try:
            store.delete(stored)
        except Exception as error:  # noqa: BLE001 - the request already failed; the object is noted, never lost track of
            log.error("could not remove unreferenced %s: %s", stored.key, type(error).__name__)
            try:
                self.repository.record_orphan({"key": stored.key, "bucket": stored.bucket, "version_id": stored.version_id, "document_id": document_id, "revision": revision, "reason": reason, "noted_at": now()})
            except PyMongoError:
                log.error("could not note unreferenced %s for reconciliation", stored.key)

    def _digest(self, content: str) -> str:
        try:
            return sha256(content)
        except UnicodeEncodeError:
            raise ApiError(422, "invalid_document", "the content is not valid UTF-8 text") from None

    @staticmethod
    def _not_found(document_id: str, revision: int | None) -> ApiError:
        label = document_id if revision is None else f"{document_id} revision {revision}"
        return ApiError(404, "document_not_found", f"no document {label}")

    @staticmethod
    def _taken(document_id: str, revision: int) -> ApiError:
        return ApiError(409, "revision_conflict", f"revision {revision} of {document_id} already exists: start again from the latest revision")

    def _check(self, filename: str, content: str) -> None:
        try:
            size = len(content.encode("utf-8"))
        except UnicodeEncodeError:  # a lone surrogate, which a JSON \\u escape can carry
            raise ApiError(422, "invalid_document", "the content is not valid UTF-8 text") from None
        if size > self.settings.max_document_bytes:
            raise ApiError(413, "document_too_large", f"the content is {size} bytes, and the limit is {self.settings.max_document_bytes}")
        try:  # the core's own contract for a policy document's name and text
            PolicyDocumentInput.model_validate({"document_id": "upload", "filename": filename, "content": content})
        except ValidationError as error:
            first = error.errors(include_url=False)[0]
            raise ApiError(422, "invalid_document", f"{'.'.join(map(str, first['loc']))}: {first['msg']}") from None


def _profile_fields(given: dict) -> dict:
    return {name: value.model_dump() if hasattr(value, "model_dump") else value for name, value in given.items()}
