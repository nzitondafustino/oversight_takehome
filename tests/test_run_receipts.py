"""Test end-to-end receipt exports with replay recordings or fake model answers."""

import json
import shutil
from decimal import Decimal

import pytest

from expense_validator import run_receipts as runner
from expense_validator.classification import load_vocabulary
from expense_validator.classification.backends.llm import CONFIDENCE_LEVELS, build_classifier, build_prompt
from expense_validator.validator import ExpenseValidator
from expense_validator.compiler.validate import read_fields
from expense_validator.config import ROOT, load_config
from expense_validator.normalization import NoOpTranslator, build_translator
from expense_validator.policy.schema import RuleSet

CONFIG = load_config()
REPLAY = ROOT / "evaluation" / "final" / "replay"
VOCABULARY = load_vocabulary(CONFIG.paths.taxonomy, CONFIG.paths.rule_fields)
RULESET = RuleSet.from_json(CONFIG.paths.ruleset.read_text())
FIELDS = read_fields(CONFIG.paths.rule_fields.read_text())
OUTPUT = [json.loads(line) for line in runner.OUTPUT.read_text().splitlines()]
BY_ID = {output["receipt_id"]: output for output in OUTPUT}
RECEIPTS = {receipt.receipt_id: receipt for receipt in runner.load_receipts()}

TOP = ["receipt_id", "disposition", "line_items", "exceptions", "review_signals", "metadata"]  # the brief's, exactly
LINE = ["line_id", "description_raw", "category", "restricted", "confidence"]
LINE_EXTENSIONS = {"english_translation", "attributes", "classification_error", "classification_details"}  # in metadata.line_items
EXCEPTION = ["rule_id", "policy_doc", "severity", "line_ids", "evidence", "explanation"]
METADATA = {"model", "retriever", "latency_ms", "translation_model", "prompt_sha256", "ruleset_version", "replayed", "classification_backend",
            "decided", "disposition_reasons", "open_questions", "uncertainty", "source_quotes", "line_items"}
ANSWER_KEYS = {"labels", "notes", "expected_exceptions", "gold_disposition", "ground_truth", "reasoning"}


def _validator(mode="replay", cache_dir=REPLAY, **injected):
    return ExpenseValidator.from_config(ROOT / "config.yaml", mode=mode, cache_dir=cache_dir, **injected)


def _all_keys(value):
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in _all_keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in _all_keys(item)}
    return set()


# --- The committed output ---


def test_one_object_for_each_of_the_32_receipts_sorted_by_id():
    ids = [output["receipt_id"] for output in OUTPUT]
    assert len(ids) == 32 == len(set(ids))
    assert ids == sorted(RECEIPTS) == sorted(ids)


def test_every_object_has_exactly_the_brief_fields_and_the_extensions_only_in_metadata():
    for output in OUTPUT:
        assert list(output) == TOP, output["receipt_id"]
        assert output["disposition"] in {"flag", "review", "pass"}
        assert isinstance(output["metadata"]["decided"], bool)
        assert set(output["metadata"]) == METADATA
        assert [(item["rule_id"], item["line_ids"]) for item in output["metadata"]["source_quotes"]] == [(item["rule_id"], item["line_ids"]) for item in output["exceptions"]]


def test_line_items_keep_the_printed_text_and_hold_no_invented_reasoning():
    levels = {float(level) for level in CONFIDENCE_LEVELS}
    for output in OUTPUT:
        receipt = RECEIPTS[output["receipt_id"]]
        assert [line["line_id"] for line in output["line_items"]] == [item.line_id for item in receipt.line_items]
        for line, item in zip(output["line_items"], receipt.line_items):
            assert list(line) == LINE
            extensions = output["metadata"]["line_items"].get(item.line_id, {})
            assert set(extensions) <= LINE_EXTENSIONS
            assert line["description_raw"] == item.description_raw
            assert ("english_translation" in extensions) == (item.language != "en")
            assert line["category"] in VOCABULARY.categories and line["restricted"] is VOCABULARY.categories[line["category"]].restricted
            assert line["confidence"] in levels
    assert not ANSWER_KEYS & _all_keys(OUTPUT)


def test_exceptions_cite_real_lines_and_the_policys_own_words():
    for output in OUTPUT:
        line_ids = {item.line_id for item in RECEIPTS[output["receipt_id"]].line_items}
        for exception in output["exceptions"]:
            assert list(exception) == EXCEPTION
            assert set(exception["line_ids"]) <= line_ids
            assert exception["severity"] in {"critical", "high", "medium", "low"}
            policy = " ".join((CONFIG.paths.policy_docs / exception["policy_doc"]).read_text().replace("*", "").split())
            assert exception["evidence"] and exception["evidence"] in policy, exception["rule_id"]
            assert exception["explanation"].strip()


def test_review_signals_have_a_reason_and_exc_03_carries_its_details():
    for output in OUTPUT:
        assert all(set(signal) == {"rule_id", "reason"} and signal["reason"].strip() for signal in output["review_signals"])
        has_exc03 = any(signal["rule_id"] == "EXC-03" for signal in output["review_signals"])
        assert has_exc03 == bool(output["metadata"]["uncertainty"]), output["receipt_id"]


