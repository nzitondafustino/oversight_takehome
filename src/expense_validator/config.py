"""Load settings from config.yaml and secrets from .env. An environment variable wins over .env."""

import os
from collections.abc import Mapping
from pathlib import Path

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool, field_validator, model_validator

def _project_root() -> Path:
    """The folder holding config.yaml, data/ and rules/, which the command lines default to.

    EXPENSE_VALIDATOR_ROOT when set; else the source checkout this package runs from, as in development and an
    editable install; else, for an installed copy, the current folder. The library itself reads only the
    config_path it is given.
    """
    if os.environ.get("EXPENSE_VALIDATOR_ROOT"):
        return Path(os.environ["EXPENSE_VALIDATOR_ROOT"]).resolve()
    checkout = Path(__file__).resolve().parents[2]
    return checkout if (checkout / "config.yaml").is_file() else Path.cwd()


ROOT = _project_root()


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LlmConfig(_Strict):
    model: str | None = None
    url: str
    temperature: float = Field(ge=0)
    timeout_seconds: int = Field(gt=0)


class PathsConfig(_Strict):
    policy_docs: Path
    taxonomy: Path
    llm_cache: Path
    rule_logic: Path
    ruleset: Path
    rule_fields: Path


class TranslationConfig(_Strict):
    enabled: StrictBool = False
    model: str | None = None




class JevThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    recipient_type: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    venue_type: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    item_kind: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    essential_toiletry: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)


LANGGRAPH = "langgraph"  # the orchestrator's name as a classification backend: never one of its own backends

# A classification backend is an execution adapter, named for the protocol it speaks, never for a provider or model.
CHAT_COMPLETIONS = "chat_completions"  # an LLM through chat completions
DECISIONS = "decisions"  # a decision model, such as TypeSafe's Jev, through a Decisions API
# The adapters' earlier names. Accepted wherever a backend is named, so older configuration and requests still run on
# the same adapter. Records keep the name they were made with.
LEGACY_BACKENDS = {"openrouter": CHAT_COMPLETIONS, "jev": DECISIONS}
EARLIER_NAMES = {adapter: earlier for earlier, adapter in LEGACY_BACKENDS.items()}

# How a LangGraph prompt identity names each adapter: 1 by its earlier name, as every run before the rename was hashed;
# 2 by its own. Only the names differ, so a version-1 hash of a configuration is exactly the one recorded for it then.
PROMPT_IDENTITY_VERSION = 2
PROMPT_IDENTITY_VERSIONS = (1, 2)


def canonical_backend(name: str | None) -> str | None:
    """The adapter's name for a backend named either way; any other name unchanged, for its own checks to judge."""
    return LEGACY_BACKENDS.get(name, name) if isinstance(name, str) else name


class LangGraphBackendConfig(_Strict):
    """One backend in the orchestrator's order. The validator checks the backend and model against the registry."""

    name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")  # this backend's alias in the order, such as primary
    backend: str = Field(pattern=r"\S")  # a name in classification.backends.registry.BACKENDS
    model: str = Field(pattern=r"\S")

    @field_validator("backend")
    @classmethod
    def _not_the_orchestrator(cls, backend: str) -> str:
        if backend == LANGGRAPH:
            raise ValueError("langgraph cannot be one of its own backends")
        return canonical_backend(backend)


class LangGraphRoutingConfig(BaseModel):
    """When a line goes on to the next backend. A line below min_confidence is low confidence."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    min_confidence: float = Field(default=0.9, ge=0, le=1, allow_inf_nan=False)
    fallback_on_error: bool = True
    fallback_on_low_confidence: bool = True


class ReflectionReviewerConfig(_Strict):
    """The reviewer. The validator checks it against the registry of reviewers."""

    backend: str = Field(pattern=r"\S")  # a name in classification.backends.registry.REVIEWERS
    model: str = Field(pattern=r"\S")

    @field_validator("backend")
    @classmethod
    def _not_the_orchestrator(cls, backend: str) -> str:
        if backend == LANGGRAPH:
            raise ValueError("langgraph cannot be its own reviewer")
        return canonical_backend(backend)


class ReflectionConfig(BaseModel):
    """Configure optional feedback-guided classification review.

    Attributes:
        enabled: Whether reflection runs; disabled by default.
        reviewer: Registered reviewer configuration.
        max_retries: Maximum revisions per line, excluding the initial classification.
        max_total_calls_per_line: Limit covering classification, review, revision,
            failures, cache hits, and replay misses.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    enabled: bool = False
    reviewer: ReflectionReviewerConfig | None = None
    max_retries: int = Field(default=2, ge=0, le=10)
    max_total_calls_per_line: int = Field(default=6, ge=1, le=50)

    @model_validator(mode="after")
    def _needs_a_reviewer(self):
        if self.enabled and self.reviewer is None:
            raise ValueError("reflection is enabled, so it needs a reviewer")
        return self


