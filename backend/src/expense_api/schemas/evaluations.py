"""Evaluation datasets, jobs and their results."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, StrictBool, StrictInt

from expense_api.schemas.common import RequestBody
from expense_api.schemas.validation import ClassificationSelection

Status = Literal["queued", "running", "cancel_requested", "completed", "failed", "cancelled", "budget_exhausted"]


class Budget(RequestBody):
    """Independent limits of a live run: each one given applies, and one left out is no limit of that kind.
    Best effort: a call's cost is known only once it returns (see EVALUATIONS.md)."""

    max_spend_usd: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=6, description="US dollars, as text or an exact number, such as \"0.50\"")
    max_provider_calls: StrictInt | None = Field(default=None, gt=0, le=100_000)
    max_receipts: StrictInt | None = Field(default=None, gt=0, description="Refuses the run unless the dataset holds at most this many receipts")

    def caps(self) -> dict:
        """The limits given, as stored: dollars as exact decimal text."""
        values = {"max_spend_usd": None if self.max_spend_usd is None else format(self.max_spend_usd, "f"), "max_provider_calls": self.max_provider_calls, "max_receipts": self.max_receipts}
        return {name: value for name, value in values.items() if value is not None}


class EvaluationCreate(RequestBody):
    dataset_id: str = Field(min_length=1, max_length=64, description="One exact, immutable dataset version")
    ruleset_id: str = Field(min_length=1, max_length=64, description="One exact, immutable ruleset version")
    preview: StrictBool = Field(default=False, description="Evaluate against a version not published for production, where the deployment allows previews")
    execution_mode: Literal["replay", "live"] = Field(default="replay", description="replay (the default) reads only recorded answers; live calls paid providers")
    classification: ClassificationSelection = ClassificationSelection()
    confirm_live: StrictBool = Field(default=False, description="Live only: true to confirm that this run calls paid providers")
    budget: Budget | None = Field(default=None, description="Live only: at least one limit, unless confirm_uncapped")
    confirm_uncapped: StrictBool = Field(default=False, description="Live only: true, with no budget, to confirm a run with no spending, call or receipt limit")
    idempotency_key: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.:-]{1,128}$", description="The same key and request return the same evaluation")
    label: str | None = Field(default=None, max_length=120)


class CompareRequest(RequestBody):
    evaluation_ids: list[str] = Field(min_length=2, max_length=10)


class DatasetSummary(BaseModel):
    dataset_id: str
    name: str
    version: int
    description: str | None
    format: str
    filename: str
    schema_version: str
    context_complete: bool
    original_sha256: str
    canonical_sha256: str
    stats: dict[str, Any]
    created_at: datetime


class DatasetDetail(DatasetSummary):
    cases: list[dict[str, Any]] | None = None  # the canonical cases, with include_cases=true


class DatasetPage(BaseModel):
    items: list[DatasetSummary]
    total: int
    limit: int
    offset: int


class EvaluationSummary(BaseModel):
    evaluation_id: str
    label: str | None
    status: Status
    dataset: dict[str, Any]
    ruleset: dict[str, Any]
    preview: bool
    execution_mode: str
    configuration: dict[str, Any]  # the backend and models it runs with, and their identity
    progress: dict[str, Any]
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    summary: dict[str, Any] | None  # key quality metrics, once finished
    spend_summary: dict[str, Any]  # new spending in USD, and whether every live call's cost is known
    stop_reason: str | None
    error: dict[str, Any] | None


class EvaluationDetail(EvaluationSummary):
    classification: dict[str, Any]  # as requested
    replay: dict[str, Any] | None
    live: dict[str, Any] | None
    budget: dict[str, Any] | None
    attempts: int
    updated_at: datetime
    cancel_requested_at: datetime | None
    lease: dict[str, Any] | None
    result_context: str | None
    context_complete: bool | None
    note: str | None
    error_summary: dict[str, int] | None
    metrics_schema_version: str | None
    spend: dict[str, Any]  # by model, backend and call purpose; worked out from the stored calls while running


class EvaluationPage(BaseModel):
    items: list[EvaluationSummary]
    total: int
    limit: int
    offset: int


class ResultPage(BaseModel):
    evaluation_id: str
    items: list[dict[str, Any]]
    total: int
    limit: int
    offset: int
