"""The rulesets collection: one document per immutable version, whose lifecycle fields alone ever change."""

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError

from expense_api.errors import ApiError

# Lists leave the large parts out.
SUMMARY = {"artifact_json": False, "approval_json": False, "compilation.documents": False, "compilation.comparisons_json": False, "compilation.logic_snapshot": False}
VERSION_ATTEMPTS = 5
# The only fields a transition may set. The artifact, its hash, its source and its compilation are written once; the
# one exception is the one-off move of a compilation's document text to S3 references (move_compilation_documents).
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

    def with_document_text(self, after_id: str | None, limit: int) -> list[dict]:
        """Versions whose compilation still holds document text, stored before it moved to S3, by ID after after_id."""
        query = {"compilation.documents.content": {"$exists": True}} | ({} if after_id is None else {"_id": {"$gt": after_id}})
        return list(self.collection.find(query, {"compilation.documents": True, "status": True, "name": True, "version": True}, sort=[("_id", ASCENDING)], limit=limit))

    def move_compilation_documents(self, ruleset_id: str, original: list[dict], references: list[dict]) -> bool:
        """Replace a compilation's document entries, text and all, with their verified references, in one atomic update.

        Matches only while the entries are exactly as read: a second run, or a changed version, updates nothing. No other
        field, lifecycle or not, is touched.
        """
        result = self.collection.update_one({"_id": ruleset_id, "compilation.documents": original}, {"$set": {"compilation.documents": references}})
        return result.modified_count == 1

    def document_keys(self) -> set[str]:
        """Every S3 key a compiled version's documents reference."""
        keys = set()
        for record in self.collection.find({"compilation.documents.storage": {"$exists": True}}, {"compilation.documents.storage.key": True}):
            keys |= {entry["storage"]["key"] for entry in record["compilation"]["documents"] if "storage" in entry}
        return keys

    def latest_version(self, name: str) -> int | None:
        latest = self.collection.find_one({"name": name}, {"version": True}, sort=[("version", DESCENDING)])
        return None if latest is None else latest["version"]

    def published_before(self, ruleset_id: str, published_at) -> dict | None:
        """The version published most recently before published_at (any time when None), other than this one, whole."""
        query: dict = {"status": "published", "_id": {"$ne": ruleset_id}}
        if published_at is not None:
            query["published_at"] = {"$lt": published_at}
        return self.collection.find_one(query, sort=[("published_at", DESCENDING), ("_id", DESCENDING)])

    def supersessions(self) -> dict[str, dict]:
        """Derive legacy and new duplicate lineage without rewriting signed artifacts or old records."""
        found = {}
        query = {"status": "published", "source.kind": "policy_compiler", "$or": [{"duplicate_of": {"$exists": True}}, {"source.description": {"$regex": "^Duplicate of "}}], "deleted_at": {"$exists": False}}
        for successor in self.collection.find(query, SUMMARY).sort([("published_at", DESCENDING), ("_id", DESCENDING)]):
            origin = successor.get("duplicate_of") or successor["source"]["description"].removeprefix("Duplicate of ").partition(";")[0]
            if origin in found:
                continue
            original = self.collection.find_one({"_id": origin, "name": successor["name"], "version": successor.get("based_on_version"),
                "artifact_sha256": successor["artifact_sha256"], "status": {"$in": ["draft", "in_review"]}}, {"_id": True})
            if original:
                found[origin] = {"ruleset_id": successor["_id"], "name": successor["name"], "version": successor["version"]}
        return found

    def get(self, ruleset_id: str) -> dict | None:
        record = self.collection.find_one({"_id": ruleset_id, "deleted_at": {"$exists": False}})
        if record is not None:
            record["superseded_by"] = self.supersessions().get(ruleset_id)
        return record

    def page(self, name: str | None, limit: int, offset: int, include_superseded: bool = False) -> tuple[list[dict], int]:
        superseded = self.supersessions()
        query = {"deleted_at": {"$exists": False}, **({} if name is None else {"name": name})}
        if not include_superseded:
            query["_id"] = {"$nin": list(superseded)}
        items = list(self.collection.find(query, SUMMARY).sort([("created_at", DESCENDING), ("_id", DESCENDING)]).skip(offset).limit(limit))
        for item in items:
            item["superseded_by"] = superseded.get(item["_id"])
        return items, self.collection.count_documents(query)

    def transition(self, ruleset_id: str, expected: dict, changes: dict, event: dict) -> dict | None:
        """Apply changes and record event in one atomic update, only if the version still matches expected.

        Returns the updated version, or None when it no longer matches: another request changed it first.
        """
        if not set(changes) <= LIFECYCLE_FIELDS:
            raise ValueError(f"a transition may not change {sorted(set(changes) - LIFECYCLE_FIELDS)}")
        return self.collection.find_one_and_update(
            {"_id": ruleset_id, "deleted_at": {"$exists": False}, **expected},
            {"$set": changes, "$push": {"transitions": event}},
            return_document=ReturnDocument.AFTER,
        )
