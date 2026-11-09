"""Per-request services, built from what create_app put on app.state. Nothing mutable is shared between requests."""

from fastapi import Request
from pymongo.database import Database

from expense_api.config import Settings
from expense_api.repositories import DatasetRepository, DocumentApprovalRepository, DocumentProfileRepository, DocumentRepository, ExtractionRepository, RulesetRepository, RunRepository, ensure_indexes
from expense_api.services.compilation import Compilation
from expense_api.services.compilation_documents import CompilationDocuments
from expense_api.services.datasets import DatasetService
from expense_api.services.document_store import DocumentStore
from expense_api.services.documents import DocumentService
from expense_api.services.evaluations import EvaluationService
from expense_api.repositories.extraction_jobs import ExtractionJobRepository
from expense_api.services.extraction_jobs import ExtractionJobs, in_thread
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


def get_document_store(request: Request) -> DocumentStore:
    """The deployment's S3 document store, built on first use. Unconfigured, it is a 503 for the request that needs it."""
    state = request.app.state
    if state.document_store is None:
        state.document_store = DocumentStore(get_settings(request))
    return state.document_store


def get_document_service(request: Request) -> DocumentService:
    database = get_database(request)
    return DocumentService(get_settings(request), DocumentRepository(database["documents"], database["document_orphans"]), lambda: get_document_store(request), DocumentProfileRepository(database["document_profiles"]), DocumentApprovalRepository(database["document_approvals"]))


def get_compilation(request: Request) -> Compilation:
    """The compiler's adapter, reading compiled versions' documents back from S3 when it rebuilds one."""
    return Compilation(get_settings(request), CompilationDocuments(lambda: get_document_store(request)))


def get_extraction_service(request: Request) -> ExtractionService:
    settings, database = get_settings(request), get_database(request)
    return ExtractionService(settings, get_document_service(request), ExtractionRepository(database["extractions"], database["extraction_reviews"], database["extraction_change_requests"], database["rulesets"]), get_compilation(request))


def get_extraction_jobs(request: Request) -> ExtractionJobs:
    """Background extractions. The services it runs with read only app.state, which outlives the request."""
    run = getattr(request.app.state, "run_in_background", in_thread)
    return ExtractionJobs(ExtractionJobRepository(get_database(request)["extraction_jobs"]), get_extraction_service(request), run)


def get_ruleset_service(request: Request) -> RulesetService:
    settings = get_settings(request)
    return RulesetService(settings, RulesetRepository(get_database(request)["rulesets"]), get_compilation(request), get_extraction_service(request))


def get_lifecycle_service(request: Request) -> LifecycleService:
    settings = get_settings(request)
    return LifecycleService(settings, get_ruleset_service(request), get_compilation(request))


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
