"""Policy documents and their revisions; extractions of selected revisions, and their reviews.

A candidate ruleset version is made from a ready extraction with POST /v1/rulesets and {"extraction": {...}}.
"""

from typing import Annotated

from expense_validator.policy.param_use import helper_param_use, referenced_params
from fastapi import APIRouter, Depends, Query, Response

from expense_api.api.json_body import DecimalJSONRoute
from expense_api.dependencies import get_document_service, get_extraction_jobs, get_extraction_service, get_ruleset_service, get_lifecycle_service
from expense_api.schemas.common import errors
from expense_api.schemas.documents import (
    ApprovalAction,
    Baseline,
    ChangeRequest,
    ChangeRequestCreate,
    ChangeRequestResolve,
    ComparisonPage,
    DocumentCreate,
    DocumentDetail,
    DocumentPage,
    DocumentId,
    DocumentSummary,
    ExtractionCreate,
    ExtractionPlanSummary,
    ExtractionVersionCreate,
    ExtractionJob,
    ExtractionListItem,
    ExtractionPage,
    ExtractionSummary,
    Filename,
    ReviewCreate,
    ReviewDetail,
    ProfileUpdate,
    ReviewSummary,
    RevisionApproval,
    SourceLocation,
    RevisionCreate,
)
from expense_api.errors import ApiError
from expense_api.schemas.rulesets import RulesetCreate, RulesetDetail, TransitionRequest, ReviewRequest, ApproveRequest, PublishRequest
from expense_api.services.rulesets import RulesetService
from expense_api.services.lifecycle import LifecycleService
from expense_api.services.documents import DocumentService
from expense_api.services.extraction_jobs import ExtractionJobs
from expense_api.services.extractions import ExtractionService

router = APIRouter(prefix="/v1/documents", tags=["documents"], route_class=DecimalJSONRoute)
Documents = Annotated[DocumentService, Depends(get_document_service)]
Extractions = Annotated[ExtractionService, Depends(get_extraction_service)]
Jobs = Annotated[ExtractionJobs, Depends(get_extraction_jobs)]


# Extraction routes come first, so "extractions" is never read as a document ID.


@router.post("/extraction-plan", response_model=ExtractionPlanSummary, responses=errors(403, 404, 409, 422, 503))
def plan_extraction(body: ExtractionCreate, extractions: Extractions):
    """Resolve IDs, revisions, baseline and rule scope without extraction, provider calls or database writes."""
    revisions = extractions.check(body)
    plan = extractions.plan(body)
    return {**(plan.report or {"mode": "full"}), "recording_set": plan.recording_set,
        "documents": [{"document_id": item["document_id"], "revision": item["revision"]} for item in revisions]}


@router.post("/extractions/{extraction_id}/ruleset", status_code=201, response_model=RulesetDetail, responses=errors(404, 409, 422, 500, 503))
def extraction_version(extraction_id: str, body: ExtractionVersionCreate, extractions: Extractions,
                       rulesets: Annotated[RulesetService, Depends(get_ruleset_service)],
                       lifecycle: Annotated[LifecycleService, Depends(get_lifecycle_service)]):
    """Create a new version from any extraction mode; optionally accept, approve and publish when review is ready.

    Name defaults to the resolved baseline's name (or policies). Review defaults to the latest saved review.
    Compiler validation and publication-mode safeguards apply; unresolved decisions are never guessed.
    """
    from expense_api.api.v1.rulesets import detail
    extraction = extractions.get(extraction_id)
    name = body.name or (extraction.get("run") or {}).get("ruleset_name") or "policies"
    review_id = body.review_id
    if review_id is None and extraction["reviews"]:
        latest = max(extraction["reviews"], key=lambda review: (review["created_at"], review["_id"]))
        if not latest["ready_for_approval"]:
            raise ApiError(409, "review_not_ready", "the latest review is unresolved; resolve it or provide an explicit ready review_id")
        review_id = latest["_id"]
    if body.publish and body.mode not in lifecycle.settings.allowed_publication_modes:
        raise ApiError(422, "publication_mode_not_allowed", "this publication mode is not enabled")
    # A retry resumes the exact extraction/review version instead of accumulating duplicate draft versions.
    query = {"name": name, "compilation.extraction_id": extraction_id, "deleted_at": {"$exists": False},
        "compilation.prepared_review.review_id": review_id}
    record = rulesets.repository.collection.find_one(query, sort=[("version", -1)])
    if record is None:
        record = rulesets.create(RulesetCreate(name=name, extraction={"extraction_id": extraction_id, **({"review_id": review_id} if review_id else {})}))
    if body.publish:
        identifier = record["_id"]
        if record["status"] in ("approved", "published") and record["approval_mode"] != body.mode:
            raise ApiError(409, "publication_conflict", "the existing version was approved for another publication mode")
        if record["status"] == "draft":
            record = lifecycle.submit(identifier, TransitionRequest(actor=body.actor, reason=body.note))
        if record["status"] == "in_review":
            if not record.get("review"):
                lifecycle.review(identifier, ReviewRequest(actor=body.actor, decision="accepted", reason=body.note))
            record = lifecycle.approve(identifier, ApproveRequest(actor=body.actor, mode=body.mode, note=body.note))
        record = lifecycle.publish(identifier, PublishRequest(actor=body.actor))
    return detail(record)


