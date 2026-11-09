"""Create, read, cancel, compare and export evaluations. The worker runs them; nothing here calls a model."""

import csv
import io
import json
from decimal import Decimal

from bson import ObjectId
from expense_validator import ConfigurationError, ReceiptResult, ValidatorSettings
from expense_validator.config import LANGGRAPH
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from pymongo.database import Database

from expense_api.config import Settings
from expense_api.errors import ApiError
from expense_api.repositories import EvaluationRepository, ResultRepository, UsageRepository
from expense_api.repositories.evaluations import TERMINAL
from expense_api.schemas.evaluations import EvaluationCreate
from expense_api.services.datasets import DatasetService
from expense_api.services.discovery import classification_identity, describe_setup, recorded_names
from expense_api.services.evaluation_metrics import METRICS_SCHEMA_VERSION, compute
from expense_api.services.evaluation_runner import Outcome, core_settings, final_state, folder_sha256, live_cache, models_by_role, plan, scoring_inputs
from expense_api.services.rulesets import ArtifactStore, RulesetService, now, sha256
from expense_api.services.runs import canonical_json, csv_cell, export_bytes, read_legacy_result
from expense_api.services.spending import names_earlier_adapters, spend_report, summarize
from expense_api.services.validation import RULESET_REFERENCE, authorize_classification, check_publication

EXPORT_SCHEMA = "evaluation-export/2"  # 2: each result in the brief's shape (ReceiptResult.to_record); 1 held the core's whole result


