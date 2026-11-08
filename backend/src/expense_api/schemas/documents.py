"""Policy documents, their immutable revisions, extractions of selected revisions, and reviews of extractions."""

from datetime import datetime
from typing import Annotated, Any, Literal

from expense_validator import FieldComparison
from pydantic import BaseModel, Field, field_validator, model_validator

from expense_api.api.json_body import as_standard_json
from expense_api.schemas.common import RequestBody

# The core's filename rule (PolicyDocumentInput), with a shorter limit: rules cite a document by this name.
Filename = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*\.md$", max_length=120, examples=["meals-and-entertainment-policy.md"])]
Title = Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
DocumentId = Annotated[str, Field(pattern=r"^doc_[0-9a-f]{24}$")]


class DocumentSource(RequestBody):
    """Where the text came from. Recorded as given: provenance, not proof."""

    kind: str = Field(default="upload", pattern=r"^[a-z][a-z_]{0,39}$")
    description: str | None = Field(default=None, max_length=500)
    reference: str | None = Field(default=None, max_length=200)


class DocumentCreate(RequestBody):
    filename: Filename
    content: str = Field(description="UTF-8 Markdown in the policy-document format, stored exactly as sent")
    title: Title | None = None
    source: DocumentSource = DocumentSource()


class RevisionCreate(RequestBody):
    """New content for an existing document, which keeps its filename: rules cite documents by filename."""

    content: str
    based_on_revision: int | None = Field(default=None, ge=1, description="The revision this one replaces. Any other revision created since is a 409.")
    title: Title | None = None
    source: DocumentSource = DocumentSource()


class DocumentSummary(BaseModel):
    document_id: str
    revision: int
    filename: str
    title: str | None
    content_sha256: str
    size_bytes: int
    source: DocumentSource
    based_on_revision: int | None
    created_at: datetime


class DocumentDetail(DocumentSummary):
    content: str


class DocumentPage(BaseModel):
    items: list[DocumentSummary]  # each document's latest revision
    total: int
    limit: int
    offset: int


class RevisionRef(RequestBody):
    document_id: DocumentId
    revision: int = Field(ge=1)


class RecordedWith(RequestBody):
    """The generation settings readings were recorded live with, to replay exactly those readings."""

    extraction_model: str = Field(min_length=1, max_length=200, pattern=r"\S")
    temperature: float = Field(ge=0, le=2)


class ExtractionCreate(RequestBody):
    documents: list[RevisionRef] = Field(min_length=1, max_length=50, description="Exact revisions: together they are the policy candidate")
    execution_mode: Literal["replay", "live"] = Field(default="replay", description="replay reads recorded readings; live calls a paid model and must be allowed by the deployment")
    recording_set: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]{1,64}$", description="Replay only: the recorded readings to read")
    recorded_with: RecordedWith | None = Field(default=None, description="Replay only: read only readings recorded live with these settings")
    extraction_model: str | None = Field(default=None, min_length=1, max_length=200, pattern=r"\S", description="Live only: the model, from the deployment's allowed models")
    temperature: float | None = Field(default=None, description="Live only. The core checks it")
    timeout_seconds: int | None = Field(default=None, description="Live only. The core checks it")

    @model_validator(mode="after")
    def _settings_fit_the_mode(self):
        if self.execution_mode == "replay":
            if self.recording_set is None:
                raise ValueError("replay needs a recording_set")
            if any(value is not None for value in (self.extraction_model, self.temperature, self.timeout_seconds)):
                raise ValueError("replay reads recordings, so it takes no extraction_model, temperature or timeout_seconds")
        else:
            if self.recording_set is not None or self.recorded_with is not None:
                raise ValueError("live extraction records into the deployment's live recording set, so it takes no recording_set or recorded_with")
            if self.extraction_model is None:
                raise ValueError("live extraction needs an extraction_model")
        return self


ExtractionStatus = Literal["no_candidate", "review_required", "ready_for_approval"]


class ExtractedDocument(BaseModel):
    document_id: str
    revision: int
    filename: str
    content_sha256: str
    reading_error: str | None


class ExtractionProvenance(BaseModel):
    execution_mode: Literal["replay", "live"]
    recording_set: str
    recorded_with: RecordedWith | None  # the settings a later replay must use
    reading_kind: Literal["model", "simulation"]  # simulation readings never authorize production
    extraction_model: str | None
    temperature: float | None
    timeout_seconds: int | None
    overrides: list[str]
    prompt_sha256: str


class ReviewSummary(BaseModel):
    review_id: str
    review_sha256: str
    policy: str
    ready_for_approval: bool
    remaining: int
    error_issues: int
    created_at: datetime


class ExtractionListItem(BaseModel):
    """An extraction as listed: everything stored beside the core's result, which is not read for a list."""

    extraction_id: str
    status: ExtractionStatus  # never "approved": approval is a ruleset version's lifecycle step
    documents: list[ExtractedDocument]
    provenance: ExtractionProvenance
    extraction_sha256: str
    output_sha256: str
    candidate_ruleset_sha256: str | None
    result_sha256: str  # SHA-256 of the stored core ExtractionResult
    ready_for_approval: bool  # as the compiler says, before any review
    blockers: list[str]
    agreement_summary: dict[str, Any]
    comparison_count: int
    reviews: list[ReviewSummary]
    created_at: datetime


class ExtractionSummary(ExtractionListItem):
    review_required: list[FieldComparison]  # the fields an exceptions_only review must decide: read from the core's result


class ExtractionPage(BaseModel):
    items: list[ExtractionListItem]  # newest first
    total: int
    limit: int
    offset: int


class ComparisonPage(BaseModel):
    extraction_sha256: str
    items: list[FieldComparison]
    total: int
    limit: int
    offset: int


class ReviewCreate(RequestBody):
    actor: Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
    extraction_sha256: str = Field(pattern=r"^[0-9a-f]{64}$", description="The snapshot this review is of: another is a 409")
    review_policy: Literal["exceptions_only", "all_fields"] = "exceptions_only"
    decisions: list[dict[str, Any]] = Field(description="The core's ReviewDecision records: confirm, accept_regex, accept_llm, override or reject")

    @field_validator("decisions")
    @classmethod
    def _standard_json(cls, value):
        return as_standard_json(value)


class ReviewIssue(BaseModel):
    level: str
    code: str
    message: str
    comparison_id: str | None


class ReviewDetail(BaseModel):
    review_id: str
    extraction_id: str
    actor: dict[str, Any]
    extraction_sha256: str
    review_sha256: str
    candidate_ruleset_sha256: str | None
    policy: str
    decisions: list[dict[str, Any]]
    ready_for_approval: bool  # False: this review cannot become a candidate version
    complete: bool
    remaining: list[str]
    issues: list[ReviewIssue]
    created_at: datetime