@router.post("/extractions", status_code=201, response_model=ExtractionSummary, responses=errors(403, 404, 409, 422, 503))
def create_extraction(body: ExtractionCreate, response: Response, extractions: Extractions):
    """Extract interpretations of exact document revisions with the core's PolicyCompiler, and store the snapshot."""
    extraction = extractions.create(body)
    response.headers["Location"] = f"/v1/documents/extractions/{extraction['_id']}"
    return extraction_summary(extraction, extractions)


@router.post("/extraction-jobs", status_code=202, response_model=ExtractionJob, responses=errors(403, 404, 409, 422, 503))
def start_extraction_job(body: ExtractionCreate, response: Response, jobs: Jobs):
    """Start an extraction in the background, after checking it can run; follow it with GET /extraction-jobs/{job_id}.

    A live extraction reads the documents one at a time, so its progress names each document. Use this rather than
    POST /extractions for a live extraction: it is never cut off by a proxy's request timeout."""
    job = jobs.start(body)
    response.headers["Location"] = f"/v1/documents/extraction-jobs/{job['_id']}"
    return ExtractionJob(job_id=job["_id"], **job)


@router.get("/extraction-jobs/{job_id}", response_model=ExtractionJob, responses=errors(404, 503))
def get_extraction_job(job_id: str, jobs: Jobs):
    """Where a background extraction is: its progress, then its extraction_id, or the error that stopped it."""
    job = jobs.get(job_id)
    return ExtractionJob(job_id=job["_id"], **job)


