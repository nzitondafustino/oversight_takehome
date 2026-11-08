"""Read persisted runs: history, findings and exports, all from the stored core result. Nothing is re-validated.

The core's ReceiptResult holds four kinds of record, each with its own contract. A finding here is one of them,
kept whole in `record`, with the few fields the filters read copied beside it only where the core has them:

| kind          | core record          | rule_id | severity | line_ids                 |
|---------------|----------------------|---------|----------|--------------------------|
| exception     | ExceptionResult      | yes     | yes      | yes                      |
| review_signal | ReviewSignalResult   | yes     | none     | none                     |
| open_question | OpenQuestionResult   | yes     | none     | yes (not a violation)    |
| uncertainty   | UncertaintyResult    | none    | none     | its one line_id          |
"""

import csv
import io
import json

from expense_validator import BatchResult
from pydantic import ValidationError

from expense_api.services.discovery import recorded_names
from expense_api.errors import ApiError
from expense_api.repositories import RunRepository
from expense_api.services.rulesets import sha256

KINDS = ("exception", "review_signal", "open_question", "uncertainty")
_FIELDS = {"exception": "exceptions", "review_signal": "review_signals", "open_question": "open_questions", "uncertainty": "uncertainty"}


def load_results(run: dict) -> BatchResult:
    """The run's stored core result, refused when it no longer matches its hash or cannot be read."""
    text = run.get("results_json")
    if not isinstance(text, str):
        raise ApiError(500, "corrupt_run", f"run {run['_id']} has no stored result")
    if run.get("results_sha256") is not None and sha256(text) != run["results_sha256"]:
        raise ApiError(500, "corrupt_run", f"run {run['_id']} does not match its stored hash")
    try:
        data = json.loads(text)
        data.pop("customer_id", None)
        data["results"] = [read_legacy_result(result) for result in data["results"]]
        return BatchResult.model_validate(data)
    except (ValidationError, ValueError, TypeError, KeyError, AttributeError):
        raise ApiError(500, "corrupt_run", f"the stored result of run {run['_id']} cannot be read") from None


def read_legacy_result(result: dict) -> dict:
    """Project old result metadata into the current contract after verifying stored bytes.

    Historical JSON and its hash remain untouched; policy outcomes are never recomputed.
    """
    result = dict(result)
    if "provenance" in result:
        result["provenance"] = {key: value for key, value in result["provenance"].items() if key != "customer_id"}
    return result


def summarize(batch: BatchResult) -> dict:
    """What a history page shows of a run, worked out once when it is stored."""
    backends = {result.provenance.classification_backend for result in batch.results}
    models = {result.metadata.model for result in batch.results}
    counts = {kind: sum(len(getattr(result, _FIELDS[kind])) for result in batch.results) for kind in KINDS}
    return {"classification_backend": backends.pop() if len(backends) == 1 else None,
            # The core's metadata.model: the model, or LangGraph's order. Absent from a run stored before it was kept.
            "classification_model": models.pop() if len(models) == 1 else None,
            "counts": counts}


def findings(batch: BatchResult) -> list[dict]:
    """Every finding, in a fixed order: receipts as stored (receipt ID order), then each kind in KINDS order, then
    the core's own order. A receipt's findings are never merged: each rule's record stays apart."""
    items = []
    for result in batch.results:
        for kind in KINDS:
            for index, record in enumerate(getattr(result, _FIELDS[kind])):
                data = record.model_dump(mode="json")
                rule_id = data.get("rule_id")
                items.append({
                    "finding_id": f"{result.receipt_id}:{kind}:{index}",
                    "kind": kind,
                    "receipt_id": result.receipt_id,
                    "receipt_disposition": result.disposition,
                    "receipt_decided": result.decided,
                    "rule_id": rule_id,
                    "severity": data.get("severity"),
                    "line_ids": list(data["line_ids"]) if "line_ids" in data else ([data["line_id"]] if "line_id" in data else None),
                    # The core's link from rules to the disposition: the reasons that name this rule.
                    "disposition_reasons": [reason.model_dump(mode="json") for reason in result.disposition_reasons if rule_id is not None and rule_id in reason.rule_ids],
                    "record": data,  # the core's record, whole: evidence, source quote, explanation or reasons
                })
    return items


