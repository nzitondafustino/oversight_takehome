"""The documents collection: one metadata record per immutable revision, its content in S3 (services/document_store.py).

A record is never updated or deleted, with one exception: the one-off migration moves a record written before S3
storage existed, whose text was held here, to an S3 reference, in a single atomic update (migrate_to_storage).
"""

from pymongo import ASCENDING, DESCENDING
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError

from expense_api.errors import ApiError

SUMMARY = {"content": False, "storage": False}
METADATA = {"content": False}  # a revision's record without a legacy record's text


class DocumentRepository:
    def __init__(self, collection: Collection, orphans: Collection | None = None):
        self.collection, self.orphans = collection, orphans

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

    def metadata(self, document_id: str, revision: int | None = None) -> dict | None:
        """A revision's record without any text: the latest when no revision is given."""
        query = {"document_id": document_id} | ({} if revision is None else {"revision": revision})
        return self.collection.find_one(query, METADATA, sort=[("revision", DESCENDING)])

    def record_orphan(self, entry: dict) -> None:
        """Note an S3 object no record may reference, for reconciliation. Never fails the request that found it."""
        if self.orphans is not None:
            self.orphans.insert_one(entry)

    def legacy(self, after_id: str | None, limit: int) -> list[dict]:
        """Records that still hold their text here, those written before S3 storage, by ID after after_id."""
        query = {"content": {"$exists": True}} | ({} if after_id is None else {"_id": {"$gt": after_id}})
        return list(self.collection.find(query, sort=[("_id", ASCENDING)], limit=limit))

    def referenced_keys(self) -> set[str]:
        """Every S3 key a record references."""
        return {record["storage"]["key"] for record in self.collection.find({"storage": {"$exists": True}}, {"storage.key": True})}

    def noted_orphans(self) -> list[dict]:
        return [] if self.orphans is None else list(self.orphans.find({}, {"_id": False}))

    def migrate_to_storage(self, record_id: str, content_sha256: str, storage: dict) -> bool:
        """Swap a legacy record's text for its verified S3 reference, in one atomic update. Nothing else changes.

        Matches only the same record, still holding its text, with the same hash: a second run, or a changed record,
        updates nothing.
        """
        result = self.collection.update_one(
            {"_id": record_id, "content": {"$exists": True}, "content_sha256": content_sha256, "storage": {"$exists": False}},
            {"$set": {"storage": storage}, "$unset": {"content": ""}},
        )
        return result.modified_count == 1

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


class DocumentProfileRepository:
    """The document_profiles collection: one mutable business description per document, by document ID.

    A profile is never part of a revision: changing one never touches a revision's text, hash or S3 object.
    """

    PROFILE = ("category", "description", "scope", "duplicate_of", "profile_updated_at")

    def __init__(self, collection: Collection):
        self.collection = collection

    def of_each(self, document_ids: list[str]) -> dict[str, dict]:
        return {item["_id"]: {name: item.get(name) for name in self.PROFILE} for item in self.collection.find({"_id": {"$in": document_ids}})}

    def duplicates_of(self, document_id: str) -> list[str]:
        return [item["_id"] for item in self.collection.find({"duplicate_of": document_id}, {"_id": True})]

    def set(self, document_id: str, fields: dict, actor: str | None, at) -> dict:
        update = {**fields, "profile_updated_at": at, "profile_updated_by": None if actor is None else {"claimed": actor, "verified": False}}
        self.collection.update_one({"_id": document_id}, {"$set": update}, upsert=True)
        return self.of_each([document_id]).get(document_id, {})


class DocumentApprovalRepository:
    """The document_approvals collection: one record per exact revision, with every approval action appended.

    A record's status only moves by a conditional update from the status the action expects, so two reviewers acting
    at once cannot both succeed; history is only ever appended.
    """

    def __init__(self, collection: Collection):
        self.collection = collection

    @staticmethod
    def key(document_id: str, revision: int) -> str:
        return f"{document_id}:{revision}"

    def get(self, document_id: str, revision: int) -> dict | None:
        return self.collection.find_one({"_id": self.key(document_id, revision)})

    def of_each(self, revisions: list[tuple[str, int]]) -> dict[tuple[str, int], dict]:
        found = self.collection.find({"_id": {"$in": [self.key(document_id, revision) for document_id, revision in revisions]}}, {"history": False})
        return {(item["document_id"], item["revision"]): item for item in found}

    def move(self, document_id: str, revision: int, expected: str, event: dict) -> bool:
        """Apply one action if the revision is still in the expected status. False when another action came first."""
        changes = {"status": event["to_status"], "content_sha256": event["content_sha256"], "updated_at": event["at"], "updated_by": event["actor"]}
        if expected == "draft":
            existing = self.get(document_id, revision)
            if existing is None:
                try:
                    self.collection.insert_one({"_id": self.key(document_id, revision), "document_id": document_id, "revision": revision, **changes, "history": [event]})
                    return True
                except DuplicateKeyError:
                    return False
        result = self.collection.update_one({"_id": self.key(document_id, revision), "status": expected}, {"$set": changes, "$push": {"history": event}})
        return result.modified_count == 1
