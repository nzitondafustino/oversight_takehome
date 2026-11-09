"""Plan and run evaluation jobs through ExpenseValidator.

The API calls plan; the worker calls run_job. Jobs first evaluate and store each
receipt separately for progress, cancellation and spending. A second pass replays
the same answers as one batch to evaluate rules spanning receipts, such as TIME-03.
If that pass cannot run, result_context marks the results as isolated receipts.

Live calls use metered clients, including LangGraph nodes, reviewers and revisers
injected through the core's client_factory.
"""

import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

import httpx
from expense_validator import ConfigurationError, EvidenceError, ExpenseValidator, InvalidInputError, ReplayAnswerMissing, ValidatorSettings
from expense_validator.classification.backends import jev, llm
from expense_validator.classification.core.common import load_vocabulary
from expense_validator.compiler.llm_extract import cached
from expense_validator.compiler.validate import read_fields
from expense_validator.engine.inputs import admit_receipt
from expense_validator.evaluation.classification import missing_recordings
from expense_validator.evaluation.coverage import rule_coverage
from expense_validator.policy.schema import RuleSet
from expense_validator.config import CHAT_COMPLETIONS, DECISIONS, LANGGRAPH, PROMPT_IDENTITY_VERSION
from expense_validator.normalization import LlmTranslator
from expense_validator.normalization import translation
from openrouter import OpenRouter
from openrouter.errors import OpenRouterError
from openrouter.utils import RetryConfig
from pymongo.database import Database

from expense_api.config import Settings
from expense_api.errors import ApiError
from expense_api.repositories import DatasetRepository, ResultRepository, RulesetRepository, UsageRepository
from expense_api.services.datasets import load_canonical
from expense_api.services.evaluation_metrics import METRICS_SCHEMA_VERSION, compute, receipt_scoring, summary
from expense_api.services.compilation_documents import CompilationDocuments
from expense_api.services.document_store import DocumentStore
from expense_api.services.rulesets import ArtifactStore, now, sha256
from expense_api.services.spending import (
    BUDGET_REASONS,
    NO_PRICING,
    EvaluationStopped,
    Meter,
    MeteredClient,
    Pricing,
    cached_calls,
    derived_records,
    expected_calls,
    spend_report,
    usd,
)

log = logging.getLogger("expense_api")
CORE_VERSION = version("expense-policy-validator")
# Every call a live run makes goes through a MeteredClient: the adapters' completions are built here by the core's own
# public functions, and LangGraph's nodes, reviewer and reviser through the core's client_factory.
LIVE_BACKENDS = (CHAT_COMPLETIONS, DECISIONS, LANGGRAPH)
NO_RETRY = RetryConfig("none", None, False)  # a retried request may be billed twice, unseen: never retry in an evaluation
# The core settings an evaluation fixes when it is created. The worker runs with exactly these.
SNAPSHOT_FIELDS = {"classification_backend", "classification_model", "langgraph", "jev_thresholds", "translation_enabled", "translation_model", "temperature", "timeout_seconds"}


