"""Validation requests and persisted runs."""

from datetime import datetime
from typing import Annotated, Any, Literal

from expense_validator import BatchResult, Mode
from pydantic import BaseModel, Field, StrictBool, field_validator

from expense_api.api.json_body import as_standard_json
from expense_api.schemas.common import RequestBody

ModelId = Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S", examples=["anthropic/claude-haiku-5.5"])]


class ClassificationSelection(RequestBody):
    """The classification this request chooses. Anything left out comes from the core's config.yaml."""

    backend: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{0,39}$", description="openrouter, jev or langgraph, as the deployment allows")
    model: ModelId | None = None
    translation_model: ModelId | None = None
    langgraph: dict[str, Any] | None = Field(default=None, description="Changes to config.yaml's classification.langgraph, as the core's langgraph_config takes them")
    replay_set: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]{1,64}$", description="A named set of recorded answers, replay mode only")

    @field_validator("langgraph")
    @classmethod
    def _numbers_as_floats(cls, value: dict | None) -> dict | None:
        # The core reads routing thresholds as floats, strictly; bodies decode numbers as Decimal.
        return None if value is None else as_standard_json(value)


class ValidationRequest(RequestBody):
    ruleset_id: str = Field(min_length=1, max_length=64, description="One exact, immutable ruleset version")
    receipts: list[dict[str, Any]] = Field(min_length=1, description="Raw receipt records in the core's format (data/README.md), without labels or notes")
    context_complete: StrictBool = Field(default=True, description="Whether every receipt that could matter to a cross-receipt rule is included, true by default: submit all relevant receipts in one batch. False leaves totals a missing receipt could change undecided.")
    execution_mode: Mode = Field(default="replay", description="replay (the default) answers only from recordings; live calls paid providers and must be allowed by the deployment")
    classification: ClassificationSelection = ClassificationSelection()
    preview: StrictBool = Field(default=False, description="Validate against a version that is not published for production, such as a draft. Only where the deployment allows previews; the run records it.")


class RunRuleset(BaseModel):
    ruleset_id: str
    name: str
    version: int
    status: str  # at the time of the run
    publication_mode: str | None = None  # production or simulation, when the version was published
    artifact_sha256: str
    ruleset_version: str


class RunExecution(BaseModel):
    started_at: datetime
    completed_at: datetime
    duration_ms: int
    receipt_count: int
    core_version: str
    api_version: str


class RunResponse(BaseModel):
    run_id: str
    status: Literal["completed"]
    ruleset: RunRuleset
    execution_mode: Mode
    preview: bool = False  # whether the run used a version not published for production
    context_complete: bool
    classification: ClassificationSelection  # as requested; each receipt's metadata names the models that answered
    results: BatchResult  # the core's result, unchanged
    # Orchestrator, provider, adapter (backend) and model, from the receipts' provenance and metadata, worked out when read.
    setup: dict[str, Any] | None = None
    execution: RunExecution
    created_at: datetime
