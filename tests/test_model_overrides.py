"""Test independent model overrides with fake OpenRouter completions."""

import json
import shutil

import pytest
import yaml
from pydantic import SecretStr

from expense_validator import ConfigurationError, ExpenseValidator, ReplayAnswerMissing, ValidatorSettings
from expense_validator import run_receipts as runner
from expense_validator import validator as validator_module
from expense_validator.classification.backends import llm as llm_module
from expense_validator.config import ROOT, load_config

CONFIG = ROOT / "config.yaml"
REPLAY = ROOT / "evaluation" / "final" / "replay"
DEFAULTS = load_config()
RECEIPTS = {receipt.receipt_id: receipt for receipt in runner.load_receipts()}
FRENCH = RECEIPTS["RCP-008"]  # three French lines, so both models are asked
ANSWER = '{"category": "food_beverage", "confidence": 0.7, "recipient_name": null, "recipient_type": null, "venue_type": null, "item_kind": null, "essential_toiletry": null}'


@pytest.fixture
def openrouter(monkeypatch):
    """Records each (kind, model) a request is made for, and refuses any real client."""
    calls = []

    def classification(client, model, temperature, vocabulary):
        return lambda prompt, text: calls.append(("classification", model)) or ANSWER

    def translation(client, model, temperature):
        def complete(prompt, text):
            calls.append(("translation", model))
            return "Mineral water 50cl" if "50cl" in text else "Salad"  # keeps the line's numbers, as the checks require

        return complete

    monkeypatch.setattr(llm_module, "openrouter_completion", classification)
    monkeypatch.setattr(validator_module, "translation_completion", translation)
    monkeypatch.setattr(validator_module, "OpenRouter", lambda **settings: object())
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    return calls


def _live(tmp_path, **models):
    return ExpenseValidator.from_config(CONFIG, mode="live", cache_dir=tmp_path / "cache", **models)


# --- Defaults and overrides ---


def test_without_overrides_the_config_models_are_used():
    settings = ValidatorSettings.from_config(CONFIG, mode="replay", cache_dir=REPLAY)
    assert (settings.classification_model, settings.translation_model) == (DEFAULTS.classification.model, DEFAULTS.translation.model)
    result = ExpenseValidator(settings).validate_batch([FRENCH], context_complete=True).results[0]
    assert (result.metadata.model, result.metadata.translation_model) == (DEFAULTS.classification.model, DEFAULTS.translation.model)


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"classification_model": "vendor/classifier-b"}, ("vendor/classifier-b", DEFAULTS.translation.model)),
        ({"translation_model": "vendor/translator-b"}, (DEFAULTS.classification.model, "vendor/translator-b")),
        ({"classification_model": "vendor/classifier-b", "translation_model": "vendor/translator-b"}, ("vendor/classifier-b", "vendor/translator-b")),
    ],
)
def test_each_model_is_overridden_on_its_own(overrides, expected):
    settings = ValidatorSettings.from_config(CONFIG, mode="replay", cache_dir=REPLAY, **overrides)
    assert (settings.classification_model, settings.translation_model) == expected
    assert settings.translation_enabled is DEFAULTS.translation.enabled


def test_settings_can_be_built_directly_with_either_model():
    base = ValidatorSettings.from_config(CONFIG, mode="deterministic").model_dump()
    for name in ("classification_model", "translation_model"):
        settings = ValidatorSettings.model_validate({**base, name: "vendor/other"})
        assert getattr(settings, name) == "vendor/other"


@pytest.mark.parametrize("value", ["", "   ", 42, lambda prompt, text: ANSWER])
def test_an_override_must_be_a_model_id(value):
    for name in ("classification_model", "translation_model"):
        with pytest.raises(ConfigurationError, match="must be a model ID"):
            ExpenseValidator.from_config(CONFIG, mode="replay", cache_dir=REPLAY, **{name: value})


# --- Routing, caching and metadata in live mode ---


def test_each_client_is_built_for_its_own_model(openrouter, tmp_path):
    result = _live(tmp_path, classification_model="vendor/classifier-b", translation_model="vendor/translator-b").validate_batch([FRENCH], context_complete=True).results[0]
    assert {model for kind, model in openrouter if kind == "classification"} == {"vendor/classifier-b"}
    assert {model for kind, model in openrouter if kind == "translation"} == {"vendor/translator-b"}
    assert (result.metadata.model, result.metadata.translation_model) == ("vendor/classifier-b", "vendor/translator-b")


