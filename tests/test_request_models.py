"""Test request-specific model selection with a shared validator and fake providers."""

import json
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
import yaml

from expense_validator import ConfigurationError, ExpenseValidator, ReplayAnswerMissing
from expense_validator import run_receipts as runner
from expense_validator import validator as validator_module
from expense_validator.classification.backends import llm as llm_module
from expense_validator.config import ROOT, load_config
from expense_validator.normalization import NoOpTranslator

CONFIG = ROOT / "config.yaml"
REPLAY = ROOT / "evaluation" / "final" / "replay"
DEFAULTS = load_config()
RECEIPTS = {receipt.receipt_id: receipt for receipt in runner.load_receipts()}
ENGLISH, FRENCH = RECEIPTS["RCP-001"], RECEIPTS["RCP-008"]
CATEGORY_OF = {"m/food": "food_beverage", "m/other": "other", "m/office": "office_supplies"}


def _answer(category):
    return json.dumps({"category": category, "confidence": 0.7, "recipient_name": None, "recipient_type": None, "venue_type": None, "item_kind": None, "essential_toiletry": None})


@pytest.fixture
def openrouter(monkeypatch):
    calls, lock = [], threading.Lock()

    def classification(client, model, temperature, vocabulary):
        def complete(prompt, text):
            with lock:
                calls.append(("classification", model))
            return _answer(CATEGORY_OF.get(model, "food_beverage"))

        return complete

    def translation(client, model, temperature):
        def complete(prompt, text):
            with lock:
                calls.append(("translation", model))
            return "Mineral water 50cl" if "50cl" in text else "Salad"

        return complete

    monkeypatch.setattr(llm_module, "openrouter_completion", classification)
    monkeypatch.setattr(validator_module, "translation_completion", translation)
    monkeypatch.setattr(validator_module, "OpenRouter", lambda **settings: object())
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    return calls


def _models(calls, kind):
    return {model for found, model in calls if found == kind}


def _categories(batch):
    return {line.category for result in batch.results for line in result.line_items}


# --- Precedence ---


def test_a_request_model_comes_before_the_validators_and_then_the_configs(openrouter, tmp_path):
    configured = ExpenseValidator.from_config(CONFIG, mode="live", cache_dir=None)
    initialized = ExpenseValidator.from_config(CONFIG, mode="live", cache_dir=None, classification_model="m/office")
    assert configured.validate_batch([ENGLISH], context_complete=True).results[0].metadata.model == DEFAULTS.classification.model
    assert initialized.validate_batch([ENGLISH], context_complete=True).results[0].metadata.model == "m/office"
    requested = initialized.validate_batch([ENGLISH], context_complete=True, classification_model="m/other")
    assert requested.results[0].metadata.model == "m/other" and _categories(requested) == {"other"}
    assert initialized.validate_batch([ENGLISH], context_complete=True).results[0].metadata.model == "m/office", "the request left the validator as it was"


def test_classification_only_and_translation_only_requests(openrouter):
    validator = ExpenseValidator.from_config(CONFIG, mode="live", cache_dir=None)
    only_classification = validator.validate_batch([FRENCH], context_complete=True, classification_model="m/other").results[0].metadata
    assert (only_classification.model, only_classification.translation_model) == ("m/other", DEFAULTS.translation.model)
    openrouter.clear()
    only_translation = validator.validate_batch([FRENCH], context_complete=True, translation_model="t/other").results[0].metadata
    assert (only_translation.model, only_translation.translation_model) == (DEFAULTS.classification.model, "t/other")
    assert _models(openrouter, "translation") == {"t/other"} and _models(openrouter, "classification") == {DEFAULTS.classification.model}


# --- One validator, many requests ---


def test_consecutive_requests_with_different_models_leave_nothing_shared_changed(openrouter):
    validator = ExpenseValidator.from_config(CONFIG, mode="live", cache_dir=None)
    settings, engine = validator.settings, validator.engine
    first = validator.validate_batch([ENGLISH], context_complete=True, classification_model="m/food", translation_model="t/one")
    second = validator.validate_batch([ENGLISH], context_complete=True, classification_model="m/other", translation_model="t/two")
    assert (_categories(first), _categories(second)) == ({"food_beverage"}, {"other"})
    assert first.results[0].metadata.model == "m/food" and second.results[0].metadata.model == "m/other"
    assert validator.settings is settings and validator.engine is engine
    assert (validator.settings.classification_model, validator.settings.translation_model) == (DEFAULTS.classification.model, DEFAULTS.translation.model)


