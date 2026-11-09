"""Run a batch through the core's ExpenseValidator against one stored ruleset version, and persist the run."""

import logging
import time
from collections.abc import Callable
from importlib.metadata import version
from pathlib import Path

import httpx
from bson import ObjectId
from expense_validator import (
    BatchResult,
    ConfigurationError,
    EvidenceError,
    ExpenseValidator,
    InvalidInputError,
    ReplayAnswerMissing,
    ValidatorError,
    ValidatorSettings,
)
from expense_validator.config import LANGGRAPH, canonical_backend
from openrouter.errors import OpenRouterError

import expense_api
from expense_api.config import Settings
from expense_api.errors import ApiError
from expense_api.repositories import RunRepository
from expense_api.schemas.validation import ValidationRequest
from expense_api.services.discovery import adapter_mismatches, is_chat_model
from expense_api.services.rulesets import ArtifactStore, RulesetService, now, sha256
from expense_api.services.runs import summarize

log = logging.getLogger("expense_api")
CORE_VERSION = version("expense-policy-validator")


class ValidationService:
    def __init__(
        self,
        settings: Settings,
        rulesets: RulesetService,
        runs: RunRepository,
        store: ArtifactStore,
        build_validator: Callable[[ValidatorSettings], ExpenseValidator] = ExpenseValidator,  # tests pass a stand-in
    ):
        self.settings, self.rulesets, self.runs, self.store = settings, rulesets, runs, store
        self.build_validator = build_validator

    def validate(self, request: ValidationRequest) -> dict:
        """Validate the receipts synchronously and return the persisted run. Nothing is persisted on failure."""
        replay_set = self._authorize(request)
        record = self.rulesets.get(request.ruleset_id)
        self._check_publication(record, request.preview)
        validator = self._validator(request.execution_mode, replay_set, self.store.path(record), self.store.policy_docs(record, self.rulesets.compilation.documents))
        started, clock = now(), time.perf_counter()
        batch = self._run(validator, request)
        duration_ms, completed = round((time.perf_counter() - clock) * 1000), now()
        _check_identity(batch, request, record)
        results = batch.model_dump_json()
        run = {
            "_id": f"run_{ObjectId()}",
            "status": "completed",
            "ruleset": {name: record.get(key) for name, key in RULESET_REFERENCE.items()},
            "execution_mode": request.execution_mode,
            "preview": request.preview,
            "context_complete": request.context_complete,
            "classification": request.classification.model_dump(mode="json") | {"replay_set": replay_set},
            "receipt_ids": [result.receipt_id for result in batch.results],
            "dispositions": {disposition: sum(result.disposition == disposition for result in batch.results) for disposition in ("flag", "review", "pass")},
            # The core's result as its own JSON: an exact copy, read back with BatchResult on retrieval.
            "results_json": results,
            "results_sha256": sha256(results),
            # For history pages and their filters only; every finding is read from results_json.
            "summary": summarize(batch),
            "execution": {
                "started_at": started,
                "completed_at": completed,
                "duration_ms": duration_ms,
                "receipt_count": len(batch.results),
                "core_version": CORE_VERSION,
                "api_version": expense_api.__version__,
            },
            "created_at": completed,
        }
        self.runs.insert(run)
        return run

    def get(self, run_id: str) -> dict:
        run = self.runs.get(run_id)
        if run is None:
            raise ApiError(404, "run_not_found", f"no run {run_id}")
        return run

    def _authorize(self, request: ValidationRequest) -> str | None:
        """Refuse what the deployment does not allow, before anything runs. Returns the replay set's name."""
        return authorize_classification(self.settings, request.classification, request.execution_mode, len(request.receipts))

    def _check_publication(self, record: dict, preview: bool) -> None:
        check_publication(self.settings, record, preview)

    def _validator(self, mode: str, replay_set: str | None, ruleset_path: Path, policy_docs_path: Path | None = None) -> ExpenseValidator:
        """A new validator for this request alone, reading exactly the stored ruleset."""
        cache_dir = self.settings.replay_sets[replay_set] if mode == "replay" else self.settings.live_cache_dir
        try:
            core = ValidatorSettings.from_config(self.settings.core_config, mode=mode, cache_dir=cache_dir)
            return self.build_validator(core.model_copy(update={"ruleset_path": ruleset_path, **({"policy_docs_path": policy_docs_path} if policy_docs_path is not None else {})}))
        except ConfigurationError as error:
            if mode == "live":
                raise ApiError(503, "live_inference_unavailable", str(error)) from None
            log.error("the validator cannot be configured for %s mode: %s", mode, error)
            raise ApiError(500, "validator_configuration_error", f"the validator cannot be configured for {mode} mode") from None

    def _run(self, validator: ExpenseValidator, request: ValidationRequest) -> BatchResult:
        selection = request.classification
        choices = {
            "classification_backend": selection.backend,
            "classification_model": selection.model,
            "translation_model": selection.translation_model,
            "langgraph_config": selection.langgraph,
        }
        try:
            return validator.validate_batch(request.receipts, context_complete=request.context_complete, **choices)
        except InvalidInputError as error:
            raise ApiError(422, "invalid_receipts", str(error)) from None
        except ReplayAnswerMissing as error:
            # Replay never calls a model, so a gap stays a gap. The missing requests hold receipt text, so only their count is returned.
            raise ApiError(409, "replay_recording_missing", str(error), {"missing_answers": len(error.missing)}) from None
        except ConfigurationError as error:
            if any(value is not None for value in choices.values()):  # the validator itself was checked when it was built
                raise ApiError(422, "unsupported_configuration", str(error)) from None
            raise
        except EvidenceError:
            raise ApiError(500, "evidence_unverifiable", "a finding's policy evidence could not be verified against the policy documents") from None
        except (OpenRouterError, httpx.HTTPError) as error:
            # The core turns a provider failure on a line into that line's classification_error; this is one that escaped it.
            log.error("provider failure: %s", type(error).__name__)
            raise ApiError(502, "provider_failure", "a model provider failed") from None
        except ValidatorError as error:
            log.error("validator failure: %s", type(error).__name__)
            raise ApiError(500, "validator_error", "the validator failed") from None


