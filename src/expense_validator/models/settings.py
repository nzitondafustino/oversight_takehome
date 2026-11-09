"""Validator settings."""

from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator

from expense_validator.config import CHAT_COMPLETIONS, LANGGRAPH, JevThresholds, LangGraphConfig, canonical_backend, load_config
from expense_validator.exceptions import ConfigurationError
from expense_validator.models.results import Mode

CompilerMode = Literal["replay", "live"]


class _ConfigCache:
    """Sentinel for the config file's llm_cache. None means no cache."""

    def __repr__(self) -> str:
        return "CONFIG_CACHE"


CONFIG_CACHE = _ConfigCache()


MODEL_FIELDS = ("classification_model", "translation_model")


def check_model_ids(**models: object) -> dict[str, str]:
    """Return the given model overrides.

    Raises:
        ConfigurationError: If an override is not a non-empty string.
    """
    given = {}
    for name, value in models.items():
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            raise ConfigurationError(f"{name} must be a model ID, such as anthropic/claude-haiku-5.5, or None")
        given[name] = value
    return given


class ValidatorSettings(BaseModel):
    """Explicit validator settings."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Mode
    ruleset_path: Path
    fields_path: Path
    taxonomy_path: Path
    policy_docs_path: Path
    cache_dir: Path | None = None
    jev_thresholds: JevThresholds = JevThresholds()
    classification_backend: str = CHAT_COMPLETIONS  # an adapter in classification.backends.registry.BACKENDS, or langgraph; the validator checks it
    langgraph: LangGraphConfig = LangGraphConfig()  # the orchestrator's backends and routing, read only when the backend is langgraph
    classification_model: str | None = None
    translation_enabled: bool = False
    translation_model: str | None = None
    temperature: float = 0.0
    timeout_seconds: int = 180
    openrouter_api_key: SecretStr | None = None  # only read in live mode, and never logged

    @field_validator("classification_backend")
    @classmethod
    def _canonical_backend(cls, backend: str) -> str:
        return canonical_backend(backend)  # an earlier adapter name runs the same adapter

    @classmethod
    def from_config(
        cls,
        config_path: Path,
        *,
        mode: Mode,
        cache_dir: "Path | None | _ConfigCache" = CONFIG_CACHE,
        classification_model: str | None = None,
        translation_model: str | None = None,
        classification_backend: str | None = None,
        langgraph_config: "Mapping[str, object] | LangGraphConfig | None" = None,
    ) -> "ValidatorSettings":
        """Build settings from a config.yaml, with paths relative to its folder.

        Args:
            config_path: Path to config.yaml.
            mode: replay, live or deterministic.
            cache_dir: Answer cache; the config's llm_cache by default, None for no cache.
            classification_model: OpenRouter model ID replacing the config's.
            translation_model: OpenRouter model ID replacing the config's. Does not enable translation.
            classification_backend: chat_completions or decisions (or their earlier names, openrouter and jev), replacing the config's. Choosing another backend than the
                config's needs a classification_model for it, as the config's model is for its own backend.
            langgraph_config: Changes to config.yaml's classification.langgraph, field by field: a backends list
                replaces the whole list, and a routing field only that field. Only for the langgraph backend.

        Raises:
            ConfigurationError: If the file or settings are invalid.
        """
        check_model_ids(classification_model=classification_model, translation_model=translation_model)
        config = _load(config_path)
        backend = classification_backend or config.classification.backend
        own_model = config.classification.model if backend == config.classification.backend else None
        if langgraph_config is not None and backend != LANGGRAPH:
            raise ConfigurationError(f"langgraph_config applies only to the langgraph backend, and this one is {backend}")
        try:
            langgraph = config.classification.langgraph.merged(langgraph_config)
        except ValueError as error:
            raise ConfigurationError(f"langgraph_config is not valid: {_first_line(error)}") from None
        return cls(
            mode=mode,
            ruleset_path=config.paths.ruleset,
            fields_path=config.paths.rule_fields,
            taxonomy_path=config.paths.taxonomy,
            policy_docs_path=config.paths.policy_docs,
            cache_dir=None if mode == "deterministic" else (config.paths.llm_cache if cache_dir is CONFIG_CACHE else cache_dir),
            classification_backend=backend,
            langgraph=langgraph,
            jev_thresholds=config.classification.jev_thresholds,
            classification_model=classification_model or own_model,
            translation_enabled=config.translation.enabled,
            translation_model=translation_model or config.translation.model,
            temperature=config.llm.temperature,
            timeout_seconds=config.llm.timeout_seconds,
            openrouter_api_key=config.openrouter_api_key if mode == "live" else None,
        )


def _first_line(error: ValueError) -> str:
    """The first problem of an error, without pydantic's documentation link."""
    if isinstance(error, ValidationError):
        first = error.errors()[0]
        return f"{'.'.join(map(str, first['loc'])) or 'value'}: {first['msg']}"
    return str(error).splitlines()[0]