class EvaluationService:
    def __init__(self, settings: Settings, database: Database, datasets: DatasetService, rulesets: RulesetService):
        self.settings, self.database, self.datasets, self.rulesets = settings, database, datasets, rulesets
        self.jobs = EvaluationRepository(database["evaluations"])
        self.results = ResultRepository(database["evaluation_results"])
        self.usage = UsageRepository(database["evaluation_usage"])

    # --- Creating ---

    def create(self, request: EvaluationCreate) -> tuple[dict, bool]:
        """Persist a queued job with an immutable snapshot of what it will run. Returns it and whether it is new."""
        # Fields added later are left out at their defaults, so a request made before them keeps its hash.
        dumped = request.model_dump(mode="json", exclude={"idempotency_key"} | ({"confirm_uncapped"} if not request.confirm_uncapped else set()))
        if dumped.get("budget"):
            dumped["budget"] = {name: value for name, value in dumped["budget"].items() if value is not None}
        request_sha256 = sha256(canonical_json(dumped))
        if request.idempotency_key is not None:
            existing = self.jobs.by_idempotency_key(request.idempotency_key)
            if existing is not None:
                return self._same_request(existing, request_sha256), False
        dataset = self.datasets.get(request.dataset_id)
        receipts = dataset["stats"]["receipts"]
        record = self.rulesets.get(request.ruleset_id)
        check_publication(self.settings, record, request.preview)
        replay_set = authorize_classification(self.settings, request.classification, request.execution_mode, receipts)
        self._check_live_request(request, receipts)
        evaluation_id = f"eval_{ObjectId()}"
        planned = self._plan(request, replay_set, evaluation_id, ArtifactStore(self.settings.artifact_dir).path(record))
        if request.execution_mode == "live":
            # Every model a live run may call costs money, whether the request chose it or config.yaml did: each must be
            # allowed for the role it plays.
            allowlists = {"classification": self.settings.classification_allowlist, "translation": self.settings.translation_allowlist}
            for role, models in models_by_role(planned.core).items():
                unlisted = sorted(set(models) - allowlists[role])
                if unlisted:
                    raise ApiError(422, "model_not_allowed", f"a live evaluation may call only allowed {role} models, and would call {unlisted}", {"role": role, "allowed": sorted(allowlists[role])})
        moment = now()
        job = {
            "_id": evaluation_id,
            "label": request.label,
            "idempotency_key": request.idempotency_key or evaluation_id,
            "request_sha256": request_sha256,
            "status": "queued",
            "attempts": 0,
            "dataset": {"dataset_id": dataset["_id"], "name": dataset["name"], "version": dataset["version"], "canonical_sha256": dataset["canonical_sha256"],
                        "original_sha256": dataset["original_sha256"], "receipts": receipts, "context_complete": dataset["context_complete"]},
            "ruleset": {name: record.get(key) for name, key in RULESET_REFERENCE.items()},
            "preview": request.preview,
            "execution_mode": request.execution_mode,
            "classification": request.classification.model_dump(mode="json") | {"replay_set": replay_set},
            "configuration": {
                "classification_backend": planned.core.classification_backend,
                "model_label": model_label(planned.core),
                "models": planned.models,
                "snapshot": planned.snapshot,
                "fingerprint": planned.fingerprint,
                # The translator the run uses, and where it came from: the request, or config.yaml when it named none.
                "translation": {"enabled": planned.core.translation_enabled, "model": planned.core.translation_model if planned.core.translation_enabled else None,
                                "source": "request" if request.classification.translation_model else "config"},
            },
            "replay": None if replay_set is None else {"set": replay_set, "sha256": folder_sha256(self.settings.replay_sets[replay_set])},
            "live": None if request.execution_mode != "live" else {"confirmed": True, "uncapped": request.confirm_uncapped, "answers": f"evaluations/{evaluation_id}", "retries": "none"},
            # Every limit kind, null where none was set; null altogether for an uncapped or replay run.
            "budget": None if request.budget is None or not request.budget.caps() else {"max_spend_usd": None, "max_provider_calls": None, "max_receipts": None} | request.budget.caps(),
            "progress": {"total_receipts": receipts, "processed_receipts": 0, "failed_receipts": 0, "percent": 0.0},
            "lease": None,
            "created_at": moment,
            "updated_at": moment,
            "started_at": None,
            "completed_at": None,
            "cancel_requested_at": None,
            "stop_reason": None,
            "error": None,
            "note": None,
            "error_summary": None,
            "result_context": None,
            "context_complete": None,
            "summary": None,
            "spend_summary": {"new_spend_usd": "0", "cost_completeness": "complete", "live_calls": 0},
            "metrics": None,
            "metrics_schema_version": None,
            "spend": None,
        }
        stored = self.jobs.insert(job)
        if stored["_id"] != evaluation_id:  # a concurrent request with the same key stored first
            return self._same_request(stored, request_sha256), False
        return stored, True

    def _same_request(self, existing: dict, request_sha256: str) -> dict:
        if existing.get("deleted_at"):
            raise ApiError(409, "evaluation_deleted", "this idempotency key belongs to a deleted evaluation; use a new key")
        if existing["request_sha256"] != request_sha256:
            raise ApiError(409, "idempotency_conflict", "this idempotency_key was used for a different request", {"evaluation_id": existing["_id"]})
        return existing

    def _check_live_request(self, request: EvaluationCreate, receipts: int) -> None:
        if request.execution_mode != "live":
            if request.confirm_live or request.confirm_uncapped or request.budget is not None:
                raise ApiError(422, "live_only_field", "confirm_live, confirm_uncapped and budget apply only to live evaluations")
            return
        if not request.confirm_live:
            raise ApiError(422, "live_confirmation_required", "a live evaluation calls paid providers: set confirm_live to true")
        caps = request.budget.caps() if request.budget is not None else {}
        if caps and request.confirm_uncapped:
            raise ApiError(422, "uncapped_with_caps", "confirm_uncapped is for a run with no limit: leave out the budget, or confirm_uncapped")
        if not caps and request.confirm_uncapped and not self.settings.allow_uncapped_evaluations:
            raise ApiError(403, "uncapped_disabled", "this deployment does not allow live evaluations without a limit: set at least one limit")
        if not caps and not request.confirm_uncapped:
            raise ApiError(422, "budget_required", "a live evaluation needs at least one limit (max_spend_usd, max_provider_calls or max_receipts), or confirm_uncapped to run with none")
        if request.budget is not None and request.budget.max_spend_usd is not None and request.budget.max_spend_usd <= 0:
            raise ApiError(422, "budget_zero", "a live evaluation with a zero spending budget can make no call: it is never started")
        if request.budget is not None and request.budget.max_receipts is not None and receipts > request.budget.max_receipts:
            raise ApiError(422, "receipt_limit_exceeded", f"the dataset holds {receipts} receipts, more than the budget's max_receipts of {request.budget.max_receipts}")

    def _plan(self, request: EvaluationCreate, replay_set: str | None, evaluation_id: str, ruleset_path):
        mode = request.execution_mode
        try:
            cache_dir = self.settings.replay_sets[replay_set] if mode == "replay" else live_cache(self.settings, evaluation_id)
            return plan(core_settings(self.settings, mode, cache_dir, request.classification, ruleset_path))
        except ConfigurationError as error:
            if mode == "live" and ("OPENROUTER_API_KEY" in str(error) or "LIVE_CACHE_DIR" in str(error)):
                raise ApiError(503, "live_inference_unavailable", str(error)) from None
            raise ApiError(422, "unsupported_configuration", str(error)) from None

    # --- Reading ---

    def get(self, evaluation_id: str) -> dict:
        job = self.jobs.get(evaluation_id)
        if job is None:
            raise ApiError(404, "evaluation_not_found", f"no evaluation {evaluation_id}")
        # Still running: worked out from the calls stored so far. A report stored before the adapters were renamed is
        # worked out again from the same calls, so by_backend groups each adapter once; its totals cannot change.
        if job["spend"] is None or names_earlier_adapters(job["spend"]):
            job = {**job, "spend": spend_report(self.usage.all(evaluation_id))}
        return job

    def page(self, filters: dict, limit: int, offset: int) -> tuple[list[dict], int]:
        if filters.get("created_from") and filters.get("created_to") and filters["created_from"] > filters["created_to"]:
            raise ApiError(422, "invalid_filter", "created_from is after created_to")
        query = {}
        for name, field in (("dataset_id", "dataset.dataset_id"), ("ruleset_id", "ruleset.ruleset_id"), ("backend", "configuration.classification_backend"),
                            ("model", "configuration.models"), ("status", "status"), ("execution_mode", "execution_mode")):
            if filters.get(name) is not None:
                query[field] = filters[name]
        if filters.get("backend") is not None:  # runs recorded under an adapter's earlier name are the same adapter's
            query["configuration.classification_backend"] = {"$in": recorded_names(filters["backend"])}
        created = {operator: filters[name] for name, operator in (("created_from", "$gte"), ("created_to", "$lt")) if filters.get(name) is not None}
        if created:
            query["created_at"] = created
        return self.jobs.page(query, limit, offset)

    def metrics(self, evaluation_id: str, scoring: str = "latest") -> dict:
        """A finished run's stored metrics: its latest rescoring by default, or with scoring original the metrics it was
        scored with when it finished. Never computed again here. While it runs, provisional metrics, never stored."""
        job = self.get(evaluation_id)
        rescorings = job.get("rescorings") or []
        if job["metrics"] is not None:
            original = job["metrics"].get("schema_version") or job.get("metrics_schema_version")
            scoring_info = {"original_schema_version": original, "current_schema_version": METRICS_SCHEMA_VERSION, "rescorings": [{"schema_version": item["schema_version"], "rescored_at": item["rescored_at"]} for item in rescorings]}
            if scoring == "latest" and rescorings:
                latest = rescorings[-1]
                return {"evaluation_id": evaluation_id, "status": job["status"], "provisional": False, "scored": {"kind": "rescored", "rescored_at": latest["rescored_at"], **scoring_info}, **latest["metrics"]}
            return {"evaluation_id": evaluation_id, "status": job["status"], "provisional": False, "scored": {"kind": "original", **scoring_info}, **job["metrics"]}
        # Not finished: the receipts stored so far, each as evaluated alone. Never stored, so never mistaken for the result.
        rows = [self._row(row) for row in self.results.all(evaluation_id)]
        inputs, _ = scoring_inputs(job, rows, self.database, None)
        metrics = compute(rows, result_context="isolated_receipts", context_complete=None, dataset_receipts=job["progress"]["total_receipts"], inputs=inputs)
        return {"evaluation_id": evaluation_id, "status": job["status"], "provisional": True, "scored": {"kind": "provisional"}, **metrics}

    def rescore(self, evaluation_id: str) -> dict:
        """Score a finished run again with the current scoring, from its stored results and its exact dataset and
        ruleset versions, and keep it beside the metrics it finished with, which never change. Rescoring with the
        version already used last returns that rescoring: the same inputs give the same numbers."""
        job = self.get(evaluation_id)
        if job["status"] not in TERMINAL or job["metrics"] is None:
            raise ApiError(409, "evaluation_not_finished", "only a finished evaluation is rescored: its metrics are provisional while it runs")
        rescorings = job.get("rescorings") or []
        if (rescorings[-1]["schema_version"] if rescorings else job["metrics"].get("schema_version")) == METRICS_SCHEMA_VERSION:
            return self.metrics(evaluation_id)
        rows = [self._row(row) for row in self.results.all(evaluation_id)]
        inputs, coverage = scoring_inputs(job, rows, self.database, self.settings)
        metrics = compute(rows, result_context=job["result_context"], context_complete=job["context_complete"], dataset_receipts=job["progress"]["total_receipts"], inputs=inputs, coverage=coverage)
        entry = {"schema_version": METRICS_SCHEMA_VERSION, "rescored_at": now(), "metrics": metrics}
        if self.jobs.transition(evaluation_id, {"status": job["status"], "rescorings": {"$size": len(rescorings)}} if rescorings else {"status": job["status"], "rescorings": {"$exists": False}}, {"rescorings": [*rescorings, entry]}) is None:
            raise ApiError(409, "evaluation_changed", "the evaluation changed while it was rescored: try again")
        return self.metrics(evaluation_id)

    def results_page(self, evaluation_id: str, filters: dict, limit: int, offset: int) -> tuple[list[dict], int]:
        self.get(evaluation_id)
        query = {}
        if filters.get("receipt_id"):
            query["receipt_id"] = {"$in": filters["receipt_id"]}
        for name, field in (("classification_correct", "scoring.classification_correct"), ("error", "filters.has_error"), ("uncertain", "filters.uncertain")):
            if filters.get(name) is not None:
                query[field] = filters[name]
        if filters.get("category"):
            query["filters.categories"] = {"$in": filters["category"]}
        if filters.get("disposition"):
            query["filters.disposition"] = {"$in": filters["disposition"]}
        rows, total = self.results.page(evaluation_id, query, limit, offset)
        return self._items(evaluation_id, rows), total

    def _items(self, evaluation_id: str, rows: list[dict]) -> list[dict]:
        usage = self.usage.for_receipts(evaluation_id, [row["receipt_id"] for row in rows])
        return [
            {
                "receipt_id": row["receipt_id"],
                "index": row["index"],
                "status": row["status"],
                "error": row["error"],
                "context": row["context"],
                "latency_ms": row["latency_ms"],
                "expected": row["expected"],
                "scoring": row["scoring"],
                "result": self._row(row)["result"],  # the core's ReceiptResult, unchanged, with its evidence
                "spend": summarize([record for record in usage if record["receipt_id"] == row["receipt_id"]]),
            }
            for row in rows
        ]

    def _row(self, row: dict) -> dict:
        text = row["result_json"]
        if text is not None and sha256(text) != row["result_sha256"]:
            raise ApiError(500, "corrupt_result", f"the stored result of {row['receipt_id']} does not match its hash")
        return {key: value for key, value in {**row, "result": None if text is None else read_legacy_result(json.loads(text))}.items()
                if key != "customer_id"}

    # --- Cancelling ---

    def cancel(self, evaluation_id: str) -> dict:
        """Cancel a queued job at once; ask a running one to stop before its next receipt or provider call."""
        for _ in range(3):  # the job may move between the read and the conditional write
            job = self.get(evaluation_id)
            if job["status"] in TERMINAL:
                raise ApiError(409, "not_cancellable", f"evaluation {evaluation_id} is already {job['status']}")
            if job["status"] == "cancel_requested":
                return job
            if job["status"] == "queued":
                changed = self.jobs.transition(evaluation_id, {"status": "queued"}, final_state(job, Outcome(stop_reason="cancelled"), self.database, self.settings))
            else:
                moment = now()
                changed = self.jobs.transition(evaluation_id, {"status": "running"}, {"status": "cancel_requested", "cancel_requested_at": moment, "updated_at": moment})
            if changed is not None:
                return self.get(evaluation_id)
        raise ApiError(409, "concurrent_change", "the evaluation kept changing; try again")

    # --- Comparing ---

    def compare(self, evaluation_ids: list[str]) -> dict:
        if len(set(evaluation_ids)) != len(evaluation_ids):
            raise ApiError(422, "invalid_request", "each evaluation may be named once")
        found = {job["_id"]: job for job in self.jobs.many(evaluation_ids)}
        missing = [evaluation_id for evaluation_id in evaluation_ids if evaluation_id not in found]
        if missing:
            raise ApiError(404, "evaluation_not_found", f"no evaluations {missing}")
        unfinished = {evaluation_id: found[evaluation_id]["status"] for evaluation_id in evaluation_ids if found[evaluation_id]["status"] != "completed"}
        if unfinished:
            raise ApiError(409, "evaluation_not_completed", "only completed evaluations are compared", unfinished)
        jobs = [found[evaluation_id] for evaluation_id in evaluation_ids]
        return {"comparability": comparability(jobs), "evaluations": [comparison_row(job) for job in jobs]}

    # --- Exporting ---

    def export(self, evaluation_id: str, format: str, scoring: str = "latest") -> bytes:
        job = self.get(evaluation_id)
        if job["status"] not in TERMINAL:
            raise ApiError(409, "evaluation_not_finished", f"evaluation {evaluation_id} is {job['status']}: export it once it has finished")
        rows = self.results.all(evaluation_id)
        items = self._items(evaluation_id, rows)
        if format == "pdf":
            from expense_api.services.pdf_reports import evaluation_pdf
            return evaluation_pdf(jsonable_encoder({"evaluation": detail(job), "metrics": self.metrics(evaluation_id, scoring), "results": items}))
        if format == "json":
            usage = [{name: value for name, value in record.items() if name != "created_at"} for record in self.usage.all(evaluation_id)]
            results = [{**item, "result": None if item["result"] is None else brief_record(item)} for item in items]
            body = {"export_schema": EXPORT_SCHEMA, "evaluation": detail(job), "metrics": job["metrics"], "results": results, "usage": usage}
            return export_bytes(jsonable_encoder(body))
        return export_csv(job, items)


