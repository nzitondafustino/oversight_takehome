"""Application settings, read from EXPENSE_API_* environment variables."""

import tempfile
from pathlib import Path
from typing import Literal

from expense_validator.config import CHAT_COMPLETIONS, DECISIONS, LANGGRAPH, canonical_backend
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Deployment settings. Nothing here can be chosen by a request."""

    model_config = SettingsConfigDict(env_prefix="EXPENSE_API_", extra="ignore", frozen=True)

    mongo_uri: SecretStr = SecretStr("mongodb://localhost:27017")  # may hold credentials, so never logged
    mongo_database: str = "expense_api"
    mongo_timeout_ms: int = Field(default=2000, gt=0)

    # The core's config.yaml: its taxonomy, field catalog, policy documents and translation settings. Its
    # ruleset path is never used, as every run reads a stored ruleset.
    core_config: Path
    # Where stored rulesets are written as files, named by their SHA-256, for the validator to read.
    artifact_dir: Path = Path(tempfile.gettempdir()) / "expense_api" / "rulesets"

    # Named folders of recorded model answers. A request picks one by name, never by path.
    replay_sets: dict[str, Path] = {}
    default_replay_set: str | None = None

    # Live inference costs money: it needs this switch and an explicit execution_mode of live in the request.
    allow_live: bool = False
    # A live evaluation with no spending, call or receipt limit: refused unless this is set as well as allow_live.
    allow_uncapped_evaluations: bool = False
    live_cache_dir: Path | None = None  # None keeps no live answers
    # Model IDs a request may choose. In live mode, a request may choose no model unless this lists it. It is the
    # policy compiler's extraction allowlist, and the classification or translation allowlist where that is not set.
    allowed_models: frozenset[str] = frozenset()
    # Models allowed for classification (each direct or LangGraph model, and the reviewer) and for translation, kept
    # apart so that a translator is never offered or accepted as a classifier, nor a classifier as a translator.
    # None: allowed_models, as before these existed.
    allowed_classification_models: frozenset[str] | None = None
    allowed_translation_models: frozenset[str] | None = None
    # Execution adapters and the orchestrator a request may choose. Earlier adapter names (openrouter, jev) are read
    # as the adapters they name.
    allowed_backends: frozenset[str] = frozenset({CHAT_COMPLETIONS, DECISIONS, LANGGRAPH})

    max_receipts_per_request: int = Field(default=500, gt=0)

    # Named folders of recorded policy readings for the compiler to replay.
    compiler_recording_sets: dict[str, Path] = {}
    # The recording set a live extraction records its model readings into, so that every later step can replay
    # them. It must hold model readings only. None: live extraction is unavailable. It also needs allow_live.
    compiler_live_recording_set: str | None = None
    max_document_bytes: int = Field(default=512 * 1024, gt=0)  # UTF-8 bytes of one document's content
    # Which publications an approval may authorize. Simulation readings never authorize production, and a
    # version published by simulation is validated only as a preview.
    allowed_publication_modes: frozenset[Literal["production", "simulation"]] = frozenset({"production"})
    # Whether a request may validate against a version not published for production, by asking for a preview.
    allow_preview_validation: bool = False

    # Evaluation datasets: the largest upload accepted, in bytes, and the most receipts one may hold.
    max_dataset_bytes: int = Field(default=4 * 1024 * 1024, gt=0)
    max_dataset_receipts: int = Field(default=500, gt=0)
    # A versioned table of per-token prices, used only to estimate a live call's cost when the provider reports
    # none. None: no estimates, so such a call is unpriced. See EVALUATIONS.md for its format.
    pricing_table: Path | None = None
    # The evaluation worker: how long a claimed job's lease lasts without a heartbeat, how often an idle worker
    # looks for work, and how many times a replay job is requeued after its worker was lost. Live jobs never are.
    worker_lease_seconds: int = Field(default=120, gt=0)
    worker_poll_seconds: float = Field(default=2.0, gt=0)
    max_evaluation_attempts: int = Field(default=3, gt=0)

    @field_validator("allowed_backends")
    @classmethod
    def _canonical_backends(cls, backends: frozenset[str]) -> frozenset[str]:
        return frozenset(canonical_backend(name) for name in backends)

    @property
    def classification_allowlist(self) -> frozenset[str]:
        return self.allowed_models if self.allowed_classification_models is None else self.allowed_classification_models

    @property
    def translation_allowlist(self) -> frozenset[str]:
        return self.allowed_models if self.allowed_translation_models is None else self.allowed_translation_models

    @model_validator(mode="after")
    def _default_replay_set_exists(self):
        if self.default_replay_set is not None and self.default_replay_set not in self.replay_sets:
            raise ValueError(f"default_replay_set {self.default_replay_set!r} is not in replay_sets")
        if self.compiler_live_recording_set is not None and self.compiler_live_recording_set not in self.compiler_recording_sets:
            raise ValueError(f"compiler_live_recording_set {self.compiler_live_recording_set!r} is not in compiler_recording_sets")
        return self
