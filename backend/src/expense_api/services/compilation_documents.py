"""Store compiled policy documents by immutable S3 reference.

Reuse a source revision's object when possible; otherwise write the text under the
ruleset ID and content hash. MongoDB entries hold document metadata, size_bytes and
storage references. Rebuilds verify hashes before returning text to the compiler.

Legacy entries with inline content remain readable until migrate_rulesets moves
them to S3.
"""

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass

from expense_api.errors import ApiError
from expense_api.services.document_store import DocumentStore, ObjectExists, StoredObject

log = logging.getLogger(__name__)


@dataclass
class Persisted:
    entries: list[dict]
    created: list[StoredObject]  # objects this call wrote, which nothing else references yet


class CompilationDocuments:
    def __init__(self, store: Callable[[], DocumentStore]):
        self.store = store  # asked for only when S3 is needed, so a legacy snapshot reads without it

    def persist(self, ruleset_id: str, documents: list[dict]) -> Persisted:
        """Store document text or reuse matching revision objects.

        Args:
            ruleset_id: Fixed ID of the compiled ruleset version.
            documents: Compiler inputs with document_id, filename, content and
                sha256; optionally revision and its storage reference.

        Returns:
            Storage entries and objects created by this call for later cleanup.

        Raises:
            ApiError: If integrity checks or storage fail. Newly created objects
                are removed where possible; cleanup failures are logged.
        """
        entries, created = [], []
        try:
            for document in documents:
                data = document["content"].encode("utf-8")
                digest = hashlib.sha256(data).hexdigest()
                if digest != document["sha256"]:
                    raise ApiError(500, "integrity_error", f"{document['filename']} does not match the hash the compiler read")
                stored = self._reusable(document, digest) or self._write(ruleset_id, data, digest, created)
                entry = {name: document[name] for name in ("document_id", "filename", "sha256", "revision") if name in document}
                entries.append(entry | {"size_bytes": len(data), "storage": stored.reference()})
        except ApiError:
            self.discard(created, ruleset_id, "compilation_not_stored")
            raise
        return Persisted(entries, created)

    def resolve(self, entries: list[dict]) -> list[dict]:
        """The compiler's input, exactly as it was: each document's text from S3 (or a legacy entry), checked."""
        resolved = []
        for entry in entries:
            if "storage" in entry:
                content = self.store().read(entry["storage"], entry["sha256"], entry["size_bytes"])
            elif "content" in entry:  # stored before compilation documents moved to S3
                content = entry["content"]
                if hashlib.sha256(content.encode("utf-8")).hexdigest() != entry["sha256"]:
                    raise ApiError(500, "integrity_error", f"the stored text of {entry['filename']} does not match the hash the compiler read")
            else:
                raise ApiError(500, "integrity_error", f"the compilation keeps no text for {entry['filename']}")
            resolved.append({"document_id": entry["document_id"], "filename": entry["filename"], "content": content})
        return resolved

    def discard(self, created: list[StoredObject], ruleset_id: str, reason: str) -> None:
        """Remove objects this request wrote and nothing references. A failure is logged, never raised."""
        for stored in created:
            try:
                self.store().delete(stored)
            except Exception as error:  # noqa: BLE001 - the request already failed; the object is reported by --orphans
                log.error("could not remove unreferenced %s of %s (%s): %s", stored.key, ruleset_id, reason, type(error).__name__)

    @staticmethod
    def _reusable(document: dict, digest: str) -> StoredObject | None:
        """A stored revision's own S3 object, when the document came from one with exactly this text."""
        storage = document.get("storage")
        if not storage or document.get("content_sha256", digest) != digest:
            return None
        return StoredObject(storage["bucket"], storage["region"], storage["key"], storage.get("version_id"))

    def _write(self, ruleset_id: str, data: bytes, digest: str, created: list[StoredObject]) -> StoredObject:
        store = self.store()
        key = store.ruleset_key(ruleset_id, digest)
        try:
            stored = store.put(key, data, digest)
        except ObjectExists:
            # The same text twice in one version, or an object an interrupted attempt left: used only if it is this text.
            stored = store.existing(key)
            if stored is None:
                raise ApiError(409, "compilation_conflict", f"{key} changed while this version was being stored: try again") from None
            try:
                store.read(stored.reference(), digest, len(data))
            except ApiError as error:
                if error.code == "integrity_error":
                    raise ApiError(409, "compilation_conflict", f"{key} holds other text: it is never replaced") from None
                raise
            return stored
        created.append(stored)
        return stored