def brief_record(item: dict) -> dict:
    """A stored result in the brief's shape, the same for every model; what is particular to one is in its metadata."""
    try:
        return ReceiptResult.model_validate(item["result"]).to_record()
    except ValidationError:
        raise ApiError(500, "corrupt_result", f"the stored result of {item['receipt_id']} cannot be read") from None


def model_label(core) -> str | None:
    if core.classification_backend != LANGGRAPH:
        return core.classification_model
    return ", ".join(f"{child.name}={child.backend}:{child.model}" for child in core.langgraph.backends)


def setup_of(job: dict) -> dict:
    """The run's orchestrator, provider, adapter and model, from what it recorded."""
    configuration = job["configuration"]
    snapshot = configuration.get("snapshot") or {}
    return describe_setup(configuration.get("classification_backend"), snapshot.get("classification_model"), snapshot.get("langgraph"))


def summary_of(job: dict) -> dict:
    return {
        "evaluation_id": job["_id"],
        "label": job.get("label"),
        "status": job["status"],
        "dataset": job["dataset"],
        "ruleset": job["ruleset"],
        "preview": job["preview"],
        "execution_mode": job["execution_mode"],
        "configuration": {name: job["configuration"].get(name) for name in ("classification_backend", "model_label", "models", "snapshot", "fingerprint", "translation")}
        | {"setup": setup_of(job)},
        "progress": job["progress"],
        "created_at": job["created_at"],
        "started_at": job["started_at"],
        "completed_at": job["completed_at"],
        "summary": job["summary"],
        "spend_summary": job["spend_summary"],
        "stop_reason": job["stop_reason"],
        "error": job["error"],
    }


