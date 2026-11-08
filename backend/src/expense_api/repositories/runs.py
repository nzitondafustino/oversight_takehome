"""The runs collection. A run is written once, when it has completed, and never changed."""

from pymongo import ASCENDING, DESCENDING
from pymongo.collection import Collection

# History pages leave the stored core result out: only what the summary needs is read.
SUMMARY = {"results_json": False}
NEWEST_FIRST = [("created_at", DESCENDING), ("_id", DESCENDING)]  # run IDs are unique and increase, so ties keep one order


class RunRepository:
    def __init__(self, collection: Collection):
        self.collection = collection

    def ensure_indexes(self) -> None:
        self.collection.create_index([("ruleset.ruleset_id", ASCENDING)], name="ruleset_id")
        self.collection.create_index([("created_at", DESCENDING)], name="created")
        # History: newest first, with the run ID as the tie-breaker, by each filter.
        self.collection.create_index([("created_at", DESCENDING), ("_id", DESCENDING)], name="created_id")
        self.collection.create_index([("ruleset.ruleset_id", ASCENDING), ("created_at", DESCENDING), ("_id", DESCENDING)], name="ruleset_created")
        self.collection.create_index([("ruleset.name", ASCENDING), ("ruleset.version", ASCENDING), ("created_at", DESCENDING)], name="ruleset_version_created")
        self.collection.create_index([("summary.classification_backend", ASCENDING), ("created_at", DESCENDING), ("_id", DESCENDING)], name="backend_created")
        self.collection.create_index([("execution_mode", ASCENDING), ("created_at", DESCENDING), ("_id", DESCENDING)], name="mode_created")

    def insert(self, document: dict) -> None:
        self.collection.insert_one(document)

    def get(self, run_id: str) -> dict | None:
        return self.collection.find_one({"_id": run_id})

    def page(self, filters: dict, limit: int, offset: int) -> tuple[list[dict], int]:
        """A page of runs, newest first, without their results."""
        query = {}
        for name, field in (("ruleset_id", "ruleset.ruleset_id"), ("ruleset_name", "ruleset.name"), ("ruleset_version", "ruleset.version"),
                            ("classification_backend", "summary.classification_backend"), ("execution_mode", "execution_mode")):
            if filters.get(name) is not None:
                query[field] = filters[name]
        if filters.get("preview") is not None:
            query["preview"] = True if filters["preview"] else {"$ne": True}  # a Phase 1 run has no preview field: it was none
        created = {operator: filters[name] for name, operator in (("created_from", "$gte"), ("created_to", "$lt")) if filters.get(name) is not None}
        if created:
            query["created_at"] = created
        items = list(self.collection.find(query, SUMMARY).sort(NEWEST_FIRST).skip(offset).limit(limit))
        return items, self.collection.count_documents(query)
