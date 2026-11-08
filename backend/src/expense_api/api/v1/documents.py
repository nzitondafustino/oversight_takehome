"""Policy documents and their revisions; extractions of selected revisions, and their reviews.

A candidate ruleset version is made from a ready extraction with POST /v1/rulesets and {"extraction": {...}}.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from expense_api.api.json_body import DecimalJSONRoute
from expense_api.dependencies import get_document_service, get_extraction_service
from expense_api.schemas.common import errors
from expense_api.schemas.documents import (
    ComparisonPage,
    DocumentCreate,
    DocumentDetail,
    DocumentPage,
    DocumentId,
    DocumentSummary,
    ExtractionCreate,
    ExtractionListItem,
    ExtractionPage,
    ExtractionSummary,
    Filename,
    ReviewCreate,
    ReviewDetail,
    ReviewSummary,
    RevisionCreate,
)
from expense_api.services.documents import DocumentService
from expense_api.services.extractions import ExtractionService

router = APIRouter(prefix="/v1/documents", tags=["documents"], route_class=DecimalJSONRoute)
Documents = Annotated[DocumentService, Depends(get_document_service)]
Extractions = Annotated[ExtractionService, Depends(get_extraction_service)]


# Extraction routes come first, so "extractions" is never read as a document ID.


@router.post("/extractions", status_code=201, response_model=ExtractionSummary, responses=errors(403, 404, 409, 422, 503))
def create_extraction(body: ExtractionCreate, response: Response, extractions: Extractions):
    """Extract interpretations of exact document revisions with the core's PolicyCompiler, and store the snapshot."""
    extraction = extractions.create(body)
    response.headers["Location"] = f"/v1/documents/extractions/{extraction['_id']}"
    return extraction_summary(extraction, extractions)


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


@router.post("/{document_id}/revisions", status_code=201, response_model=DocumentDetail, responses=errors(404, 409, 413, 422, 503))
def create_revision(document_id: str, body: RevisionCreate, response: Response, documents: Documents):
    """Store changed content as the next revision. Earlier revisions never change."""
    record = documents.add_revision(document_id, body)
    response.headers["Location"] = f"/v1/documents/{document_id}?revision={record['revision']}"
    return DocumentDetail(**record)


def extraction_summary(extraction: dict, extractions: ExtractionService) -> ExtractionSummary:
    review_required = [comparison for comparison in extractions.result(extraction["_id"]).comparisons if comparison.review_required]
    return ExtractionSummary(review_required=review_required, **_extraction_fields(extraction))


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