RULESET_REFERENCE = {"ruleset_id": "_id", "name": "name", "version": "version", "status": "status", "publication_mode": "publication_mode", "artifact_sha256": "artifact_sha256", "ruleset_version": "ruleset_version"}


def _values(data: object, key: str) -> list:
    """Every value stored under key, at any depth, such as each model in a langgraph configuration."""
    if isinstance(data, dict):
        return [value for name, item in data.items() for value in ([item] if name == key and isinstance(item, str) else _values(item, key))]
    if isinstance(data, list):
        return [value for item in data for value in _values(item, key)]
    return []


def _check_identity(batch: BatchResult, request: ValidationRequest, record: dict) -> None:
    """Refuse a result that is not for exactly these receipts, lines, mode and ruleset."""
    sent = {receipt["receipt_id"]: [line["line_id"] for line in receipt["line_items"]] for receipt in request.receipts}
    returned = {result.receipt_id: [line.line_id for line in result.line_items] for result in batch.results}
    same = (
        sent == returned
        and batch.mode == request.execution_mode
        and all(result.provenance.ruleset_sha256 == record["artifact_sha256"] for result in batch.results)
    )
    if not same:
        raise ApiError(500, "integrity_error", "the validator's result does not match the request or the stored ruleset")


def authorize_classification(settings: Settings, selection, mode: str, receipt_count: int) -> str | None:
    """Refuse a classification the deployment does not allow, before anything runs. Returns the replay set's name.

    Shared by synchronous validation and evaluations, so both apply the same switches and allowlists.
    """
    if mode == "live" and not settings.allow_live:
        raise ApiError(403, "live_inference_disabled", "this deployment does not allow live inference")
    if receipt_count > settings.max_receipts_per_request:
        raise ApiError(422, "too_many_receipts", f"at most {settings.max_receipts_per_request} receipts per request")
    # An earlier adapter name (openrouter, jev) is the adapter it names, for the allowlist as for the core.
    backends = [canonical_backend(name) for name in (selection.backend, *_values(selection.langgraph, "backend"))]
    refused = sorted({backend for backend in backends if backend is not None and backend not in settings.allowed_backends})
    if refused:
        raise ApiError(422, "backend_not_allowed", f"not an allowed classification backend: {refused}", {"allowed": sorted(settings.allowed_backends)})
    mismatches = adapter_mismatches(settings, selection)
    if mismatches:
        # Each model a request names must be one its adapter runs. The adapter is never swapped for one that would.
        first = mismatches[0]
        raise ApiError(422, "model_adapter_mismatch", f"{first['model']} cannot run on the {first['backend']} adapter{first['where']}: it needs {' or '.join(first['compatible']) or 'no registered adapter'}",
                       {"mismatches": [{key: item[key] for key in ("model", "backend", "node", "compatible")} for item in mismatches]})
    _check_reflection_limits(settings, selection)
    if selection.translation_model is not None and not is_chat_model(selection.translation_model):
        # The translator sends chat completions: a model that never answers them would fail every translation it is sent.
        raise ApiError(422, "translation_model_unsupported", f"{selection.translation_model} does not answer chat completions, so it cannot translate")
    # Each model is checked against its own role's allowlist: a translator is never accepted as a classifier, nor the
    # reverse. A live request choosing a model must find it listed, as every model costs money.
    roles = {
        "classification": ({model for model in (selection.model, *_values(selection.langgraph, "model")) if model is not None}, settings.classification_allowlist),
        "translation": ({selection.translation_model} - {None}, settings.translation_allowlist),
    }
    for role, (models, allowed) in roles.items():
        if models - allowed and (allowed or mode == "live"):
            raise ApiError(422, "model_not_allowed", f"not an allowed {role} model: {sorted(models - allowed)}", {"role": role, "allowed": sorted(allowed)})
    if mode != "replay":
        if selection.replay_set is not None:
            raise ApiError(422, "replay_set_not_applicable", f"replay_set applies only to replay mode, not {mode}")
        return None
    name = selection.replay_set or settings.default_replay_set
    if name is None:
        raise ApiError(422, "replay_set_required", "name a replay_set: this deployment has no default", {"available": sorted(settings.replay_sets)})
    if name not in settings.replay_sets:
        raise ApiError(422, "unknown_replay_set", f"no replay set {name!r}", {"available": sorted(settings.replay_sets)})
    return name


