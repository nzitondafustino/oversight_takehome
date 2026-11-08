"""MongoDB access, kept apart from the API and the services."""

from pymongo.database import Database
from pymongo.errors import OperationFailure

from expense_api.repositories.documents import DocumentRepository
from expense_api.repositories.evaluations import DatasetRepository, EvaluationRepository, ResultRepository, UsageRepository
from expense_api.repositories.extractions import ExtractionRepository
from expense_api.repositories.rulesets import RulesetRepository
from expense_api.repositories.runs import RunRepository

__all__ = ["DatasetRepository", "DocumentRepository", "EvaluationRepository", "ResultRepository", "UsageRepository", "ExtractionRepository", "RulesetRepository", "RunRepository", "ensure_indexes"]


def ensure_indexes(database: Database) -> None:
    _migrate_indexes(database)
    RulesetRepository(database["rulesets"]).ensure_indexes()
    RunRepository(database["runs"]).ensure_indexes()
    DocumentRepository(database["documents"]).ensure_indexes()
    ExtractionRepository(database["extractions"], database["extraction_reviews"]).ensure_indexes()
    DatasetRepository(database["evaluation_datasets"]).ensure_indexes()
    EvaluationRepository(database["evaluations"]).ensure_indexes()
    ResultRepository(database["evaluation_results"]).ensure_indexes()
    UsageRepository(database["evaluation_usage"]).ensure_indexes()


def _migrate_indexes(database: Database) -> None:
    """Remove legacy scope indexes only after checking the new uniqueness constraints.

    Never renumber versions, rewrite immutable artifacts, or delete history to merge namespaces.
    Conflicting legacy names/versions or idempotency keys require an explicit operator decision.
    """
    collections = ("rulesets", "runs", "documents", "extractions", "extraction_reviews",
                   "evaluation_datasets", "evaluations", "evaluation_results", "evaluation_usage")
    legacy = [(database[name], index) for name in collections
              for index, spec in database[name].index_information().items()
              if any(field == "customer_id" for field, _ in spec["key"])]
    if not legacy:
        return
    for name, fields in (("rulesets", ("name", "version")),
                         ("evaluation_datasets", ("name", "version")),
                         ("documents", ("document_id", "revision")),
                         ("evaluations", ("idempotency_key",))):
        duplicates = list(database[name].aggregate([
            {"$group": {"_id": {field: f"${field}" for field in fields}, "count": {"$sum": 1}}},
            {"$match": {"count": {"$gt": 1}}}, {"$limit": 1},
        ]))
        if duplicates:
            raise OperationFailure(f"Single-company index migration blocked: duplicate {name} {fields}; "
                                   "resolve conflicting legacy identities before startup. No history was changed.")
    for collection, index in legacy:
        collection.drop_index(index)
