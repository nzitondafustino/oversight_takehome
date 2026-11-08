"""The evaluation collections: datasets, jobs, per-receipt results and provider-call usage.

- evaluation_datasets: one immutable version per document. Nothing ever updates one.
- evaluations: one job per document. Only its lifecycle, progress, lease and outcome fields change, and every
  change is a conditional update, so two workers can never both own a job or move it from a stale state.
- evaluation_results: one receipt's outcome per document, keyed by evaluation and receipt, so a resumed job
  never stores a receipt twice.
- evaluation_usage: one model call per document, keyed so that a call is recorded at most once.
"""

from datetime import datetime

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError

from expense_api.errors import ApiError

NEWEST_FIRST = [("created_at", DESCENDING), ("_id", DESCENDING)]  # IDs are unique ObjectIds, so ties keep one order
VERSION_ATTEMPTS = 5
DATASET_CONTENT = ("original", "canonical_json")
# History pages read only what a summary shows: never the stored metrics, which hold matrices and breakdowns.
JOB_DETAIL = ("metrics", "spend")


def _without(*fields: str) -> dict:
    """A projection leaving fields out. A new dict each time, as a driver may add to the one it is given."""
    return {field: False for field in fields}
ACTIVE = ("running", "cancel_requested")
TERMINAL = ("completed", "failed", "cancelled", "budget_exhausted")


class DatasetRepository:
    def __init__(self, collection: Collection):
        self.collection = collection

    def ensure_indexes(self) -> None:
        self.collection.create_index([("name", ASCENDING), ("version", ASCENDING)], unique=True, name="name_version")
        self.collection.create_index([("created_at", DESCENDING), ("_id", DESCENDING)], name="created_id")
        self.collection.create_index([("name", ASCENDING), ("original_sha256", ASCENDING)], name="name_original")

    def create(self, document: dict) -> dict:
        """Insert the next version of the document's name. The unique index settles a race."""
        for _ in range(VERSION_ATTEMPTS):
            latest = self.collection.find_one({"name": document["name"]}, {"version": True}, sort=[("version", DESCENDING)])
            record = {**document, "version": 1 if latest is None else latest["version"] + 1}
            try:
                self.collection.insert_one(record)
                return {name: value for name, value in record.items() if name not in DATASET_CONTENT}
            except DuplicateKeyError:
                continue
        raise ApiError(409, "version_conflict", "too many concurrent versions of this dataset name; try again")

    def find_upload(self, name: str, original_sha256: str) -> dict | None:
        return self.collection.find_one({"name": name, "original_sha256": original_sha256}, _without(*DATASET_CONTENT), sort=[("version", ASCENDING)])

    def get(self, dataset_id: str, *, with_content: bool = False) -> dict | None:
        return self.collection.find_one({"_id": dataset_id}, None if with_content else _without("original"))

    def page(self, name: str | None, limit: int, offset: int) -> tuple[list[dict], int]:
        query = {} if name is None else {"name": name}
        items = list(self.collection.find(query, _without(*DATASET_CONTENT)).sort(NEWEST_FIRST).skip(offset).limit(limit))
        return items, self.collection.count_documents(query)


