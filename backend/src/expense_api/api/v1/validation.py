"""POST /v1/validate and GET /v1/runs/{run_id}."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from expense_api.api.json_body import DecimalJSONRoute
from expense_api.dependencies import get_validation_service
from expense_api.schemas.common import errors
from expense_api.schemas.validation import ClassificationSelection, RunExecution, RunResponse, RunRuleset, ValidationRequest
from expense_api.services.discovery import run_setup
from expense_api.services.runs import load_results
from expense_api.services.validation import ValidationService

router = APIRouter(prefix="/v1", tags=["validation"], route_class=DecimalJSONRoute)
Service = Annotated[ValidationService, Depends(get_validation_service)]


@router.post("/validate", status_code=201, response_model=RunResponse, responses=errors(403, 404, 409, 422, 500, 502, 503))
def validate(body: ValidationRequest, response: Response, service: Service):
    """Validate receipts against one exact ruleset version, synchronously, and persist the completed run."""
    run = service.validate(body)
    response.headers["Location"] = f"/v1/runs/{run['_id']}"
    return run_response(run)


@router.get("/runs/{run_id}", response_model=RunResponse, responses=errors(404, 422, 503))
def get_run(run_id: str, service: Service):
    return run_response(service.get(run_id))


def run_response(run: dict) -> RunResponse:
    results = load_results(run)  # refused as corrupt_run, never returned empty, when the stored result is damaged
    return RunResponse(
        run_id=run["_id"],
        status=run["status"],
        ruleset=RunRuleset(**run["ruleset"]),
        execution_mode=run["execution_mode"],
        preview=run.get("preview", False),  # a Phase 1 run predates previews
        context_complete=run["context_complete"],
        classification=ClassificationSelection.model_validate(run["classification"]),
        results=results,
        setup=setup_of_results(results),
        execution=RunExecution(**run["execution"]),
        created_at=run["created_at"],
    )


def setup_of_results(results) -> dict | None:
    """The run's classification as every receipt's provenance and metadata recorded it; null when receipts differ."""
    recorded = {(result.provenance.classification_backend, result.metadata.model) for result in results.results}
    if len(recorded) != 1:
        return None
    backend, model = recorded.pop()
    return run_setup(backend, model)
