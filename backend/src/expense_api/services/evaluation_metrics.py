"""Compute evaluation metrics from stored results and dataset answers.

Scoring uses expense_validator.evaluation. Outcomes distinguish correct, incorrect,
missing, classification_error and execution_error; only correct counts as correct.
Unlabelled fields are not scored, and unavailable metrics include a reason.

Stored metrics retain their schema version. Requested rescoring is stored beside
the original scores. See backend/EVALUATIONS.md for schema history.
"""

from collections import Counter, defaultdict
from decimal import Decimal

from expense_validator.evaluation import keyword_baseline
from expense_validator.evaluation.detection import detection_scores
from expense_validator.evaluation.error_analysis import diagnose
from expense_validator.evaluation.metrics import DISPOSITIONS, UNCERTAINTY_RULE, classification_scores, disposition_scores, prf, rate

METRICS_SCHEMA_VERSION = "evaluation-metrics/3"
NOT_EVALUATED = "not_evaluated"  # the disposition matrix column for a receipt that could not be evaluated
DEFINITIONS = {
    "classification": "per labelled line; macro is the mean over the categories in the answers; a category never predicted has precision 0; a line with no category or a failure is wrong",
    "restricted": "positive class restricted = true, per labelled line; a line with no restricted value counts as not restricted",
    "disposition": "the engine's three dispositions, as decided; a pass or flag the engine did not fully decide (decided false) stays in its cell and is counted apart as undecided; a receipt not evaluated is its own column and never correct",
    "findings": "expected exceptions and review signals matched on receipt and rule ID; a disposition never counts as a finding; EXC-03 signals are never expected; a receipt not evaluated misses its findings",
    "evidence": "for matched exceptions whose answer lists lines: the cited line IDs equal the expected ones as sets",
    "baseline": f"{keyword_baseline.KEYWORD_BASELINE_VERSION}: whole-word terms over the raw description only, scored with the same functions and denominators",
}
NO_CATEGORY = "(none)"  # the confusion matrix column for a line with no predicted category, for whatever reason


NOTHING_RUN = "no receipt has been run yet"


def unavailable(reason: str) -> dict:
    return {"available": False, "reason": reason}


def line_outcome(line: dict | None, expected: str) -> str:
    if line is None:
        return "execution_error"
    if line.get("classification_error") is not None:
        return "classification_error"
    if line["category"] is None:
        return "missing"
    return "correct" if line["category"] == expected else "incorrect"


def receipt_scoring(result: dict | None, expected: dict) -> dict:
    """One receipt against its answers: what the results page shows and filters on."""
    lines = {} if result is None else {line["line_id"]: line for line in result["line_items"]}
    scored = {}
    for line_id, label in sorted(expected["lines"].items()):
        if label["category"] is None:
            continue
        line = lines.get(line_id)
        scored[line_id] = {"expected": label["category"], "predicted": None if line is None else line["category"], "outcome": line_outcome(line if result is not None else None, label["category"])}
    disposition = expected["disposition"]
    return {
        "lines": scored,
        "classification_correct": None if not scored else all(item["outcome"] == "correct" for item in scored.values()),
        "expected_disposition": disposition,
        "disposition_correct": None if disposition is None else (result is not None and result["disposition"] == disposition),
    }