def _load(config_path: Path):
    config_path = Path(config_path)
    if not config_path.is_file():
        raise ConfigurationError(f"no config file at {config_path}")
    try:
        return load_config(config_path.parent, config_path.name)
    except (ValidationError, ValueError, TypeError, KeyError) as error:
        raise ConfigurationError(f"{config_path} is not valid settings: {str(error).splitlines()[0]}") from None


MAX_TEMPERATURE = 2  # OpenRouter's range is 0 to 2
REPLAY_TAKES_NO_GENERATION = "replay reads recordings by document, so it takes no extraction_model, temperature or timeout_seconds"


def generation_overrides(*, extraction_model: object = None, temperature: object = None, timeout_seconds: object = None) -> dict[str, object]:
    """Return the given compiler generation overrides. None means not given.

    Raises:
        ConfigurationError: If a value is invalid.
    """
    given = check_model_ids(extraction_model=extraction_model)
    if temperature is not None:
        number = isinstance(temperature, (int, float)) and not isinstance(temperature, bool)
        if not number or not 0 <= temperature <= MAX_TEMPERATURE:  # NaN fails both comparisons
            raise ConfigurationError(f"temperature must be a number from 0 to {MAX_TEMPERATURE}, or None")
        given["temperature"] = float(temperature)
    if timeout_seconds is not None:
        if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise ConfigurationError("timeout_seconds must be a whole number of seconds above 0, or None")
        given["timeout_seconds"] = timeout_seconds
    return given


class RecordedWith(BaseModel):
    """The generation settings a replay must find its recordings made with. Replay mode only."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    extraction_model: str = Field(pattern=r"\S")
    temperature: float = Field(ge=0, le=MAX_TEMPERATURE, allow_inf_nan=False)


class CompilerSettings(BaseModel):
    """Explicit policy compiler settings.

    Attributes:
        ruleset_path: The production ruleset, which a simulation never writes.
        recordings_dir: Replay reads recordings from it. Live mode, when set, records each OpenRouter answer
            in it as a model reading, with its provenance.
        cache_dir: Answer cache for live mode, keyed by model, temperature, prompt and document.
        recorded_with: Replay mode only. None replays recordings named by document hash, which record no
            generation settings. Given, replay uses only recordings made with these settings.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: CompilerMode
    rule_logic_path: Path
    rule_logic_text: str | None = Field(default=None, min_length=1)  # immutable scoped logic snapshot, used instead of the file when supplied
    fields_path: Path
    ruleset_path: Path
    recordings_dir: Path | None = None
    cache_dir: Path | None = None
    extraction_model: str | None = None
    llm_url: str = "https://openrouter.ai/api/v1/chat/completions"
    temperature: float = Field(default=0.0, ge=0, le=MAX_TEMPERATURE, allow_inf_nan=False)
    timeout_seconds: int = Field(default=180, gt=0)
    openrouter_api_key: SecretStr | None = None  # only read in live mode, and never logged
    recorded_with: RecordedWith | None = None

    @classmethod
    def from_config(
        cls,
        config_path: Path,
        *,
        mode: CompilerMode,
        recordings_dir: Path | None = None,
        cache_dir: "Path | None | _ConfigCache" = CONFIG_CACHE,
        extraction_model: str | None = None,
        temperature: float | None = None,
        timeout_seconds: int | None = None,
        recorded_with: RecordedWith | None = None,
    ) -> "CompilerSettings":
        """Build settings from a config.yaml, with paths relative to its folder.

        Each generation setting given here replaces the config's llm value. None keeps it.

        Args:
            config_path: Path to config.yaml.
            mode: replay or live.
            recordings_dir: Recorded readings: read in replay mode, and written in live mode when given.
            cache_dir: Live answer cache; the config's llm_cache by default, None for no cache.
            extraction_model: OpenRouter model ID, live mode only.
            temperature: From 0 to 2, live mode only.
            timeout_seconds: Request timeout, live mode only.
            recorded_with: The settings the recordings to replay were made with, replay mode only.

        Raises:
            ConfigurationError: If the file, settings or an override are invalid.
        """
        overrides = generation_overrides(extraction_model=extraction_model, temperature=temperature, timeout_seconds=timeout_seconds)
        if mode == "replay" and overrides:
            raise ConfigurationError(REPLAY_TAKES_NO_GENERATION)
        config = _load(config_path)
        live = mode == "live"
        try:
            return cls(
                mode=mode,
                rule_logic_path=config.paths.rule_logic,
                fields_path=config.paths.rule_fields,
                ruleset_path=config.paths.ruleset,
                recordings_dir=recordings_dir,
                cache_dir=(config.paths.llm_cache if cache_dir is CONFIG_CACHE else cache_dir) if live else None,
                extraction_model=overrides.get("extraction_model", config.llm.model) if live else None,
                llm_url=config.llm.url,
                temperature=overrides.get("temperature", config.llm.temperature),
                timeout_seconds=overrides.get("timeout_seconds", config.llm.timeout_seconds),
                openrouter_api_key=config.openrouter_api_key if live else None,
                recorded_with=recorded_with,
            )
        except ValidationError as error:
            first = error.errors()[0]
            raise ConfigurationError(f"{config_path} is not valid compiler settings: {'.'.join(map(str, first['loc']))}: {first['msg']}") from None
