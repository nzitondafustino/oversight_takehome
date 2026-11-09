"""Runs cases/offline_eval/quality.yaml: exception detection, evidence, the restricted matrix, the keyword baseline
and error analysis. Then checks rule coverage and the agreement of detection with rule_scores on the real data."""

import json
from pathlib import Path

import pytest
from test_offline_eval import line, picked
from yaml_cases import exact, load_cases

from expense_validator.compiler.validate import read_fields
from expense_validator.config import load_config
from expense_validator.engine.inputs import admit_receipt
from expense_validator.evaluation import coverage, detection, error_analysis, keyword_baseline, metrics
from expense_validator.policy.schema import RuleSet

DATA = Path(__file__).parents[2] / "data" / "receipts.jsonl"


def finding(partial: dict) -> dict:
    return {"kind": "exception", "severity": "high", "line_ids": [], **partial}


def receipt(partial: dict, number: int) -> dict:
    return {
        "receipt_id": f"R{number}",
        "evaluated": partial.get("evaluated", True),
        "expected": [finding(item) for item in partial.get("expected", [])],
        "predicted": [finding(item) for item in partial.get("predicted", [])],
    }


@pytest.mark.parametrize("case", load_cases("offline_eval", "detection"))
def test_detection(case):
    found = detection.detection_scores([receipt(item, number) for number, item in enumerate(case["receipts"], start=1)], case["rules"])
    assert exact(picked(found, case["expected"])) == exact(case["expected"])


@pytest.mark.parametrize("case", load_cases("offline_eval", "evidence"))
def test_evidence(case):
    matches = [{"receipt_id": "R1", "rule_id": "ALC-01", "expected": finding(item["expected"]), "found": finding(item["found"])} for item in case["matches"]]
    found = detection.evidence_scores(matches)
    assert exact(picked(found, case["expected"])) == exact(case["expected"])


@pytest.mark.parametrize("case", load_cases("offline_eval", "restricted_matrix"))
def test_restricted_matrix(case):
    found = metrics.classification_scores([line(item) for item in case["lines"]])
    assert exact(picked(found, case["expected"])) == exact(case["expected"])


@pytest.mark.parametrize("case", load_cases("offline_eval", "keyword_baseline"))
def test_keyword_baseline(case):
    found = keyword_baseline.classify(case["description"])
    assert exact(picked(found, case["expected"])) == exact(case["expected"])


@pytest.mark.parametrize("case", load_cases("offline_eval", "baseline_comparison"))
def test_baseline_comparison(case):
    found = keyword_baseline.compare([{"receipt_id": "R1", **item} for item in case["lines"]])
    assert exact(picked(found, case["expected"])) == exact(case["expected"])


@pytest.mark.parametrize("case", load_cases("offline_eval", "error_analysis"))
def test_error_analysis(case):
    base = {"evaluated": True, "lines": [], "missing": [], "unexpected": [], "expected_disposition": None, "disposition": None, "uncertain": False, "uncertainty": []}
    receipts = [{"receipt_id": f"R{number}", **base, **item} for number, item in enumerate(case["receipts"], start=1)]
    for item in receipts:
        item["lines"] = [{"language": "en", "translation": None, "confidence": None, "error": None, **entry} for entry in item["lines"]]
    found = error_analysis.diagnose(receipts)
    assert exact(picked(found, case["expected"])) == exact(case["expected"])


def _golden():
    """Each golden receipt with its answers, and as admitted with its answers as its classifications."""
    config = load_config()
    fields = read_fields(config.paths.rule_fields.read_text())
    rows = []
    for text in DATA.read_text().splitlines():
        record = json.loads(text)
        answers = {item["line_id"]: item["labels"] for item in record["line_items"]}
        raw = {name: value for name, value in record.items() if name not in ("labels", "notes")}
        raw["line_items"] = [{name: value for name, value in item.items() if name != "labels"} for item in record["line_items"]]
        admitted = admit_receipt(raw, fields).with_classifications({line_id: {"category": label["category"], "restricted": label["restricted"], "confidence": "1"} for line_id, label in answers.items()})
        rows.append((record, admitted))
    return rows, RuleSet.model_validate_json(config.paths.ruleset.read_text()), fields


def test_rule_coverage_is_measured_on_the_receipts_not_read_from_the_ruleset():
    rows, ruleset, fields = _golden()
    found = coverage.rule_coverage([admitted for _, admitted in rows], ruleset, fields)["rules"]
    assert set(found) == {rule.rule_id for rule in ruleset.rules}  # every rule is listed, run or not
    positive = {item["rule_id"] for record, _ in rows for item in record["labels"]["expected_exceptions"]}
    for rule_id in positive:  # an expected finding needs its rule to have met its subject on that receipt
        assert found[rule_id]["subject_receipts"] > 0, rule_id
    for rule in ruleset.rules:
        if rule.status != "active":
            assert found[rule.rule_id]["execution"] == "never_runs"
        if rule.kind == "process" and rule.directive == "guidance":
            assert found[rule.rule_id]["execution"] == "never_runs"  # loaded, yet never exercised
    assert all(row["subject_receipts"] <= row["gate_receipts"] <= len(rows) for row in found.values())


def test_detection_agrees_with_rule_scores_on_the_real_answers():
    """Two scorers of the same thing must not drift: on the golden answers against themselves, and against nothing."""
    rows, _, _ = _golden()
    for raised in (True, False):
        receipts, outcomes = [], []
        for record, _ in rows:
            gold = [{"rule_id": item["rule_id"], "kind": "exception", "line_ids": sorted(item["line_ids"]), "severity": item["severity"]} for item in record["labels"]["expected_exceptions"]]
            gold += [{"rule_id": item["rule_id"], "kind": "signal", "line_ids": None, "severity": None} for item in record["labels"]["review_signals"]]
            receipts.append({"receipt_id": record["receipt_id"], "evaluated": True, "expected": gold, "predicted": gold if raised else []})
            exceptions = {item["rule_id"]: {"line_ids": item["line_ids"], "severity": item["severity"]} for item in gold if item["kind"] == "exception"}
            signals = [item["rule_id"] for item in gold if item["kind"] == "signal"]
            outcomes.append({"exceptions": exceptions if raised else {}, "gold_exceptions": exceptions, "signals": signals if raised else [], "gold_signals": signals,
                             "uncertain": False, "unresolved": [], "open": [], "proof": None})
        new, old = detection.detection_scores(receipts), metrics.rule_scores(outcomes)
        assert new["micro"] == old["strict"]
        assert {rule: (row["tp"], row["fp"], row["fn"]) for rule, row in new["per_rule"].items()} == {rule: (row["tp"], row["fp"], row["fn"]) for rule, row in old["rules"].items()}
        assert new["evidence"]["exact"] == old["evidence_exact"]
