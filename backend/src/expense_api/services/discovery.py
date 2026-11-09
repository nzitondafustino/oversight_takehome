"""What the installed core supports and this deployment allows: backends and the field catalog. No model is called.

Three things are kept apart: installed (the core's registry and its optional packages), enabled (this deployment's
settings), and available (both, with what live needs present). A configured key is reported only as present: it
says nothing about whether the provider would answer, and its value never leaves the process.

Four things are also kept apart, so that a client never presents one as another: the orchestrator (direct, or
LangGraph), the provider a request goes to (OpenRouter for every registered backend), the execution adapter that
speaks to it (a registered backend: chat completions or the Decisions API), and the model. The adapter follows from
the model, by each backend's own check, and stays the identifier configuration, requests and records use.
"""

import hashlib
import importlib.util

from expense_validator import ConfigurationError, ValidatorSettings
from expense_validator.classification.backends.registry import ADAPTERS, BACKENDS, REVIEWERS, REVISERS, backends_for
from expense_validator.classification.core.common import load_taxonomy
from expense_validator.compiler.validate import read_fields
from expense_validator.config import CHAT_COMPLETIONS, DECISIONS, LANGGRAPH, LEGACY_BACKENDS, canonical_backend

from expense_api.config import Settings
from expense_api.errors import ApiError

# Display names only. The identifiers come from the core's registry; one without a name here shows its identifier.
# A registered backend is named for its execution adapter: its provider and model are reported beside it.
NAMES = {CHAT_COMPLETIONS: "Chat completions adapter", DECISIONS: "Decisions API adapter", LANGGRAPH: "LangGraph orchestrator"}
PROVIDER_NAMES = {"openrouter": "OpenRouter"}
DIRECT = "direct"  # no orchestrator: one adapter classifies every line


def backends(settings: Settings) -> dict:
    core = _core(settings, "replay")
    key_present = _core(settings, "live").openrouter_api_key is not None
    langgraph_installed = importlib.util.find_spec("langgraph") is not None
    entries = [_backend(settings, name, backend.cache_folder, backend.check_model, core, key_present) for name, backend in BACKENDS.items()]
    orchestrator = _backend(settings, LANGGRAPH, None, None, core, key_present)
    configured = core.langgraph
    orchestrator["installed"] = langgraph_installed
    orchestrator["available"] = orchestrator["available"] and langgraph_installed
    orchestrator["capabilities"] |= {
        # Each model node: its adapter (backend), the provider that adapter calls, and its model.
        "order": [{"name": child.name, "backend": child.backend, "model": child.model, **_adapter(child.backend)} for child in configured.backends],
        "routing": configured.routing.model_dump(),
        "reflection": {"enabled": configured.reflection.enabled,
                       "reviewer": None if configured.reflection.reviewer is None else configured.reflection.reviewer.model_dump() | _adapter(configured.reflection.reviewer.backend),
                       "max_retries": configured.reflection.max_retries, "max_total_calls_per_line": configured.reflection.max_total_calls_per_line,
                       "feedback_context": configured.reflection.feedback_context,
                       # What a request may ask for: the deployment's maximums, and the adapters that revise with structured context.
                       "limits": {"max_retries": settings.max_reflection_retries, "max_total_calls_per_line": settings.max_reflection_calls_per_line},
                       "feedback_contexts": ["reviewer_feedback", "structured"],
                       "structured_revisers": [name for name, reviser in REVISERS.items() if reviser.structured_prompt is not None]},
    }
    return {
        "default_backend": core.classification_backend,
        "execution_modes": {"default": "replay", "replay": bool(settings.replay_sets), "live": settings.allow_live, "deterministic": True},
        "credential_configured": key_present,
        "allowed_models": sorted(settings.allowed_models),  # the compiler's extraction allowlist, and each role's where it has none
        "allowed_classification_models": sorted(settings.classification_allowlist),
        "allowed_translation_models": sorted(settings.translation_allowlist),
        "backends": [*entries, orchestrator],
        "orchestrators": _orchestrators(core, orchestrator),
        "providers": _providers(entries, key_present),
        "classification_models": _classification_models(settings, core, entries),
        "policy_compiler": _policy_compiler(settings, key_present),
        "rulesets": {"preview_allowed": settings.allow_preview_validation, "publication_modes": sorted(settings.allowed_publication_modes)},
        "evaluations": {"uncapped_allowed": settings.allow_uncapped_evaluations},
        "translation": _translation(settings, core),
    }