class JobFailed(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass(frozen=True)
class Plan:
    core: ValidatorSettings
    snapshot: dict
    models: list[str]
    fingerprint: dict


# --- Planning, shared by the API (at creation) and the worker (before running) ---


def core_settings(settings: Settings, mode: str, cache_dir: Path | None, selection, ruleset_path: Path) -> ValidatorSettings:
    """The core's settings for this selection, with every override applied the way the core applies them."""
    core = ValidatorSettings.from_config(
        settings.core_config,
        mode=mode,
        cache_dir=cache_dir,
        classification_model=selection.model,
        translation_model=selection.translation_model,
        classification_backend=selection.backend,
        langgraph_config=selection.langgraph,
    )
    return core.model_copy(update={"ruleset_path": ruleset_path})


def from_snapshot(settings: Settings, mode: str, cache_dir: Path | None, snapshot: dict, ruleset_path: Path) -> ValidatorSettings:
    """The core's settings with the stored snapshot's classification, whatever config.yaml says today."""
    base = ValidatorSettings.from_config(settings.core_config, mode=mode, cache_dir=cache_dir)
    try:
        return ValidatorSettings.model_validate({**base.model_dump(), **snapshot, "ruleset_path": ruleset_path})
    except ValueError as error:
        raise JobFailed("configuration_changed", f"the stored configuration no longer loads: {str(error).splitlines()[0]}") from None


def plan(core: ValidatorSettings) -> Plan:
    """Check the configuration as the core would run it, and fix what the job must run with.

    Raises:
        ConfigurationError: If the core refuses the configuration.
    """
    if core.mode == "live":
        _check_live(core)
        # The core's checks; nothing is called. LangGraph connects each node through a client_factory instead.
        if core.classification_backend == LANGGRAPH:
            validator = ExpenseValidator(core, client_factory=_never_connected)
        else:
            validator = ExpenseValidator(core, classifier_model=_never_called)
    else:
        validator = ExpenseValidator(core)
    snapshot = core.model_dump(mode="json", include=SNAPSHOT_FIELDS)
    return Plan(core=core, snapshot=snapshot, models=models_of(core), fingerprint=fingerprint(core, validator))


def _never_called(prompt: str, text: str) -> str:
    raise AssertionError("planning calls no model")


def _never_connected(purpose: str, backend: str):
    raise AssertionError("planning connects to no provider")


def _check_live(core: ValidatorSettings) -> None:
    if core.classification_backend not in LIVE_BACKENDS:
        raise ConfigurationError(f"live evaluation supports {', '.join(LIVE_BACKENDS)}, whose every call can be metered and capped, not {core.classification_backend}")
    if core.openrouter_api_key is None:
        raise ConfigurationError("live mode needs OPENROUTER_API_KEY")


def models_by_role(core: ValidatorSettings) -> dict[str, list[str]]:
    """The models of models_of, by the role each plays, for checking each against its own allowlist."""
    classification = set()
    if core.classification_backend == LANGGRAPH:
        classification |= {child.model for child in core.langgraph.backends}
        if core.langgraph.reflection.enabled:
            classification.add(core.langgraph.reflection.reviewer.model)
    elif core.classification_model:
        classification.add(core.classification_model)
    translation = {core.translation_model} if core.translation_enabled and core.translation_model else set()
    return {"classification": sorted(classification), "translation": sorted(translation)}


def models_of(core: ValidatorSettings) -> list[str]:
    """Every model the configuration may call: each backend's, the reviewer's and the translator's."""
    models = set()
    if core.classification_backend == LANGGRAPH:
        models |= {child.model for child in core.langgraph.backends}
        if core.langgraph.reflection.enabled:
            models.add(core.langgraph.reflection.reviewer.model)
    elif core.classification_model:
        models.add(core.classification_model)
    if core.translation_enabled and core.translation_model:
        models.add(core.translation_model)
    return sorted(models)


def fingerprint(core: ValidatorSettings, validator: ExpenseValidator, version: int = PROMPT_IDENTITY_VERSION) -> dict:
    """What the job's results depend on besides the dataset and the answers, to refuse a run where any changed.

    version is the prompt identity version the hash names adapters by. A job records the one it was made with; one
    recorded without it was made before versions existed, under version 1, and is checked under it, never rewritten.
    """
    found = {
        "prompt_sha256": validator.prompt_sha256_at(version),
        "ruleset_sha256": validator.ruleset_sha256,
        "taxonomy_sha256": sha256(core.taxonomy_path.read_text()),
        "fields_sha256": sha256(core.fields_path.read_text()),
        "core_version": CORE_VERSION,
    }
    return found if version == 1 else found | {"prompt_identity_version": version}


def stored_fingerprint_version(job: dict) -> int:
    """The prompt identity version a stored job was fingerprinted under: 1 when it records none."""
    return job["configuration"]["fingerprint"].get("prompt_identity_version", 1)


def folder_sha256(path: Path) -> str:
    """One hash of every file in a folder of recorded answers, by relative path and content."""
    digest = hashlib.sha256()
    for file in sorted(item for item in path.rglob("*") if item.is_file()):
        digest.update(f"{file.relative_to(path).as_posix()}\0{hashlib.sha256(file.read_bytes()).hexdigest()}\n".encode())
    return digest.hexdigest()


def live_cache(settings: Settings, evaluation_id: str) -> Path:
    """A live run's own answer cache: empty when it starts, so every answer it pays for is metered, and the
    folder it can be replayed from afterwards."""
    if settings.live_cache_dir is None:
        raise ConfigurationError("live evaluations need EXPENSE_API_LIVE_CACHE_DIR, where each run keeps its answers")
    return settings.live_cache_dir / "evaluations" / evaluation_id


def openrouter_client(core: ValidatorSettings) -> OpenRouter:
    if core.openrouter_api_key is None:
        raise JobFailed("live_inference_unavailable", "live mode needs OPENROUTER_API_KEY")
    return OpenRouter(api_key=core.openrouter_api_key.get_secret_value(), timeout_ms=core.timeout_seconds * 1000, retry_config=NO_RETRY)


def live_validator(core: ValidatorSettings, meter: Meter, client_factory: Callable) -> ExpenseValidator:
    """The core's validator with its live completions built by the core's own functions over metered clients."""
    client = client_factory(core)
    translator = None
    if core.translation_enabled:
        # An injected translator is not cached by the core, so it is cached here under the core's own key, which
        # is what a replay of this run's folder reads.
        complete = translation.openrouter_completion(MeteredClient(client, meter, purpose="translation", backend=CHAT_COMPLETIONS), core.translation_model, core.temperature)
        translator = LlmTranslator(cached(complete, core.translation_model, core.cache_dir))
    if core.classification_backend == LANGGRAPH:
        # Each node, the reviewer and each reviser send through their own metered client, named by the call's purpose
        # and the node's adapter: the meter counts and caps every call, and stops the run before one it would not allow.
        # The core caches each answer in the run's folder, which the replay after the run reads.
        def metered(purpose: str, backend: str) -> MeteredClient:
            return MeteredClient(client, meter, purpose=purpose, backend=backend)

        return ExpenseValidator(core, translator=translator, client_factory=metered)
    vocabulary = load_vocabulary(core.taxonomy_path, core.fields_path)
    classify = MeteredClient(client, meter, purpose="classification", backend=core.classification_backend)
    if core.classification_backend == DECISIONS:
        classifier = jev.openrouter_decisions(classify, core.classification_model)
    else:
        classifier = llm.openrouter_completion(classify, core.classification_model, core.temperature, vocabulary)
    return ExpenseValidator(core, classifier_model=classifier, translator=translator)


# --- Running ---


@dataclass
class Outcome:
    stop_reason: str | None = None  # cancelled, a budget reason, worker_stopping or lease_lost
    error: dict | None = None  # a job-level failure: {code, message}
    result_context: str = "isolated_receipts"
    context_complete: bool | None = None
    note: str | None = None


class Lease:
    """What a job may do while its worker holds it: check for cancellation and renew the lease (guard), and write
    its own fields (update). Each raises EvaluationStopped once the job should stop."""

    def __init__(self, guard: Callable[[], None], update: Callable[[dict], None]):
        self.guard, self.update = guard, update


def run_job(job: dict, *, settings: Settings, database: Database, lease: Lease, client_factory: Callable = openrouter_client, pricing: Pricing = NO_PRICING) -> Outcome:
    results = ResultRepository(database["evaluation_results"])
    usage = UsageRepository(database["evaluation_usage"])
    try:
        cases, ruleset_path = _inputs(job, settings, database)
        mode = job["execution_mode"]
        if mode == "replay":
            cache_dir = settings.replay_sets.get(job["replay"]["set"])
            if cache_dir is None or folder_sha256(cache_dir) != job["replay"]["sha256"]:
                raise JobFailed("replay_set_changed", f"replay set {job['replay']['set']!r} is missing or no longer holds the answers the job was created with")
        else:
            cache_dir = live_cache(settings, job["_id"])
            cache_dir.mkdir(parents=True, exist_ok=True)
        core = from_snapshot(settings, mode, cache_dir, job["configuration"]["snapshot"], ruleset_path)
        version = RulesetRepository(database["rulesets"]).get(job["ruleset"]["ruleset_id"])
        documents = CompilationDocuments(lambda: DocumentStore(settings))
        sources = ArtifactStore(settings.artifact_dir).policy_docs(version, documents)
        if sources is not None:
            core = core.model_copy(update={"policy_docs_path": sources})
        meter = None
        if mode == "live":
            meter = Meter(evaluation_id=job["_id"], attempt=job["attempts"], budget=job["budget"], pricing=pricing,
                          guard=lease.guard, store=usage.record, sequence=usage.next_sequence(job["_id"]))
            _check_live(core)
            validator = live_validator(core, meter, client_factory)
            replayer = ExpenseValidator(core.model_copy(update={"mode": "replay", "openrouter_api_key": None}))
        else:
            validator = replayer = ExpenseValidator(core)
        if fingerprint(core, replayer, stored_fingerprint_version(job)) != job["configuration"]["fingerprint"]:
            raise JobFailed("configuration_changed", "the prompt, ruleset, taxonomy, field catalog or core version differs from what the job was created with")
    except JobFailed as failure:
        return Outcome(error={"code": failure.code, "message": failure.message})
    except ConfigurationError as error:
        return Outcome(error={"code": "configuration_error", "message": str(error)})

    done = results.done(job["_id"])
    processed, failed = len(done), sum(1 for row in results.all(job["_id"]) if row["status"] == "error")
    for index, case in enumerate(cases):
        receipt_id = case["receipt"]["receipt_id"]
        if receipt_id in done:
            continue  # a resumed replay job: this receipt was stored before its worker was lost
        try:
            lease.guard()
        except EvaluationStopped as stop:
            return Outcome(stop_reason=stop.reason)
        if meter is not None:
            meter.receipt_id = receipt_id
        started = time.perf_counter()
        result, error = None, None
        try:
            result = validator.validate_batch([case["receipt"]], context_complete=False).results[0]
            routed = missing_recordings([result]) if mode == "replay" else []
            if routed:
                # A LangGraph node with no recording let a later node answer in its place. Ordinary replay allows that;
                # an evaluation never scores it, so the receipt fails exactly as a replay miss does.
                result, error = None, {"code": "replay_recording_missing", "message": f"{len(routed)} model answers are not in the recordings"}
        except EvaluationStopped as stop:
            return Outcome(stop_reason=stop.reason)  # the receipt is left unstored; its calls are recorded
        except ReplayAnswerMissing as missing:
            # Replay never falls back to a model: the gap is this receipt's error. Missing requests hold receipt text, so only their count is kept.
            error = {"code": "replay_recording_missing", "message": f"{len(missing.missing)} model answers are not in the recordings"}
        except InvalidInputError as invalid:
            error = {"code": "invalid_receipt", "message": str(invalid)}
        except EvidenceError:
            error = {"code": "evidence_unverifiable", "message": "a finding's policy evidence could not be verified"}
        except (OpenRouterError, httpx.HTTPError) as failure:
            error = {"code": "provider_failure", "message": f"a model provider failed: {type(failure).__name__}"}
        except ConfigurationError as problem:
            return Outcome(error={"code": "configuration_error", "message": str(problem)})
        latency_ms = round((time.perf_counter() - started) * 1000)
        record = json.loads(result.model_dump_json()) if result is not None else None
        results.put(_result_document(job, index, case, result, error, latency_ms))
        if record is not None:
            calls = expected_calls(record)
            derived = derived_records(job["_id"], receipt_id, calls, "replayed") if mode == "replay" else derived_records(job["_id"], receipt_id, cached_calls(calls, meter.attempted, receipt_id), "cached")
            for item in derived:
                usage.record(item)
        processed += 1
        failed += error is not None
        try:
            lease.update(_progress(job, processed, failed, meter))
        except EvaluationStopped as stop:
            return Outcome(stop_reason=stop.reason)
    return _dataset_pass(job, cases, replayer, results, lease)


def _inputs(job: dict, settings: Settings, database: Database) -> tuple[list[dict], Path]:
    record = DatasetRepository(database["evaluation_datasets"]).get(job["dataset"]["dataset_id"], include_deleted=True)
    if record is None or record["canonical_sha256"] != job["dataset"]["canonical_sha256"]:
        raise JobFailed("dataset_changed", "the dataset version is missing or differs from the one the job was created with")
    try:
        cases = load_canonical(record)["cases"]
    except ApiError as error:
        raise JobFailed(error.code, error.message) from None
    ruleset = RulesetRepository(database["rulesets"]).get(job["ruleset"]["ruleset_id"])
    if ruleset is None or ruleset["artifact_sha256"] != job["ruleset"]["artifact_sha256"]:
        raise JobFailed("ruleset_changed", "the ruleset version is missing or differs from the one the job was created with")
    try:
        return cases, ArtifactStore(settings.artifact_dir).path(ruleset)
    except ApiError as error:
        raise JobFailed(error.code, error.message) from None


def _result_document(job: dict, index: int, case: dict, result, error: dict | None, latency_ms: int) -> dict:
    text = None if result is None else result.model_dump_json()  # the core's ReceiptResult, exactly
    moment = now()
    return {
        "_id": f"{job['_id']}:{case['receipt']['receipt_id']}",
        "evaluation_id": job["_id"],
        "index": index,
        "receipt_id": case["receipt"]["receipt_id"],
        "status": "error" if result is None else "ok",
        "error": error,
        "context": "isolated_receipts",
        "result_json": text,
        "result_sha256": None if text is None else sha256(text),
        "expected": case["expected"],
        "latency_ms": latency_ms,
        **_derived_fields(None if text is None else json.loads(text), case["expected"]),
        "created_at": moment,
        "updated_at": moment,
    }


def _derived_fields(result: dict | None, expected: dict) -> dict:
    """What the results page filters on, worked out once from the stored result."""
    return {
        "scoring": receipt_scoring(result, expected),
        "filters": {
            "disposition": None if result is None else result["disposition"],
            "categories": [] if result is None else sorted({line["category"] for line in result["line_items"] if line["category"] is not None}),
            "has_error": result is None or any(line.get("classification_error") is not None for line in result["line_items"]),
            "uncertain": result is not None and bool(result["uncertainty"]),
        },
    }


def _progress(job: dict, processed: int, failed: int, meter: Meter | None) -> dict:
    total = job["progress"]["total_receipts"]
    changes = {"progress": {"total_receipts": total, "processed_receipts": processed, "failed_receipts": failed, "percent": round(100 * processed / total, 1)}, "updated_at": now()}
    if meter is not None:
        changes["spend_summary"] = {"new_spend_usd": usd(meter.spent), "cost_completeness": "incomplete" if meter.unverifiable else "complete", "live_calls": meter.calls}
    return changes


def _dataset_pass(job: dict, cases: list[dict], replayer: ExpenseValidator, results: ResultRepository, lease: Lease) -> Outcome:
    stored = {row["receipt_id"]: row for row in results.all(job["_id"])}
    evaluated = [case for case in cases if stored[case["receipt"]["receipt_id"]]["status"] == "ok"]
    if not evaluated:
        return Outcome(note="no receipt could be evaluated")
    complete = job["dataset"]["context_complete"] and len(evaluated) == len(cases)  # a missing receipt leaves totals across receipts open
    try:
        lease.guard()
        batch = replayer.validate_batch([case["receipt"] for case in evaluated], context_complete=complete)
    except EvaluationStopped as stop:
        return Outcome(stop_reason=stop.reason)
    except (ReplayAnswerMissing, InvalidInputError, EvidenceError) as problem:
        # Such as a line whose live call failed: its answer was never cached, so the dataset cannot be replayed.
        return Outcome(note=f"the dataset pass could not run ({type(problem).__name__}), so each receipt's result is as evaluated alone")
    if sorted(result.receipt_id for result in batch.results) != sorted(case["receipt"]["receipt_id"] for case in evaluated):
        return Outcome(error={"code": "integrity_error", "message": "the dataset pass returned other receipts than it was given"})
    for result in batch.results:
        row = stored[result.receipt_id]
        text = result.model_dump_json()
        results.update(row["_id"], {"result_json": text, "result_sha256": sha256(text), "context": "dataset", "updated_at": now(), **_derived_fields(json.loads(text), row["expected"])})
    return Outcome(result_context="dataset", context_complete=complete)


# --- Finishing: metrics and spending from what was stored, then the job's terminal state ---


def scoring_inputs(job: dict, rows: list[dict], database: Database, settings: Settings | None) -> tuple[dict | None, dict | None]:
    """What scoring needs beyond the stored results, from the job's exact dataset and ruleset versions: each line's
    description and language, for the keyword baseline and error analysis, and rule coverage measured on the
    evaluated receipts as admitted with their classified lines. Without settings, coverage is not measured."""
    record = DatasetRepository(database["evaluation_datasets"]).get(job["dataset"]["dataset_id"], include_deleted=True)
    if record is None or record["canonical_sha256"] != job["dataset"]["canonical_sha256"]:
        return None, {"available": False, "reason": "the dataset version is missing or differs from the one the job ran"}
    try:
        cases = {case["receipt"]["receipt_id"]: case["receipt"] for case in load_canonical(record)["cases"]}
    except ApiError as error:  # the stored dataset fails its hash: score without it rather than fail the finished job
        return None, {"available": False, "reason": error.message}
    inputs = {line["line_id"]: {"description": line.get("description_raw"), "language": line.get("language")} for receipt in cases.values() for line in receipt["line_items"]}
    if settings is None:
        return inputs, None
    ruleset = RulesetRepository(database["rulesets"]).get(job["ruleset"]["ruleset_id"])
    if ruleset is None or ruleset["artifact_sha256"] != job["ruleset"]["artifact_sha256"]:
        return inputs, {"available": False, "reason": "the ruleset version is missing or differs from the one the job ran"}
    fields = read_fields(ValidatorSettings.from_config(settings.core_config, mode="deterministic").fields_path.read_text())
    admitted = []
    for row in rows:
        if row["result"] is None:
            continue  # not evaluated: it exercised no rule
        classified = {line["line_id"]: {name: value for name, value in {"category": line["category"], "restricted": line["restricted"], "confidence": line["confidence"], **(line.get("attributes") or {})}.items() if value is not None}
                      for line in row["result"]["line_items"] if line.get("classification_error") is None}
        try:
            admitted.append(admit_receipt(cases[row["receipt_id"]], fields).with_classifications(classified))
        except (KeyError, ValueError) as error:
            return inputs, {"available": False, "reason": f"{row['receipt_id']} could not be admitted again to measure coverage: {type(error).__name__}"}
    return inputs, rule_coverage(admitted, RuleSet.model_validate_json(ruleset["artifact_json"]), fields)


def final_state(job: dict, outcome: Outcome, database: Database, settings: Settings | None = None) -> dict:
    """The fields a finished job sets, from its stored results and usage, whether it completed or not."""
    rows = [{**row, "result": None if row["result_json"] is None else json.loads(row["result_json"])} for row in ResultRepository(database["evaluation_results"]).all(job["_id"])]
    inputs, coverage = scoring_inputs(job, rows, database, settings)
    metrics = compute(
        rows,
        result_context=outcome.result_context,
        context_complete=outcome.context_complete,
        dataset_receipts=job["progress"]["total_receipts"],
        inputs=inputs,
        coverage=coverage,
    )
    spend = spend_report(UsageRepository(database["evaluation_usage"]).all(job["_id"]))
    if outcome.error is not None:
        status = "failed"
    elif outcome.stop_reason in BUDGET_REASONS:
        status = "budget_exhausted"
    elif outcome.stop_reason == "cancelled":
        status = "cancelled"
    else:
        status = "completed"
    total = job["progress"]["total_receipts"]
    processed, failed = len(rows), sum(1 for row in rows if row["status"] == "error")
    moment = now()
    return {
        "status": status,
        "stop_reason": outcome.stop_reason,
        "error": outcome.error,
        "note": outcome.note,
        "result_context": outcome.result_context,
        "context_complete": outcome.context_complete,
        "progress": {"total_receipts": total, "processed_receipts": processed, "failed_receipts": failed, "percent": round(100 * processed / total, 1)},
        "error_summary": metrics["operational"]["execution_error_codes"],
        "metrics": metrics,
        "metrics_schema_version": METRICS_SCHEMA_VERSION,
        "summary": summary(metrics),
        "spend": spend,
        "spend_summary": {"new_spend_usd": spend["new_spend_usd"], "cost_completeness": spend["cost_completeness"], "live_calls": spend["calls"]["live"]},
        "lease": None,
        "completed_at": moment,
        "updated_at": moment,
    }
