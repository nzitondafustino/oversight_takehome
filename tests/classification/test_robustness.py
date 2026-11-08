"""Runs the cases in cases/classification/robustness.yaml, and checks the robustness set itself. No network."""

import json
from decimal import Decimal

import pytest
import yaml
from yaml_cases import exact, load_cases

from expense_validator.classification import load_vocabulary
from expense_validator.evaluation import robustness as module
from expense_validator.evaluation.cli import robustness as cli
from expense_validator.classification.backends.llm import build_prompt
from expense_validator.evaluation.classification import ATTRIBUTES, score_lines
from expense_validator.config import ROOT, load_config
from expense_validator.ingestion import IngestionMode, iter_jsonl
from expense_validator.classification.backends.llm import classify_receipt
from expense_validator.compiler.llm_extract import cached
from expense_validator.normalization import NoOpTranslator, normalize_receipt
from expense_validator.evaluation.dataset import load_dataset
from expense_validator.evaluation.predictions import Source

CONFIG = load_config()
VOCABULARY = load_vocabulary(CONFIG.paths.taxonomy, CONFIG.paths.rule_fields)
CASES = yaml.safe_load(cli.LINES.read_text())["cases"]
ANSWER = '{"category": "other", "confidence": 0.1, "recipient_name": null, "recipient_type": null, "venue_type": null, "item_kind": null, "essential_toiletry": null}'


def _row(spec, index=0):
    """A scored row from a case's short form, so that each case names only what it is about."""
    failed = spec.get("failed", False)
    gold = spec.get("gold", "other")
    return {
        "line_id": spec.get("id", f"L{index}"),
        "gold_category": gold,
        "gold_restricted": spec.get("gold_restricted", False),
        "acceptable": [gold, *spec.get("acceptable", [])],
        "category": None if failed else spec.get("category", gold),
        "restricted": None if failed else spec.get("restricted", False),
        "confidence": None if failed else Decimal(spec.get("confidence", "0.9")),
        "error": "answer is malformed" if failed else None,
        "ambiguity": spec.get("ambiguity", "clear"),
        "challenges": [],
        "exempt": spec.get("exempt", False),
        "gold_attributes": spec.get("gold_attributes", {}),
        "predicted_attributes": {name: spec.get("predicted_attributes", {}).get(name) for name in ATTRIBUTES},
    }


def _rows(case):
    return [_row(spec, index) for index, spec in enumerate(case["rows"])]


# --- Calculations ---


@pytest.mark.parametrize("case", load_cases("robustness", "thresholds"))
def test_a_threshold_accepts_as_exc_03_does(case):
    threshold = case["threshold"]
    row = module.threshold_table(_rows(case), [Decimal(threshold)])[threshold]
    found = {"accepted": row["coverage"]["numerator"], "accepted_wrong": row["accepted_wrong"], "set_aside_right": row["set_aside_right"], "set_aside_wrong": row["set_aside_wrong"]}
    assert found == case["expected"]


@pytest.mark.parametrize("case", load_cases("robustness", "levels"))
def test_accuracy_is_counted_at_each_level(case):
    levels = module.confidence_levels(_rows(case))
    assert {level: [row["numerator"], row["denominator"]] for level, row in levels.items()} == case["expected"]


@pytest.mark.parametrize("case", load_cases("robustness", "separation"))
def test_separation_reports_only_what_it_has(case):
    found = module.confidence_separation(_rows(case))
    assert exact({name: found[name] for name in case["expected"]}) == exact(case["expected"])


@pytest.mark.parametrize("case", load_cases("robustness", "confusion"))
def test_the_confusion_matrix_loses_no_line(case):
    assert module.confusion_matrix(_rows(case)) == case["expected"]


@pytest.mark.parametrize("case", load_cases("robustness", "groups"))
def test_groups_keep_strict_and_acceptable_apart(case):
    assert module.by_group(_rows(case), lambda row: [row["ambiguity"]]) == case["expected"]


@pytest.mark.parametrize("case", load_cases("robustness", "attributes"))
def test_attributes_tell_fabricated_from_missed(case):
    assert module.attribute_scores(_rows(case))[case["field"]] == case["expected"]


@pytest.mark.parametrize("case", load_cases("robustness", "safety"))
def test_safety_names_the_lines_at_risk(case):
    found = module.safety_report(_rows(case))
    assert {name: found[name] for name in case["expected"]} == case["expected"]


def test_a_failed_line_is_wrong_on_every_headline_score():
    scores = score_lines([_row({"gold": "other", "failed": True})])
    assert scores["accuracy"]["numerator"] == 0
    assert scores["restricted_accuracy"]["numerator"] == 0
    assert scores["failures"]["numerator"] == 1


# --- The set itself ---


def test_the_labels_agree_with_the_taxonomy_and_the_engine():
    assert cli.check_gold(CONFIG, VOCABULARY) == []


def test_the_set_is_the_agreed_size_with_unique_ids_and_a_reason_for_each_label():
    ids = [case["id"] for case in CASES]
    assert 40 <= len(ids) <= 60 and len(set(ids)) == len(ids)
    assert all(case["explanation"].strip() and case["challenges"] and case["ambiguity"] in {"clear", "ambiguous", "indeterminate"} for case in CASES)


def test_no_line_is_copied_from_the_benchmark():
    benchmark = {line.description_raw.casefold() for item in iter_jsonl(ROOT / "data" / "receipts.jsonl", IngestionMode.EVALUATION) for line in item.receipt.line_items}
    assert not [case["id"] for case in CASES if case["input"]["description"].casefold() in benchmark]


def test_a_case_with_a_second_reading_says_it_is_not_clear():
    assert not [case["id"] for case in CASES if case["gold"].get("acceptable") and case["ambiguity"] == "clear"]


