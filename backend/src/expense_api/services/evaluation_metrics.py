"""Quality metrics of one evaluation, from its stored per-receipt results and the dataset's answers.

The scores are the core's own (expense_validator.evaluation.metrics): classification_scores, disposition_scores,
rule_scores, prf and rate, so a metric here means what it means in the core's offline evaluation. This module only
chooses what each is computed over, and keeps the outcomes apart:

- correct and incorrect: the line has a category, and it is or is not the expected one.
- missing: the line has no category and no error, as when LangGraph ends on an abstention.
- classification_error: the classifier failed on the line, so it has an error and no category.
- execution_error: the receipt itself could not be evaluated, such as a replay answer not recorded.

Only correct counts as correct. Every rate states its numerator and denominator. A field without an answer is
never scored: a metric with nothing to score is unavailable, with the reason, never zero. Stored metrics carry
METRICS_SCHEMA_VERSION and are never rewritten, so a later change to scoring cannot alter a past run's numbers.
"""

from collections import Counter, defaultdict
from decimal import Decimal

from expense_validator.evaluation.metrics import DISPOSITIONS, UNCERTAINTY_RULE, classification_scores, disposition_scores, prf, rate, rule_scores

METRICS_SCHEMA_VERSION = "evaluation-metrics/1"
NO_CATEGORY = "(none)"  # the confusion matrix column for a line with no predicted category, for whatever reason


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


def compute(rows: list[dict], *, result_context: str, context_complete: bool | None, dataset_receipts: int) -> dict:
    """The evaluation's metrics. rows are its stored results in dataset order, each with expected and result.

    A run that stopped early, cancelled or out of budget, is scored over the receipts it ran: the others are
    counted as not_run, never as errors or as wrong.
    """
    evaluated = [row for row in rows if row["result"] is not None]
    return {
        "schema_version": METRICS_SCHEMA_VERSION,
        # dataset: every receipt evaluated together, as one validate_batch call would. isolated_receipts: each
        # alone, as the run did not finish its dataset pass, so rules spanning receipts were left undecided.
        "result_context": result_context,
        "context_complete": context_complete,
        "classification": _classification(rows),
        "disposition": _disposition(rows),
        "findings": _findings(rows),
        "operational": _operational(rows, evaluated, dataset_receipts),
    }


def summary(metrics: dict) -> dict:
    """The few numbers a history page shows."""
    value = lambda block, *path: _dig(metrics.get(block, {}), *path)
    return {
        "category_accuracy": value("classification", "accuracy", "value"),
        "macro_f1": value("classification", "macro", "f1"),
        "disposition_accuracy": value("disposition", "accuracy", "value"),
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
    return {
        "available": True,
        "receipts": len(labelled),
        "evaluated": len(outcomes),
        "execution_errors": sum(failed.values()),
        "accuracy": rate(correct, len(labelled)),  # a receipt that could not be evaluated counts as not correct
        "accuracy_when_evaluated": core["accuracy"],
        "per_disposition": per_disposition,
        "matrix_gold_by_predicted": matrix,
        "execution_errors_by_gold": {disposition: failed[disposition] for disposition in DISPOSITIONS},
        "undetermined": rate(sum(1 for outcome in outcomes if not outcome["decided"]), len(outcomes)),
        **{name: core[name] for name in ("false_pass", "missed_flag_passed", "missed_flag_any", "false_flag", "review", "abstention", "flagged_undecided")},
    }


def _findings(rows: list[dict]) -> dict:
    labelled = [row for row in rows if row["expected"]["findings"] is not None]
    if not labelled:
        return unavailable("no receipt has expected findings (expected_exceptions with review_signals)")
    outcomes = []
    for row in labelled:
        if row["result"] is None:
            continue
        result, expected = row["result"], row["expected"]["findings"]
        outcomes.append({
            "exceptions": {item["rule_id"]: {"line_ids": sorted(item["line_ids"]), "severity": item["severity"]} for item in result["exceptions"]},
            "gold_exceptions": {item["rule_id"]: {"line_ids": item["line_ids"], "severity": item["severity"]} for item in expected["exceptions"]},
            "signals": sorted({item["rule_id"] for item in result["review_signals"] if item["rule_id"] != UNCERTAINTY_RULE}),
            "gold_signals": expected["signals"],
            "uncertain": bool(result["uncertainty"]),
            # The public result does not say which open rules were unresolved or covered by a proof, so the core's
            # breakdown of misses by kind is left out rather than guessed.
            "unresolved": [], "open": sorted({item["rule_id"] for item in result["open_questions"]}), "proof": None,
        })
    if not outcomes:
        return unavailable("no receipt with expected findings could be evaluated")
    core = rule_scores(outcomes)
    without_kinds = lambda scores: {name: value for name, value in scores.items() if name != "fn_by_kind"}
    return {
        "available": True,
        "receipts": len(labelled),
        "evaluated": len(outcomes),
        "execution_errors": len(labelled) - len(outcomes),
        # strict counts every EXC-03 uncertainty signal as a false positive, as the answers never hold one.
        "micro": core["strict"],
        "micro_without_uncertainty_signals": core["diagnostic"],
        "evidence_exact": core["evidence_exact"],
        "per_rule": {rule_id: without_kinds(scores) for rule_id, scores in core["rules"].items()},
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
