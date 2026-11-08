"""Run history and findings. A finding keeps the core's record whole; see services/runs.py for the four kinds."""

from datetime import datetime
from typing import Any, Literal

from expense_validator import Mode
from pydantic import BaseModel

from expense_api.schemas.validation import ClassificationSelection, RunRuleset

FindingKind = Literal["exception", "review_signal", "open_question", "uncertainty"]


class FindingCounts(BaseModel):
    exception: int  # active exceptions: the policy violations
    review_signal: int  # includes EXC-03's uncertainty signal
    open_question: int  # undecided rules: not violations
    uncertainty: int  # EXC-03 details, one per line and field


class RunSummary(BaseModel):
    run_id: str
    status: Literal["completed"]
    created_at: datetime
    ruleset: RunRuleset
    execution_mode: Mode
    preview: bool
    context_complete: bool
    classification: ClassificationSelection  # as requested
    classification_backend: str | None  # as the receipts' provenance reports it; None for a run stored before it was recorded, or in deterministic mode
    # Orchestrator, provider, adapter (backend) and model, worked out when read; None for a run stored before summaries.
    setup: dict[str, Any] | None = None
    receipt_count: int
    dispositions: dict[str, int]
    counts: FindingCounts | None  # None for a run stored before counts were recorded: GET its findings for them


class RunPage(BaseModel):
    items: list[RunSummary]
    total: int
    limit: int
    offset: int


class Finding(BaseModel):
    finding_id: str  # receipt, kind and position in the core's list: stable for a stored run
    kind: FindingKind
    receipt_id: str
    receipt_disposition: Literal["flag", "review", "pass"]
    receipt_decided: bool
    rule_id: str | None  # None for an uncertainty record, which names no rule
    severity: str | None  # only an exception has one, and it may be None
    line_ids: list[str] | None  # None for a review signal, which names no lines
    disposition_reasons: list[dict[str, Any]]  # the receipt's disposition reasons that name this rule
    record: dict[str, Any]  # the core's record, unchanged


class FindingPage(BaseModel):
    run_id: str
    items: list[Finding]
    total: int
    limit: int
    offset: int
