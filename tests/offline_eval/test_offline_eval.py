"""Runs cases/offline_eval/metrics.yaml, and checks the harness end to end on real and synthetic data."""

import ast
import json
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from yaml_cases import exact, load_cases

from expense_validator.compiler.validate import read_fields
from expense_validator.config import ROOT, load_config
from expense_validator.engine import evaluation as engine_evaluation
from expense_validator.engine.inputs import allowed_values
from expense_validator.engine.models import Receipt
from expense_validator.ingestion import IngestionError
from expense_validator.offline_eval import metrics, runner
from expense_validator.offline_eval.__main__ import main, smoke_predictions
from expense_validator.offline_eval.dataset import load_dataset
from expense_validator.offline_eval.predictions import LinePrediction, PredictionSet, Source, load_predictions
from expense_validator.offline_eval.report import baseline_comparison
from expense_validator.offline_eval.runner import evaluate_predictions
from expense_validator.offline_eval.scenarios import built_in_scenarios, load_oracle_recipients
from expense_validator.policy.schema import RuleSet

PATHS = load_config().paths
RULESET = RuleSet.from_json(PATHS.ruleset.read_text())
FIELDS = read_fields(PATHS.rule_fields.read_text())
DATA = ROOT / "data" / "receipts.jsonl"
SMOKE = ROOT / "evaluation" / "synthetic" / "proof_smoke.jsonl"
RECIPIENTS = load_oracle_recipients(ROOT / "evaluation" / "oracle_recipients.yaml")
CATEGORIES = sorted(allowed_values(FIELDS, "item.category"))
DATASET = load_dataset(DATA, "northwind")
SCENARIOS = {item.key: item for item in built_in_scenarios(DATASET, RECIPIENTS, CATEGORIES)}


def picked(actual, wanted):
    """Whether what a case names, at any depth, is what came back. Anything it leaves out is not compared."""
    if isinstance(wanted, dict):
        return {key: picked(actual[key], value) for key, value in wanted.items()}
    return actual


def outcome(partial):
    base = {
        "receipt_id": "R1",
        "expense_type": "Meals - Team",
        "disposition": "pass",
        "gold_disposition": "pass",
        "decided": True,
        "reason_codes": [],
        "exceptions": {},
        "gold_exceptions": {},
        "signals": [],
        "gold_signals": [],
        "uncertain": False,
        "uncertainty": [],
        "unresolved": [],
        "open": [],
        "proof": None,
    }
    return {**base, **partial}


def line(partial):
    confidence = partial.get("confidence")
    return {
        "line_id": "L1",
        "gold_category": "food_beverage",
        "gold_restricted": False,
        "category": None,
        "restricted": None,
        **partial,
        "confidence": None if confidence is None else Decimal(confidence),
    }


@pytest.mark.parametrize("case", load_cases("offline_eval", "rates"))
def test_a_rate_has_its_denominator(case):
    found = metrics.rate(case["numerator"], case["denominator"])
    assert exact(picked(found, case["expected"])) == exact(case["expected"])


@pytest.mark.parametrize("case", load_cases("offline_eval", "prf"))
def test_precision_recall_and_f1(case):
    assert exact(metrics.prf(case["tp"], case["fp"], case["fn"])) == exact({"tp": case["tp"], "fp": case["fp"], "fn": case["fn"], **case["expected"]})


@pytest.mark.parametrize("case", load_cases("offline_eval", "rule_scores"))
def test_rule_scores(case):
    found = metrics.rule_scores([outcome(item) for item in case["outcomes"]])
    assert exact(picked(found, case["expected"])) == exact(case["expected"])


@pytest.mark.parametrize("case", load_cases("offline_eval", "dispositions"))
def test_disposition_scores(case):
    found = metrics.disposition_scores([outcome(item) for item in case["outcomes"]])
    assert exact(picked(found, case["expected"])) == exact(case["expected"])


@pytest.mark.parametrize("case", load_cases("offline_eval", "exact"))
def test_exact_match(case):
    found = metrics.exact_match([outcome(item) for item in case["outcomes"]])
    assert exact(picked(found, case["expected"])) == exact(case["expected"])


