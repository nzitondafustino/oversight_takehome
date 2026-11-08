"""Test the public validator using replay and fake models without network access."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import ValidationError

import expense_validator
from expense_validator import (
    BatchResult,
    ConfigurationError,
    ExpenseValidator,
    InvalidInputError,
    ReceiptResult,
    ReplayAnswerMissing,
    ValidatorError,
    ValidatorSettings,
)
from expense_validator.config import ROOT
from expense_validator.normalization import NoOpTranslator
from expense_validator.run_receipts import OUTPUT, load_receipts

CONFIG = ROOT / "config.yaml"
REPLAY = ROOT / "evaluation" / "final" / "replay"
RECEIPTS = {receipt.receipt_id: receipt for receipt in load_receipts()}
RAW = {json.loads(line)["receipt_id"]: json.loads(line) for line in (ROOT / "data" / "receipts.jsonl").read_text().splitlines()}
ANSWER = '{"category": "food_beverage", "confidence": 0.7, "recipient_name": null, "recipient_type": null, "venue_type": null, "item_kind": null, "essential_toiletry": null}'


@pytest.fixture(scope="module")
def replay():
    return ExpenseValidator.from_config(CONFIG, mode="replay", cache_dir=REPLAY)


def _rules(result):
    return [exception.rule_id for exception in result.exceptions]


def _without_answers(record):
    """A raw receipt as a client would send it: the dataset's answers removed."""
    clean = {name: value for name, value in record.items() if name not in {"labels", "notes"}}
    clean["line_items"] = [{name: value for name, value in line.items() if name != "labels"} for line in record["line_items"]]
    return clean


# --- The public surface ---


def test_the_package_exports_the_api():
    for name in ("ExpenseValidator", "ValidatorSettings", "BatchResult", "ReceiptResult", "ValidatorError", "ReplayAnswerMissing", "Receipt"):
        assert hasattr(expense_validator, name)
    assert set(expense_validator.__all__) >= {"ExpenseValidator", "ValidatorSettings", "BatchResult", "ValidatorError"}
    with pytest.raises(AttributeError):
        expense_validator.NotAThing


def test_replay_reproduces_the_committed_output(replay):
    batch = replay.validate_batch(list(RECEIPTS.values()), context_complete=True)
    assert isinstance(batch, BatchResult) and all(isinstance(result, ReceiptResult) for result in batch.results)
    assert "".join(json.dumps(result.to_record(), ensure_ascii=False) + "\n" for result in batch.results) == OUTPUT.read_text()


def test_results_are_typed_frozen_and_carry_provenance(replay):
    result = replay.validate_batch([RECEIPTS["RCP-014"]], context_complete=True).for_receipt("RCP-014")
    assert result.disposition == "flag" and _rules(result) == ["ALC-03", "DOC-03", "HOUSE-01"]
    assert result.provenance.mode == "replay" and result.provenance.receipts_in_context == 1
    assert len(result.provenance.ruleset_sha256) == 64
    with pytest.raises(ValidationError):
        result.disposition = "pass"
    assert "provenance" not in result.to_record()


def test_raw_receipts_are_accepted_and_answers_in_them_refused(replay):
    clean = replay.validate_batch([_without_answers(RAW["RCP-014"])], context_complete=True)
    assert clean.results[0].to_record() == replay.validate_batch([RECEIPTS["RCP-014"]], context_complete=True).results[0].to_record()
    with pytest.raises(InvalidInputError, match="evaluation-only fields"):
        replay.validate_batch([RAW["RCP-014"]], context_complete=True)


# --- One context per call ---


def test_one_calls_receipts_never_reach_anothers_context(replay):
    together = replay.validate_batch([RECEIPTS["RCP-020"], RECEIPTS["RCP-021"]], context_complete=True)
    assert all("TIME-03" in _rules(result) for result in together.results)
    first = replay.validate_batch([RECEIPTS["RCP-020"]], context_complete=True)
    second = replay.validate_batch([RECEIPTS["RCP-021"]], context_complete=True)
    assert "TIME-03" not in _rules(first.results[0]) and "TIME-03" not in _rules(second.results[0])
    assert replay.validate_batch([RECEIPTS["RCP-020"]], context_complete=True) == first, "an earlier call left nothing behind"


def test_a_partial_context_neither_confirms_nor_clears_a_total_across_receipts(replay):
    complete = replay.validate_batch([RECEIPTS["RCP-020"]], context_complete=True).results[0]
    partial = replay.validate_batch([RECEIPTS["RCP-020"]], context_complete=False).results[0]
    assert complete.disposition == "pass" and complete.decided
    # Nothing found is a pass under EXC-01, but provisional: not decided, with what is open still named.
    assert partial.disposition == "pass" and not partial.decided
    assert {question.rule_id for question in partial.open_questions} == {"MEAL-01", "TIME-03"}
    assert [reason.code for reason in partial.disposition_reasons] == ["nothing_found", "incomplete_evidence"]
    assert partial.provenance.context_complete is False


