"""Ruleset requests and responses, including the lifecycle: draft, in_review, approved, published."""

from datetime import datetime
from typing import Annotated, Any, Literal

from expense_validator import FieldComparison, PolicyDocumentInput
from pydantic import BaseModel, Field, field_validator, model_validator

from expense_api.api.json_body import as_standard_json
from expense_api.schemas.common import RequestBody

RulesetName = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$", examples=["default"])]
RulesetStatus = Literal["draft", "in_review", "approved", "published"]
PublishMode = Literal["production", "simulation"]
# Who a request says is acting. No authentication backs it, so it is recorded as a claim, never as an identity.
Actor = Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S", examples=["j.doe"])]
Reason = Annotated[str, Field(min_length=1, max_length=1000, pattern=r"\S")]


class RulesetSource(RequestBody):
    """Where the content came from. Recorded as given: it is provenance, not proof."""

    kind: str = Field(default="upload", pattern=r"^[a-z][a-z_]{0,39}$", examples=["upload", "policy_compiler"])
    description: str | None = Field(default=None, max_length=500)
    reference: str | None = Field(default=None, max_length=200, examples=["rules/ruleset.json"])


class CompileSource(RequestBody):
    """Policy documents for the core's PolicyCompiler, read from one named set of recorded readings."""

    documents: list[PolicyDocumentInput] = Field(min_length=1, max_length=50)
    recording_set: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$", description="A server-side set of recorded readings; the compiler never calls a model here")


class ExtractionSource(RequestBody):
    """A stored extraction of document revisions, with the review that readies it unless it needed none."""

    extraction_id: str = Field(pattern=r"^ext_[0-9a-f]{24}$")
    review_id: str | None = Field(default=None, pattern=r"^rev_[0-9a-f]{24}$")


class RulesetCreate(RequestBody):
    """A new draft version from exactly one source: an extraction, policy documents to compile, or an uploaded artifact."""

    name: RulesetName  # the versions of one name form one history
    artifact: dict[str, Any] | None = Field(default=None, description="A compiled ruleset in the core's RuleSet schema. Structurally checked only: it can never be approved.")
    compile: CompileSource | None = Field(default=None, description="Policy documents to compile here, from one recording set.")
    extraction: ExtractionSource | None = Field(default=None, description="A reviewed extraction of stored document revisions (POST /v1/documents/extractions).")
    based_on_version: int | None = Field(default=None, ge=1, description="The version this one replaces. When given, any other version created since is a 409.")
    source: RulesetSource = RulesetSource()

    @model_validator(mode="after")
    def _one_content(self):
        if [self.artifact, self.compile, self.extraction].count(None) != 2:
            raise ValueError("give exactly one of artifact, compile and extraction")
        return self


class TransitionRequest(RequestBody):
    actor: Actor | None = None
    reason: Reason | None = None


class ReviewRequest(RequestBody):
    """A review decision. Accepting a compiled version needs the compiler's review decisions unless nothing awaits one."""

    actor: Actor
    decision: Literal["accepted", "rejected"]
    reason: Reason | None = None
    review_policy: Literal["exceptions_only", "all_fields"] = "exceptions_only"
    decisions: list[dict[str, Any]] | None = Field(default=None, description="The core's ReviewDecision records, one per comparison, for this version's extraction")

    @field_validator("decisions")
    @classmethod
    def _standard_json(cls, value):
        return None if value is None else as_standard_json(value)

    @model_validator(mode="after")
    def _rejection_has_reason(self):
        if self.decision == "rejected" and self.reason is None:
            raise ValueError("a rejection needs a reason")
        if self.decision == "rejected" and self.decisions is not None:
            raise ValueError("a rejection takes no review decisions")
        return self


class ApproveRequest(RequestBody):
    actor: Actor  # becomes the approval's approved_by: a claim, as the core says it must be authenticated by the caller
    mode: PublishMode = Field(default="production", description="The publication the approval must authorize. Simulation readings never authorize production.")
    note: str = Field(default="", max_length=1000)
    approval: dict[str, Any] | None = Field(default=None, description="An approval record from the core's PolicyCompiler.approve. Without one, the compiler makes it here.")

    @field_validator("approval")
    @classmethod
    def _standard_json(cls, value):
        return None if value is None else as_standard_json(value)


class PublishRequest(RequestBody):
    actor: Actor | None = None
    approval_reference: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$", description="The approval to publish. Another one is a 409.")


class ClaimedActor(BaseModel):
    claimed: str
    verified: Literal[False] = False  # nothing authenticates it in this deployment


class Review(BaseModel):
    decision: Literal["accepted", "rejected"]
    reviewer: ClaimedActor
    reason: str | None
    decided_at: datetime
    policy: str | None
    decisions: list[dict[str, Any]] | None
    review_sha256: str | None  # the core's hash of the reviewed interpretation, which an approval binds


class CompiledDocument(BaseModel):
    document_id: str
    filename: str
    sha256: str
    revision: int | None = None  # the stored revision, for a version made from an extraction


class PreparedReview(BaseModel):
    """The extraction review a version was made from: the lifecycle review reuses its decisions unless given others."""

    review_id: str
    policy: str
    review_sha256: str


class Compilation(BaseModel):
    recording_set: str
    recorded_with: dict[str, Any] | None = None  # the live generation settings a replay must use
    extraction_id: str | None = None
    prepared_review: PreparedReview | None = None
    reading_kind: Literal["model", "simulation"]
    extraction_sha256: str
    output_sha256: str
    ready_for_approval: bool  # without review decisions
    blockers: list[str]
    comparisons_needing_review: int
    documents: list[CompiledDocument] = []


class Transition(BaseModel):
    from_status: RulesetStatus | None
    to_status: RulesetStatus
    at: datetime
    actor: ClaimedActor | None
    content_hash: str
    approval_reference: str | None
    reason: str | None


class RulesetSummary(BaseModel):
    ruleset_id: str
    name: str
    version: int
    status: RulesetStatus
    ruleset_version: str  # the artifact's own label, such as v1
    schema_version: str
    artifact_sha256: str  # the content hash: SHA-256 of the exact stored artifact text
    rule_count: int
    active_rule_count: int
    source: RulesetSource
    based_on_version: int | None = None
    compilation: Compilation | None = None  # None for an uploaded artifact, which no approval can cover
    review: Review | None = None
    approval_reference: str | None = None  # SHA-256 of the stored approval record
    approval_mode: PublishMode | None = None
    publication_mode: PublishMode | None = None
    created_at: datetime
    submitted_at: datetime | None = None
    approved_at: datetime | None = None
    published_at: datetime | None


class RulesetDetail(RulesetSummary):
    artifact: dict[str, Any]  # the core's canonical serialization: numbers as exact decimal strings
    transitions: list[Transition] = []


class RulesetPage(BaseModel):
    items: list[RulesetSummary]
    total: int
    limit: int
    offset: int


class ComparisonPage(BaseModel):
    extraction_sha256: str
    comparisons: list[FieldComparison]