def compute(rows: list[dict], *, result_context: str, context_complete: bool | None, dataset_receipts: int, inputs: dict | None = None, coverage: dict | None = None) -> dict:
    """Compute metrics for the receipts attempted by an evaluation.

    Unattempted receipts count as not_run, rather than errors or incorrect results.

    Args:
        rows: Attempted receipts in dataset order, with expected and result fields.
        result_context: Whether results reflect a dataset batch or isolated receipts.
        context_complete: Whether the receipt context is complete, or None if unknown.
        dataset_receipts: Total receipts in the dataset, including unattempted ones.
        inputs: Line descriptions and languages keyed by line ID. None omits the
            keyword baseline and language breakdowns.
        coverage: Core rule coverage, or the reason it could not be measured.

    Returns:
        Versioned quality and operational metrics, with unavailability reasons.
    """
    evaluated = [row for row in rows if row["result"] is not None]
    if not rows:  # nothing has run, as in a job's first moments: say that, not that the answers lack labels
        coverage = coverage or {"available": False, "reason": NOTHING_RUN}
    findings = _findings(rows, coverage)
    evidence = {"available": True, **findings.pop("evidence")} if findings.get("available") else unavailable(findings["reason"])  # one block, never two names
    return {
        "schema_version": METRICS_SCHEMA_VERSION,
        "scoring": {"baseline_version": keyword_baseline.KEYWORD_BASELINE_VERSION, "definitions": DEFINITIONS},
        # dataset: every receipt evaluated together, as one validate_batch call would. isolated_receipts: each
        # alone, as the run did not finish its dataset pass, so rules spanning receipts were left undecided.
        "result_context": result_context,
        "context_complete": context_complete,
        "classification": _classification(rows),
        "disposition": _disposition(rows),
        "findings": findings,
        "evidence": evidence,
        "baseline": _baseline(rows, inputs),
        "error_analysis": _error_analysis(rows, inputs, findings),
        "coverage": _coverage(rows, coverage, findings),
        "operational": _operational(rows, evaluated, dataset_receipts),
    } | ({name: unavailable(NOTHING_RUN) for name in ("classification", "disposition", "findings", "evidence", "baseline")} if not rows else {})


def summary(metrics: dict) -> dict:
    """The few numbers a history page shows."""
    value = lambda block, *path: _dig(metrics.get(block, {}), *path)
    return {
        "category_accuracy": value("classification", "accuracy", "value"),
        "macro_f1": value("classification", "macro", "f1"),
        "disposition_accuracy": value("disposition", "accuracy", "value"),
        "restricted_f1": value("classification", "restricted", "f1"),
        "finding_f1": value("findings", "micro", "f1"),
        "evidence_exact": value("evidence", "exact", "value"),
        "uncertainty_rate": value("operational", "uncertainty", "receipts", "value"),
        "coverage": value("operational", "coverage", "value"),
        "execution_error_rate": value("operational", "execution_errors", "value"),
    }


def _dig(data: dict, *path):
    for key in path:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _classification(rows: list[dict]) -> dict:
    scored, outcomes, matrix = [], Counter(), defaultdict(Counter)
    for row in rows:
        lines = {} if row["result"] is None else {line["line_id"]: line for line in row["result"]["line_items"]}
        for line_id, label in sorted(row["expected"]["lines"].items()):
            if label["category"] is None:
                continue
            line = lines.get(line_id) if row["result"] is not None else None
            outcome = line_outcome(line, label["category"])
            outcomes[outcome] += 1
            category = None if line is None or outcome == "classification_error" else line["category"]
            matrix[label["category"]][category or NO_CATEGORY] += 1
            scored.append({
                "gold_category": label["category"],
                "gold_restricted": label["restricted"],
                "category": category,
                "restricted": None if line is None else line["restricted"],
                "confidence": None if line is None or line["confidence"] is None else Decimal(str(line["confidence"])),
            })
    if not scored:
        return unavailable("no line has an expected category")
    core = classification_scores(scored)  # a line with no category is wrong, never left out
    columns = sorted({column for counts in matrix.values() for column in counts} - {NO_CATEGORY}) + ([NO_CATEGORY] if any(NO_CATEGORY in counts for counts in matrix.values()) else [])
    return {
        "available": True,
        "lines": len(scored),
        "outcomes": {name: outcomes[name] for name in ("correct", "incorrect", "missing", "classification_error", "execution_error")},
        "accuracy": core["accuracy"],  # correct / every labelled line, whatever its outcome
        "accuracy_when_predicted": rate(outcomes["correct"], outcomes["correct"] + outcomes["incorrect"]),
        "coverage": rate(outcomes["correct"] + outcomes["incorrect"], len(scored)),
        "macro": core["macro"],  # the mean over the expected categories, as the core defines it
        "per_category": {category: {**scores, "support": scores["tp"] + scores["fn"]} for category, scores in core["per_category"].items()},
        "confusion_matrix": {"rows": sorted(matrix), "columns": columns, "counts": {gold: {column: matrix[gold][column] for column in columns} for gold in sorted(matrix)}},
        "restricted": {**core["restricted"], "lines": sum(1 for line in scored if line["gold_restricted"] is not None)},
        "restricted_matrix": core["restricted_matrix"],
        "extra_predicted": core["extra_predicted"],
    }