@router.get("/extractions", response_model=ExtractionPage, responses=errors(422, 503))
def list_extractions(
    extractions: Extractions,
    document_id: Annotated[DocumentId | None, Query(description="Only extractions that read a revision of this document")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """Extractions, newest first, with their reviews. The core's stored results are not read: GET one for its review_required fields."""
    items, total = extractions.page(document_id, limit, offset)
    return ExtractionPage(items=[ExtractionListItem(**_extraction_fields(item)) for item in items], total=total, limit=limit, offset=offset)


@router.get("/extractions/{extraction_id}", response_model=ExtractionSummary, responses=errors(404, 422, 500, 503))
def get_extraction(extraction_id: str, extractions: Extractions):
    return extraction_summary(extractions.get(extraction_id), extractions)


@router.get("/extractions/{extraction_id}/comparisons", response_model=ComparisonPage, responses=errors(404, 422, 500, 503))
def get_comparisons(
    extraction_id: str,
    extractions: Extractions,
    review_required: Annotated[bool, Query(description="Only the fields an exceptions_only review must decide")] = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """Every field comparison, as the core gave it, a page at a time. Nothing is truncated in storage."""
    result = extractions.result(extraction_id)
    items = [comparison for comparison in result.comparisons if comparison.review_required or not review_required]
    return ComparisonPage(extraction_sha256=result.extraction_sha256, items=items[offset:offset + limit], total=len(items), limit=limit, offset=offset)


@router.post("/extractions/{extraction_id}/review", status_code=201, response_model=ReviewDetail, responses=errors(404, 409, 422, 503))
def review_extraction(extraction_id: str, body: ReviewCreate, response: Response, extractions: Extractions):
    """Review the snapshot with the core's review contract. Stored whether ready or not; only a ready one can become a candidate."""
    review = extractions.review(extraction_id, body)
    response.headers["Location"] = f"/v1/documents/extractions/{extraction_id}"
    return review_detail(review)


@router.get("/extractions/{extraction_id}/baseline", response_model=Baseline, responses=errors(404, 500, 503))
def extraction_baseline(extraction_id: str, extractions: Extractions):
    """This compilation beside the latest published version: what its rules change, which clauses changed, and which
    earlier decisions answer the same question (unchanged) or must be made again (changed)."""
    return Baseline(**extractions.baseline(extraction_id))


@router.get("/extractions/{extraction_id}/comparisons/{comparison_id}/source", response_model=SourceLocation, responses=errors(404, 500, 503))
def get_source(extraction_id: str, comparison_id: str, extractions: Extractions):
    """The compared field's clause in the exact revision read, with its lines and disputed values when verified."""
    return extractions.source(extraction_id, comparison_id)


@router.get("/extractions/{extraction_id}/reviews/{review_id}", response_model=ReviewDetail, responses=errors(404, 503))
def get_review(extraction_id: str, review_id: str, extractions: Extractions):
    """One stored review with its decisions, as sent: to continue a review where it was left."""
    return review_detail(extractions.get_review(extraction_id, review_id))


@router.get("/extractions/{extraction_id}/change-requests", response_model=list[ChangeRequest], responses=errors(404, 503))
def list_change_requests(extraction_id: str, extractions: Extractions):
    """Every change request raised on this extraction's fields, oldest first, open or resolved."""
    return [change_request(item) for item in extractions.change_requests(extraction_id)]


@router.post("/extractions/{extraction_id}/change-requests", status_code=201, response_model=ChangeRequest, responses=errors(404, 422, 503))
def create_change_request(extraction_id: str, body: ChangeRequestCreate, extractions: Extractions):
    """Ask for one compared field to be corrected. Nothing is rewritten: while it is open, no version is made from the
    extraction and no version made from it is approved."""
    return change_request(extractions.request_change(extraction_id, body))


@router.post("/extractions/{extraction_id}/change-requests/{change_request_id}/resolve", response_model=ChangeRequest, responses=errors(404, 409, 422, 503))
def resolve_change_request(extraction_id: str, change_request_id: str, body: ChangeRequestResolve, extractions: Extractions):
    """Close a change request, with a note saying how it was addressed, such as a new revision and extraction."""
    return change_request(extractions.resolve_change(extraction_id, change_request_id, body))


@router.post("", status_code=201, response_model=DocumentDetail, responses=errors(413, 422, 503))
def create_document(body: DocumentCreate, response: Response, documents: Documents):
    """Store a Markdown policy document as revision 1, exactly as sent."""
    record = documents.create(body)
    response.headers["Location"] = f"/v1/documents/{record['document_id']}?revision=1"
    return DocumentDetail(**record)


@router.get("", response_model=DocumentPage, responses=errors(422, 503))
def list_documents(
    documents: Documents,
    filename: Annotated[Filename | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """Each document's latest revision, newest document first, without content."""
    items, total = documents.page(filename, limit, offset)
    return DocumentPage(items=[DocumentSummary(**item) for item in items], total=total, limit=limit, offset=offset)


@router.get("/{document_id}", response_model=DocumentDetail, responses=errors(404, 422, 500, 503))
def get_document(document_id: str, documents: Documents, revision: Annotated[int | None, Query(ge=1, description="The latest when left out")] = None):
    return DocumentDetail(**documents.get(document_id, revision))


@router.patch("/{document_id}/profile", response_model=DocumentSummary, responses=errors(404, 409, 422, 503))
def update_profile(document_id: str, body: ProfileUpdate, documents: Documents):
    """Change a document's category, description or scope. Its revisions and their text never change."""
    return DocumentSummary(**documents.update_profile(document_id, body))


@router.get("/{document_id}/revisions/{revision}/approval", response_model=RevisionApproval, responses=errors(404, 503))
def get_approval(document_id: str, revision: int, documents: Documents):
    """One exact revision's approval and its whole history. A revision never acted on is a draft."""
    return RevisionApproval(**documents.approval(document_id, revision))


@router.post("/{document_id}/revisions/{revision}/approval", response_model=RevisionApproval, responses=errors(404, 409, 422, 500, 503))
def act_on_approval(document_id: str, revision: int, body: ApprovalAction, documents: Documents):
    """Submit, approve, or return to draft one exact revision, named by its content hash. Recorded with who and when."""
    return RevisionApproval(**documents.act_on_approval(document_id, revision, body))


@router.post("/{document_id}/revisions", status_code=201, response_model=DocumentDetail, responses=errors(404, 409, 413, 422, 503))
def create_revision(document_id: str, body: RevisionCreate, response: Response, documents: Documents):
    """Store changed content as the next revision. Earlier revisions never change."""
    record = documents.add_revision(document_id, body)
    response.headers["Location"] = f"/v1/documents/{document_id}?revision={record['revision']}"
    return DocumentDetail(**record)


def extraction_summary(extraction: dict, extractions: ExtractionService) -> ExtractionSummary:
    result = extractions.result(extraction["_id"])
    review_required = [comparison for comparison in result.comparisons if comparison.review_required]
    titles = {} if result.candidate is None else {rule.rule_id: rule.title for rule in result.candidate.ruleset.rules if getattr(rule, "title", None)}
    logic = {} if result.candidate is None else {rule.rule_id: rule_logic(rule) for rule in related_rules(result.candidate.ruleset.rules, {c.rule_id for c in review_required})}
    discrepancies = [item.model_dump() for item in result.discrepancies]
    return ExtractionSummary(review_required=review_required, rule_titles=titles, rule_logic=logic, discrepancies=discrepancies, **_extraction_fields(extraction))


def related_rules(rules, under_review: set[str]) -> list:
    """The rules under review, and every rule one of them replaces or is replaced by: what a decision on it affects."""
    def replaced(rule) -> set[str]:
        return {item.rule_id for item in getattr(rule, "replaces", None) or ()}

    related = set(under_review)
    for rule in rules:
        if rule.rule_id in under_review:
            related |= replaced(rule)
        elif replaced(rule) & under_review:
            related.add(rule.rule_id)
    return [rule for rule in rules if rule.rule_id in related]


def rule_logic(rule) -> dict:
    """What a candidate rule does, as the ruleset states it, and for each param where it is compared."""
    data = rule.model_dump(mode="json")
    conditions = {name: referenced_params(data.get(name)) for name in ("applies_when", "select")}
    uses = {
        name: {"in_conditions": [where for where, names in conditions.items() if name in names], "helper": helper_param_use(data.get("helper"), name)}
        for name in data.get("params") or {}
    }
    keep = ("kind", "scope", "helper", "outcome", "severity", "severity_cases", "applies_when", "select", "params", "title", "status", "replaces",
            "waived_by_ref", "modifies", "requires_ref", "directive", "policy_doc")
    return {**{name: data.get(name) for name in keep}, "param_uses": uses}


def change_request(record: dict) -> ChangeRequest:
    return ChangeRequest(change_request_id=record["_id"], **{name: value for name, value in record.items() if name != "_id"})


def _extraction_fields(extraction: dict) -> dict:
    """The stored fields of an extraction as ExtractionListItem names them, with a summary of each review."""
    reviews = [
        ReviewSummary(review_id=review["_id"], review_sha256=review["review_sha256"], policy=review["policy"], ready_for_approval=review["ready_for_approval"],
                      remaining=len(review["remaining"]), error_issues=sum(issue["level"] == "error" for issue in review["issues"]), created_at=review["created_at"])
        for review in extraction["reviews"]
    ]
    fields = {name: extraction[name] for name in ExtractionListItem.model_fields if name in extraction and name not in ("extraction_id", "reviews")}
    return {"extraction_id": extraction["_id"], "reviews": reviews, **fields}


def review_detail(review: dict) -> ReviewDetail:
    return ReviewDetail(review_id=review["_id"], **{name: review[name] for name in ReviewDetail.model_fields if name != "review_id"})