def detail(job: dict) -> dict:
    return {
        **summary_of(job),
        **{name: job.get(name) for name in ("classification", "replay", "live", "budget", "attempts", "updated_at", "cancel_requested_at", "lease", "result_context",
                                            "context_complete", "note", "error_summary", "metrics_schema_version", "spend")},
    }


# --- Comparison ---


UNKNOWN = object()  # a run whose translator was never recorded: never taken to match another's


def effective_translation(job: dict):
    """The translator a run used: its recorded configuration.translation, else its snapshot, which is the effective
    configuration the worker ran with (from_snapshot). None when translation was off; UNKNOWN when neither says."""
    recorded = job["configuration"].get("translation")
    if recorded is not None:
        return recorded["model"] if recorded["enabled"] else None
    snapshot = job["configuration"].get("snapshot") or {}
    if "translation_enabled" not in snapshot or "translation_model" not in snapshot:
        return UNKNOWN
    return snapshot["translation_model"] if snapshot["translation_enabled"] else None


def comparability(jobs: list[dict]) -> dict:
    """Whether the runs differ only in how they classify. Anything else makes them not directly comparable.

    The translator counts as configuration, not as how a run classifies: it changes the text a classifier reads.
    """
    issues, codes = [], []

    def issue(code: str, text: str) -> None:
        codes.append(code)
        issues.append(text)

    for code, label, key in (("dataset", "datasets", lambda job: job["dataset"]["canonical_sha256"]), ("ruleset", "rulesets", lambda job: job["ruleset"]["artifact_sha256"]),
                             ("catalog", "taxonomies or field catalogs", lambda job: (job["configuration"]["fingerprint"]["taxonomy_sha256"], job["configuration"]["fingerprint"]["fields_sha256"])),
                             ("core_version", "core versions", lambda job: job["configuration"]["fingerprint"]["core_version"])):
        if len({key(job) for job in jobs}) > 1:
            issue(code, f"different {label}")
    translators = [effective_translation(job) for job in jobs]
    if any(model is UNKNOWN for model in translators):
        issue("translation_model_unknown", "some runs did not record their translation model, so it cannot be shown to match")
    elif len(set(translators)) > 1:
        issue("translation_model", f"different translation models: {', '.join(sorted(model or 'off' for model in set(translators)))}")
    if any(job["result_context"] != "dataset" for job in jobs):
        issue("result_context", "some runs hold receipts evaluated alone, not as one dataset")
    if any(job["progress"]["failed_receipts"] for job in jobs):
        issue("failed_receipts", "some runs could not evaluate every receipt")
    if len({job["execution_mode"] for job in jobs}) > 1:
        issue("execution_mode", "replay and live runs are mixed: a replay repeats recorded answers, a live run asks the models again")
    return {
        "directly_comparable": not issues,
        "issues": issues,
        "issue_codes": codes,
        # Even with nothing above, this is a side-by-side view of runs, not a controlled benchmark: one dataset,
        # no repeated trials and no significance test. The Wilson intervals in each run's metrics show how wide
        # its uncertainty is.
        "note": "a side-by-side view of runs, not a controlled head-to-head benchmark",
    }


