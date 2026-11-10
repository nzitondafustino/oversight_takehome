"""Policy documents, their immutable revisions, extractions of selected revisions, and reviews of extractions."""

from datetime import datetime
from typing import Annotated, Any, Literal

from expense_validator import FieldComparison
from pydantic import BaseModel, Field, PrivateAttr, field_validator, model_validator

from expense_api.api.json_body import as_standard_json
from expense_api.schemas.common import RequestBody
from expense_api.schemas.rulesets import RuleChange

# The core's filename rule (PolicyDocumentInput), with a shorter limit: rules cite a document by this name.
Filename = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*\.md$", max_length=120, examples=["meals-and-entertainment-policy.md"])]
Title = Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
DocumentId = Annotated[str, Field(pattern=r"^doc_[0-9a-f]{24}$")]
Actor = Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]

# What a document is about, chosen by a person: never guessed from a filename. None is unclassified.
DocumentCategory = Literal[
    "meals_entertainment", "gifts_awards", "approvals_exceptions", "travel_regional",
    "receipts_documentation", "merchant_restrictions", "technology_equipment",
]
ScopeValue = Annotated[str, Field(min_length=1, max_length=60, pattern=r"\S")]


class DocumentScope(RequestBody):
    """Where a document applies, as given: regions, entities and expense types. Empty is not stated."""

    regions: list[ScopeValue] = Field(default_factory=list, max_length=20)
    entities: list[ScopeValue] = Field(default_factory=list, max_length=20)
    expense_types: list[ScopeValue] = Field(default_factory=list, max_length=40)


class ProfileUpdate(RequestBody):
    """A document's business description, kept apart from its revisions: changing it never touches a revision's text.

    Only the fields given are changed; a category of null makes the document unclassified.
    """

    actor: Actor
    category: DocumentCategory | None = None
    description: str | None = Field(default=None, max_length=500)
    scope: DocumentScope | None = None
    duplicate_of: DocumentId | None = Field(default=None, description="Marks this document a duplicate of another with identical content, hidden in the library; null restores it. Nothing is deleted.")


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
    # The document's business description, as given: optional, and changed later without a new revision.
    category: DocumentCategory | None = None
    description: str | None = Field(default=None, max_length=500)
    scope: DocumentScope | None = None


class RevisionCreate(RequestBody):
    """New content for an existing document, which keeps its filename: rules cite documents by filename."""

    content: str
    based_on_revision: int | None = Field(default=None, ge=1, description="The revision this one replaces. Any other revision created since is a 409.")
    title: Title | None = None
    source: DocumentSource = DocumentSource()


DocumentApprovalStatus = Literal["draft", "needs_review", "approved"]


class ApprovalEvent(BaseModel):
    action: Literal["submit", "approve", "return_to_draft"]
    from_status: DocumentApprovalStatus
    to_status: DocumentApprovalStatus
    actor: dict[str, Any]  # a claim: this deployment authenticates no one
    note: str | None
    content_sha256: str  # the exact text the action was about
    at: datetime


class RevisionApprovalState(BaseModel):
    """The approval of one exact revision. Every revision starts as a draft: none inherits another's approval."""

    status: DocumentApprovalStatus = "draft"
    content_sha256: str | None = None
    updated_at: datetime | None = None
    updated_by: dict[str, Any] | None = None


class RevisionApproval(RevisionApprovalState):
    document_id: str
    revision: int
    history: list[ApprovalEvent] = []


class ApprovalAction(RequestBody):
    """Move one revision's approval. content_sha256 names the exact text: another revision's, or a stale view's, is a 409."""

    action: Literal["submit", "approve", "return_to_draft"]
    actor: Actor
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    note: str | None = Field(default=None, max_length=2000)


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
    # The document's profile, the same for every revision. Absent for one never described: unclassified.
    category: DocumentCategory | None = None
    description: str | None = None
    scope: DocumentScope | None = None
    # Another document whose latest revision has identical content: this one is a hidden duplicate of it. Never deleted.
    duplicate_of: str | None = None
    profile_updated_at: datetime | None = None
    approval: RevisionApprovalState = RevisionApprovalState()  # of this exact revision


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
    reading_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]{1,64}$", description="The fresh reading to replay: set when the compilation that recorded it asked for fresh_reading")