def _disposition(rows: list[dict]) -> dict:
    labelled = [row for row in rows if row["expected"]["disposition"] is not None]
    if not labelled:
        return unavailable("no receipt has an expected disposition")
    outcomes = [
        {"disposition": row["result"]["disposition"], "gold_disposition": row["expected"]["disposition"], "decided": row["result"]["decided"],
         "reason_codes": sorted({reason["code"] for reason in row["result"]["disposition_reasons"]})}
        for row in labelled if row["result"] is not None
    ]
    failed = Counter(row["expected"]["disposition"] for row in labelled if row["result"] is None)
    core = disposition_scores(outcomes)
    matrix = core["matrix_gold_by_predicted"]
    per_disposition = {}
    for disposition in DISPOSITIONS:
        tp = matrix[disposition][disposition]
        fp = sum(matrix[gold][disposition] for gold in DISPOSITIONS if gold != disposition)
        fn = sum(matrix[disposition][got] for got in DISPOSITIONS if got != disposition) + failed[disposition]  # a failed receipt misses its answer
        per_disposition[disposition] = {**prf(tp, fp, fn), "support": sum(1 for row in labelled if row["expected"]["disposition"] == disposition)}
    correct = sum(1 for outcome in outcomes if outcome["disposition"] == outcome["gold_disposition"])
    columns = [*DISPOSITIONS, NOT_EVALUATED]
    full = {gold: {**matrix[gold], NOT_EVALUATED: failed[gold]} for gold in DISPOSITIONS}
    undecided = {gold: {got: 0 for got in DISPOSITIONS} for gold in DISPOSITIONS}
    for outcome in outcomes:
        if not outcome["decided"]:
            undecided[outcome["gold_disposition"]][outcome["disposition"]] += 1
    return {
        "available": True,
        "receipts": len(labelled),
        "evaluated": len(outcomes),
        "execution_errors": sum(failed.values()),
        "accuracy": rate(correct, len(labelled)),  # a receipt that could not be evaluated counts as not correct
        "accuracy_when_evaluated": core["accuracy"],
        "per_disposition": per_disposition,
        "matrix_gold_by_predicted": matrix,
        # Every labelled receipt: the three dispositions and the receipts not evaluated, with row totals.
        "matrix_with_not_evaluated": {"columns": columns, "rows": full, "row_totals": {gold: sum(full[gold].values()) for gold in DISPOSITIONS},
                                      "correct": correct, "incorrect": len(labelled) - correct},
        # Of each cell, the receipts the engine did not fully decide: a provisional pass is not a cleared one.
        "undecided_by_cell": undecided,
        "execution_errors_by_gold": {disposition: failed[disposition] for disposition in DISPOSITIONS},
        "undetermined": rate(sum(1 for outcome in outcomes if not outcome["decided"]), len(outcomes)),
        **{name: core[name] for name in ("false_pass", "missed_flag_passed", "missed_flag_any", "false_flag", "review", "abstention", "flagged_undecided")},
    }