def test_a_translation_override_does_not_turn_translation_on(openrouter, tmp_path):
    settings = yaml.safe_load(CONFIG.read_text())
    settings["translation"]["enabled"] = False
    folder = tmp_path / "project"
    shutil.copytree(ROOT / "rules", folder / "rules")
    shutil.copytree(ROOT / "data", folder / "data")
    (folder / "config.yaml").write_text(yaml.safe_dump(settings))
    validator = ExpenseValidator.from_config(folder / "config.yaml", mode="live", cache_dir=None, translation_model="vendor/translator-b")
    assert validator.settings.translation_enabled is False
    result = validator.validate_batch([FRENCH], context_complete=True).results[0]
    assert not [call for call in openrouter if call[0] == "translation"]
    assert result.metadata.translation_model is None and all(line.english_translation is None for line in result.line_items)


def test_one_models_answers_are_never_served_for_another(openrouter, tmp_path):
    receipt = [RECEIPTS["RCP-001"]]  # English, so only the classifier is asked
    lines = len(receipt[0].line_items)
    _live(tmp_path, classification_model="vendor/a").validate_batch(receipt, context_complete=True)
    _live(tmp_path, classification_model="vendor/b").validate_batch(receipt, context_complete=True)
    assert [model for _, model in openrouter] == ["vendor/a"] * lines + ["vendor/b"] * lines
    assert len(list((tmp_path / "cache").glob("*.txt"))) == 2 * lines
    again = _live(tmp_path, classification_model="vendor/a").validate_batch(receipt, context_complete=True).results[0]
    assert len(openrouter) == 2 * lines and again.metadata.latency_ms is None, "the same model is answered from its own cache"


def test_translations_are_cached_per_translation_model(openrouter, tmp_path):
    _live(tmp_path, translation_model="vendor/t1").validate_batch([FRENCH], context_complete=True)
    first = [call for call in openrouter if call[0] == "translation"]
    _live(tmp_path, translation_model="vendor/t2").validate_batch([FRENCH], context_complete=True)
    second = [call for call in openrouter if call[0] == "translation"][len(first):]
    assert {model for _, model in first} == {"vendor/t1"} and {model for _, model in second} == {"vendor/t2"} and len(second) == len(first)


# --- Replay ---


@pytest.mark.parametrize("override", [{"classification_model": "vendor/never-recorded"}, {"translation_model": "vendor/never-recorded"}])
def test_replay_with_a_model_it_has_no_answers_for_fails_without_a_request(openrouter, override):
    validator = ExpenseValidator.from_config(CONFIG, mode="replay", cache_dir=REPLAY, **override)
    with pytest.raises(ReplayAnswerMissing):
        validator.validate_batch([FRENCH], context_complete=True)
    assert openrouter == []


# --- The command line ---


def test_the_command_line_passes_each_override_through(monkeypatch, tmp_path):
    seen = {}

    def capture(config_path, **options):
        seen.update(options)
        raise SystemExit(0)

    monkeypatch.setattr(runner.ExpenseValidator, "from_config", capture)
    with pytest.raises(SystemExit):
        runner.main(["--replay", str(REPLAY), "--classification-model", "vendor/c", "--translation-model", "vendor/t", "--out", str(tmp_path / "x.jsonl")])
    assert (seen["classification_model"], seen["translation_model"], seen["mode"]) == ("vendor/c", "vendor/t", "replay")


def test_the_command_line_with_an_unrecorded_model_writes_nothing(openrouter, tmp_path, capsys):
    out = tmp_path / "receipts.jsonl"
    assert runner.main(["--replay", str(REPLAY), "--classification-model", "vendor/never-recorded", "--out", str(out)]) == 1
    assert not out.exists() and openrouter == [] and "not in the replay cache" in capsys.readouterr().err


def test_the_command_line_defaults_still_rebuild_the_committed_file(tmp_path):
    out = tmp_path / "receipts.jsonl"
    assert runner.main(["--replay", str(REPLAY), "--out", str(out)]) == 0
    assert out.read_text() == runner.OUTPUT.read_text()
    assert {json.loads(line)["metadata"]["model"] for line in out.read_text().splitlines()} == {DEFAULTS.classification.model}


def test_a_direct_key_is_kept_secret_with_an_override():
    settings = ValidatorSettings.model_validate({**ValidatorSettings.from_config(CONFIG, mode="live", cache_dir=None, classification_model="vendor/c").model_dump(), "openrouter_api_key": SecretStr("sk-or-hidden")})
    assert "sk-or-hidden" not in repr(settings) and settings.classification_model == "vendor/c"