# --- Isolation ---


def test_only_a_cases_input_reaches_the_classifier():
    seen = []

    def complete(prompt, text):
        seen.append((prompt, text))
        return ANSWER

    rows = module.classify_cases(CASES, NoOpTranslator(), complete, VOCABULARY)
    assert len(rows) == len(seen) == len(CASES)
    allowed = {"original_description", "description_language", "english_translation", "receipt_language", "merchant_name", "merchant_category", "itemized_receipt"}
    for (_, text), case in zip(seen, CASES):
        sent = json.loads(text)
        assert set(sent) <= allowed, "a case records no amounts, so none may be sent"
        assert sent["original_description"] == case["input"]["description"]
        assert case["explanation"] not in text
        assert case["id"] not in text


def test_the_frozen_cases_record_no_amounts_to_send():
    assert not [case["id"] for case in CASES if set(case["input"]) & set(module.PLACEHOLDER_FIELDS)]


def test_only_the_placeholders_are_removed_and_the_rest_is_sent_unchanged():
    seen = []
    send = module.without_placeholders(lambda prompt, text: seen.append((prompt, text)) or ANSWER)
    line = {"original_description": "Vino tinto", "description_language": "es", "english_translation": "Red wine", "receipt_language": "es", "merchant_name": "Bar", "quantity": 1, "unit_price": "10", "currency": "USD"}
    assert send("the prompt", json.dumps(line, ensure_ascii=False)) == ANSWER
    prompt, text = seen[0]
    assert prompt == "the prompt"
    assert json.loads(text) == {name: value for name, value in line.items() if name not in {"quantity", "unit_price", "currency"}}


def test_a_line_without_placeholders_has_its_own_cache_entry(tmp_path):
    sent = []
    complete = module.without_placeholders(cached(lambda prompt, text: sent.append(text) or ANSWER, "m", tmp_path))
    case = CASES[0]
    module.classify_cases([case], NoOpTranslator(), complete, VOCABULARY)
    module.classify_cases([case], NoOpTranslator(), complete, VOCABULARY)
    assert len(sent) == 1 and "unit_price" not in sent[0]


def test_whole_receipts_still_send_their_real_amounts():
    dataset = load_dataset(cli.RECEIPTS)
    seen = []
    classify_receipt(normalize_receipt(dataset.inputs[0]), lambda prompt, text: seen.append(json.loads(text)) or ANSWER, VOCABULARY)
    printed = dataset.inputs[0].line_items[0]
    assert (seen[0]["quantity"], seen[0]["unit_price"], seen[0]["currency"]) == (printed.quantity, str(printed.unit_price), dataset.inputs[0].currency)


def test_no_explanation_or_case_is_in_the_prompt():
    prompt = build_prompt(VOCABULARY)
    assert not [case["id"] for case in CASES if case["explanation"] in prompt or case["id"] in prompt]


def test_a_line_receipt_is_built_from_the_input_alone():
    case = CASES[0]
    receipt = module.line_receipt(case["id"], case["input"])
    assert receipt.line_items[0].category is None
    assert receipt.line_items[0].restricted is None
    assert receipt.line_items[0].confidence is None


# --- Runs ---


def test_a_run_is_never_overwritten(tmp_path, monkeypatch):
    (tmp_path / "runs" / "first-run").mkdir(parents=True)
    monkeypatch.setattr(cli, "FOLDER", tmp_path)
    monkeypatch.setattr(cli, "key_spend", lambda config: pytest.fail("a run that cannot be written must not spend or read usage"))
    with pytest.raises(SystemExit, match="never overwritten"):
        cli.run("first-run", CONFIG, VOCABULARY)


@pytest.mark.parametrize("spend", [cli.SPENDING_STOP_USD, cli.SPENDING_STOP_USD + 1])
def test_a_run_stops_at_the_spending_limit_before_any_request(tmp_path, monkeypatch, spend):
    monkeypatch.setattr(cli, "FOLDER", tmp_path)
    monkeypatch.setattr(cli, "key_spend", lambda config: spend)
    monkeypatch.setattr(cli, "build_classifier", lambda config, vocabulary: pytest.fail("no classifier may be built past the limit"))
    with pytest.raises(SystemExit, match="not running"):
        cli.run("new", CONFIG, VOCABULARY)
    assert not (tmp_path / "runs").exists()


def test_the_stop_is_inside_the_brief_budget():
    assert cli.SPENDING_STOP_USD < 5


# --- Called from Python ---

RULESET, FIELDS = cli._rules(CONFIG)


def test_the_labels_are_checked_from_explicit_inputs():
    dataset = load_dataset(cli.RECEIPTS)
    wrong = {**CASES[0], "gold": {**CASES[0]["gold"], "category": "not_a_category"}}
    found = module.check_labels([wrong], dataset, cli.load_receipt_attributes(), VOCABULARY, RULESET, FIELDS)
    assert found == [f"{wrong['id']}: not_a_category is not in the taxonomy"]


def test_whole_receipts_are_scored_from_explicit_inputs():
    dataset = load_dataset(cli.RECEIPTS)
    classified = module.classify_receipts(dataset, NoOpTranslator(), lambda prompt, text: ANSWER, VOCABULARY)
    source = Source(kind="synthetic", name="stand-in", description="Every line other, at confidence 0.1")
    summary, predictions = module.score_receipts(dataset, classified, cli.load_receipt_attributes(), RULESET, FIELDS, source)
    assert summary["failed"] == [] and len(predictions.predictions) == len(classified)
    assert summary["classification_accuracy"]["denominator"] == len(classified)
    assert {receipt["receipt_id"] for receipt in summary["receipts"]} == set(dataset.answers)