@pytest.mark.parametrize("case", load_cases("offline_eval", "proofs"))
def test_proof_scores(case):
    found = metrics.proof_scores([outcome(item) for item in case["outcomes"]], ["GIFT-04"])
    assert exact(picked(found, case["expected"])) == exact(case["expected"])


@pytest.mark.parametrize("case", load_cases("offline_eval", "classification"))
def test_classification_scores(case):
    found = metrics.classification_scores([line(item) for item in case["lines"]])
    assert exact(picked(found, case["expected"])) == exact(case["expected"])


@pytest.mark.parametrize("case", load_cases("offline_eval", "buckets"))
def test_confidence_buckets(case):
    value = None if case["confidence"] is None else Decimal(case["confidence"])
    assert metrics.confidence_bucket(value, Decimal("0.5")) == case["expected"]["bucket"]


@pytest.mark.parametrize("case", load_cases("offline_eval", "calibration"))
def test_calibration(case):
    points = [(Decimal(confidence), correct) for confidence, correct in case["points"]]
    found = metrics.calibration(points, bins=case["bins"])
    assert exact(picked(found, case["expected"])) == exact(case["expected"])


@pytest.mark.parametrize("case", load_cases("offline_eval", "families"))
def test_rule_family(case):
    assert metrics.rule_family(case["rule_id"]) == case["expected"]["family"]


@pytest.mark.parametrize("case", load_cases("offline_eval", "sources_refused"))
def test_a_claim_about_a_source_can_be_refused(case):
    with pytest.raises(ValueError, match=case["expected"]):
        PredictionSet.model_validate(case["prediction_set"])


# --- End to end ---


def run_of(key):
    scenario = SCENARIOS[key]
    return evaluate_predictions(DATASET, scenario.predictions, RULESET, FIELDS, complete=scenario.complete)


def prediction_set(predictions, kind="synthetic", customer="northwind", model=None):
    return PredictionSet(customer_id=customer, source=Source(kind=kind, name="T", description="test", model=model), predictions=tuple(predictions))


BASELINE = yaml.safe_load((ROOT / "evaluation" / "baseline_10_6.yaml").read_text())["scenarios"]


@pytest.mark.parametrize("key", sorted(BASELINE))
def test_the_frozen_benchmarks_are_reproduced_on_every_figure(key):
    assert baseline_comparison({key: run_of(key)}, {key: BASELINE[key]})[key]["differs"] == {}


@pytest.mark.parametrize("key", sorted(SCENARIOS))
def test_only_actual_predictions_are_measured(key):
    run = run_of(key)
    assert run["measured_classifier_performance"] is False
    assert run["source"]["kind"] in {"oracle", "synthetic", "none"}
    assert run["source"]["model"] is None
    assert run["calibration"]["status"] == "not_evaluated"
    assert run["secondary_field_accuracy"]["status"] == "not_evaluated"


def test_a_file_of_actual_predictions_is_measured_and_calibrated(tmp_path):
    made = [{"line_id": item.line_id, "category": item.category, "confidence": "0.9"} for answer in DATASET.answers.values() for item in answer.lines]
    made[0]["category"] = "tobacco"
    path = tmp_path / "run.json"
    path.write_text(json.dumps({"customer_id": "northwind", "source": {"kind": "actual", "name": "real", "description": "d", "model": "m"}, "predictions": made}))
    run = evaluate_predictions(DATASET, load_predictions(path), RULESET, FIELDS)
    assert run["measured_classifier_performance"] is True
    assert run["calibration"]["status"] == "evaluated"
    assert run["calibration"]["points"] == 79
    assert run["warnings"] == []


def test_actual_predictions_that_match_the_answers_exactly_raise_a_warning():
    made = [LinePrediction(line_id=item.line_id, category=item.category) for answer in DATASET.answers.values() for item in answer.lines]
    run = evaluate_predictions(DATASET, prediction_set(made, "actual", model="m"), RULESET, FIELDS)
    assert any("did not reach" in warning for warning in run["warnings"])
    assert run["calibration"]["status"] == "not_evaluated"


