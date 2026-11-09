"""Backend and taxonomy discovery."""

from typing import Any, Literal

from pydantic import BaseModel


class ReplaySet(BaseModel):
    name: str
    present: bool
    answer_files: int | None = None  # files in the backend's answer folder; the root folder also holds translations
    folder: str | None = None


class Replay(BaseModel):
    supported: bool
    sets: list[ReplaySet]


class Live(BaseModel):
    permitted: bool  # enabled here, and ALLOW_LIVE
    credential_configured: bool  # a key is set: not proof the provider would answer
    available: bool


class Backend(BaseModel):
    """A registered backend, which is an execution adapter (the protocol a model needs), or the LangGraph orchestrator."""

    id: str  # from the core's registry, or langgraph: what requests, config.yaml and records name
    name: str
    kind: Literal["adapter", "orchestrator"]
    legacy_ids: list[str] = []  # earlier names for this adapter, such as jev, which requests and records may still use
    provider: str | None  # the API provider an adapter calls; null for the orchestrator, whose nodes each have one
    protocol: str | None  # chat_completions or decisions; null for the orchestrator
    installed: bool  # in the installed core, with any optional package it needs
    enabled: bool  # in ALLOWED_BACKENDS
    available: bool  # installed and enabled, for replay or deterministic use
    default: bool  # config.yaml's classification.backend
    default_model: str | None
    allowed_models: list[str]  # allowed models this backend's own check accepts
    capabilities: dict[str, Any]  # review and revise; for langgraph, its configured order, routing and reflection
    replay: Replay
    live: Live


class CompilerRecordingSet(BaseModel):
    name: str  # what an extraction's recording_set names
    present: bool  # the folder exists: not which documents it holds readings for
    live_target: bool  # live extraction records its readings here


class CompilerReplay(BaseModel):
    supported: bool  # at least one recording set is configured
    sets: list[CompilerRecordingSet]


class CompilerLive(BaseModel):
    permitted: bool  # ALLOW_LIVE
    configured: bool  # a live recording set is named for readings to be recorded into
    credential_configured: bool  # a key is set: not proof the provider would answer
    available: bool  # all three


class PolicyCompiler(BaseModel):
    """What POST /v1/documents/extractions can use here."""

    replay: CompilerReplay
    live: CompilerLive
    extraction_models: list[str] = []  # the only models a live extraction may name: chat models, never Jev or a translator


class RulesetPermissions(BaseModel):
    """Which ruleset versions requests may use here."""

    preview_allowed: bool  # ALLOW_PREVIEW_VALIDATION: validate or evaluate a version not published for production
    publication_modes: list[str]  # ALLOWED_PUBLICATION_MODES: the publications an approval may authorize


class TranslationModel(BaseModel):
    model: str
    provider: str  # the translator always sends chat completions through this provider
    default: bool  # config.yaml's translation.model
    allowed: bool  # in the translation allowlist: a live run may call it


class Translation(BaseModel):
    """The translator: called only for non-English lines, but counted among a live run's models whenever enabled."""

    enabled: bool  # config.yaml's translation.enabled
    default_model: str | None
    # The default, and every allowed model the core accepts for chat completions, which the translator sends.
    models: list[TranslationModel]


class EvaluationPermissions(BaseModel):
    uncapped_allowed: bool  # ALLOW_UNCAPPED_EVALUATIONS: a live evaluation may run with no limit


class OrchestratorNode(BaseModel):
    name: str
    backend: str  # the node's execution adapter, by the name configured
    model: str | None
    provider: str | None
    protocol: str | None
    adapter: str | None = None  # the adapter's own name, whichever name is configured


class Orchestrator(BaseModel):
    """How lines reach a model: direct (the chosen model's adapter classifies every line) or LangGraph (its configured nodes)."""

    id: str  # direct, or langgraph, which is also its backend identifier in requests
    name: str
    installed: bool
    enabled: bool
    available: bool
    default: bool
    nodes: list[OrchestratorNode] | None  # LangGraph's model nodes in order, each with its own provider and model; null for direct


class Provider(BaseModel):
    id: str
    name: str
    adapters: list[str]  # the registered backends that call this provider
    credential_configured: bool  # a key is set: not proof the provider would answer


class ClassificationModel(BaseModel):
    """A model the deployment names for classification, with the one execution adapter its ID needs."""

    model: str
    provider: str
    backend: str  # the execution adapter a request names for this model; chosen from the model, never by the user
    protocol: str
    default: bool  # config.yaml's direct classification model
    allowed: bool  # in the classification allowlist: a live run may call it
    sources: list[str]  # default, langgraph:<node> or allowed: where the deployment names it
    capabilities: dict[str, Any]  # the adapter's review and revise


class Backends(BaseModel):
    default_backend: str
    execution_modes: dict[str, Any]
    credential_configured: bool
    allowed_models: list[str]  # the compiler's extraction allowlist, and each role's where it has none
    allowed_classification_models: list[str]
    allowed_translation_models: list[str]
    backends: list[Backend]
    orchestrators: list[Orchestrator]
    providers: list[Provider]
    classification_models: list[ClassificationModel]
    policy_compiler: PolicyCompiler
    rulesets: RulesetPermissions
    evaluations: EvaluationPermissions
    translation: Translation


class Source(BaseModel):
    path: str
    sha256: str
    input_contract_version: int | None = None


class Category(BaseModel):
    id: str
    restricted: bool | None  # None when the taxonomy has no row for a catalog category
    examples: str | None


class Entity(BaseModel):
    id: str
    countries: list[str]


class Country(BaseModel):
    id: str
    home_currency: str


class Taxonomy(BaseModel):
    """Read from the core's rules/fields.yaml and data/category-taxonomy.md. A section the catalog lacks is None."""

    catalog: Source
    taxonomy: Source
    categories: list[Category] | None
    entities: list[Entity] | None
    countries: list[Country] | None
    expense_types: list[str] | None
    currencies: dict[str, int] | None  # ISO 4217 code: minor units, from the input contract
    vocabularies: dict[str, list[str]]  # every catalog field that lists the only values it may take
    fields: dict[str, dict[str, str]]  # every field a rule may read: its type and any unit