@pytest.mark.parametrize("complete", [True, False])
def test_the_api_reports_the_engines_disposition_for_every_receipt(replay, complete):
    batch = replay.validate_batch(list(RECEIPTS.values()), context_complete=complete)
    classified = []
    for receipt in RECEIPTS.values():
        lines = batch.for_receipt(receipt.receipt_id).line_items
        classified.append(receipt.with_classifications({line.line_id: {"category": line.category, "restricted": line.restricted, "confidence": None if line.confidence is None else str(line.confidence), **(line.attributes or {})} for line in lines}))
    run = replay.engine.evaluate(classified, complete=complete)
    for result in batch.results:
        core = run.result_for(result.receipt_id)
        assert (result.disposition, result.decided) == (core.disposition, core.decided), result.receipt_id
        assert [(reason.code, reason.basis, reason.rule_ids) for reason in result.disposition_reasons] == [(reason.code, reason.basis, reason.rule_ids) for reason in core.reasons]
        assert [question.rule_id for question in result.open_questions] == [question.rule_id for question in core.open_questions]
        assert [item.rule_id for item in result.exceptions] == [group.rule_id for group in core.active_exceptions]


def test_context_is_complete_unless_said_otherwise_and_must_be_a_boolean(replay):
    left_out = replay.validate_batch([RECEIPTS["RCP-001"]])
    assert left_out.context_complete is True and left_out == replay.validate_batch([RECEIPTS["RCP-001"]], context_complete=True)
    with pytest.raises(InvalidInputError, match="True or False"):
        replay.validate_batch([RECEIPTS["RCP-001"]], context_complete="yes")


def test_concurrent_calls_give_the_same_results_as_one_at_a_time(replay):
    jobs = [("RCP-020", "RCP-021", "RCP-014"), ("RCP-020", "RCP-013"), ("RCP-022", "RCP-016", "RCP-025")] * 4
    expected = {ids: replay.validate_batch([RECEIPTS[i] for i in ids], context_complete=True) for ids in jobs[:3]}
    with ThreadPoolExecutor(max_workers=8) as pool:
        found = list(pool.map(lambda ids: replay.validate_batch([RECEIPTS[i] for i in ids], context_complete=True), jobs))
    assert all(batch == expected[ids] for ids, batch in zip(jobs, found))


def test_the_validator_keeps_no_request_state(replay):
    before = dict(vars(replay))
    replay.validate_batch(list(RECEIPTS.values())[:3], context_complete=True)
    assert vars(replay) == before


# --- Modes ---


def test_replay_calls_no_model_and_fails_on_a_missing_answer(tmp_path):
    for path in REPLAY.glob("*.txt"):
        (tmp_path / path.name).write_text(path.read_text())
    sorted(tmp_path.glob("*.txt"))[0].unlink()
    before = sorted(path.name for path in tmp_path.iterdir())
    validator = ExpenseValidator.from_config(CONFIG, mode="replay", cache_dir=tmp_path)
    with pytest.raises(ReplayAnswerMissing) as error:
        validator.validate_batch(list(RECEIPTS.values()), context_complete=True)
    assert error.value.missing and sorted(path.name for path in tmp_path.iterdir()) == before


def test_replay_takes_no_model_and_needs_its_answers():
    with pytest.raises(ConfigurationError, match="takes no classifier_model"):
        ExpenseValidator.from_config(CONFIG, mode="replay", cache_dir=REPLAY, classifier_model=lambda prompt, text: ANSWER)
    with pytest.raises(ConfigurationError, match="needs cache_dir"):
        ExpenseValidator.from_config(CONFIG, mode="replay", cache_dir=Path("/nonexistent/replay"))


def test_live_mode_calls_the_given_model_and_caches_only_where_told(tmp_path):
    calls = []

    def model(prompt, text):
        calls.append(text)
        return ANSWER

    uncached = ExpenseValidator.from_config(CONFIG, mode="live", cache_dir=None, classifier_model=model, translator=NoOpTranslator())
    uncached.validate_batch([RECEIPTS["RCP-001"]], context_complete=True)
    uncached.validate_batch([RECEIPTS["RCP-001"]], context_complete=True)
    assert len(calls) == 2 * len(RECEIPTS["RCP-001"].line_items)
    cache = tmp_path / "cache"
    cached = ExpenseValidator.from_config(CONFIG, mode="live", cache_dir=cache, classifier_model=model, translator=NoOpTranslator())
    first = cached.validate_batch([RECEIPTS["RCP-001"]], context_complete=True).results[0]
    second = cached.validate_batch([RECEIPTS["RCP-001"]], context_complete=True).results[0]
    assert len(calls) == 3 * len(RECEIPTS["RCP-001"].line_items) and len(list(cache.glob("*.txt"))) == len(RECEIPTS["RCP-001"].line_items)
    assert first.metadata.latency_ms is not None and second.metadata.latency_ms is None
    assert not list(cache.glob("*.tmp")), "an answer is renamed into place, never left half written"


def test_a_model_failing_on_a_line_is_a_result_not_an_error():
    validator = ExpenseValidator.from_config(CONFIG, mode="live", cache_dir=None, classifier_model=lambda prompt, text: "not json", translator=NoOpTranslator())
    result = validator.validate_batch([RECEIPTS["RCP-001"]], context_complete=True).results[0]
    assert all(line.category is None and line.classification_error for line in result.line_items)
    assert result.disposition == "review" and any(signal.rule_id == "EXC-03" for signal in result.review_signals)