def comparison_row(job: dict) -> dict:
    metrics, spend = job["metrics"], job["spend"]
    classification, disposition, operational = metrics["classification"], metrics["disposition"], metrics["operational"]
    receipts = operational["evaluated"]
    correct = classification["outcomes"]["correct"] if classification["available"] else None
    new_spend = spend["new_spend_usd"]
    complete = spend["cost_completeness"] == "complete"
    live = job["execution_mode"] == "live"

    def per(count):
        if not live or not complete or not count:
            return None  # replay adds no spend, and an incomplete total would understate the cost
        return format(Decimal(new_spend) / count, "f")

    return {
        "evaluation_id": job["_id"],
        "label": job.get("label"),
        "dataset": {key: job["dataset"][key] for key in ("dataset_id", "name", "version", "canonical_sha256")},
        "ruleset": {key: job["ruleset"][key] for key in ("ruleset_id", "name", "version", "artifact_sha256")},
        "execution_mode": job["execution_mode"],
        "backend": job["configuration"]["classification_backend"],  # as recorded
        "setup": setup_of(job),
        # The same identity whichever adapter names a run recorded: what to match runs on, never the recorded names.
        "classification_identity": classification_identity(setup_of(job)),
        "model_label": job["configuration"]["model_label"],
        "models": job["configuration"]["models"],
        # The effective translator; translation_known is false for a run that never recorded one.
        "translation_model": None if effective_translation(job) is UNKNOWN else effective_translation(job),
        "translation_known": effective_translation(job) is not UNKNOWN,
        "langgraph": job["configuration"]["snapshot"]["langgraph"] if job["configuration"]["classification_backend"] == LANGGRAPH else None,
        "result_context": job["result_context"],
        "category_accuracy": classification.get("accuracy"),
        "macro_f1": classification.get("macro", {}).get("f1") if classification["available"] else None,
        "disposition_accuracy": disposition.get("accuracy"),
        "uncertainty_rate": operational["uncertainty"]["receipts"],
        "total_latency_ms": operational["latency"]["total_ms"],
        "mean_latency_per_receipt_ms": operational["latency"]["mean_per_receipt_ms"],
        "provider_calls": spend["calls"],
        "new_spend_usd": new_spend,
        "cost_completeness": spend["cost_completeness"],
        "historical_cost_usd": spend["historical"],
        "cost_per_receipt_usd": per(receipts),
        "cost_per_correct_prediction_usd": per(correct),
    }