def test_concurrent_requests_each_get_their_own_models(openrouter, tmp_path):
    validator = ExpenseValidator.from_config(CONFIG, mode="live", cache_dir=tmp_path / "shared-cache")
    jobs = [(model, translation) for model in CATEGORY_OF for translation in ("t/one", "t/two")] * 3

    def run(job):
        model, translation = job
        return job, validator.validate_batch([ENGLISH, FRENCH], context_complete=True, classification_model=model, translation_model=translation)

    with ThreadPoolExecutor(max_workers=8) as pool:
        for (model, translation), batch in pool.map(run, jobs):
            assert _categories(batch) == {CATEGORY_OF[model]}, model
            assert {(result.metadata.model, result.metadata.translation_model) for result in batch.results} == {(model, translation)}
    assert not list((tmp_path / "shared-cache").glob("*.tmp"))


def test_each_requests_model_has_its_own_cache_entries(openrouter, tmp_path):
    validator = ExpenseValidator.from_config(CONFIG, mode="live", cache_dir=tmp_path / "cache")
    lines = len(ENGLISH.line_items)
    validator.validate_batch([ENGLISH], context_complete=True, classification_model="m/food")
    other = validator.validate_batch([ENGLISH], context_complete=True, classification_model="m/other")
    assert _categories(other) == {"other"} and len(openrouter) == 2 * lines
    again = validator.validate_batch([ENGLISH], context_complete=True, classification_model="m/food")
    assert _categories(again) == {"food_beverage"} and len(openrouter) == 2 * lines and again.results[0].metadata.latency_ms is None


# --- Metadata, provenance and translation ---


def test_metadata_and_provenance_report_what_the_request_used(openrouter):
    validator = ExpenseValidator.from_config(CONFIG, mode="live", cache_dir=None)
    chosen = validator.validate_batch([FRENCH], context_complete=True, translation_model="t/other").results[0]
    assert chosen.provenance.model_overrides == ("translation_model",)
    assert chosen.metadata.translation_model == "t/other" and chosen.metadata.model == DEFAULTS.classification.model
    plain = validator.validate_batch([FRENCH], context_complete=True).results[0]
    assert plain.provenance.model_overrides == ()
    assert "provenance" not in chosen.to_record()


def test_a_translation_model_does_not_turn_translation_on(openrouter, tmp_path):
    settings = yaml.safe_load(CONFIG.read_text())
    settings["translation"]["enabled"] = False
    folder = tmp_path / "project"
    shutil.copytree(ROOT / "rules", folder / "rules")
    shutil.copytree(ROOT / "data", folder / "data")
    (folder / "config.yaml").write_text(yaml.safe_dump(settings))
    validator = ExpenseValidator.from_config(folder / "config.yaml", mode="live", cache_dir=None)
    result = validator.validate_batch([FRENCH], context_complete=True, translation_model="t/other").results[0]
    assert _models(openrouter, "translation") == set() and result.metadata.translation_model is None
    assert all(line.english_translation is None for line in result.line_items)


# --- Replay and deterministic mode ---


@pytest.mark.parametrize("override", [{"classification_model": "m/never-saved"}, {"translation_model": "t/never-saved"}])
def test_replay_with_a_requested_model_it_has_no_answers_for_sends_nothing(openrouter, override):
    validator = ExpenseValidator.from_config(CONFIG, mode="replay", cache_dir=REPLAY)
    with pytest.raises(ReplayAnswerMissing):
        validator.validate_batch([FRENCH], context_complete=True, **override)
    assert openrouter == []
    assert validator.validate_batch([FRENCH], context_complete=True).results[0].metadata.model == DEFAULTS.classification.model


