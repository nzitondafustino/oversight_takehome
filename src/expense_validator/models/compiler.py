"""Define compiler inputs and results; re-export Approval and Resolution."""

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from expense_validator.compiler.agreement import Resolution
from expense_validator.compiler.approval import Approval
from expense_validator.compiler.extract import Clause
from expense_validator.compiler.validate import Issue
from expense_validator.models.settings import CompilerMode
from expense_validator.policy.schema import RuleSet

PublishMode = Literal["production", "simulation"]
ReadingKind = Literal["simulation", "model"]

__all__ = [
    "Approval",
    "CandidateRuleset",
    "CompilerMode",
    "DocumentReading",
    "ExtractionResult",
    "FieldComparison",
    "PolicyDocumentInput",
    "PublicationResult",
    "PublishMode",
    "ReadingKind",
    "Resolution",
    "ReviewAction",
    "ReviewDecision",
    "ReviewIssue",
    "ReviewItem",
    "ReviewPolicy",
    "ReviewResult",
    "ReviewedField",
]
ComparisonStatus = Literal["agreed", "disagree", "unconfirmed", "only_regex", "only_llm"]
ReviewAction = Literal["confirm", "accept_regex", "accept_llm", "override", "reject"]
# exceptions_only: a decision is needed for every field not agreed. all_fields: for every field.
ReviewPolicy = Literal["exceptions_only", "all_fields"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PolicyDocumentInput(_Model):
    """One policy document in the existing Markdown format.

    Attributes:
        document_id: The caller's identifier, carried into results.
        filename: A bare .md name, with no folder. Rules and approvals refer to documents by it.
        content: The document text.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    document_id: str = Field(pattern=r"\S", max_length=200)
    filename: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*\.md$", max_length=200)
    content: str = Field(pattern=r"\S")


class DocumentReading(_Model):
    document_id: str
    filename: str
    sha256: str
    reading_error: str | None = None  # why the LLM reading could not be used


class ReviewItem(_Model):
    """One field the regex and LLM readings did not agree on, with any reviewer resolution.

    A Resolution for it needs its policy_doc, rule_id, field and clause_hash.
    """

    document_id: str
    policy_doc: str
    rule_id: str
    clause_hash: str | None
    field: str
    status: str  # disagree, unconfirmed, only_regex or only_llm
    regex: Any = None
    llm: Any = None
    resolution: dict[str, Any] | None = None  # value, reviewer and rationale; None while unresolved


class FieldComparison(_Model):
    """Compare one typed rule field across extractors.

    Attributes:
        comparison_id: Identity bound to document text, rule, and field.
        regex_value: Regex reading; rule presence is present or absent.
        agreed_value: Shared value when readings agree.
        evidence: Supporting clause text, if available.
        location: Section and heading line, if available.
        review_required: Whether exceptions_only review requires a decision.
    """

    comparison_id: str
    document_id: str
    policy_doc: str
    rule_id: str
    field: str
    status: ComparisonStatus
    regex_value: Any = None
    llm_value: Any = None
    agreed_value: Any = None
    evidence: str | None = None
    location: dict[str, Any] | None = None
    clause_hash: str | None = None
    extraction_sha256: str
    review_required: bool


class ReviewDecision(BaseModel):
    """A reviewer's decision on one comparison of one exact extraction.

    value is given only with override, where None is a value: a missing value is never read as one.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @model_validator(mode="before")
    @classmethod
    def _drop_a_legacy_customer(cls, data):
        # A decision stored before customer_id was removed names an extraction whose identity held a customer,
        # so it is read, and refused as stale_extraction rather than failing to load.
        if isinstance(data, dict) and "customer_id" in data:
            data = {name: value for name, value in data.items() if name != "customer_id"}
        return data

    comparison_id: str = Field(pattern=r"\S")
    extraction_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    action: ReviewAction
    value: Any = None
    reviewer: str = Field(pattern=r"\S")
    rationale: str | None = None

    @property
    def has_value(self) -> bool:
        return "value" in self.model_fields_set


class ReviewIssue(_Model):
    """Something that blocks approval of a review. Notes do not block.

    Attributes:
        code: unknown_comparison, duplicate_decision, stale_extraction, confirm_not_agreed,
            no_source_value, value_required, value_not_allowed, rationale_required, unsupported_value,
            executable_conflict, rejected, reading_error, no_candidate or source_only.
    """

    level: Literal["error", "note"]
    code: str
    message: str
    comparison_id: str | None = None


class ReviewedField(_Model):
    """A comparison with the decision applied to it and its final value.

    Attributes:
        outcome: agreed (kept without a decision), confirmed, regex, llm, override, rejected, or undecided.
        final_value: None when rejected or undecided.
    """

    comparison: FieldComparison
    decision: ReviewDecision | None
    outcome: Literal["agreed", "confirmed", "regex", "llm", "override", "rejected", "undecided"]
    final_value: Any = None
    decision_required: bool


class ReviewResult(_Model):
    """The reviewed interpretation of one extraction. Not an approval, and never written anywhere.

    Attributes:
        remaining: Comparisons the policy still needs a valid decision for.
        complete: Nothing remains.
        ready_for_approval: Complete, with no error issue and a valid candidate.
        review_sha256: Hash of everything else here, which an approval of this review binds.
    """

    reading_kind: ReadingKind
    extraction_sha256: str
    policy: ReviewPolicy
    candidate_ruleset_sha256: str | None
    fields: tuple[ReviewedField, ...]
    issues: tuple[ReviewIssue, ...]
    remaining: tuple[str, ...]
    complete: bool
    ready_for_approval: bool
    review_sha256: str

    def to_json(self) -> str:
        """Return the review as canonical JSON: sorted keys, so equal reviews give equal text."""
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)


class CandidateRuleset(_Model):
    """A built and validated ruleset. Never active until published with an approval."""

    ruleset: RuleSet
    ruleset_sha256: str


class ExtractionResult(_Model):
    """Everything a reviewer needs to resolve and approve one extraction. Nothing is written or activated.

    Attributes:
        agreement: The exact agreement output an approval binds to, by output_sha256.
        candidate: None when the ruleset did not build or validate.
        blockers: What stands in the way of approval. Empty exactly when ready_for_approval.
    """

    mode: CompilerMode
    reading_kind: ReadingKind
    extraction_model: str | None  # None in replay, which reads recordings by document
    temperature: float | None  # None when no OpenRouter request is made: replay or an injected completion
    timeout_seconds: int | None
    overrides: tuple[str, ...]  # the settings this request chose
    prompt_sha256: str
    documents: tuple[DocumentReading, ...]
    clauses: tuple[Clause, ...]
    review_items: tuple[ReviewItem, ...]
    validation: tuple[Issue, ...]
    blockers: tuple[str, ...]
    ready_for_approval: bool
    output_sha256: str
    agreement: dict[str, Any]
    candidate: CandidateRuleset | None
    extraction_sha256: str  # the readings, documents and generation settings; review decisions bind it
    comparisons: tuple[FieldComparison, ...]  # every field, agreed or not, in a fixed order


class PublicationResult(_Model):
    published: bool
    mode: PublishMode
    out: Path
    reading_kind: ReadingKind
    output_sha256: str
    ruleset_sha256: str | None
    reasons: tuple[str, ...]  # why nothing was written; empty when published
    extraction_model: str | None
    temperature: float | None
    timeout_seconds: int | None
    overrides: tuple[str, ...]