def _translation(settings: Settings, core: ValidatorSettings) -> dict:
    """The configured translator, and the models allowed for translation that could replace it: chat models only.

    Only the translation allowlist is read, so a classification model is never offered as a translator.
    """
    default = core.translation_model if core.translation_enabled else None
    allowed = settings.translation_allowlist
    candidates = {model for model in allowed if is_chat_model(model)} | ({default} if default else set())
    return {
        "enabled": core.translation_enabled,
        "default_model": default,
        # The translator sends chat completions through OpenRouter, whatever adapter classifies.
        "models": [{"model": model, "provider": ADAPTERS[CHAT_COMPLETIONS].provider, "default": model == default, "allowed": model in allowed} for model in sorted(candidates)],
    }


def _adapter(backend: str) -> dict:
    """A registered backend's provider, protocol and canonical adapter name, under its name or an earlier one, such as
    jev for decisions; null for a name the core does not register. The name as recorded stays in `backend`."""
    adapter = ADAPTERS.get(canonical_backend(backend))
    if adapter is None:
        return {"provider": None, "protocol": None, "adapter": None}
    return {"provider": adapter.provider, "protocol": adapter.protocol, "adapter": canonical_backend(backend)}


def _orchestrators(core: ValidatorSettings, langgraph: dict) -> list[dict]:
    """Direct, where the chosen model's adapter classifies every line, and LangGraph, which runs its configured nodes."""
    direct = {"id": DIRECT, "name": "Direct", "installed": True, "enabled": True, "available": True, "default": core.classification_backend != LANGGRAPH, "nodes": None}
    nodes = langgraph["capabilities"]["order"]
    return [direct, {key: langgraph[key] for key in ("id", "installed", "enabled", "available", "default")} | {"name": "LangGraph", "nodes": nodes}]


def _providers(entries: list[dict], key_present: bool) -> list[dict]:
    """The providers the registered adapters call, in registry order: each with its adapters and whether live could reach it."""
    providers: dict[str, dict] = {}
    for entry in entries:
        provider = entry["provider"]
        if provider is None:
            continue
        item = providers.setdefault(provider, {"id": provider, "name": PROVIDER_NAMES.get(provider, provider), "adapters": [], "credential_configured": key_present})
        item["adapters"].append(entry["id"])
    return list(providers.values())


def _classification_models(settings: Settings, core: ValidatorSettings, entries: list[dict]) -> list[dict]:
    """Every model the deployment names for classification, each with the one adapter its ID needs and that adapter's provider.

    Named by: config.yaml's classification model, each LangGraph node's model, and the classification allowlist, never
    the translation one. A model no registered adapter accepts, or more than one would, is left out, as a request
    naming it would be refused or ambiguous.
    """
    sources: dict[str, list[str]] = {}
    if core.classification_backend != LANGGRAPH and core.classification_model:
        sources.setdefault(core.classification_model, []).append("default")
    for child in core.langgraph.backends:
        if child.model:
            sources.setdefault(child.model, []).append(f"langgraph:{child.name}")
    allowed = settings.classification_allowlist
    for model in sorted(allowed):
        sources.setdefault(model, []).append("allowed")
    by_id = {entry["id"]: entry for entry in entries}
    models = []
    for model, named_by in sources.items():
        adapters = [name for name in backends_for(model) if name in by_id]
        if len(adapters) != 1:
            continue
        entry = by_id[adapters[0]]
        models.append({
            "model": model,
            "provider": entry["provider"],
            "backend": entry["id"],  # the execution adapter a request names for this model
            "protocol": entry["protocol"],
            "default": core.classification_backend == entry["id"] and core.classification_model == model,
            "allowed": model in allowed,
            "sources": named_by,
            "capabilities": entry["capabilities"],
        })
    return models


def extraction_models(settings: Settings) -> list[str]:
    """The models live policy extraction may use: the deployment's own list, or else the allowed models the compiler
    can send chat completions to, without any that is allowed only as a translator. Never a decision model such as Jev."""
    if settings.allowed_extraction_models is not None:
        return sorted(model for model in settings.allowed_extraction_models if is_chat_model(model))
    translation_only = settings.translation_allowlist - settings.classification_allowlist
    return sorted(model for model in settings.allowed_models if is_chat_model(model) and model not in translation_only)


def is_chat_model(model: str) -> bool:
    """Whether the core accepts the model for OpenRouter chat completions, as the translator sends them."""
    return _fits(BACKENDS[CHAT_COMPLETIONS].check_model, model)


def _policy_compiler(settings: Settings, key_present: bool) -> dict:
    """The compiler's recording sets by name, never by path, and whether live extraction could run here."""
    live_set = settings.compiler_live_recording_set
    sets = [{"name": name, "present": path.is_dir(), "live_target": name == live_set} for name, path in sorted(settings.compiler_recording_sets.items())]
    configured = live_set is not None
    return {
        "replay": {"supported": bool(sets), "sets": sets},
        "live": {"permitted": settings.allow_live, "configured": configured, "credential_configured": key_present, "available": settings.allow_live and configured and key_present},
        "extraction_models": extraction_models(settings),  # the only models a live extraction may name
    }


