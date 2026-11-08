"""Per-request services, built from what create_app put on app.state. Nothing mutable is shared between requests."""

from fastapi import Request
from pymongo.database import Database

from expense_api.config import Settings
from expense_api.repositories import DatasetRepository, DocumentRepository, ExtractionRepository, RulesetRepository, RunRepository, ensure_indexes
from expense_api.services.compilation import Compilation
from expense_api.services.datasets import DatasetService
from expense_api.services.documents import DocumentService
from expense_api.services.evaluations import EvaluationService
from expense_api.services.extractions import ExtractionService
from expense_api.services.lifecycle import LifecycleService
from expense_api.services.rulesets import ArtifactStore, RulesetService
from expense_api.services.runs import RunService
from expense_api.services.validation import ValidationService


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_raw_database(request: Request) -> Database:
    """The database as it is, for the health check."""
    return request.app.state.database


def get_database(request: Request) -> Database:
    """The database with its indexes, created here if MongoDB was down at startup."""
    state = request.app.state
    if not state.indexes_ready:
        ensure_indexes(state.database)  # a PyMongoError here is a 503
        state.indexes_ready = True
    return state.database


def get_document_service(request: Request) -> DocumentService:
    return DocumentService(get_settings(request), DocumentRepository(get_database(request)["documents"]))


def get_extraction_service(request: Request) -> ExtractionService:
    settings, database = get_settings(request), get_database(request)
    return ExtractionService(settings, get_document_service(request), ExtractionRepository(database["extractions"], database["extraction_reviews"]), Compilation(settings))


def get_ruleset_service(request: Request) -> RulesetService:
    settings = get_settings(request)
    return RulesetService(settings, RulesetRepository(get_database(request)["rulesets"]), Compilation(settings), get_extraction_service(request))


def get_lifecycle_service(request: Request) -> LifecycleService:
    settings = get_settings(request)
    return LifecycleService(settings, get_ruleset_service(request), Compilation(settings))


def get_validation_service(request: Request) -> ValidationService:
    settings = get_settings(request)
    return ValidationService(
        settings,
        get_ruleset_service(request),
        RunRepository(get_database(request)["runs"]),
        ArtifactStore(settings.artifact_dir),
        build_validator=request.app.state.build_validator,
    )


def get_run_service(request: Request) -> RunService:
    return RunService(RunRepository(get_database(request)["runs"]))


def get_dataset_service(request: Request) -> DatasetService:
    return DatasetService(get_settings(request), DatasetRepository(get_database(request)["evaluation_datasets"]))


def get_evaluation_service(request: Request) -> EvaluationService:
    return EvaluationService(get_settings(request), get_database(request), get_dataset_service(request), get_ruleset_service(request))