class EvaluationRepository:
    def __init__(self, collection: Collection):
        self.collection = collection

    def ensure_indexes(self) -> None:
        # Every job has an idempotency key: the client's, or its own ID, so the unique index needs no partial filter.
        self.collection.create_index([("idempotency_key", ASCENDING)], unique=True, name="idempotency")
        self.collection.create_index([("status", ASCENDING), ("created_at", ASCENDING), ("_id", ASCENDING)], name="queue")
        self.collection.create_index([("status", ASCENDING), ("lease.expires_at", ASCENDING)], name="leases")
        self.collection.create_index([("created_at", DESCENDING), ("_id", DESCENDING)], name="created_id")
        for field in ("dataset.dataset_id", "ruleset.ruleset_id", "status", "execution_mode", "configuration.classification_backend", "configuration.models"):
            self.collection.create_index([(field, ASCENDING), ("created_at", DESCENDING), ("_id", DESCENDING)], name=f"{field}_created")

    def insert(self, document: dict) -> dict:
        """Insert a job, or return the one already holding its idempotency key."""
        try:
            self.collection.insert_one(document)
            return document
        except DuplicateKeyError:
            existing = self.by_idempotency_key(document["idempotency_key"])
            if existing is None:
                raise
            return existing

    def by_idempotency_key(self, key: str) -> dict | None:
        return self.collection.find_one({"idempotency_key": key})

    def get(self, evaluation_id: str, *, summary: bool = False) -> dict | None:
        return self.collection.find_one({"_id": evaluation_id}, _without(*JOB_DETAIL) if summary else None)

    def get_any(self, evaluation_id: str) -> dict | None:
        """A job by ID alone, for the worker."""
        return self.collection.find_one({"_id": evaluation_id})

    def page(self, query: dict, limit: int, offset: int) -> tuple[list[dict], int]:
        query = dict(query)
        items = list(self.collection.find(query, _without(*JOB_DETAIL)).sort(NEWEST_FIRST).skip(offset).limit(limit))
        return items, self.collection.count_documents(query)

    def many(self, evaluation_ids: list[str]) -> list[dict]:
        return list(self.collection.find({"_id": {"$in": evaluation_ids}}))

    def claim(self, worker_id: str, moment: datetime, expires_at: datetime) -> dict | None:
        """Atomically take the oldest queued job. MongoDB applies one findAndModify at a time to a document, so two
        workers can never both move the same job out of queued."""
        return self.collection.find_one_and_update(
            {"status": "queued"},
            {"$set": {"status": "running", "lease": {"worker_id": worker_id, "claimed_at": moment, "heartbeat_at": moment, "expires_at": expires_at}, "updated_at": moment},
             "$inc": {"attempts": 1}},
            sort=[("created_at", ASCENDING), ("_id", ASCENDING)],
            return_document=ReturnDocument.AFTER,
        )

    def owned_update(self, evaluation_id: str, worker_id: str, changes: dict, *, statuses=ACTIVE, extra: dict | None = None) -> dict | None:
        """Apply changes only while this worker still holds the job's lease. None: the lease was lost."""
        update = {"$set": changes, **(extra or {})}
        return self.collection.find_one_and_update(
            {"_id": evaluation_id, "lease.worker_id": worker_id, "status": {"$in": list(statuses)}},
            update,
            return_document=ReturnDocument.AFTER,
        )

    def transition(self, evaluation_id: str, expected: dict, changes: dict) -> dict | None:
        """Apply changes only if the job still matches expected. None: it changed first."""
        return self.collection.find_one_and_update({"_id": evaluation_id, **expected}, {"$set": changes}, return_document=ReturnDocument.AFTER)

    def stale(self, moment: datetime) -> list[dict]:
        return list(self.collection.find({"status": {"$in": list(ACTIVE)}, "lease.expires_at": {"$lt": moment}}).sort([("created_at", ASCENDING), ("_id", ASCENDING)]))


class ResultRepository:
    def __init__(self, collection: Collection):
        self.collection = collection

    def ensure_indexes(self) -> None:
        self.collection.create_index([("evaluation_id", ASCENDING), ("index", ASCENDING)], unique=True, name="evaluation_index")
        self.collection.create_index([("evaluation_id", ASCENDING), ("receipt_id", ASCENDING)], name="evaluation_receipt")

    def put(self, document: dict) -> None:
        """Store a receipt's outcome. Keyed by evaluation and receipt, so storing it again replaces, never adds."""
        self.collection.replace_one({"_id": document["_id"]}, document, upsert=True)

    def update(self, result_id: str, changes: dict) -> None:
        self.collection.update_one({"_id": result_id}, {"$set": changes})

    def done(self, evaluation_id: str) -> set[str]:
        return {item["receipt_id"] for item in self.collection.find({"evaluation_id": evaluation_id}, {"receipt_id": True})}

    def all(self, evaluation_id: str) -> list[dict]:
        return list(self.collection.find({"evaluation_id": evaluation_id}).sort([("index", ASCENDING)]))

    def page(self, evaluation_id: str, query: dict, limit: int, offset: int) -> tuple[list[dict], int]:
        query = {"evaluation_id": evaluation_id, **query}
        items = list(self.collection.find(query).sort([("index", ASCENDING)]).skip(offset).limit(limit))
        return items, self.collection.count_documents(query)


class UsageRepository:
    def __init__(self, collection: Collection):
        self.collection = collection

    def ensure_indexes(self) -> None:
        self.collection.create_index([("evaluation_id", ASCENDING), ("sequence", ASCENDING)], name="evaluation_sequence")
        self.collection.create_index([("evaluation_id", ASCENDING), ("receipt_id", ASCENDING)], name="evaluation_receipt")

    def record(self, document: dict) -> bool:
        """Record one call once. The ID names the call, so recording it again is refused and counts nothing twice."""
        try:
            self.collection.insert_one(document)
            return True
        except DuplicateKeyError:
            return False

    def all(self, evaluation_id: str) -> list[dict]:
        return list(self.collection.find({"evaluation_id": evaluation_id}).sort([("sequence", ASCENDING), ("_id", ASCENDING)]))

    def for_receipts(self, evaluation_id: str, receipt_ids: list[str]) -> list[dict]:
        return list(self.collection.find({"evaluation_id": evaluation_id, "receipt_id": {"$in": receipt_ids}}).sort([("sequence", ASCENDING), ("_id", ASCENDING)]))

    def next_sequence(self, evaluation_id: str) -> int:
        last = self.collection.find_one({"evaluation_id": evaluation_id}, {"sequence": True}, sort=[("sequence", DESCENDING)])
        return 0 if last is None else last["sequence"] + 1