def _detection_receipts(rows: list[dict]) -> list[dict]:
    """Each labelled receipt's expected and raised findings, as detection_scores reads them."""
    receipts = []
    for row in rows:
        expected = row["expected"]["findings"]
        if expected is None:
            continue
        gold = [{"rule_id": item["rule_id"], "kind": "exception", "line_ids": item.get("line_ids"), "severity": item.get("severity")} for item in expected["exceptions"]]
        gold += [{"rule_id": rule_id, "kind": "signal", "line_ids": None, "severity": None} for rule_id in expected["signals"]]
        result, raised = row["result"], []
        if result is not None:
            raised = [{"rule_id": item["rule_id"], "kind": "exception", "line_ids": sorted(item["line_ids"]), "severity": item["severity"]} for item in result["exceptions"]]
            raised += [{"rule_id": rule_id, "kind": "signal", "line_ids": [], "severity": None} for rule_id in sorted({item["rule_id"] for item in result["review_signals"] if item["rule_id"] != UNCERTAINTY_RULE})]
            if result["uncertainty"]:  # one uncertainty signal per uncertain receipt, as the answers never expect one
                raised.append({"rule_id": UNCERTAINTY_RULE, "kind": "signal", "line_ids": [], "severity": None})
        receipts.append({"receipt_id": row["receipt_id"], "evaluated": result is not None, "expected": gold, "predicted": raised})
    return receipts


def _findings(rows: list[dict], coverage: dict | None = None) -> dict:
    receipts = _detection_receipts(rows)
    if not receipts:
        return unavailable("no receipt has expected findings (expected_exceptions with review_signals)")
    rules = sorted((coverage or {}).get("rules", {}))  # every rule of the ruleset, so untested ones are listed
    core = detection_scores(receipts, rules)
    evaluated = sum(1 for receipt in receipts if receipt["evaluated"])
    return {
        "available": True,
        "receipts": len(receipts),
        "evaluated": evaluated,
        "execution_errors": len(receipts) - evaluated,
        **core,
    }


def _labelled_lines(rows: list[dict], inputs: dict | None) -> list[dict]:
    """Every labelled line, with its answers, what the main engine predicted (None when it gave nothing), and input."""
    lines = []
    for row in rows:
        result = {} if row["result"] is None else {line["line_id"]: line for line in row["result"]["line_items"]}
        for line_id, label in sorted(row["expected"]["lines"].items()):
            if label["category"] is None:
                continue
            line = result.get(line_id) or {}
            source = (inputs or {}).get(line_id, {})
            lines.append({
                "receipt_id": row["receipt_id"], "line_id": line_id,
                "description": line.get("description_raw") or source.get("description"), "language": source.get("language"),
                "translation": line.get("english_translation"),
                "gold_category": label["category"], "gold_restricted": label["restricted"],
                "category": None if line.get("classification_error") else line.get("category"),
                "restricted": None if line.get("classification_error") else line.get("restricted"),
                "confidence": line.get("confidence"),
                "error": line.get("classification_error") or (None if row["result"] is not None else (row.get("error") or {}).get("code", "not_evaluated")),
            })
    return lines


def _baseline(rows: list[dict], inputs: dict | None) -> dict:
    lines = [line for line in _labelled_lines(rows, inputs) if line["gold_restricted"] is not None]
    if not lines:
        return unavailable("no line has an expected restricted value")
    if any(line["description"] is None for line in lines):
        return unavailable("a labelled line's description is not available to the keyword baseline")
    return {"available": True, **keyword_baseline.compare(lines)}


def _error_analysis(rows: list[dict], inputs: dict | None, findings: dict) -> dict:
    lines = _labelled_lines(rows, inputs)
    by_receipt = lambda items: {receipt_id: [item for item in items if item["receipt_id"] == receipt_id] for receipt_id in {item["receipt_id"] for item in items}}
    missing, unexpected = (by_receipt(findings.get(name, [])) for name in ("missing", "unexpected"))
    receipts = [
        {
            "receipt_id": row["receipt_id"], "evaluated": row["result"] is not None,
            "lines": [line for line in lines if line["receipt_id"] == row["receipt_id"]],
            "missing": missing.get(row["receipt_id"], []), "unexpected": unexpected.get(row["receipt_id"], []),
            "expected_disposition": row["expected"]["disposition"], "disposition": None if row["result"] is None else row["result"]["disposition"],
            "uncertain": row["result"] is not None and bool(row["result"]["uncertainty"]),
            "uncertainty": [] if row["result"] is None else [{"line_id": item["line_id"], "field": item["field"], "reason": item["reason"]} for item in row["result"]["uncertainty"]],
        }
        for row in rows
    ]
    return {"available": True, **diagnose(receipts)}