def test_live_mode_without_a_key_or_a_model_is_refused(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    settings = ValidatorSettings.from_config(CONFIG, mode="live", cache_dir=tmp_path).model_copy(update={"openrouter_api_key": None})
    with pytest.raises(ConfigurationError, match="needs OPENROUTER_API_KEY"):
        ExpenseValidator(settings)


def test_deterministic_mode_evaluates_classified_receipts_without_a_model(replay):
    replayed = replay.validate_batch(list(RECEIPTS.values()), context_complete=True)
    classified = []
    for receipt in RECEIPTS.values():
        lines = {line.line_id: line for line in replayed.for_receipt(receipt.receipt_id).line_items}
        classified.append(receipt.with_classifications({line_id: {"category": line.category, "restricted": line.restricted, "confidence": str(line.confidence), **(line.attributes or {})} for line_id, line in lines.items()}))
    deterministic = ExpenseValidator.from_config(CONFIG, mode="deterministic").validate_batch(classified, context_complete=True)
    for found, expected in zip(deterministic.results, replayed.results):
        assert (found.disposition, found.exceptions, found.open_questions, found.decided) == (expected.disposition, expected.exceptions, expected.open_questions, expected.decided)
        assert found.metadata.model is None and found.metadata.prompt_sha256 is None and found.metadata.replayed is False


def test_deterministic_mode_reads_classified_records_and_flags_unclassified_lines():
    validator = ExpenseValidator.from_config(CONFIG, mode="deterministic")
    record = _without_answers(RAW["RCP-024"])
    record["line_items"][0].update(category="tobacco", restricted=True, confidence="0.9")
    result = validator.validate_batch([record], context_complete=True).results[0]
    assert "PERS-03" in _rules(result)
    assert any(item.line_id == "RCP-024-L2" and item.state == "missing" for item in result.uncertainty), "an unclassified line is uncertain, not guessed"
    with pytest.raises(ConfigurationError, match="takes no classifier_model"):
        ExpenseValidator.from_config(CONFIG, mode="deterministic", classifier_model=lambda prompt, text: ANSWER)


# --- Errors ---


def test_a_receipt_given_twice_is_refused(replay):
    with pytest.raises(InvalidInputError, match="more than once"):
        replay.validate_batch([RECEIPTS["RCP-001"], RECEIPTS["RCP-001"]], context_complete=True)


def test_a_receipt_that_is_not_one_is_refused(replay):
    with pytest.raises(InvalidInputError, match="must be a Receipt or a mapping"):
        replay.validate_batch(["RCP-001"], context_complete=True)
    with pytest.raises(InvalidInputError, match="invalid receipt"):
        replay.validate_batch([{"receipt_id": "X", "line_items": []}], context_complete=True)


def test_a_classified_receipt_is_refused_where_a_model_classifies(replay):
    classified = RECEIPTS["RCP-001"].with_classifications({RECEIPTS["RCP-001"].line_items[0].line_id: {"category": "food_beverage"}})
    with pytest.raises(InvalidInputError, match="normalization comes before classification"):
        replay.validate_batch([classified], context_complete=True)


def test_the_default_cache_is_the_configs_and_none_means_no_cache():
    assert ValidatorSettings.from_config(CONFIG, mode="live").cache_dir == ROOT / ".cache" / "llm"
    assert ValidatorSettings.from_config(CONFIG, mode="live", cache_dir=None).cache_dir is None
    assert ValidatorSettings.from_config(CONFIG, mode="deterministic").cache_dir is None


def test_bad_configuration_is_a_configuration_error(tmp_path):
    with pytest.raises(ConfigurationError, match="no config file"):
        ExpenseValidator.from_config(tmp_path / "missing.yaml", mode="deterministic")
    settings = ValidatorSettings.from_config(CONFIG, mode="deterministic")
    with pytest.raises(ConfigurationError, match="ruleset_path"):
        ExpenseValidator(settings.model_copy(update={"ruleset_path": tmp_path / "none.json"}))
    broken = tmp_path / "ruleset.json"
    broken.write_text("{}")
    with pytest.raises(ConfigurationError, match="cannot be loaded"):
        ExpenseValidator(settings.model_copy(update={"ruleset_path": broken}))


def test_every_error_is_a_validator_error():
    for error in (ConfigurationError, InvalidInputError, ReplayAnswerMissing, expense_validator.EvidenceError):
        assert issubclass(error, ValidatorError)


def test_settings_never_show_the_key():
    settings = ValidatorSettings.model_validate({**ValidatorSettings.from_config(CONFIG, mode="live", cache_dir=REPLAY).model_dump(), "openrouter_api_key": "sk-or-secret"})
    assert "sk-or-secret" not in repr(settings) and "sk-or-secret" not in str(settings.model_dump())
    assert ValidatorSettings.from_config(CONFIG, mode="replay", cache_dir=REPLAY).openrouter_api_key is None
