"""The documents collection: one document per immutable revision. Nothing here updates or deletes one."""

from pymongo import ASCENDING, DESCENDING
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError

from expense_api.errors import ApiError

SUMMARY = {"content": False}


class DocumentRepository:
    def __init__(self, collection: Collection):
        self.collection = collection

    def ensure_indexes(self) -> None:
        self.collection.create_index([("document_id", ASCENDING), ("revision", ASCENDING)], unique=True, name="document_revision")
        self.collection.create_index([("created_at", DESCENDING)], name="created")
        self.collection.create_index([("filename", ASCENDING)], name="filename")

    def insert(self, revision: dict) -> dict:
        """Insert one revision. Its number is taken: another revision with it is a conflict, never a replacement."""
        try:
            self.collection.insert_one(revision)
        except DuplicateKeyError:
            raise ApiError(409, "revision_conflict", f"revision {revision['revision']} of {revision['document_id']} already exists: start again from the latest revision") from None
        return revision

    def get(self, document_id: str, revision: int | None = None) -> dict | None:
        query = {"document_id": document_id} | ({} if revision is None else {"revision": revision})
        return self.collection.find_one(query, sort=[("revision", DESCENDING)])

    def page(self, filename: str | None, limit: int, offset: int) -> tuple[list[dict], int]:
        """Each document's latest revision, newest document first."""
        match = {} if filename is None else {"filename": filename}
        latest = [
            {"$match": match},
            {"$sort": {"revision": DESCENDING}},
            {"$group": {"_id": "$document_id", "latest": {"$first": "$$ROOT"}}},
            {"$replaceRoot": {"newRoot": "$latest"}},
            {"$project": SUMMARY},
            {"$sort": {"document_id": DESCENDING}},  # document IDs increase as documents are made
        ]
        items = list(self.collection.aggregate([*latest, {"$skip": offset}, {"$limit": limit}]))
        return items, len(self.collection.distinct("document_id", match))