def _backend(settings: Settings, name: str, cache_folder: str | None, check_model, core: ValidatorSettings, key_present: bool) -> dict:
    enabled = name in settings.allowed_backends
    if check_model is None:  # langgraph: each backend in its order has its own model
        models, default_model = [], None
    else:
        models, default_model = [model for model in sorted(settings.classification_allowlist) if _fits(check_model, model)], core.classification_model if core.classification_backend == name else None
    replay = [_replay_set(set_name, path, cache_folder) for set_name, path in sorted(settings.replay_sets.items())]
    live_permitted = enabled and settings.allow_live
    return {
        "id": name,
        "name": NAMES.get(name, name),
        "kind": "orchestrator" if check_model is None else "adapter",
        "legacy_ids": [old for old, new in LEGACY_BACKENDS.items() if new == name],  # earlier names requests and records may use
        **(_adapter(name) if check_model is not None else {"provider": None, "protocol": None}),
        "installed": True,  # in the core's registry, or the core's own orchestrator
        "enabled": enabled,
        "available": enabled,  # replay or deterministic use: needs nothing more
        "default": core.classification_backend == name,
        "default_model": default_model,
        "allowed_models": models,  # the deployment's allowed classification models this backend's own check accepts
        "capabilities": {"review": name in REVIEWERS, "revise": name in REVISERS},
        "replay": {"supported": True, "sets": replay},
        "live": {"permitted": live_permitted, "credential_configured": key_present, "available": live_permitted and key_present},
    }


def _fits(check_model, model: str) -> bool:
    try:
        check_model(model)
    except ValueError:
        return False
    return True


def _replay_set(name: str, path, cache_folder: str | None) -> dict:
    """Whether a replay set holds any answer files for this backend: presence only, not which receipts they cover."""
    if cache_folder is None:
        return {"name": name, "present": path.is_dir()}
    folder = path / cache_folder if cache_folder else path
    # The root folder also holds translations, so for a backend whose answers sit there the count includes them.
    answers = sum(1 for item in folder.iterdir() if item.is_file()) if folder.is_dir() else 0
    return {"name": name, "present": folder.is_dir(), "answer_files": answers, "folder": cache_folder or "."}


def taxonomy(settings: Settings) -> dict:
    """The core's field catalog and category taxonomy, as the validator reads them, with their content hashes."""
    core = _core(settings, "replay")
    try:
        fields_text, taxonomy_text = core.fields_path.read_text(), core.taxonomy_path.read_text()
        fields, categories = read_fields(fields_text), load_taxonomy(core.taxonomy_path)
    except (OSError, ValueError) as error:
        raise ApiError(503, "taxonomy_unavailable", f"the field catalog or taxonomy cannot be read: {type(error).__name__}") from None
    if not isinstance(fields, dict):
        raise ApiError(503, "taxonomy_unavailable", "the field catalog is not a mapping")
    entity = fields.get("receipt.entity", {})
    country = fields.get("receipt.employee_country", {})
    catalog_categories = fields.get("item.category", {}).get("values")
    return {
        "catalog": {"path": core.fields_path.name, "sha256": hashlib.sha256(fields_text.encode()).hexdigest(), "input_contract_version": fields.get("input_contract", {}).get("version")},
        "taxonomy": {"path": core.taxonomy_path.name, "sha256": hashlib.sha256(taxonomy_text.encode()).hexdigest()},
        # The catalog's categories, in its order, each with the taxonomy's restricted flag and examples when it has a row.
        "categories": None if catalog_categories is None else [
            {"id": name, "restricted": categories[name].restricted if name in categories else None, "examples": categories[name].examples if name in categories else None}
            for name in catalog_categories
        ],
        "entities": None if "values" not in entity else [{"id": name, "countries": list(entity.get("countries", {}).get(name, []))} for name in entity["values"]],
        "countries": None if "home_currency" not in country else [{"id": code, "home_currency": currency} for code, currency in country["home_currency"].items()],
        "expense_types": fields.get("receipt.submitted_expense_type", {}).get("values"),
        "currencies": fields.get("input_contract", {}).get("minor_units"),
        # Every controlled vocabulary in the catalog: each field that lists the only values it may take.
        "vocabularies": {name: field["values"] for name, field in fields.items() if isinstance(field, dict) and "values" in field},
        "fields": {name: {key: value for key, value in field.items() if key in ("type", "unit")} for name, field in fields.items() if isinstance(field, dict) and "type" in field},
    }


def _core(settings: Settings, mode: str) -> ValidatorSettings:
    try:
        return ValidatorSettings.from_config(settings.core_config, mode=mode, cache_dir=None)
    except ConfigurationError as error:
        raise ApiError(503, "core_configuration_unavailable", f"the core's configuration cannot be read: {error}") from None


