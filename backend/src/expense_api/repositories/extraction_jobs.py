"""The extraction_jobs collection: one record per extraction run in the background, with its progress."""

from pymongo import DESCENDING, ReturnDocument
from pymongo.collection import Collection


class ExtractionJobRepository:
    def __init__(self, collection: Collection):
        self.collection = collection

    def ensure_indexes(self) -> None:
        self.collection.create_index([("created_at", DESCENDING)], name="created")

    def insert(self, job: dict) -> None:
        self.collection.insert_one(job)

    def get(self, job_id: str) -> dict | None:
        return self.collection.find_one({"_id": job_id})

    def update(self, job_id: str, changes: dict, *, unless_finished: bool = True) -> dict | None:
        """Apply changes to a job still queued or running: a finished job never changes again."""
        match = {"_id": job_id, **({"status": {"$in": ["queued", "running"]}} if unless_finished else {})}
        return self.collection.find_one_and_update(match, {"$set": changes}, return_document=ReturnDocument.AFTER)
