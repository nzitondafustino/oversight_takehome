"""The evaluation workbench: datasets, background evaluations, their metrics, results, spending, comparison and export.

Creating an evaluation only persists a queued job; the worker process (python -m expense_api.worker) runs it.
Poll GET /v1/evaluations/{evaluation_id} for its status and progress.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from pydantic import AwareDatetime

from expense_api.api.json_body import DecimalJSONRoute, as_standard_json
from expense_api.dependencies import get_dataset_service, get_evaluation_service, get_settings
from expense_api.config import Settings
from expense_api.errors import ApiError
from expense_api.schemas.common import errors
from expense_api.schemas.evaluations import (
    CompareRequest,
    DatasetDetail,
    DatasetPage,
    DatasetSummary,
    EvaluationCreate,
    EvaluationDetail,
    EvaluationPage,
    EvaluationSummary,
    ResultPage,
    Status,
)
from expense_api.services.datasets import DatasetService, load_canonical
from expense_api.services.evaluations import EvaluationService, detail, summary_of

router = APIRouter(prefix="/v1/evaluations", tags=["evaluations"], route_class=DecimalJSONRoute)
Datasets = Annotated[DatasetService, Depends(get_dataset_service)]
Evaluations = Annotated[EvaluationService, Depends(get_evaluation_service)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]
EXPORT_FORMATS = ("json", "csv")


# Dataset routes come first, so "datasets" is never read as an evaluation ID.


@router.post("/datasets", status_code=201, response_model=DatasetSummary, responses={200: {"model": DatasetSummary, "description": "These exact bytes were uploaded before: that version"}, **errors(413, 415, 422, 503)})
def upload_dataset(
    response: Response,
    datasets: Datasets,
    settings: Annotated[Settings, Depends(get_settings)],
    file: Annotated[UploadFile, File(description="A .yaml, .yml, .json or .jsonl dataset")],
    name: Annotated[str | None, Form(max_length=64, description="Required unless the file names the dataset")] = None,
    description: Annotated[str | None, Form(max_length=2000)] = None,
    context_complete: Annotated[bool | None, Form(description="Whether the receipts are all that could matter to a rule spanning receipts; true by default")] = None,
):
    """Store a dataset as a new immutable version of its name. The same bytes again return the stored version."""
    raw = file.file.read(settings.max_dataset_bytes + 1)
    if len(raw) > settings.max_dataset_bytes:
        raise ApiError(413, "dataset_too_large", f"a dataset is at most {settings.max_dataset_bytes} bytes")
    record, created = datasets.create(filename=file.filename or "", raw=raw, name=name, description=description, context_complete=context_complete)
    response.status_code = 201 if created else 200
    response.headers["Location"] = f"/v1/evaluations/datasets/{record['_id']}"
    return dataset_summary(record)


@router.get("/datasets", response_model=DatasetPage, responses=errors(422, 503))
def list_datasets(datasets: Datasets, name: Annotated[str | None, Query(max_length=64)] = None, limit: Limit = 20, offset: Offset = 0):
    """Dataset versions, newest first, without their content."""
    items, total = datasets.page(name, limit, offset)
    return DatasetPage(items=[dataset_summary(item) for item in items], total=total, limit=limit, offset=offset)


@router.get("/datasets/{dataset_id}", response_model=DatasetDetail, responses=errors(404, 422, 500, 503))
def get_dataset(dataset_id: str, datasets: Datasets, include_cases: Annotated[bool, Query(description="Include the canonical cases")] = False):
    record = datasets.get(dataset_id)
    # Shown as standard JSON numbers. The record is the canonical text, exact, under canonical_sha256.
    return DatasetDetail(**dataset_summary(record), cases=as_standard_json(load_canonical(record)["cases"]) if include_cases else None)


@router.post("", status_code=201, response_model=EvaluationDetail, responses={200: {"model": EvaluationDetail, "description": "The evaluation this idempotency_key already made"}, **errors(403, 404, 409, 422, 503)})
def create_evaluation(body: EvaluationCreate, response: Response, evaluations: Evaluations):
    """Queue an evaluation of one dataset version against one ruleset version. The worker runs it."""
    job, created = evaluations.create(body)
    response.status_code = 201 if created else 200
    response.headers["Location"] = f"/v1/evaluations/{job['_id']}"
    return detail(evaluations.get(job["_id"]))


@router.get("", response_model=EvaluationPage, responses=errors(422, 503))
def list_evaluations(
    evaluations: Evaluations,
    dataset_id: Annotated[str | None, Query(max_length=64)] = None,
    ruleset_id: Annotated[str | None, Query(max_length=64)] = None,
    backend: Annotated[str | None, Query(pattern=r"^[a-z][a-z0-9_-]{0,39}$")] = None,
    model: Annotated[str | None, Query(max_length=200, description="Any model the run may call: a backend's, the reviewer's or the translator's")] = None,
    status: Annotated[Status | None, Query()] = None,
    execution_mode: Annotated[str | None, Query(pattern=r"^(replay|live)$")] = None,
    created_from: Annotated[AwareDatetime | None, Query(description="Inclusive, with a time zone")] = None,
    created_to: Annotated[AwareDatetime | None, Query(description="Exclusive, with a time zone")] = None,
    limit: Limit = 20,
    offset: Offset = 0,
):
    """Evaluations, newest first, with key metrics and spending but no results."""
    filters = {"dataset_id": dataset_id, "ruleset_id": ruleset_id, "backend": backend, "model": model, "status": status, "execution_mode": execution_mode, "created_from": created_from, "created_to": created_to}
    items, total = evaluations.page(filters, limit, offset)
    return EvaluationPage(items=[EvaluationSummary(**summary_of(item)) for item in items], total=total, limit=limit, offset=offset)


@router.post("/compare", responses=errors(404, 409, 422, 503))
def compare(body: CompareRequest, evaluations: Evaluations):
    """Completed evaluations side by side, saying whether they can be compared directly."""
    return evaluations.compare(body.evaluation_ids)


@router.get("/{evaluation_id}", response_model=EvaluationDetail, responses=errors(404, 422, 503))
def get_evaluation(evaluation_id: str, evaluations: Evaluations):
    """Status, progress, configuration and spending by model, backend and call purpose."""
    return detail(evaluations.get(evaluation_id))


@router.get("/{evaluation_id}/metrics", responses=errors(404, 422, 500, 503))
def get_metrics(evaluation_id: str, evaluations: Evaluations):
    """The stored metrics of a finished run; provisional ones, never stored, while it runs."""
    return evaluations.metrics(evaluation_id)


@router.get("/{evaluation_id}/results", response_model=ResultPage, responses=errors(404, 422, 500, 503))
def get_results(
    evaluation_id: str,
    evaluations: Evaluations,
    receipt_id: Annotated[list[str] | None, Query()] = None,
    classification_correct: Annotated[bool | None, Query(description="true: every labelled line correct; false: any not. Receipts without labelled lines match neither")] = None,
    category: Annotated[list[str] | None, Query(description="A category predicted on any line")] = None,
    disposition: Annotated[list[str] | None, Query(description="flag, review or pass, as predicted")] = None,
    error: Annotated[bool | None, Query(description="true: the receipt or one of its lines failed")] = None,
    uncertain: Annotated[bool | None, Query(description="true: the receipt has an EXC-03 uncertainty signal")] = None,
    limit: Limit = 20,
    offset: Offset = 0,
):
    """Each receipt's core result, whole, with its answers, how it scored and what it cost, in dataset order."""
    if disposition and not set(disposition) <= {"flag", "review", "pass"}:
        raise ApiError(422, "invalid_filter", "disposition is flag, review or pass")
    filters = {"receipt_id": receipt_id, "classification_correct": classification_correct, "category": category, "disposition": disposition, "error": error, "uncertain": uncertain}
    items, total = evaluations.results_page(evaluation_id, filters, limit, offset)
    return ResultPage(evaluation_id=evaluation_id, items=items, total=total, limit=limit, offset=offset)