def test_missing_predictions_stay_uncertain_and_are_counted():
    answer_line = DATASET.answers["RCP-001"].lines[0]
    one = [LinePrediction(line_id=answer_line.line_id, category=answer_line.category, confidence="0.9")]
    run = evaluate_predictions(DATASET, prediction_set(one), RULESET, FIELDS)
    assert run["classification"]["missing_category"]["numerator"] == 78
    assert run["classification"]["missing_category"]["denominator"] == 79
    assert run["classification"]["missing_confidence"]["numerator"] == 78
    assert run["disposition"]["false_pass"]["numerator"] == 0


def test_a_prediction_for_a_line_not_in_the_dataset_is_refused():
    with pytest.raises(ValueError, match="not in this customer's receipts"):
        evaluate_predictions(DATASET, prediction_set([LinePrediction(line_id="NOPE-L1", category="other")]), RULESET, FIELDS)


def test_another_customers_predictions_are_refused():
    with pytest.raises(ValueError, match="predictions are for acme"):
        evaluate_predictions(DATASET, prediction_set([], customer="acme"), RULESET, FIELDS)


def test_mixed_confidence_leaves_out_only_the_low_lines():
    run = run_of("G0")
    buckets = run["breakdowns"]["line_confidence"]
    assert sum(item["lines"] for item in buckets.values()) == 79
    assert buckets["below_0.5"]["lines"] > 0 and buckets["0.9_to_1"]["lines"] > 0
    # Every category is right here, so no rule can be falsely raised, and only low lines are uncertain.
    assert run["rules"]["diagnostic"]["fp"] == 0
    assert run["disposition"]["false_flag"]["numerator"] == 0
    assert run["disposition"]["false_pass"]["numerator"] == 0
    states = {item["state"] for receipt in run["receipts"] for item in receipt["uncertainty"] if item["field"] == "category"}
    assert states == {"low_confidence"}


def test_a_receipt_with_several_findings_scores_each_on_its_own():
    receipts = {row["receipt_id"]: row for row in run_of("A")["receipts"]}
    several = [row for row in receipts.values() if len(row["gold_exceptions"]) > 1]
    assert several
    for row in several:
        assert set(row["exceptions"]) == set(row["gold_exceptions"])


def test_secondary_fields_reach_the_engine_and_their_uncertainty_is_broken_down(tmp_path):
    row = dict(json.loads(SMOKE.read_text().splitlines()[0]))
    row["line_items"][0]["labels"]["category"] = "personal_care"
    row["baggage_delay_ref"] = "BAG-1"
    path = tmp_path / "one.jsonl"
    path.write_text(json.dumps(row) + "\n")
    dataset = load_dataset(path, "c")
    line_id = row["line_items"][0]["line_id"]
    given = {"recipient_type": "client", "venue_type": "made_up", "item_kind": "made_up"}
    predictions = prediction_set([LinePrediction(line_id=line_id, category="personal_care", confidence="0.9", **given)], customer="c")
    found = evaluate_predictions(dataset, predictions, RULESET, FIELDS)["breakdowns"]["missing_field"]
    assert set(found) == {"essential_toiletry:missing", "item_kind:unknown", "venue_type:unknown"}


# --- Proofs, through the real engine ---


def smoke_run():
    data = load_dataset(SMOKE, "synthetic-smoke")
    return evaluate_predictions(data, smoke_predictions(data), RULESET, FIELDS)


def test_proofs_are_counted_with_no_active_exception_and_their_critical_cases_named():
    run = smoke_run()
    proofs = run["proofs"]
    assert proofs["receipt_ids"] == ["SYN-001", "SYN-002", "SYN-003"]
    assert proofs["flag_without_active_exception"] == 3
    assert proofs["correct_flag"]["value"] == 1.0
    assert proofs["false_flag"]["value"] == 0.0
    assert proofs["gold_rule_in_proof"]["value"] == 1.0
    assert proofs["possible_critical_receipts"] == ["SYN-001", "SYN-002", "SYN-003"]
    assert proofs["actually_critical"] == 1
    assert proofs["readings_total"] == 9


def test_a_proof_flag_is_a_right_disposition_but_no_true_positive():
    receipts = {row["receipt_id"]: row for row in smoke_run()["receipts"]}
    row = receipts["SYN-001"]
    assert (row["disposition"], row["gold_disposition"], row["exceptions"]) == ("flag", "flag", {})
    run = smoke_run()
    assert run["rules"]["rules"]["GIFT-01"]["tp"] == 0
    assert run["rules"]["rules"]["GIFT-01"]["fn_by_kind"]["silent"] == 0


