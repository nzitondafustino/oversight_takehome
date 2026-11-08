"""The rulesets collection: one document per immutable version, whose lifecycle fields alone ever change."""

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError

from expense_api.errors import ApiError

# Lists leave the large parts out.
SUMMARY = {"artifact_json": False, "approval_json": False, "compilation.documents": False, "compilation.comparisons_json": False}
VERSION_ATTEMPTS = 5
# The only fields a transition may set. The artifact, its hash, its source and its compilation are written once.
LIFECYCLE_FIELDS = frozenset({"status", "submitted_at", "review", "approved_at", "approval_json", "approval_reference", "approval_mode", "published_at", "publication_mode"})


class RulesetRepository:
    def __init__(self, collection: Collection):
        self.collection = collection

    def ensure_indexes(self) -> None:
        # The unique index is what makes concurrent creates of one name take different versions.
        self.collection.create_index([("name", ASCENDING), ("version", ASCENDING)], unique=True, name="name_version")
        self.collection.create_index([("created_at", DESCENDING)], name="created")
        self.collection.create_index([("name", ASCENDING), ("status", ASCENDING)], name="name_status")
        self.collection.create_index([("artifact_sha256", ASCENDING)], name="artifact_sha256")

    def create(self, document: dict, based_on_version: int | None = None) -> dict:
        """Insert the next version of the document's name.

        With based_on_version, the new version must directly follow it: another version created since is a conflict.
        """
        if based_on_version is not None:
            record = {**document, "version": based_on_version + 1}
            try:
                self.collection.insert_one(record)
            except DuplicateKeyError:
                raise ApiError(409, "version_conflict", f"version {based_on_version + 1} of {document['name']} already exists: start again from the latest version") from None
            return record
        for _ in range(VERSION_ATTEMPTS):
            latest = self.collection.find_one({"name": document["name"]}, {"version": True}, sort=[("version", DESCENDING)])
            record = {**document, "version": 1 if latest is None else latest["version"] + 1}
            try:
                self.collection.insert_one(record)
                return record
            except DuplicateKeyError:
                continue  # another request took this version first
        raise ApiError(409, "version_conflict", "too many concurrent versions of this ruleset name; try again")

    def latest_version(self, name: str) -> int | None:
        latest = self.collection.find_one({"name": name}, {"version": True}, sort=[("version", DESCENDING)])
        return None if latest is None else latest["version"]

    def get(self, ruleset_id: str) -> dict | None:
        return self.collection.find_one({"_id": ruleset_id})

    def page(self, name: str | None, limit: int, offset: int) -> tuple[list[dict], int]:
        query = {} if name is None else {"name": name}
        items = list(self.collection.find(query, SUMMARY).sort([("created_at", DESCENDING), ("_id", DESCENDING)]).skip(offset).limit(limit))
        return items, self.collection.count_documents(query)

    def transition(self, ruleset_id: str, expected: dict, changes: dict, event: dict) -> dict | None:
        """Apply changes and record event in one atomic update, only if the version still matches expected.

        Returns the updated version, or None when it no longer matches: another request changed it first.
        """
        if not set(changes) <= LIFECYCLE_FIELDS:
            raise ValueError(f"a transition may not change {sorted(set(changes) - LIFECYCLE_FIELDS)}")
        return self.collection.find_one_and_update(
            {"_id": ruleset_id, **expected},
            {"$set": changes, "$push": {"transitions": event}},
            return_document=ReturnDocument.AFTER,
        )