class ExtractionCreate(RequestBody):
    _resolved_plan: Any = PrivateAttr(default=None)
    documents: list[RevisionRef] = Field(default=[], max_length=50, description="Explicit immutable revisions. With run_mode, IDs or filenames may instead resolve latest revisions")
    run_mode: Literal["full", "incremental", "remove"] | None = Field(default=None, description="full: selected or all latest documents; incremental: merge changed documents into a published base; remove: exclude documents and derived rules from a published base. None preserves the original exact-revision API")
    document_ids: list[DocumentId] = Field(default=[], max_length=50)
    filenames: list[Filename] = Field(default=[], max_length=50)
    base_ruleset_id: str | None = Field(default=None, min_length=1, max_length=64)
    ruleset_name: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$", description="Resolve the latest published version of this name; without it incremental/removal uses the latest published version company-wide")
    execution_mode: Literal["replay", "live"] = Field(default="replay", description="replay reads recorded readings; live calls a paid model and must be allowed by the deployment")
    recording_set: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]{1,64}$", description="Replay only: the recorded readings to read")
    recorded_with: RecordedWith | None = Field(default=None, description="Replay only: read only readings recorded live with these settings")
    extraction_model: str | None = Field(default=None, min_length=1, max_length=200, pattern=r"\S", description="Live only: the model, from the deployment's allowed models")
    temperature: float | None = Field(default=None, description="Live only. The core checks it")
    timeout_seconds: int | None = Field(default=None, description="Live only. The core checks it")
    fresh_reading: bool = Field(default=False, description="Live only: read every document with the model again, even where a reading with the same model and temperature is recorded. Each reading is recorded under this compilation's ID, and later steps replay exactly those")
    actor: str | None = Field(default=None, min_length=1, max_length=200, pattern=r"\S", description="Who asks for the rule overrides: needed with them")
    rule_overrides: list[RuleChange] = Field(default=[], max_length=50, description="Reviewed corrections of the draft rules, such as a param a revised policy changes. The compiler builds and validates them like the rule logic itself; each needs a rationale")
    carry_overrides_from: str | None = Field(default=None, min_length=1, max_length=64, description="A published ruleset version whose rule corrections this compilation keeps, each with its own reviewer and rationale. A rule given in rule_overrides replaces that rule's carried correction")

    @model_validator(mode="after")
    def _overrides_are_attributed(self):
        if self.rule_overrides and self.actor is None:
            raise ValueError("rule_overrides need an actor: who corrects the draft rules")
        if any(not (change.rationale or "").strip() for change in self.rule_overrides):
            raise ValueError("each rule override needs a rationale: why the draft rule changes")
        return self

    @model_validator(mode="after")
    def _settings_fit_the_mode(self):
        if self.run_mode is None and (not self.documents or self.document_ids or self.filenames or self.base_ruleset_id or self.ruleset_name):
            raise ValueError("select run_mode to resolve document selectors or a baseline; legacy extraction needs exact documents")
        if self.run_mode == "remove" and self.execution_mode != "replay":
            raise ValueError("removal reuses recorded policy evidence and cannot make live calls")
        if self.execution_mode == "replay":
            if self.recording_set is None and self.run_mode not in ("incremental", "remove"):
                raise ValueError("replay needs a recording_set")
            if any(value is not None for value in (self.extraction_model, self.temperature, self.timeout_seconds)):
                raise ValueError("replay reads recordings, so it takes no extraction_model, temperature or timeout_seconds")
            if self.fresh_reading:
                raise ValueError("fresh_reading asks the model again, so it needs execution_mode live")
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
    rule_overrides_from: str | None = None  # the published version whose rule corrections this extraction kept
    prompt_sha256: str


class ReviewSummary(BaseModel):
    review_id: str
    review_sha256: str
    policy: str
    ready_for_approval: bool
    remaining: int
    error_issues: int
    created_at: datetime


class VersionRef(BaseModel):
    ruleset_id: str
    name: str
    version: int
    status: str


class CompilationCall(BaseModel):
    model: str | None
    document_sha256: str
    latency_ms: int
    failed: bool
    cost_usd: str | None
    input_tokens: int | None
    output_tokens: int | None


class CompilationAnalytics(BaseModel):
    duration_ms: int
    provider_latency_ms: int
    live_calls: int
    failed_calls: int
    reused_documents: int
    documents: int
    new_spend_usd: str
    cost_completeness: Literal["complete", "incomplete"]
    unpriced_calls: int
    input_tokens: int | None
    output_tokens: int | None
    calls: list[CompilationCall]


class ExtractionListItem(BaseModel):
    run: dict[str, Any] | None = None
    """An extraction as listed: everything stored beside the core's result, which is not read for a list."""

    extraction_id: str
    analytics: CompilationAnalytics | None = None  # older snapshots did not record usage or timing
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
    versions: list[VersionRef] = []  # the ruleset versions made from this extraction
    open_change_requests: int = 0
    # Reviewed corrections of the draft rules this extraction was compiled with, each with who and why.
    rule_overrides: list[dict[str, Any]] = []


class ExtractionProgress(BaseModel):
    stage: Literal["queued", "reading", "building", "done"] = Field(description="reading: the documents, one at a time (live); building: the candidate ruleset")
    done: int = Field(description="Documents read so far")
    total: int
    current: str | None = Field(default=None, description="The document being read")


