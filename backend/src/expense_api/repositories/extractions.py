"""The extractions and extraction_reviews collections. Each record is written once and never changed."""

from pymongo import ASCENDING, DESCENDING
from pymongo.collection import Collection

# Reads that leave out the core's full records, which can be hundreds of kilobytes.
WITHOUT_RESULT = {"result_json": False}


class ExtractionRepository:
    def __init__(self, extractions: Collection, reviews: Collection):
        self.extractions, self.reviews = extractions, reviews

    def ensure_indexes(self) -> None:
        self.extractions.create_index([("created_at", DESCENDING)], name="created")
        self.extractions.create_index([("extraction_sha256", ASCENDING)], name="extraction_sha256")
        self.extractions.create_index([("documents.document_id", ASCENDING)], name="documents")
        self.reviews.create_index([("extraction_id", ASCENDING), ("created_at", ASCENDING)], name="extraction_created")

    def insert(self, extraction: dict) -> None:
        self.extractions.insert_one(extraction)

    def get(self, extraction_id: str, with_result: bool = False) -> dict | None:
        return self.extractions.find_one({"_id": extraction_id}, None if with_result else WITHOUT_RESULT)

    def insert_review(self, review: dict) -> None:
        self.reviews.insert_one(review)

    def get_review(self, extraction_id: str, review_id: str) -> dict | None:
        return self.reviews.find_one({"_id": review_id, "extraction_id": extraction_id})

    def reviews_of(self, extraction_id: str) -> list[dict]:
        return self.reviews_of_each([extraction_id])

    def reviews_of_each(self, extraction_ids: list[str]) -> list[dict]:
        """The reviews of these extractions, oldest first, in one read and without their results."""
        return list(self.reviews.find({"extraction_id": {"$in": extraction_ids}}, WITHOUT_RESULT).sort([("created_at", ASCENDING), ("_id", ASCENDING)]))

    def page(self, document_id: str | None, limit: int, offset: int) -> tuple[list[dict], int]:
        """Extractions, newest first, without the core's results; with a document ID, only those that read it."""
        match = {} if document_id is None else {"documents.document_id": document_id}
        items = self.extractions.find(match, WITHOUT_RESULT).sort([("created_at", DESCENDING), ("_id", DESCENDING)]).skip(offset).limit(limit)
        return list(items), self.extractions.count_documents(match)
