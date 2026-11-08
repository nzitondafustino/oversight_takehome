"""Store policy documents as immutable revisions, exactly as sent: no text is ever rewritten."""

from bson import ObjectId
from expense_validator import PolicyDocumentInput
from pydantic import ValidationError

from expense_api.config import Settings
from expense_api.errors import ApiError
from expense_api.repositories import DocumentRepository
from expense_api.schemas.documents import DocumentCreate, RevisionCreate
from expense_api.services.rulesets import now, sha256


class DocumentService:
    def __init__(self, settings: Settings, repository: DocumentRepository):
        self.settings, self.repository = settings, repository

    def create(self, request: DocumentCreate) -> dict:
        return self._store(request, f"doc_{ObjectId()}", request.filename, 1, None)

    def add_revision(self, document_id: str, request: RevisionCreate) -> dict:
        latest = self.get(document_id)
        if request.based_on_revision is not None and request.based_on_revision != latest["revision"]:
            raise ApiError(409, "revision_conflict", f"revision {request.based_on_revision} is not the latest revision, {latest['revision']}: start again from the latest")
        if sha256(request.content) == latest["content_sha256"]:
            raise ApiError(409, "revision_unchanged", "the content is the same as the latest revision", {"revision": latest["revision"]})
        return self._store(request, document_id, latest["filename"], latest["revision"] + 1, latest["revision"])

    def get(self, document_id: str, revision: int | None = None) -> dict:
        record = self.repository.get(document_id, revision)
        if record is None:
            label = document_id if revision is None else f"{document_id} revision {revision}"
            raise ApiError(404, "document_not_found", f"no document {label}")
        if sha256(record["content"]) != record["content_sha256"]:
            raise ApiError(500, "integrity_error", f"{document_id} revision {record['revision']} does not match its stored hash")
        return record

    def page(self, filename: str | None, limit: int, offset: int) -> tuple[list[dict], int]:
        return self.repository.page(filename, limit, offset)

    def _store(self, request: DocumentCreate | RevisionCreate, document_id: str, filename: str, revision: int, based_on: int | None) -> dict:
        self._check(filename, request.content)
        return self.repository.insert({
            "_id": f"{document_id}:{revision}",
            "document_id": document_id,
            "revision": revision,
            "based_on_revision": based_on,
            "filename": filename,
            "title": request.title,
            "content": request.content,
            "content_sha256": sha256(request.content),
            "size_bytes": len(request.content.encode("utf-8")),
            "source": request.source.model_dump(),
            "created_at": now(),
        })

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