class LangGraphConfig(_Strict):
    """The orchestrator's backends, in order, its routing, and its optional reflection."""

    backends: tuple[LangGraphBackendConfig, ...] = ()
    routing: LangGraphRoutingConfig = LangGraphRoutingConfig()
    reflection: ReflectionConfig = ReflectionConfig()

    @field_validator("backends")
    @classmethod
    def _unique_names(cls, backends: tuple) -> tuple:
        names = [backend.name for backend in backends]
        repeated = sorted({name for name in names if names.count(name) > 1})
        if repeated:
            raise ValueError(f"backend names given more than once: {repeated}")
        return backends

    def merged(self, override: "Mapping[str, object] | LangGraphConfig | None") -> "LangGraphConfig":
        """Return a new config with an override applied field by field. This config is not changed.

        A given backends list replaces the whole list. A given routing or reflection field replaces only that
        field; a given reflection reviewer replaces the whole reviewer.

        Raises:
            ValueError: If the override, or the result, is not valid. Pydantic's ValidationError is a ValueError.
        """
        if override is None:
            return self
        if isinstance(override, LangGraphConfig):
            return override
        if not isinstance(override, Mapping):
            raise ValueError(f"langgraph_config must be a mapping, not {type(override).__name__}")
        unknown = sorted(set(override) - set(type(self).model_fields))
        if unknown:
            raise ValueError(f"langgraph_config has unknown fields: {unknown}")
        routing = self.routing
        if "routing" in override:
            given = override["routing"]
            if isinstance(given, LangGraphRoutingConfig):
                routing = given
            elif isinstance(given, Mapping):
                routing = {**self.routing.model_dump(), **given}  # validated below, so that an error names routing.<field>
            else:
                raise ValueError(f"langgraph_config routing must be a mapping, not {type(given).__name__}")
        reflection = self.reflection
        if "reflection" in override:
            given = override["reflection"]
            if isinstance(given, ReflectionConfig):
                reflection = given
            elif isinstance(given, Mapping):
                reflection = {**self.reflection.model_dump(), **given}  # validated below, so that an error names reflection.<field>
            else:
                raise ValueError(f"langgraph_config reflection must be a mapping, not {type(given).__name__}")
        backends = override["backends"] if "backends" in override else self.backends
        return LangGraphConfig.model_validate({"backends": backends, "routing": routing, "reflection": reflection})


class ClassificationConfig(_Strict):
    jev_thresholds: JevThresholds = JevThresholds()
    backend: str = CHAT_COMPLETIONS  # an adapter in classification.backends.registry.BACKENDS (chat_completions or decisions), or langgraph
    model: str | None = None  # the backend's model; langgraph takes each of its backends' models from langgraph.backends
    langgraph: LangGraphConfig = LangGraphConfig()  # read only when backend is langgraph

    @field_validator("backend")
    @classmethod
    def _canonical(cls, backend: str) -> str:
        return canonical_backend(backend)


class Config(_Strict):
    llm: LlmConfig
    paths: PathsConfig
    translation: TranslationConfig = TranslationConfig()
    classification: ClassificationConfig = ClassificationConfig()
    openrouter_api_key: SecretStr | None = None


def load_config(root: Path = ROOT, config_file: str = "config.yaml") -> Config:
    """Load the project configuration with absolute paths."""
    settings = yaml.safe_load((root / config_file).read_text())
    config = Config(**settings, openrouter_api_key=_api_key(root))
    paths = PathsConfig(**{name: root / path for name, path in config.paths})
    return config.model_copy(update={"paths": paths})


def _api_key(root: Path) -> str | None:
    return os.environ.get("OPENROUTER_API_KEY") or dotenv_values(root / ".env").get("OPENROUTER_API_KEY") or None