def describe_setup(backend: str | None, model: str | None, langgraph: dict | None) -> dict:
    """A recorded classification as orchestrator, provider, adapter and model, worked out when read, never stored.

    Reads only what a run recorded (its backend, model and LangGraph snapshot), so a run recorded as backend jev,
    before this view existed, reads as direct, through OpenRouter, with the Decisions API adapter.
    """
    if backend == LANGGRAPH:
        nodes = [{"name": node.get("name"), "backend": node.get("backend"), "model": node.get("model"), **_adapter(node.get("backend"))} for node in (langgraph or {}).get("backends", [])]
        return {"orchestrator": LANGGRAPH, "provider": None, "backend": None, "protocol": None, "adapter": None, "model": None, "nodes": nodes}
    if backend is None:  # deterministic: no classifier ran
        return {"orchestrator": None, "provider": None, "backend": None, "protocol": None, "adapter": None, "model": None, "nodes": None}
    return {"orchestrator": DIRECT, "backend": backend, "model": model, **_adapter(backend), "nodes": None}


def adapter_mismatches(settings: Settings, selection) -> list[dict]:
    """Every model a request names that its execution adapter would not run, before anything runs.

    Checks only what the request names: a direct `model` against its `backend` (or, with none named, config.yaml's
    default backend), and each LangGraph node override that names both. A model left to config.yaml, a name the core
    does not register (refused elsewhere), and LangGraph's own rules stay with the allowlists and the core.
    """
    found = []
    if selection.model is not None:
        backend = selection.backend
        if backend is None:
            try:
                backend = ValidatorSettings.from_config(settings.core_config, mode="deterministic").classification_backend
            except ConfigurationError:
                backend = None  # the core's own error says why, when it is built
        backend = canonical_backend(backend)
        if backend in BACKENDS:
            found.append(_mismatch(selection.model, backend, None))
    nodes = (selection.langgraph or {}).get("backends") if isinstance(selection.langgraph, dict) else None
    for node in nodes if isinstance(nodes, list) else []:
        backend = canonical_backend(node.get("backend")) if isinstance(node, dict) else None
        if backend in BACKENDS and isinstance(node.get("model"), str):
            found.append(_mismatch(node["model"], backend, node.get("name")))
    return [item for item in found if item is not None]


def _mismatch(model: str, backend: str, node: str | None) -> dict | None:
    compatible = backends_for(model)
    if backend in compatible:
        return None
    return {"model": model, "backend": backend, "node": node, "compatible": compatible, "where": f" in LangGraph node {node}" if node else ""}


def run_setup(backend: str | None, model_label: str | None, recorded: bool = True) -> dict:
    """A validation run's classification, from what its receipts' provenance and metadata recorded.

    `model_label` is the core's `metadata.model`: the model, or for LangGraph its order as `name=backend:model, ...`,
    which node names (no `=` or `,`) and registered backend names (no `:`) keep unambiguous. `recorded` is false for a
    run stored before its summary held the model: that model, or LangGraph's nodes, are then null, never guessed.
    """
    if backend != LANGGRAPH:
        return describe_setup(backend, model_label if recorded else None, None)
    nodes = _order_from_label(model_label) if recorded else None
    return {"orchestrator": LANGGRAPH, "provider": None, "backend": None, "protocol": None, "adapter": None, "model": None, "nodes": nodes}


def _order_from_label(label: str | None) -> list[dict] | None:
    if not label:
        return None
    nodes = []
    for part in label.split(", "):
        name, equals, rest = part.partition("=")
        backend, colon, model = rest.partition(":")
        if not (equals and colon and name and backend and model):
            return None  # not the core's label: shown as not recorded, never half-read
        nodes.append({"name": name, "backend": backend, "model": model, **_adapter(backend)})
    return nodes


def recorded_names(backend: str) -> list[str]:
    """Every name a record may hold for this backend: the adapter's own and its earlier ones, so history finds both."""
    canonical = canonical_backend(backend)
    return [canonical, *(old for old, new in LEGACY_BACKENDS.items() if new == canonical)]


def classification_identity(setup: dict) -> str | None:
    """What a run classified with, by each adapter's own name, so that runs made before and after the adapters were
    renamed match when they ran the same thing: `decisions:typesafe/jev-1.14`, or a LangGraph order in its order."""
    if setup["orchestrator"] == LANGGRAPH:
        nodes = setup["nodes"]
        return None if nodes is None else ", ".join(f"{node['name']}={node['adapter'] or node['backend']}:{node['model']}" for node in nodes)
    if setup["orchestrator"] is None:
        return None
    return f"{setup['adapter'] or setup['backend']}:{setup['model']}"