def test_where_a_compliant_reading_exists_or_the_category_is_not_trusted_there_is_no_proof():
    receipts = {row["receipt_id"]: row for row in smoke_run()["receipts"]}
    assert receipts["SYN-004"]["proof"] is None and receipts["SYN-004"]["disposition"] == "review"
    assert receipts["SYN-005"]["proof"] is None and receipts["SYN-005"]["disposition"] == "review"
    assert receipts["SYN-006"]["proof"] is None and receipts["SYN-006"]["disposition"] == "review"


def test_the_benchmark_has_no_proofs_in_any_scenario():
    """This dataset cannot exercise the path, which the report says outright."""
    assert sum(run_of(key)["proofs"]["receipts"]["numerator"] for key in SCENARIOS) == 0


# --- Isolation, sources and reproducibility ---


def test_the_answers_never_reach_the_engine(monkeypatch):
    seen = []
    real = engine_evaluation.evaluate_receipts

    def spy(customer_id, receipts, *args, **kwargs):
        receipts = list(receipts)
        seen.extend(receipts)
        return real(customer_id, receipts, *args, **kwargs)

    monkeypatch.setattr(runner, "evaluate_receipts", spy)
    run_of("E")
    assert len(seen) == 32
    for receipt in seen:
        dumped = receipt.model_dump()
        assert "labels" not in dumped and "notes" not in dumped
        assert all("labels" not in line and line["category"] is None for line in dumped["line_items"])


def test_customers_with_one_receipt_id_are_scored_apart():
    first = load_dataset(SMOKE, "one")
    second = load_dataset(SMOKE, "two")
    alone = evaluate_predictions(first, smoke_predictions(first), RULESET, FIELDS)
    evaluate_predictions(second, smoke_predictions(second), RULESET, FIELDS)
    again = evaluate_predictions(first, smoke_predictions(first), RULESET, FIELDS)
    assert json.dumps(alone, sort_keys=True, default=str) == json.dumps(again, sort_keys=True, default=str)
    assert alone["customer_id"] == "one"


def test_the_source_files_are_left_as_they_were(tmp_path):
    watched = [DATA, SMOKE, PATHS.ruleset, PATHS.rule_fields]
    before = [path.read_bytes() for path in watched]
    assert main(["--only", "E", "--out", str(tmp_path)]) == 0
    assert [path.read_bytes() for path in watched] == before


def test_two_runs_write_the_same_bytes(tmp_path):
    first, second = tmp_path / "1", tmp_path / "2"
    arguments = ["--only", "A", "B", "F", "G0", "H0"]
    assert main([*arguments, "--out", str(first)]) == 0
    assert main([*arguments, "--out", str(second)]) == 0
    names = sorted(path.relative_to(first) for path in first.rglob("*") if path.is_file())
    assert names == sorted(path.relative_to(second) for path in second.rglob("*") if path.is_file())
    for name in names:
        assert (first / name).read_bytes() == (second / name).read_bytes(), name


def test_a_dataset_is_held_to_the_ingestion_contract(tmp_path):
    row = json.loads(SMOKE.read_text().splitlines()[0])
    row["line_items"][0]["category"] = row["line_items"][0]["labels"]["category"]
    path = tmp_path / "one.jsonl"
    path.write_text(json.dumps(row) + "\n")
    with pytest.raises(IngestionError, match=f"{path}:1: .*may not carry classifier fields"):
        load_dataset(path, "c")


def test_a_dataset_keeps_numbers_exact_and_its_answers_out_of_its_inputs(tmp_path):
    row = json.loads(SMOKE.read_text().splitlines()[0])
    row.pop("baggage_delay_hours", None)
    text = json.dumps(row)
    path = tmp_path / "one.jsonl"
    path.write_text(text[:-1] + ', "baggage_delay_hours": 48.200000000000000001}\n')
    dataset = load_dataset(path, "c")
    assert dataset.inputs[0].baggage_delay_hours == Decimal("48.200000000000000001")
    assert isinstance(dataset.inputs[0], Receipt)
    assert all(line.category is None for line in dataset.inputs[0].line_items)
    assert dataset.answers[row["receipt_id"]].lines[0].category == row["line_items"][0]["labels"]["category"]


