"""Ruleset versions and their lifecycle. A ruleset_id names one exact, immutable version.

POST /v1/rulesets creates the next draft version of a name, and GET /v1/rulesets?name= lists a name's versions.
Each lifecycle step is a POST on the version: submit, review, approve, publish.
"""

import json
from typing import Annotated

from expense_validator import FieldComparison
from fastapi import APIRouter, Depends, Query, Response

from expense_api.api.json_body import DecimalJSONRoute
from expense_api.dependencies import get_lifecycle_service, get_ruleset_service
from expense_api.errors import ApiError
from expense_api.schemas.common import errors
from expense_api.schemas.documents import SourceLocation
from expense_api.schemas.rulesets import (
    Readiness,
    VersionChanges,
    RuleEditRequest,
    ApproveRequest,
    Compilation,
    ComparisonPage,
    PublishRequest,
    ReviewRequest,
    RulesetCreate,
    RulesetDetail,
    RulesetName,
    RulesetPage,
    RulesetSummary,
    TransitionRequest,
)
from expense_api.services.legacy import decisions_without_scope, without_scope
from expense_api.services.lifecycle import LifecycleService
from expense_api.services.rulesets import RulesetService, verified_artifact

router = APIRouter(prefix="/v1/rulesets", tags=["rulesets"], route_class=DecimalJSONRoute)
Service = Annotated[RulesetService, Depends(get_ruleset_service)]
Lifecycle = Annotated[LifecycleService, Depends(get_lifecycle_service)]
TRANSITION_ERRORS = errors(404, 409, 422, 503)


@router.post("", status_code=201, response_model=RulesetDetail, responses=errors(409, 422, 503))
def create_ruleset(body: RulesetCreate, response: Response, service: Service):
    """Store the next draft version of a name: compiled from policy documents, or an uploaded compiled artifact."""
    record = service.create(body)
    response.headers["Location"] = f"/v1/rulesets/{record['_id']}"
    return detail(record)


@router.get("", response_model=RulesetPage, responses=errors(422, 503))
def list_rulesets(
    service: Service,
    name: Annotated[RulesetName | None, Query(description="One name's versions")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    include_superseded: bool = False,
):
    """List ruleset versions, newest first, with their lifecycle state and without their artifacts."""
    items, total = service.page(name, limit, offset, include_superseded)
    return RulesetPage(items=[summary(record) for record in items], total=total, limit=limit, offset=offset)


@router.get("/{ruleset_id}", response_model=RulesetDetail, responses=errors(404, 422, 500, 503))
def get_ruleset(ruleset_id: str, service: Service):
    return detail(service.get(ruleset_id))


@router.get("/{ruleset_id}/changes", response_model=VersionChanges, responses=errors(404, 500, 503))
def get_changes(ruleset_id: str, service: Service):
    """What this version does differently from the version published before it: each rule field, before and after."""
    return VersionChanges(**service.changes(ruleset_id))


@router.get("/{ruleset_id}/comparisons", response_model=ComparisonPage, responses=errors(404, 409, 422, 503))
def get_comparisons(ruleset_id: str, service: Service, review_required: Annotated[bool, Query(description="Only the fields an exceptions_only review must decide")] = False):
    """The compiler's field comparisons for a compiled version: what review decisions name, by comparison_id."""
    record = service.get(ruleset_id)
    if record.get("compilation") is None:
        raise ApiError(409, "not_compiled", "this version was uploaded, so it has no comparisons to review")
    # A version compiled before the single-company migration stores each comparison with its customer_id.
    comparisons = [FieldComparison.model_validate(without_scope(item)) for item in json.loads(record["compilation"]["comparisons_json"])]
    return ComparisonPage(extraction_sha256=record["compilation"]["extraction_sha256"], comparisons=[item for item in comparisons if item.review_required or not review_required])


@router.get("/{ruleset_id}/comparisons/{comparison_id}/source", response_model=SourceLocation, responses=errors(404, 409, 500, 503))
def get_source(ruleset_id: str, comparison_id: str, service: Service):
    """A compared field's clause in the exact text this version was compiled from, verified, never approximate."""
    record = service.get(ruleset_id)
    if record.get("compilation") is None:
        raise ApiError(409, "not_compiled", "this version was uploaded, so it has no comparisons to review")
    comparisons = [FieldComparison.model_validate(without_scope(item)) for item in json.loads(record["compilation"]["comparisons_json"])]
    comparison = next((item for item in comparisons if item.comparison_id == comparison_id), None)
    if comparison is None:
        raise ApiError(404, "comparison_not_found", f"version {ruleset_id} has no comparison {comparison_id}")
    return service.source(ruleset_id, comparison)


@router.post("/{ruleset_id}/edits", status_code=201, response_model=RulesetDetail, responses=errors(404, 409, 422, 500, 503))
def edit_rules(ruleset_id: str, body: RuleEditRequest, response: Response, service: Service):
    """A new draft version with reviewed changes to some rules. This version, published or not, never changes:
    the compiler builds the draft again from the same documents with the changes applied, and validates it."""
    record = service.edit(ruleset_id, body)
    response.headers["Location"] = f"/v1/rulesets/{record['_id']}"
    return detail(record)


@router.get("/{ruleset_id}/readiness", response_model=Readiness, responses=errors(404, 503))
def get_readiness(ruleset_id: str, lifecycle: Lifecycle):
    """What stands between this version and publication now, each blocker linked to its rule or field."""
    return lifecycle.readiness(ruleset_id)


@router.post("/{ruleset_id}/submit", response_model=RulesetDetail, responses=TRANSITION_ERRORS)
def submit(ruleset_id: str, body: TransitionRequest, lifecycle: Lifecycle):
    """draft → in_review."""
    return detail(lifecycle.submit(ruleset_id, body))


@router.post("/{ruleset_id}/review", response_model=RulesetDetail, responses=TRANSITION_ERRORS)
def review(ruleset_id: str, body: ReviewRequest, lifecycle: Lifecycle):
    """Record a review decision: accepted keeps the version in_review for approval, rejected returns it to draft."""
    return detail(lifecycle.review(ruleset_id, body))


@router.post("/{ruleset_id}/approve", response_model=RulesetDetail, responses=TRANSITION_ERRORS)
def approve(ruleset_id: str, body: ApproveRequest, lifecycle: Lifecycle):
    """in_review → approved, once the core's approval of this exact version passes its publication safeguards."""
    return detail(lifecycle.approve(ruleset_id, body))


@router.post("/{ruleset_id}/publish", response_model=RulesetDetail, responses=TRANSITION_ERRORS)
def publish(ruleset_id: str, body: PublishRequest, lifecycle: Lifecycle):
    """approved → published, after the approval is checked again. Publishing a published version again is a no-op."""
    return detail(lifecycle.publish(ruleset_id, body))


def summary(record: dict) -> RulesetSummary:
    fields = {name: record.get(name) for name in RulesetSummary.model_fields if name not in ("ruleset_id", "compilation", "review")}
    review = record.get("review")
    if review is not None:  # decisions stored before the single-company migration name a customer_id
        review = {**review, "decisions": decisions_without_scope(review.get("decisions"))}
    compilation = record.get("compilation")
    if compilation is not None:
        compilation = Compilation(**{name: value for name, value in compilation.items() if name != "comparisons_json"})
    return RulesetSummary(ruleset_id=record["_id"], compilation=compilation, review=review, **fields)  # a Phase 1 version lacks the lifecycle fields: None


def detail(record: dict) -> RulesetDetail:
    return RulesetDetail(**summary(record).model_dump(), artifact=json.loads(verified_artifact(record)), transitions=record.get("transitions", []))