def test_requesting_the_configured_models_by_name_changes_no_output():
    validator = ExpenseValidator.from_config(CONFIG, mode="replay", cache_dir=REPLAY)
    default = validator.validate_batch(list(RECEIPTS.values()), context_complete=True)
    named = validator.validate_batch(list(RECEIPTS.values()), context_complete=True, classification_model=DEFAULTS.classification.model, translation_model=DEFAULTS.translation.model)
    assert [result.to_record() for result in named.results] == [result.to_record() for result in default.results]
    assert "".join(json.dumps(result.to_record(), ensure_ascii=False) + "\n" for result in default.results) == runner.OUTPUT.read_text()


@pytest.mark.parametrize("override", [{"classification_model": "m/any"}, {"translation_model": "t/any"}])
def test_deterministic_mode_refuses_a_model_choice(override):
    validator = ExpenseValidator.from_config(CONFIG, mode="deterministic")
    with pytest.raises(ConfigurationError, match="runs no model"):
        validator.validate_batch([ENGLISH], context_complete=True, **override)
    assert validator.validate_batch([ENGLISH], context_complete=True).results[0].metadata.model is None


@pytest.mark.parametrize("value", ["", "   ", 42, ["m/food"], lambda prompt, text: "{}"])
def test_a_requested_model_must_be_a_model_id(openrouter, value):
    validator = ExpenseValidator.from_config(CONFIG, mode="live", cache_dir=None)
    for name in ("classification_model", "translation_model"):
        with pytest.raises(ConfigurationError, match="must be a model ID"):
            validator.validate_batch([ENGLISH], context_complete=True, **{name: value})
    assert openrouter == []


# --- Objects passed in ---


def test_an_injected_classifier_cannot_be_replaced_by_a_requested_model(openrouter):
    validator = ExpenseValidator.from_config(CONFIG, mode="live", cache_dir=None, classifier_model=lambda prompt, text: _answer("gift"))
    with pytest.raises(ConfigurationError, match="classifier_model it was given"):
        validator.validate_batch([ENGLISH], context_complete=True, classification_model="m/other")
    translated = validator.validate_batch([FRENCH], context_complete=True, translation_model="t/other")
    assert _models(openrouter, "translation") == {"t/other"} and _categories(translated) == {"gift"}


def test_an_injected_translator_cannot_be_replaced_by_a_requested_model(openrouter):
    validator = ExpenseValidator.from_config(CONFIG, mode="live", cache_dir=None, translator=NoOpTranslator())
    with pytest.raises(ConfigurationError, match="translator it was given"):
        validator.validate_batch([FRENCH], context_complete=True, translation_model="t/other")
    result = validator.validate_batch([ENGLISH], context_complete=True, classification_model="m/office")
    assert _categories(result) == {"office_supplies"} and _models(openrouter, "classification") == {"m/office"}


# --- The FastAPI example's model choice ---


def _example():
    import importlib.util

    spec = importlib.util.spec_from_file_location("fastapi_app", ROOT / "examples" / "fastapi_app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _body(receipt, **models):
    record = receipt.model_dump(mode="json")
    record["line_items"] = [{name: value for name, value in line.items() if name not in {"category", "restricted", "confidence", "recipient_name", "recipient_type", "venue_type", "item_kind", "essential_toiletry"}} for line in record["line_items"]]
    return {"context_complete": True, "receipts": [record], **models}


def test_the_example_passes_a_ui_model_choice_through_and_enforces_its_allowlist(openrouter):
    example = _example()
    validator = ExpenseValidator.from_config(CONFIG, mode="live", cache_dir=None)
    status, body = example.validate_request(validator, _body(ENGLISH, classification_model="m/other"), frozenset({"m/other"}))
    assert status == 200 and body["results"][0]["metadata"]["model"] == "m/other"
    status, body = example.validate_request(validator, _body(ENGLISH, classification_model="m/expensive"), frozenset({"m/other"}))
    assert status == 422 and "not an allowed model" in body["error"]
    assert example.validate_request(validator, _body(ENGLISH, classification_model=" "))[0] == 422
    deterministic = ExpenseValidator.from_config(CONFIG, mode="deterministic")
    assert example.validate_request(deterministic, _body(ENGLISH, classification_model="m/other"))[0] == 422
