"""Evaluation metrics from stored rows: the outcomes kept apart, denominators stated, unlabelled fields unscored."""

import pytest
from conftest import case_ids, load_cases

from expense_api.services.evaluation_metrics import METRICS_SCHEMA_VERSION, compute, receipt_scoring

CASES = load_cases("evaluation_metrics")


def _row(index: int, spec: dict) -> dict:
    """A stored row with the parts of a core ReceiptResult the metrics read."""
    findings_labelled = spec.get("findings_labelled", "expected_exceptions" in spec)
    expected = {
        "disposition": spec.get("expected_disposition"),
        "lines": {line_id: {"category": category, "restricted": None} for line_id, category in spec.get("labels", {}).items()},
        "findings": None if not findings_labelled else {
            "exceptions": [{"rule_id": rule, "line_ids": sorted(lines), "severity": severity} for rule, lines, severity in spec.get("expected_exceptions", [])],
            "signals": sorted(spec.get("expected_signals", [])),
        },
    }
    if "error" in spec:
        return {"receipt_id": f"R{index}", "result": None, "error": {"code": spec["error"]}, "expected": expected, "latency_ms": None}
    result = {
        "receipt_id": f"R{index}",
        "disposition": spec.get("disposition", "pass"),
        "decided": spec.get("decided", True),
        "disposition_reasons": [{"code": code, "basis": "", "rule_ids": []} for code in spec.get("reasons", [])],
        "line_items": [{"line_id": line_id, "category": category, "restricted": None if category is None else False, "confidence": None if category is None else 0.9,
                        "classification_error": error} for line_id, category, error in spec.get("lines", [])],
        "exceptions": [{"rule_id": rule, "line_ids": lines, "severity": severity} for rule, lines, severity in spec.get("exceptions", [])],
        "review_signals": [{"rule_id": rule, "reason": ""} for rule in spec.get("signals", [])],
        "open_questions": [],
        "uncertainty": [{"line_id": line_id, "field": field, "reason": reason, "state": "", "confidence": None} for line_id, field, reason in spec.get("uncertainty", [])],
    }
    return {"receipt_id": f"R{index}", "result": result, "error": None, "expected": expected, "latency_ms": 10}


def _at(metrics: dict, path: str):
    value = metrics
    for key in path.split("."):
        value = value[key]
    return value


@pytest.mark.parametrize("case", CASES, ids=case_ids(CASES))
def test_metrics(case):
    rows = [_row(index, spec) for index, spec in enumerate(case["rows"])]
    metrics = compute(rows, result_context="dataset", context_complete=True, dataset_receipts=case.get("receipts", len(rows)))
    assert metrics["schema_version"] == METRICS_SCHEMA_VERSION
    for path, expected in case["expect"].items():
        assert _at(metrics, path) == expected, path


def test_a_receipt_is_classified_correctly_only_when_every_labelled_line_is():
    row = _row(0, {"lines": [["L1", "alcohol", None], ["L2", "gift", None]], "labels": {"L1": "alcohol"}, "expected_disposition": "flag", "disposition": "review"})
    scoring = receipt_scoring(row["result"], row["expected"])
    assert scoring == {"lines": {"L1": {"expected": "alcohol", "predicted": "alcohol", "outcome": "correct"}}, "classification_correct": True, "expected_disposition": "flag", "disposition_correct": False}
    unlabelled = _row(1, {"lines": [["L3", "alcohol", None]]})
    assert receipt_scoring(unlabelled["result"], unlabelled["expected"])["classification_correct"] is None  # nothing to score, so neither
    failed = _row(2, {"error": "invalid_receipt", "labels": {"L4": "alcohol"}, "expected_disposition": "pass"})
    assert receipt_scoring(None, failed["expected"]) == {"lines": {"L4": {"expected": "alcohol", "predicted": None, "outcome": "execution_error"}}, "classification_correct": False, "expected_disposition": "pass", "disposition_correct": False}