def test_an_open_question_keeps_the_violation_already_established():
    rcp013 = BY_ID["RCP-013"]
    assert rcp013["disposition"] == "flag" and rcp013["metadata"]["decided"] is False
    assert [exception["rule_id"] for exception in rcp013["exceptions"]] == ["GIFT-02"]
    assert [question["rule_id"] for question in rcp013["metadata"]["open_questions"]] == ["GIFT-04"]
    assert {(item["line_id"], item["field"], item["state"]) for item in rcp013["metadata"]["uncertainty"]} == {("RCP-013-L1", "recipient_type", "missing"), ("RCP-013-L2", "recipient_type", "missing")}


def test_a_replaced_finding_is_never_listed_as_an_exception():
    for receipt_id in ("RCP-020", "RCP-021"):
        rules = [exception["rule_id"] for exception in BY_ID[receipt_id]["exceptions"]]
        assert "TIME-03" in rules and "MEAL-01" not in rules, receipt_id


def test_metadata_says_only_what_is_known():
    prompt = __import__("hashlib").sha256(build_prompt(VOCABULARY).encode()).hexdigest()
    for output in OUTPUT:
        metadata = output["metadata"]
        assert metadata["retriever"] == "none" and metadata["replayed"] is True and metadata["latency_ms"] is None
        assert metadata["model"] == CONFIG.classification.model and metadata["translation_model"] == CONFIG.translation.model
        assert metadata["prompt_sha256"] == prompt and metadata["ruleset_version"] == RULESET.ruleset_version


# --- Replay ---


def test_replaying_the_saved_answers_rebuilds_the_committed_file_byte_for_byte(tmp_path):
    out = tmp_path / "receipts.jsonl"
    assert runner.main(["--replay", str(REPLAY), "--out", str(out)]) == 0
    assert out.read_text() == runner.OUTPUT.read_text()


def test_a_missing_answer_writes_nothing_and_calls_no_model(tmp_path, capsys):
    replay = tmp_path / "replay"
    shutil.copytree(REPLAY, replay)
    sorted(replay.glob("*.txt"))[0].unlink()
    out = tmp_path / "receipts.jsonl"
    assert runner.main(["--replay", str(replay), "--out", str(out)]) == 1
    assert not out.exists() and "answers are not in" in capsys.readouterr().err  # a missing translation also changes what is classified


def test_replay_needs_no_key(tmp_path):
    keyless = CONFIG.model_copy(update={"openrouter_api_key": None, "paths": CONFIG.paths.model_copy(update={"llm_cache": REPLAY})})
    build_classifier(keyless, VOCABULARY, replay=[])
    build_translator(keyless, replay=[])


# --- Context across receipts ---


def test_split_transactions_need_the_other_receipts():
    validator = _validator()
    together = validator.validate_batch(list(RECEIPTS.values()), context_complete=True)
    for receipt_id in ("RCP-020", "RCP-021"):
        assert "TIME-03" in [exception.rule_id for exception in together.for_receipt(receipt_id).exceptions]
        alone = validator.validate_batch([RECEIPTS[receipt_id]], context_complete=True).for_receipt(receipt_id)
        assert "TIME-03" not in [exception.rule_id for exception in alone.exceptions], receipt_id


def test_the_run_evaluates_every_receipt_in_one_context(monkeypatch):
    from expense_validator.engine import policy_engine

    calls = []
    original = policy_engine.evaluation.evaluate_receipts

    def spy(receipts, ruleset, fields, *, complete):
        receipts = list(receipts)
        calls.append((len(receipts), complete))
        return original(receipts, ruleset, fields, complete=complete)

    monkeypatch.setattr(policy_engine.evaluation, "evaluate_receipts", spy)
    assert runner.main(["--replay", str(REPLAY), "--out", str(ROOT / "evaluation" / "final" / "receipts.jsonl")]) == 0
    assert calls == [(32, True)]


# --- Latency, and what reaches the model ---


ANSWER = '{"category": "food_beverage", "confidence": 0.7, "recipient_name": null, "recipient_type": null, "venue_type": null, "item_kind": null, "essential_toiletry": null}'


def test_latency_is_measured_only_when_a_model_was_asked(tmp_path):
    seen = []

    def model(prompt, text):
        seen.append(text)
        return ANSWER

    validator = _validator("live", tmp_path, classifier_model=model, translator=NoOpTranslator())
    first = validator.validate_batch([RECEIPTS["RCP-001"]], context_complete=True).results[0]
    again = validator.validate_batch([RECEIPTS["RCP-001"]], context_complete=True).results[0]
    assert isinstance(first.metadata.latency_ms, int) and again.metadata.latency_ms is None
    assert len(seen) == len(RECEIPTS["RCP-001"].line_items)


def test_the_classifier_sees_receipts_without_their_answers():
    texts = []

    def model(prompt, text):
        texts.append(text)
        return ANSWER

    _validator("live", None, classifier_model=model, translator=NoOpTranslator()).validate_batch(list(RECEIPTS.values()), context_complete=True)
    assert len(texts) == 79
    assert not {"labels", "notes", "expected_exceptions", "submitted_expense_type"} & {key for text in texts for key in json.loads(text)}
    assert all(not hasattr(receipt, "labels") for receipt in RECEIPTS.values())


def test_the_committed_answers_are_for_this_prompt():
    assert BY_ID["RCP-001"]["metadata"]["prompt_sha256"] == __import__("hashlib").sha256(build_prompt(VOCABULARY).encode()).hexdigest()
    assert Decimal(str(BY_ID["RCP-001"]["line_items"][0]["confidence"])) in {Decimal(str(level)) for level in CONFIDENCE_LEVELS}