def _check_reflection_limits(settings: Settings, selection) -> None:
    """Refuse LangGraph reflection beyond the deployment's maximums, as the request and config.yaml combine.

    A configuration the core cannot read or merge is left for the core to refuse, with its own reason.
    """
    try:
        core = ValidatorSettings.from_config(settings.core_config, mode="deterministic")
    except ConfigurationError:
        return
    if canonical_backend(selection.backend or core.classification_backend) != LANGGRAPH:
        return
    try:
        reflection = core.langgraph.merged(selection.langgraph).reflection
    except ValueError:
        return
    limits = {"max_retries": settings.max_reflection_retries, "max_total_calls_per_line": settings.max_reflection_calls_per_line}
    asked = {"max_retries": reflection.max_retries, "max_total_calls_per_line": reflection.max_total_calls_per_line}
    over = sorted(name for name in limits if asked[name] > limits[name])
    if reflection.enabled and over:
        raise ApiError(422, "reflection_limit_exceeded", f"reflection asks for more than this deployment allows: {', '.join(f'{name} {asked[name]} > {limits[name]}' for name in over)}",
                       {"limits": limits, "requested": asked})


def check_publication(settings: Settings, record: dict, preview: bool) -> None:
    """Allow only a version published for production, unless the request asks for a preview the deployment allows."""
    if preview:
        if not settings.allow_preview_validation:
            raise ApiError(403, "preview_disabled", "this deployment validates only against published rulesets")
        return
    if record["status"] != "published" or record.get("publication_mode") != "production":
        state = record["status"] if record["status"] != "published" else f"published by {record.get('publication_mode')}"
        raise ApiError(409, "ruleset_not_published", f"ruleset {record['_id']} is {state}: validate against a version published for production, or ask for a preview", {"status": record["status"], "publication_mode": record.get("publication_mode")})