def matches(item: dict, kinds, severities, rule_ids, receipt_ids, dispositions) -> bool:
    return all(
        wanted is None or value in wanted
        for wanted, value in ((kinds, item["kind"]), (severities, item["severity"]), (rule_ids, item["rule_id"]), (receipt_ids, item["receipt_id"]), (dispositions, item["receipt_disposition"]))
    )


class RunService:
    def __init__(self, repository: RunRepository):
        self.repository = repository

    def get(self, run_id: str) -> dict:
        run = self.repository.get(run_id)
        if run is None:
            raise ApiError(404, "run_not_found", f"no run {run_id}")
        return run

    def page(self, filters: dict, limit: int, offset: int) -> tuple[list[dict], int]:
        if filters.get("created_from") and filters.get("created_to") and filters["created_from"] > filters["created_to"]:
            raise ApiError(422, "invalid_filter", "created_from is after created_to")
        if filters.get("classification_backend") is not None:  # runs recorded under an adapter's earlier name are the same adapter's
            filters = {**filters, "classification_backend": {"$in": recorded_names(filters["classification_backend"])}}
        return self.repository.page(filters, limit, offset)

    def findings(self, run_id: str) -> tuple[dict, list[dict]]:
        run = self.get(run_id)
        return run, findings(load_results(run))


# --- Exports: deterministic bytes from the stored result ---

CSV_COLUMNS = (
    "run_id", "ruleset_id", "ruleset_name", "ruleset_version", "ruleset_label", "ruleset_sha256", "classification_backend", "execution_mode", "preview",
    "receipt_id", "receipt_disposition", "receipt_decided", "finding_id", "kind", "rule_id", "severity", "policy_doc", "line_ids",
    "explanation", "evidence", "source_quote", "reason", "reasons", "field", "state", "confidence", "disposition_reasons", "record",
)
# A spreadsheet runs a cell starting with one of these as a formula (OWASP CSV injection).
FORMULA_START = ("=", "+", "-", "@", "\t", "\r", "\n")


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def export_json(response: dict) -> bytes:
    """The whole run, as GET /v1/runs/{run_id} returns it, in canonical JSON."""
    return (canonical_json(response) + "\n").encode("utf-8")


def export_csv(run: dict, batch: BatchResult) -> bytes:
    """One row per finding. Nested values are canonical JSON in their cell; every text cell is formula-safe."""
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n")  # RFC 4180, with quoting where a value needs it
    writer.writerow(CSV_COLUMNS)
    ruleset = run["ruleset"]
    for item in findings(batch):
        record = item["record"]
        backend = next(result.provenance.classification_backend for result in batch.results if result.receipt_id == item["receipt_id"])
        row = {
            "run_id": run["_id"], "ruleset_id": ruleset["ruleset_id"], "ruleset_name": ruleset["name"], "ruleset_version": ruleset["version"],
            "ruleset_label": ruleset["ruleset_version"], "ruleset_sha256": ruleset["artifact_sha256"], "classification_backend": backend, "execution_mode": run["execution_mode"],
            "preview": run.get("preview", False), "receipt_id": item["receipt_id"], "receipt_disposition": item["receipt_disposition"], "receipt_decided": item["receipt_decided"],
            "finding_id": item["finding_id"], "kind": item["kind"], "rule_id": item["rule_id"], "severity": item["severity"], "policy_doc": record.get("policy_doc"),
            "line_ids": None if item["line_ids"] is None else canonical_json(item["line_ids"]), "explanation": record.get("explanation"), "evidence": record.get("evidence"),
            "source_quote": record.get("source_quote"), "reason": record.get("reason"), "reasons": canonical_json(record["reasons"]) if "reasons" in record else None,
            "field": record.get("field"), "state": record.get("state"), "confidence": record.get("confidence"),
            "disposition_reasons": canonical_json(item["disposition_reasons"]), "record": canonical_json(record),
        }
        writer.writerow([csv_cell(row[column]) for column in CSV_COLUMNS])
    return output.getvalue().encode("utf-8")


def csv_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value) if isinstance(value, float) else str(value)
    text = str(value)
    return "'" + text if text.startswith(FORMULA_START) else text
