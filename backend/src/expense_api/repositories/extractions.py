"""The extractions and extraction_reviews collections, signed contents written once and retained when a review is marked deleted; and the
change requests reviewers raise about an extraction's fields, which only move from open to resolved."""

from pymongo import ASCENDING, DESCENDING
from pymongo.collection import Collection

# Reads that leave out the core's full records, which can be hundreds of kilobytes.
WITHOUT_RESULT = {"result_json": False}


class ExtractionRepository:
    def __init__(self, extractions: Collection, reviews: Collection, change_requests: Collection | None = None, rulesets: Collection | None = None):
        self.extractions, self.reviews, self.change_requests, self.rulesets = extractions, reviews, change_requests, rulesets

    def ensure_indexes(self) -> None:
        self.extractions.create_index([("created_at", DESCENDING)], name="created")
        self.extractions.create_index([("extraction_sha256", ASCENDING)], name="extraction_sha256")
        self.extractions.create_index([("documents.document_id", ASCENDING)], name="documents")
        self.reviews.create_index([("extraction_id", ASCENDING), ("created_at", ASCENDING)], name="extraction_created")
        if self.change_requests is not None:
            self.change_requests.create_index([("extraction_id", ASCENDING), ("created_at", ASCENDING)], name="extraction_created")

    def insert(self, extraction: dict) -> None:
        self.extractions.insert_one(extraction)

    def get(self, extraction_id: str, with_result: bool = False) -> dict | None:
        return self.extractions.find_one({"_id": extraction_id}, None if with_result else WITHOUT_RESULT)

    def insert_review(self, review: dict) -> None:
        self.reviews.insert_one(review)

    def get_review(self, extraction_id: str, review_id: str) -> dict | None:
        return self.reviews.find_one({"_id": review_id, "extraction_id": extraction_id, "deleted_at": {"$exists": False}})

    def reviews_of(self, extraction_id: str) -> list[dict]:
        return self.reviews_of_each([extraction_id])

    def reviews_of_each(self, extraction_ids: list[str]) -> list[dict]:
        """The reviews of these extractions, oldest first, in one read and without their results."""
        return list(self.reviews.find({"extraction_id": {"$in": extraction_ids}, "deleted_at": {"$exists": False}}, WITHOUT_RESULT).sort([("created_at", ASCENDING), ("_id", ASCENDING)]))

    def page(self, document_id: str | None, limit: int, offset: int) -> tuple[list[dict], int]:
        """Extractions, newest first, without the core's results; with a document ID, only those that read it."""
        match = {} if document_id is None else {"documents.document_id": document_id}
        items = self.extractions.find(match, {**WITHOUT_RESULT, "logic_snapshot": False}).sort([("created_at", DESCENDING), ("_id", DESCENDING)]).skip(offset).limit(limit)
        return list(items), self.extractions.count_documents(match)

    def insert_change_request(self, request: dict) -> None:
        self.change_requests.insert_one(request)

    def change_requests_of(self, extraction_id: str) -> list[dict]:
        return list(self.change_requests.find({"extraction_id": extraction_id}).sort([("created_at", ASCENDING), ("_id", ASCENDING)]))

    def open_change_requests_of_each(self, extraction_ids: list[str]) -> dict[str, int]:
        if self.change_requests is None:
            return {}
        counts = self.change_requests.aggregate([{"$match": {"extraction_id": {"$in": extraction_ids}, "status": "open"}}, {"$group": {"_id": "$extraction_id", "n": {"$sum": 1}}}])
        return {item["_id"]: item["n"] for item in counts}

    def resolve_change_request(self, extraction_id: str, request_id: str, changes: dict) -> dict | None:
        """Resolve an open request, once: a second resolution finds none open."""
        self.change_requests.update_one({"_id": request_id, "extraction_id": extraction_id, "status": "open"}, {"$set": {"status": "resolved", **changes}})
        return self.change_requests.find_one({"_id": request_id, "extraction_id": extraction_id})

    def latest_published(self) -> dict | None:
        """The most recently published ruleset version, whole: the approved baseline a new compilation is compared with."""
        if self.rulesets is None:
            return None
        return self.rulesets.find_one({"status": "published"}, sort=[("published_at", DESCENDING), ("_id", DESCENDING)])

    def versions_of_each(self, extraction_ids: list[str]) -> dict[str, list[dict]]:
        """The ruleset versions made from each extraction, oldest first. Read only."""
        if self.rulesets is None:
            return {}
        found: dict[str, list[dict]] = {}
        for record in self.rulesets.find({"compilation.extraction_id": {"$in": extraction_ids}, "deleted_at": {"$exists": False}}, {"name": True, "version": True, "status": True, "compilation.extraction_id": True}).sort([("created_at", ASCENDING), ("_id", ASCENDING)]):
            found.setdefault(record["compilation"]["extraction_id"], []).append({"ruleset_id": record["_id"], "name": record["name"], "version": record["version"], "status": record["status"]})
        return found
