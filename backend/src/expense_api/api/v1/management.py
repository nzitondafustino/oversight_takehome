"""Explicit record management. Deletions retain immutable contents and references for audit."""
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import Field
from pymongo.database import Database

from expense_api.dependencies import get_database, get_extraction_service, get_ruleset_service, get_lifecycle_service
from expense_api.api.json_body import DecimalJSONRoute
from expense_api.schemas.documents import ReviewCreate
from expense_api.schemas.rulesets import RuleEditRequest, TransitionRequest, ReviewRequest, ApproveRequest, PublishRequest
from expense_api.services.extractions import ExtractionService
from expense_api.services.rulesets import RulesetService
from expense_api.errors import ApiError
from expense_api.schemas.common import RequestBody, errors
from expense_api.services.rulesets import now
from expense_api.services.lifecycle import LifecycleService

router = APIRouter(prefix="/v1", tags=["record management"], route_class=DecimalJSONRoute)
DB = Annotated[Database, Depends(get_database)]
VISIBLE = {"deleted_at": {"$exists": False}}


class DatasetEdit(RequestBody):
    description: str | None = Field(max_length=2000)


class EvaluationEdit(RequestBody):
    label: str | None = Field(max_length=120)


class RuleRemoval(RequestBody):
    actor: str = Field(min_length=1, max_length=200, pattern=r"\S")
    rationale: str = Field(min_length=1, max_length=2000, pattern=r"\S")


@router.delete("/rulesets/{ruleset_id}/rules/{rule_id}", status_code=201, responses=errors(404, 409, 422, 503))
def remove_rule(ruleset_id: str, rule_id: str, body: RuleRemoval, service: Annotated[RulesetService, Depends(get_ruleset_service)]):
    """Create a new draft with the rule inactive; published artifacts remain immutable."""
    from expense_api.api.v1.rulesets import detail
    return detail(service.edit(ruleset_id, RuleEditRequest(actor=body.actor, rationale=body.rationale,
        changes=[{"rule_id": rule_id, "set": {"status": "not_evaluable"}}])))


@router.patch("/documents/extractions/{extraction_id}/reviews/{review_id}", status_code=201, responses=errors(404, 409, 422, 503))
def revise_review(extraction_id: str, review_id: str, body: ReviewCreate, database: DB,
                  service: Annotated[ExtractionService, Depends(get_extraction_service)]):
    """Record revised decisions under a new review identity, retaining the original signed review."""
    record = _record(database, "extraction_reviews", review_id)
    if record["extraction_id"] != extraction_id:
        raise ApiError(404, "review_not_found", "no such review for this extraction")
    from expense_api.api.v1.documents import review_detail
    return review_detail(service.review(extraction_id, body))


def _record(database: Database, collection: str, identifier: str) -> dict:
    record = database[collection].find_one({"_id": identifier, **VISIBLE})
    if record is None:
        raise ApiError(404, "record_not_found", "the record was not found or has been deleted")
    return record


@router.patch("/evaluations/datasets/{dataset_id}", responses=errors(404, 422, 503))
def edit_dataset(dataset_id: str, body: DatasetEdit, database: DB):
    _record(database, "evaluation_datasets", dataset_id)
    database["evaluation_datasets"].update_one({"_id": dataset_id, **VISIBLE}, {"$set": {"description": body.description}})
    return {"dataset_id": dataset_id, "description": body.description}


@router.delete("/evaluations/datasets/{dataset_id}", responses=errors(404, 409, 503))
def delete_dataset(dataset_id: str, database: DB):
    _record(database, "evaluation_datasets", dataset_id)
    # Keep datasets while any worker may still need their canonical receipts.
    if database["evaluations"].find_one({"dataset.dataset_id": dataset_id, "status": {"$in": ["queued", "running", "cancel_requested"]}, **VISIBLE}):
        raise ApiError(409, "dataset_in_use", "cancel or finish evaluations using this dataset before deleting it")
    database["evaluation_datasets"].update_one({"_id": dataset_id, **VISIBLE}, {"$set": {"deleted_at": now()}})
    return {"deleted": True, "dataset_id": dataset_id}


@router.patch("/evaluations/{evaluation_id}", responses=errors(404, 422, 503))
def edit_evaluation(evaluation_id: str, body: EvaluationEdit, database: DB):
    _record(database, "evaluations", evaluation_id)
    database["evaluations"].update_one({"_id": evaluation_id, **VISIBLE}, {"$set": {"label": body.label}})
    return {"evaluation_id": evaluation_id, "label": body.label}


@router.delete("/evaluations/{evaluation_id}", responses=errors(404, 409, 503))
def delete_evaluation(evaluation_id: str, database: DB):
    record = _record(database, "evaluations", evaluation_id)
    if record["status"] not in ("completed", "failed", "cancelled", "budget_exhausted"):
        raise ApiError(409, "evaluation_active", "cancel or finish the evaluation before deleting it")
    changed = database["evaluations"].update_one({"_id": evaluation_id, "status": record["status"], **VISIBLE}, {"$set": {"deleted_at": now()}})
    if changed.modified_count != 1:
        raise ApiError(409, "concurrent_change", "the evaluation changed; refresh before deleting it")
    return {"deleted": True, "evaluation_id": evaluation_id}