def test_scores_do_not_depend_on_dataset_order(tmp_path):
    rows = DATA.read_text().splitlines()
    path = tmp_path / "reversed.jsonl"
    path.write_text("\n".join(reversed(rows)) + "\n")
    flipped = load_dataset(path, "northwind")
    scenario = SCENARIOS["H0"]
    first = evaluate_predictions(DATASET, scenario.predictions, RULESET, FIELDS)
    second = evaluate_predictions(flipped, scenario.predictions, RULESET, FIELDS)
    assert json.dumps(first, sort_keys=True, default=str) == json.dumps(second, sort_keys=True, default=str)


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_h_and_i_have_the_same_lines_wrong_and_differ_only_in_confidence(seed):
    known, admitted = SCENARIOS[f"H{seed}"].predictions.by_line(), SCENARIOS[f"I{seed}"].predictions.by_line()
    answers = {line.line_id: line.category for answer in DATASET.answers.values() for line in answer.lines}
    wrong = {line_id for line_id, item in known.items() if item.category != answers[line_id]}
    assert wrong
    assert {line_id: item.category for line_id, item in known.items()} == {line_id: item.category for line_id, item in admitted.items()}
    for line_id in known:
        assert known[line_id].confidence == Decimal("0.95")
        assert admitted[line_id].confidence == (Decimal("0.30") if line_id in wrong else Decimal("0.95"))


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_g_has_every_category_right(seed):
    answers = {line.line_id: line.category for answer in DATASET.answers.values() for line in answer.lines}
    assert {item.line_id: item.category for item in SCENARIOS[f"G{seed}"].predictions.predictions} == answers


def test_a_benchmark_figure_that_moved_is_reported():
    compared = baseline_comparison({"A": run_of("A")}, {"A": {**BASELINE["A"], "accuracy": 31}})["A"]
    assert compared["reproduced"] is False
    assert compared["differs"] == {"accuracy": {"baseline": 31, "harness": 32}}


def test_the_generated_report_says_real_accuracy_is_unmeasured(tmp_path):
    assert main(["--only", "A", "--out", str(tmp_path)]) == 0
    text = (tmp_path / "report.md").read_text()
    assert "Real classifier accuracy remains unmeasured" in text
    document = json.loads((tmp_path / "metrics.json").read_text())
    assert document["real_classifier_measured"] is False


def test_a_report_with_actual_predictions_says_so(tmp_path):
    made = [{"line_id": item.line_id, "category": item.category, "confidence": "0.9"} for answer in DATASET.answers.values() for item in answer.lines]
    made[0]["category"] = "tobacco"
    path = tmp_path / "run.json"
    path.write_text(json.dumps({"customer_id": "northwind", "source": {"kind": "actual", "name": "real", "description": "d", "model": "m"}, "predictions": made}))
    out = tmp_path / "out"
    assert main(["--only", "A", "--predictions", str(path), "--out", str(out)]) == 0
    assert "remains unmeasured" not in (out / "report.md").read_text()
    assert json.loads((out / "metrics.json").read_text())["real_classifier_measured"] is True


def test_the_harness_imports_nothing_that_could_call_out():
    allowed_roots = {"argparse", "collections", "dataclasses", "decimal", "hashlib", "json", "math", "pathlib", "pydantic", "sys", "typing", "yaml", "expense_validator"}
    package = Path(metrics.__file__).parent
    for path in package.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = [item.name for item in node.names] if isinstance(node, ast.Import) else [node.module] if isinstance(node, ast.ImportFrom) else []
            for name in names:
                assert name.split(".")[0] in allowed_roots, (path.name, name)
            if isinstance(node, ast.ImportFrom):
                assert "agreement" not in (node.module or "")


def test_the_oracle_recipients_are_the_ones_the_engine_tests_use():
    cases = {case.values[0]["line_id"]: case.values[0]["expected"] for case in load_cases("helpers", "recipients")}
    assert RECIPIENTS == cases