class ExtractionJob(BaseModel):
    """An extraction running in the background: where it is, and, once finished, its extraction or why it failed."""

    job_id: str
    status: Literal["queued", "running", "succeeded", "failed"]
    execution_mode: Literal["replay", "live"]
    extraction_model: str | None
    documents: list[str]
    progress: ExtractionProgress
    extraction_id: str | None
    error: dict[str, Any] | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime


class ExtractionSummary(ExtractionListItem):
    review_required: list[FieldComparison]  # the fields an exceptions_only review must decide: read from the core's result
    rule_titles: dict[str, str] = {}  # each candidate rule's title, by rule_id, when the compiler built a candidate
    # What each rule under review does, and each rule it replaces or is replaced by: its conditions, outcome,
    # params and replacements as the candidate states them, and where each param is compared.
    rule_logic: dict[str, dict[str, Any]] = {}
    # Rules whose compiled behaviour differs from what their clause states (RuleDiscrepancy): why there is no candidate.
    discrepancies: list[dict[str, Any]] = []


class PreviousDecision(BaseModel):
    """A field of this compilation beside the same field in the published version, with the decision it was published with.

    unchanged: same clause text and the same readings, so the earlier decision answers the same question.
    changed: the clause or a reading changed since, so the earlier decision must be made again.
    """

    comparison_id: str  # in this compilation
    state: Literal["unchanged", "changed"]
    previous: dict[str, Any]  # the published comparison: values, evidence and clause_hash
    decision: dict[str, Any]  # the decision the published version was accepted with


class Baseline(BaseModel):
    """This compilation beside the latest published version: what its rules change, and the earlier decisions."""

    published: dict[str, Any] | None  # ruleset_id, name, version, published_at; None when nothing is published
    rule_changes: list[dict[str, Any]] = []  # each rule field that differs: rule_id, field, before (published), after (this draft)
    changed_clauses: list[dict[str, Any]] = []  # rules whose clause text differs from the published version's: rule_id, policy_doc
    decisions: list[PreviousDecision] = []


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


class ChangeRequestCreate(RequestBody):
    """A reviewer's request that one compared field be corrected before the extraction is used. It changes nothing
    itself: no document, reading or version is rewritten. While open, no version is made from the extraction, and no
    version made from it is approved."""

    actor: Actor
    comparison_id: str = Field(min_length=1, max_length=100, pattern=r"\S")
    requested_correction: str = Field(min_length=1, max_length=2000, pattern=r"\S")
    comment: str | None = Field(default=None, max_length=2000)


class ChangeRequestResolve(RequestBody):
    actor: Actor
    note: str = Field(min_length=1, max_length=2000, pattern=r"\S")


class ChangeRequest(BaseModel):
    change_request_id: str
    extraction_id: str
    comparison_id: str
    rule_id: str
    field: str
    policy_doc: str
    document_id: str
    requested_correction: str
    comment: str | None
    status: Literal["open", "resolved"]
    requested_by: dict[str, Any]  # a claim: this deployment authenticates no one
    created_at: datetime
    resolved_by: dict[str, Any] | None = None
    resolution_note: str | None = None
    resolved_at: datetime | None = None


class SourceDocument(BaseModel):
    document_id: str
    revision: int | None  # None for a document a version was compiled from inline
    filename: str
    content_sha256: str
    content: str  # read from storage and checked against its hash


class SourceHighlight(BaseModel):
    line: int
    start: int  # 0-based column
    end: int
    text: str
    side: Literal["regex", "llm", "clause"]  # a value only one reading has, or a number in an unconfirmed clause


class SourceLocation(BaseModel):
    """Where a compared field's source is, verified against the exact revision the compiler read; never approximate."""

    comparison_id: str
    document: SourceDocument
    revision_verified: bool
    clause_verified: bool
    location: dict[str, int] | None  # from_line and to_line of the clause, when verified and found exactly once
    section: str | None
    heading_line: int | None  # the rule's heading line, when the text confirms it
    highlights: list[SourceHighlight]
    reason: str | None  # why no exact position is shown


class ExtractionPlanSummary(BaseModel):
    mode: str
    base_ruleset_id: str | None = None
    ruleset_name: str | None = None
    recording_set: str | None = None
    documents: list[RevisionRef]
    affected_rule_ids: list[str] = []
    dependency_rule_ids: list[str] = []
    retained_rule_ids: list[str] = []
    removed_rule_ids: list[str] = []
    changed_document_ids: list[str] = []
    removed_document_ids: list[str] = []
    excluded_document_ids: list[str] = []


class ExtractionVersionCreate(RequestBody):
    name: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    review_id: str | None = Field(default=None, min_length=1, max_length=64)
    publish: bool = False
    actor: Actor | None = None
    note: str = Field(default="", max_length=1000)
    mode: Literal["production", "simulation"] = "production"

    @model_validator(mode="after")
    def _publication_is_attributed(self):
        if self.publish and (self.actor is None or not self.note.strip()):
            raise ValueError("publish needs an actor and note recording why this version is published")
        return self