@router.delete("/documents/extractions/{extraction_id}/reviews/{review_id}", responses=errors(404, 409, 503))
def delete_review(extraction_id: str, review_id: str, database: DB):
    record = _record(database, "extraction_reviews", review_id)
    if record["extraction_id"] != extraction_id:
        raise ApiError(404, "review_not_found", "no such review for this extraction")
    if database["rulesets"].find_one({"compilation.prepared_review.review_id": review_id}):
        raise ApiError(409, "review_in_use", "this review is used by a ruleset version; keep it for reproducible approval")
    database["extraction_reviews"].update_one({"_id": review_id, **VISIBLE}, {"$set": {"deleted_at": now()}})
    return {"deleted": True, "review_id": review_id}


@router.delete("/rulesets/{ruleset_id}", responses=errors(404, 409, 503))
def delete_draft(ruleset_id: str, database: DB):
    """Hide a draft or in-review version, retaining its immutable artifact and reserving its number."""
    record = _record(database, "rulesets", ruleset_id)
    if record["status"] not in ("draft", "in_review"):
        raise ApiError(409, "ruleset_not_draft", "only draft or in-review ruleset versions can be deleted")
    if database["evaluations"].find_one({"ruleset.ruleset_id": ruleset_id, "status": {"$in": ["queued", "running", "cancel_requested"]}, **VISIBLE}):
        raise ApiError(409, "ruleset_in_use", "cancel or finish evaluations using this draft before deleting it")
    changed = database["rulesets"].update_one({"_id": ruleset_id, "status": record["status"], **VISIBLE}, {"$set": {"deleted_at": now()}})
    if changed.modified_count != 1:
        raise ApiError(409, "concurrent_change", "the version changed; refresh before deleting it")
    return {"deleted": True, "ruleset_id": ruleset_id}


class DuplicatePublication(RequestBody):
    actor: str = Field(min_length=1, max_length=200, pattern=r"\S")
    rationale: str = Field(min_length=1, max_length=1000, pattern=r"\S")


def _published_duplicate(database: Database, record: dict):
    return database["rulesets"].find_one({"artifact_sha256": record["artifact_sha256"], "status": "published", "compilation": {"$ne": None}, **VISIBLE}, sort=[("published_at", -1)])


@router.get("/rulesets/{ruleset_id}/duplicate-publication", responses=errors(404, 503))
def duplicate_publication(ruleset_id: str, database: DB):
    record = _record(database, "rulesets", ruleset_id)
    from expense_api.repositories import RulesetRepository
    superseded = RulesetRepository(database["rulesets"]).get(ruleset_id).get("superseded_by")
    match = _published_duplicate(database, record) if not superseded and record["status"] in ("draft", "in_review") else None
    return {"published": None if match is None else {"ruleset_id": match["_id"], "name": match["name"], "version": match["version"], "mode": match["publication_mode"]}}


@router.post("/rulesets/{ruleset_id}/duplicate-publication", status_code=201, responses=errors(404, 409, 422, 500, 503))
def publish_duplicate(ruleset_id: str, body: DuplicatePublication, database: DB,
                      service: Annotated[RulesetService, Depends(get_ruleset_service)],
                      lifecycle: Annotated[LifecycleService, Depends(get_lifecycle_service)]):
    """Publish an identical successor with verified source provenance and a fresh approval; retain the original."""
    from expense_validator import Approval
    from expense_api.api.v1.rulesets import detail
    record = service.get(ruleset_id)
    if record.get("superseded_by"):
        return detail(service.get(record["superseded_by"]["ruleset_id"]))
    if record["status"] not in ("draft", "in_review"):
        raise ApiError(409, "invalid_transition", "only draft or in-review versions can use duplicate publication")
    match = _published_duplicate(database, record)
    if match is None:
        raise ApiError(409, "no_identical_publication", "no compiled published version has this exact artifact; similar rules require normal review")
    if (record.get("compilation") or {}).get("extraction_id"):
        service.extractions.refuse_open_change_requests(record["compilation"]["extraction_id"], "approval")
    # Rebuild and verify the donor before creating anything. No model calls or inherited approval.
    lifecycle.compilation.verify(lifecycle.compilation.rebuild(match), match, Approval.model_validate_json(match["approval_json"]), match["publication_mode"])
    if match["publication_mode"] not in lifecycle.settings.allowed_publication_modes:
        raise ApiError(422, "publication_mode_not_allowed", "the matching publication mode is not enabled on this deployment")
    draft = service.copy_duplicate(record, match, body.actor, body.rationale)
    identifier = draft["_id"]
    lifecycle.submit(identifier, TransitionRequest(actor=body.actor, reason=body.rationale))
    review = match.get("review") or {}
    lifecycle.review(identifier, ReviewRequest(actor=body.actor, decision="accepted", reason=body.rationale,
        decisions=review.get("decisions"), review_policy=review.get("policy") or "exceptions_only"))
    lifecycle.approve(identifier, ApproveRequest(actor=body.actor, mode=match["publication_mode"], note=body.rationale))
    return detail(lifecycle.publish(identifier, PublishRequest(actor=body.actor)))