def _coverage(rows: list[dict], coverage: dict | None, findings: dict) -> dict:
    """Execution coverage from the receipts, beside positive-case coverage from the answers, rule by rule."""
    if coverage is None:
        return unavailable("rule coverage is measured when a run finishes, on its receipts and its exact ruleset")
    if not coverage.get("available", True):
        return coverage
    per_rule = findings.get("per_rule", {}) if findings.get("available") else {}
    undecided = Counter(item["rule_id"] for row in rows if row["result"] is not None for item in row["result"]["open_questions"])
    table = {}
    for rule_id, row in coverage["rules"].items():
        scores = per_rule.get(rule_id, {})
        table[rule_id] = {**row, "expected": scores.get("expected", 0), "predicted": scores.get("predicted", 0), "undecided_receipts": undecided[rule_id],
                          "positive_cases": scores.get("expected", 0) > 0}
    executable = [rule_id for rule_id, row in table.items() if row["execution"] != "never_runs" and row["kind"] in ("check", "carve_out")]
    return {
        "available": True,
        "receipts": coverage["receipts"],
        "rules": len(table),
        "executable_rules": len(executable),
        # execution: met its subject on at least one receipt; positive: the answers expect it at least once.
        "exercised": sorted(rule_id for rule_id in executable if table[rule_id]["execution"] == "evaluated"),
        "not_exercised": sorted(rule_id for rule_id in executable if table[rule_id]["execution"] != "evaluated"),
        "with_positive_cases": sorted(rule_id for rule_id in executable if table[rule_id]["positive_cases"]),
        "without_positive_cases": sorted(rule_id for rule_id in executable if not table[rule_id]["positive_cases"]),
        "with_undecided": sorted(rule_id for rule_id, row in table.items() if row["undecided_receipts"]),
        "per_rule": table,
    }


def _operational(rows: list[dict], evaluated: list[dict], dataset_receipts: int) -> dict:
    lines = [line for row in evaluated for line in row["result"]["line_items"]]
    uncertain_lines = {(row["receipt_id"], item["line_id"]) for row in evaluated for item in row["result"]["uncertainty"]}
    timed = [row["latency_ms"] for row in rows if row.get("latency_ms") is not None]
    return {
        "receipts": dataset_receipts,
        "not_run": dataset_receipts - len(rows),
        "run": len(rows),
        "evaluated": len(evaluated),
        "execution_errors": rate(len(rows) - len(evaluated), len(rows)),
        "execution_error_codes": dict(sorted(Counter(row["error"]["code"] for row in rows if row["result"] is None).items())),
        "lines": len(lines),
        "coverage": rate(sum(1 for line in lines if line["category"] is not None and line.get("classification_error") is None), len(lines)),
        "classification_errors": rate(sum(1 for line in lines if line.get("classification_error") is not None), len(lines)),
        "uncertainty": {
            "receipts": rate(sum(1 for row in evaluated if row["result"]["uncertainty"]), len(evaluated)),
            "lines": rate(len(uncertain_lines), len(lines)),
            "by_reason": dict(sorted(Counter(f"{item['field']}:{item['reason']}" for row in evaluated for item in row["result"]["uncertainty"]).items())),
        },
        "undetermined_receipts": rate(sum(1 for row in evaluated if not row["result"]["decided"]), len(evaluated)),
        "dispositions": {disposition: sum(1 for row in evaluated if row["result"]["disposition"] == disposition) for disposition in DISPOSITIONS},
        # Wall-clock time per receipt in the run, models included: replay is fast, live includes each provider call.
        "latency": {"receipts_timed": len(timed), "total_ms": sum(timed) if timed else None, "mean_per_receipt_ms": round(sum(timed) / len(timed)) if timed else None},
    }