@router.get("/{evaluation_id}/export", responses={200: {"content": {"application/json": {}, "text/csv": {}}, "description": "The evaluation as a file"}, **errors(404, 409, 422, 500, 503)})
def export(evaluation_id: str, evaluations: Evaluations, format: Annotated[str, Query(description="json or csv")] = "json"):
    """A finished evaluation as a file: json holds the run, metrics, results and every call; csv one row per line."""
    if format not in EXPORT_FORMATS:
        raise ApiError(422, "unsupported_export_format", f"format is json or csv, not {format!r}", {"supported": list(EXPORT_FORMATS)})
    body = evaluations.export(evaluation_id, format)
    headers = {"Content-Disposition": f'attachment; filename="{evaluation_id}.{format}"', "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}
    return Response(content=body, media_type="application/json" if format == "json" else "text/csv; charset=utf-8", headers=headers)


@router.post("/{evaluation_id}/cancel", response_model=EvaluationDetail, responses=errors(404, 409, 422, 503))
def cancel(evaluation_id: str, evaluations: Evaluations):
    """Cancel a queued evaluation, or ask a running one to stop before its next receipt or provider call."""
    return detail(evaluations.cancel(evaluation_id))


def dataset_summary(record: dict) -> dict:
    return {
        "dataset_id": record["_id"],
        **{name: record[name] for name in ("name", "version", "description", "format", "filename", "schema_version", "context_complete", "original_sha256", "canonical_sha256", "stats", "created_at")},
    }
