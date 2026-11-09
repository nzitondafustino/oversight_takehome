"""GET /v1/runs, GET /v1/runs/{run_id}/findings and GET /v1/runs/{run_id}/export. Read-only: nothing is re-validated.

GET /v1/runs/{run_id} itself stays in validation.py, unchanged.
"""

from typing import Annotated

from expense_validator import Mode
from fastapi import APIRouter, Depends, Query, Response
from pydantic import AwareDatetime

from expense_api.api.v1.validation import run_response
from expense_api.dependencies import get_run_service
from expense_api.errors import ApiError
from expense_api.schemas.common import errors
from expense_api.schemas.runs import Finding, FindingKind, FindingPage, RunPage, RunSummary
from expense_api.services.discovery import run_setup
from expense_api.services.runs import RunService, export_csv, export_json, load_results, matches

router = APIRouter(prefix="/v1/runs", tags=["runs"])
Runs = Annotated[RunService, Depends(get_run_service)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]
EXPORT_FORMATS = ("json", "csv", "pdf")


@router.get("", response_model=RunPage, responses=errors(422, 503))
def list_runs(
    runs: Runs,
    ruleset_id: Annotated[str | None, Query(max_length=64)] = None,
    ruleset_name: Annotated[str | None, Query(max_length=64)] = None,
    ruleset_version: Annotated[int | None, Query(ge=1, description="A version of a ruleset name: give ruleset_name with it")] = None,
    classification_backend: Annotated[str | None, Query(pattern=r"^[a-z][a-z0-9_-]{0,39}$", description="As the receipts' provenance reports it")] = None,
    execution_mode: Annotated[Mode | None, Query()] = None,
    preview: Annotated[bool | None, Query()] = None,
    created_from: Annotated[AwareDatetime | None, Query(description="Inclusive, with a time zone")] = None,
    created_to: Annotated[AwareDatetime | None, Query(description="Exclusive, with a time zone")] = None,
    limit: Limit = 20,
    offset: Offset = 0,
):
    """Runs, newest first, without their results."""
    if ruleset_version is not None and ruleset_name is None and ruleset_id is None:
        raise ApiError(422, "invalid_filter", "ruleset_version numbers the versions of one name: give ruleset_name or ruleset_id with it")
    filters = {"ruleset_id": ruleset_id, "ruleset_name": ruleset_name, "ruleset_version": ruleset_version, "classification_backend": classification_backend,
               "execution_mode": execution_mode, "preview": preview, "created_from": created_from, "created_to": created_to}
    items, total = runs.page(filters, limit, offset)
    return RunPage(items=[summary(run) for run in items], total=total, limit=limit, offset=offset)


@router.get("/{run_id}/findings", response_model=FindingPage, responses=errors(404, 422, 500, 503))
def list_findings(
    run_id: str,
    runs: Runs,
    kind: Annotated[list[FindingKind] | None, Query(description="exception, review_signal, open_question or uncertainty; repeat for several")] = None,
    severity: Annotated[list[str] | None, Query(description="Only exceptions have a severity")] = None,
    rule_id: Annotated[list[str] | None, Query()] = None,
    receipt_id: Annotated[list[str] | None, Query()] = None,
    disposition: Annotated[list[str] | None, Query(description="The disposition of the finding's receipt: flag, review or pass")] = None,
    limit: Limit = 50,
    offset: Offset = 0,
):
    """The run's findings in a fixed order, each with the core's record whole. Filters combine; values within one repeat."""
    if disposition and not set(disposition) <= {"flag", "review", "pass"}:
        raise ApiError(422, "invalid_filter", "disposition is flag, review or pass")
    _, items = runs.findings(run_id)
    selected = [item for item in items if matches(item, kind, severity, rule_id, receipt_id, disposition)]
    return FindingPage(run_id=run_id, items=[Finding(**item) for item in selected[offset:offset + limit]], total=len(selected), limit=limit, offset=offset)


@router.get("/{run_id}/export", responses={200: {"content": {"application/json": {}, "text/csv": {}, "application/pdf": {}}, "description": "The run as a file"}, **errors(404, 422, 500, 503)})
def export(run_id: str, runs: Runs, format: Annotated[str, Query(description="json, csv or pdf")] = "json"):
    """The stored run as a file: json is the run with each receipt in the brief's shape; csv is one row per finding; pdf is the full batch report."""
    if format not in EXPORT_FORMATS:
        raise ApiError(422, "unsupported_export_format", f"format is json, csv or pdf, not {format!r}", {"supported": list(EXPORT_FORMATS)})
    run = runs.get(run_id)
    if format == "json":
        body, media_type = export_json(run_response(run).model_dump(mode="json"), load_results(run)), "application/json"
    elif format == "pdf":
        from expense_api.services.pdf_reports import validation_pdf
        body, media_type = validation_pdf(run_response(run).model_dump(mode="json")), "application/pdf"
    else:
        body, media_type = export_csv(run, load_results(run)), "text/csv; charset=utf-8"
    headers = {
        "Content-Disposition": f'attachment; filename="{run_id}.{format}"',  # run IDs are run_ and hex: nothing to escape
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
    }
    return Response(content=body, media_type=media_type, headers=headers)


def summary(run: dict) -> RunSummary:
    stored = run.get("summary")  # absent on a run stored before summaries: shown as unknown, never guessed
    return RunSummary(
        run_id=run["_id"],
        status=run["status"],
        created_at=run["created_at"],
        ruleset=run["ruleset"],
        execution_mode=run["execution_mode"],
        preview=run.get("preview", False),
        context_complete=run["context_complete"],
        classification=run["classification"],
        classification_backend=None if stored is None else stored["classification_backend"],
        # Worked out when read: a run stored before the model was kept shows its model or nodes as null.
        setup=None if stored is None else run_setup(stored["classification_backend"], stored.get("classification_model"), "classification_model" in stored),
        receipt_count=len(run["receipt_ids"]),
        dispositions=run["dispositions"],
        counts=None if stored is None else stored["counts"],
    )