# --- CSV ---

CSV_COLUMNS = (
    "evaluation_id", "dataset_id", "dataset_sha256", "ruleset_id", "ruleset_sha256", "execution_mode", "backend", "model_label", "result_context",
    "receipt_index", "receipt_id", "receipt_status", "receipt_error", "expected_disposition", "disposition", "disposition_correct", "decided", "uncertain",
    "line_id", "description_raw", "expected_category", "category", "line_outcome", "confidence", "classification_error",
    "receipt_new_spend_usd", "receipt_cost_completeness", "receipt_latency_ms",
)


def export_csv(job: dict, items: list[dict]) -> bytes:
    """One row per line, in dataset and line order, and one for a receipt that could not be evaluated."""
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for item in items:
        result, scoring = item["result"], item["scoring"]
        base = {
            "evaluation_id": job["_id"], "dataset_id": job["dataset"]["dataset_id"], "dataset_sha256": job["dataset"]["canonical_sha256"],
            "ruleset_id": job["ruleset"]["ruleset_id"], "ruleset_sha256": job["ruleset"]["artifact_sha256"], "execution_mode": job["execution_mode"],
            "backend": job["configuration"]["classification_backend"], "model_label": job["configuration"]["model_label"], "result_context": job["result_context"],
            "receipt_index": item["index"], "receipt_id": item["receipt_id"], "receipt_status": item["status"], "receipt_error": None if item["error"] is None else item["error"]["code"],
            "expected_disposition": scoring["expected_disposition"], "disposition": None if result is None else result["disposition"], "disposition_correct": scoring["disposition_correct"],
            "decided": None if result is None else result["decided"], "uncertain": None if result is None else bool(result["uncertainty"]),
            "receipt_new_spend_usd": item["spend"]["new_spend_usd"], "receipt_cost_completeness": item["spend"]["cost_completeness"], "receipt_latency_ms": item["latency_ms"],
        }
        lines = [] if result is None else result["line_items"]
        for line in lines or [None]:
            row = dict(base)
            if line is not None:
                scored = scoring["lines"].get(line["line_id"], {})
                row |= {"line_id": line["line_id"], "description_raw": line["description_raw"], "expected_category": scored.get("expected"), "category": line["category"],
                        "line_outcome": scored.get("outcome"), "confidence": line["confidence"], "classification_error": line.get("classification_error")}
            writer.writerow([csv_cell(row.get(column)) for column in CSV_COLUMNS])
    return output.getvalue().encode("utf-8")
