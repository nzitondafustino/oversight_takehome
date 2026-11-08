"""Runs the cases in cases/normalization, and checks normalization on the real data and the real code."""

import ast
import copy
import json
import random
import sys
import unicodedata
from pathlib import Path

import httpx
import pytest
from openrouter import OpenRouter
from pydantic import SecretStr, ValidationError
from receipt_rows import ROWS
from yaml_cases import exact, load_cases, selected

import expense_validator
from expense_validator.compiler.llm_extract import cached
from expense_validator.config import ROOT, load_config
from expense_validator.engine.models import LineItem, Receipt
from expense_validator.ingestion import IngestionMode, iter_jsonl, iter_jsonl_lines
from expense_validator.normalization import (
    LlmTranslator,
    NoOpTranslator,
    NormalizedLine,
    NormalizedReceipt,
    TranslationError,
    build_translator,
    normalize_receipt,
    normalize_text,
)
from expense_validator.normalization import translation as translation_module
from expense_validator.normalization.translation import PROMPT, RETRIES, openrouter_completion, translate_description

DATA = ROOT / "data" / "receipts.jsonl"
INGESTED = list(iter_jsonl(DATA, IngestionMode.EVALUATION))
SOURCE = Path(expense_validator.__file__).parent
FORBIDDEN_KEYS = {"labels", "notes", "ground_truth", "expected_exceptions", "review_signals", "disposition"}


class FakeTranslator:
    """Answers from a table, and records what it was given. A text not in the table is a failure."""

    def __init__(self, table):
        self.table = table
        self.calls = []

    def translate(self, text, source_language):
        self.calls.append([text, source_language])
        answer = self.table.get(text, {"raises": "not in the table"})
        if isinstance(answer, dict):
            raise TranslationError(answer["raises"])
        return answer


def _all_keys(value):
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in _all_keys(item)}
    if isinstance(value, (list, tuple)):
        return {key for item in value for key in _all_keys(item)}
    return set()


@pytest.mark.parametrize("case", load_cases("normalization", "text"))
def test_text_is_normalized_and_stays_so(case):
    found = normalize_text(case["description"])
    assert found == case["expected"]["text"]
    assert normalize_text(found) == found


@pytest.mark.parametrize("case", load_cases("normalization", "text_refused"))
def test_what_is_not_text_is_refused(case):
    with pytest.raises(TypeError, match=case["expected"]):
        normalize_text(case["description"])


@pytest.mark.parametrize("case", load_cases("normalization", "receipts"))
def test_a_receipt_is_normalized(case):
    receipt = Receipt.model_validate(case["receipt"])
    before = receipt.model_dump(mode="json")
    if case["translations"] is None:
        calls, normalized = [], normalize_receipt(receipt)
    else:
        translator = FakeTranslator(case["translations"])
        calls, normalized = translator.calls, normalize_receipt(receipt, translator)
    assert calls == case["expected"]["calls"]
    wanted = case["expected"]["lines"]
    assert len(normalized.lines) == len(wanted)
    found = [selected(line.model_dump(mode="json"), names) for line, names in zip(normalized.lines, wanted)]
    assert exact(found) == exact(wanted)
    assert normalized.receipt is receipt
    assert receipt.model_dump(mode="json") == before


@pytest.mark.parametrize("case", load_cases("normalization", "receipts_refused"))
def test_a_classified_receipt_is_refused(case):
    with pytest.raises(ValueError, match=case["expected"]):
        normalize_receipt(Receipt.model_validate(case["receipt"]))


def _client(handler):
    """The real SDK, with only the network replaced. Retries are off, so an error returns at once."""
    return OpenRouter(api_key="very-secret", client=httpx.Client(transport=httpx.MockTransport(handler)), retry_config=None)


def _response(status, body):
    return httpx.Response(status, json=body) if isinstance(body, dict) else httpx.Response(status, text=body, headers={"content-type": "application/json"})


def _responding(status, body):
    return LlmTranslator(openrouter_completion(_client(lambda request: _response(status, body)), "some/model", 0))


@pytest.mark.parametrize("case", load_cases("normalization", "provider"))
def test_a_provider_answer_is_read(case):
    assert _responding(case["status"], case["body"]).translate("x", "es") == case["expected"]["translation"]


@pytest.mark.parametrize("case", load_cases("normalization", "provider_refused"))
def test_a_provider_answer_is_a_translation_error(case):
    with pytest.raises(TranslationError, match=case["expected"]) as error:
        _responding(case["status"], case["body"]).translate("x", "es")
    assert "very-secret" not in str(error.value)


@pytest.mark.parametrize("case", load_cases("normalization", "provider_refused"))
def test_a_refused_answer_leaves_the_line_failed_without_quoting_it(case):
    outcome = translate_description("Weisswein 0,2l", "de", _responding(case["status"], case["body"]))
    assert outcome.status == "failed"
    assert outcome.translation is None
    assert "Weisswein" not in outcome.error
    assert "very-secret" not in outcome.error


@pytest.mark.parametrize("case", load_cases("normalization", "provider_refused"))
def test_a_refused_answer_is_not_cached_and_is_asked_again(case, tmp_path):
    sent = []

    def handler(request):
        sent.append(request)
        return _response(case["status"], case["body"])

    complete = cached(openrouter_completion(_client(handler), "some/model", 0), "some/model", tmp_path)
    for _ in range(2):
        with pytest.raises(TranslationError):
            complete(PROMPT, "x")
    assert len(sent) == 2
    assert list(tmp_path.iterdir()) == []


# --- Properties that hold for any text ---


def test_every_character_is_idempotent_alone_and_inside_a_word():
    for code_point in range(sys.maxunicode + 1):
        character = chr(code_point)
        once = normalize_text(f"a{character}b")
        assert normalize_text(once) == once, hex(code_point)
        assert unicodedata.is_normalized("NFC", once), hex(code_point)


def test_random_mixtures_are_idempotent_and_never_lose_visible_text():
    alphabet = "aé́è \t\n 　 ​‌‍­﻿‮\0\x7f\x85ガ日１½ﬁ"
    generator = random.Random(20261104)
    for _ in range(20000):
        text = "".join(generator.choices(alphabet, k=generator.randint(0, 12)))
        once = normalize_text(text)
        assert normalize_text(once) == once, repr(text)
        assert "  " not in once and once == once.strip(), repr(text)
        # Compared decomposed, as an accent may rightly compose with the letter before it.
        for visible in "a日１½ﬁ":
            assert unicodedata.normalize("NFD", once).count(visible) == unicodedata.normalize("NFD", text).count(visible), repr(text)


def test_no_control_character_survives():
    for code_point in range(sys.maxunicode + 1):
        character = chr(code_point)
        if unicodedata.category(character) in {"Cc", "Cs"}:
            assert normalize_text(f"a{character}b") in {"ab", "a b"}, hex(code_point)


# --- The real data, through the real ingestion ---


def test_all_the_real_receipts_are_normalized():
    results = [normalize_receipt(item.receipt) for item in INGESTED]
    assert len(results) == 32
    assert sum(len(result.lines) for result in results) == sum(len(row["line_items"]) for row in ROWS) == 79
    for result, row in zip(results, ROWS):
        assert result.receipt_id == row["receipt_id"]
        assert [line.line_id for line in result.lines] == [line["line_id"] for line in row["line_items"]]
        for line, raw in zip(result.lines, row["line_items"]):
            assert line.description_raw == raw["description_raw"]
            assert line.description_normalized == normalize_text(raw["description_raw"])
            assert line.description_normalized
            assert line.language == raw["language"]
            assert line.translation is None
            assert line.translation_status == "not_requested"


def test_every_language_in_the_real_data_is_there_and_goes_to_a_translator():
    assert {line["language"] for row in ROWS for line in row["line_items"]} == {"en", "es", "fr", "de", "pt", "ja"}
    table = {
        normalize_text(line["description_raw"]): normalize_text(line["description_raw"])
        for row in ROWS
        for line in row["line_items"]
    }
    translator = FakeTranslator(table)
    results = [normalize_receipt(item.receipt, translator) for item in INGESTED]
    lines = [line for result in results for line in result.lines]
    foreign = [line for line in lines if line.language != "en"]
    assert foreign and translator.calls == [[line.description_normalized, line.language] for line in foreign]
    assert all(line.translation_status == "translated" for line in foreign)
    assert all(line.translation_status == "skipped" for line in lines if line.language == "en")


def test_real_receipts_are_not_changed_and_no_line_is_invented():
    for item in INGESTED:
        before = item.receipt.model_dump(mode="json")
        normalized = normalize_receipt(item.receipt)
        assert normalized.receipt is item.receipt
        assert item.receipt.model_dump(mode="json") == before
        assert len(normalized.lines) == len(item.receipt.line_items)


def test_non_itemized_real_receipts_keep_only_their_own_lines():
    found = [item for item in INGESTED if not item.receipt.itemized]
    assert found
    for item in found:
        assert [line.line_id for line in normalize_receipt(item.receipt).lines] == [line.line_id for line in item.receipt.line_items]


def test_no_answer_reaches_normalized_output_or_a_translator():
    translator = FakeTranslator({})
    for item, row in zip(INGESTED, ROWS):
        normalized = normalize_receipt(item.receipt, translator)
        dumped = normalized.model_dump(mode="json")
        assert not _all_keys(dumped) & FORBIDDEN_KEYS
        for line in normalized.receipt.line_items:
            assert (line.category, line.restricted, line.confidence) == (None, None, None)
        text = normalized.model_dump_json()
        labels = row["labels"]
        answers = [row["notes"], *(found["reason"] for found in labels["expected_exceptions"] + labels["review_signals"])]
        assert not [answer for answer in answers if answer in text]
    descriptions = {(line["description_raw"], line["language"]) for row in ROWS for line in row["line_items"]}
    assert translator.calls and all(tuple(call) in descriptions for call in translator.calls)


def test_only_a_receipt_is_accepted_so_ground_truth_has_no_way_in():
    for wrong in (INGESTED[0], ROWS[0], INGESTED[0].receipt.model_dump(), None):
        with pytest.raises(TypeError, match="needs a Receipt from ingestion"):
            normalize_receipt(wrong)


def test_the_normalized_models_have_no_field_for_a_prediction_or_an_answer():
    predicted = {"category", "restricted", "confidence", "disposition", "findings", "labels", "notes"}
    assert not predicted & (set(NormalizedLine.model_fields) | set(NormalizedReceipt.model_fields))
    assert not {"description_normalized", "translation", "translation_status"} & (set(Receipt.model_fields) | set(LineItem.model_fields))


# --- The models hold together ---


def _line(**changes):
    return {
        "receipt_id": "R",
        "line_id": "L",
        "description_raw": " Vino ",
        "description_normalized": "Vino",
        "language": "es",
        "translation_status": "not_requested",
        **changes,
    }


@pytest.mark.parametrize("case", load_cases("normalization", "lines_refused"))
def test_a_line_that_does_not_hold_together_is_refused(case):
    with pytest.raises(ValidationError, match=case["expected"]):
        NormalizedLine(**_line(**case["changes"]))


def test_results_cannot_be_changed():
    normalized = normalize_receipt(INGESTED[0].receipt)
    with pytest.raises(ValidationError):
        normalized.lines = ()
    with pytest.raises(ValidationError):
        normalized.lines[0].translation = "invented"


def test_lines_that_are_not_the_receipts_are_refused():
    normalized = normalize_receipt(INGESTED[0].receipt)
    for lines in (normalized.lines[:-1], normalized.lines[::-1], (*normalized.lines, normalized.lines[0])):
        with pytest.raises(ValidationError, match="must be the receipt's lines"):
            NormalizedReceipt(receipt=normalized.receipt, lines=lines)


def test_a_result_round_trips_through_json():
    normalized = normalize_receipt(INGESTED[0].receipt)
    assert NormalizedReceipt.model_validate_json(normalized.model_dump_json()) == normalized


# --- Translators ---


def test_the_no_op_translator_has_no_text_to_give():
    with pytest.raises(TranslationError, match="switched off"):
        NoOpTranslator().translate("Vino", "es")


def test_translation_is_off_by_default():
    assert normalize_receipt(INGESTED[0].receipt, NoOpTranslator()) == normalize_receipt(INGESTED[0].receipt)


def test_a_bug_in_a_translator_is_not_recorded_as_a_failed_translation():
    class Broken:
        def translate(self, text, source_language):
            raise ZeroDivisionError

    row = next(item for item in INGESTED if any(line.language != "en" for line in item.receipt.line_items))
    with pytest.raises(ZeroDivisionError):
        normalize_receipt(row.receipt, Broken())


@pytest.mark.parametrize("error", [httpx.ConnectError("no route"), httpx.ReadTimeout("timed out"), httpx.RemoteProtocolError("cut off")])
def test_a_network_failure_is_a_translation_error(error):
    def handler(request):
        raise error

    with pytest.raises(TranslationError, match="translation request failed"):
        LlmTranslator(openrouter_completion(_client(handler), "some/model", 0)).translate("Vino", "es")


def _answering(sent, answer="Sake"):
    def handler(request):
        sent.append(request)
        message = {"role": "assistant", "content": answer}
        return httpx.Response(200, json={"id": "x", "object": "chat.completion", "created": 1, "model": "m", "system_fingerprint": None, "choices": [{"index": 0, "finish_reason": "stop", "message": message}]})

    return handler


def test_the_request_holds_only_the_template_and_the_text():
    sent = []
    LlmTranslator(openrouter_completion(_client(_answering(sent)), "some/model", 0)).translate("日本酒", "ja")
    request = sent[0]
    assert request.url == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer very-secret"
    body = json.loads(request.content)
    assert body["messages"] == [{"role": "user", "content": f"{PROMPT}\n\n日本酒\n\n"}]
    assert (body["model"], body["temperature"], body["stream"]) == ("some/model", 0, False)


def test_settings_send_the_blank_line_after_the_text_and_reuse_no_answer_to_a_request_without_it(tmp_path, monkeypatch):
    """Null content came back from the model without the blank line, so this holds the real production path to it."""
    sent = []
    cached(lambda prompt, text: "Old", "some/model", tmp_path)(PROMPT, "日本酒")
    monkeypatch.setattr(translation_module, "OpenRouter", lambda **settings: _client(_answering(sent)))
    assert build_translator(_config(tmp_path, True)).translate("日本酒", "ja") == "Sake"
    assert [json.loads(request.content)["messages"] for request in sent] == [[{"role": "user", "content": f"{PROMPT}\n\n日本酒\n\n"}]]


def test_a_failed_request_marks_only_its_own_line_through_the_real_sdk():
    def handler(request):
        if "Vino" in json.loads(request.content)["messages"][0]["content"]:
            return httpx.Response(429, json={"error": {"code": 429, "message": "Slow down"}})
        return _answering([], "Water")(request)

    line = {"description_raw": "Vino", "language": "es", "quantity": 1, "unit_price": "1", "amount": "1"}
    receipt = Receipt.model_validate(
        {
            "receipt_id": "R",
            "employee_id": "E",
            "employee_country": "US",
            "entity": "GLOBAL",
            "submitted_expense_type": "x",
            "itemized": True,
            "line_items": [{**line, "line_id": "L1"}, {**line, "line_id": "L2", "description_raw": "Agua"}],
        }
    )
    first, second = normalize_receipt(receipt, LlmTranslator(openrouter_completion(_client(handler), "some/model", 0))).lines
    assert (first.translation_status, first.translation, first.description_normalized) == ("failed", None, "Vino")
    assert "TooManyRequests" in first.translation_error
    assert (second.translation_status, second.translation) == ("translated", "Water")


def _config(tmp_path, enabled, *, model="some/model", key="secret"):
    config = load_config()
    return config.model_copy(
        update={
            "translation": config.translation.model_copy(update={"enabled": enabled, "model": model}),
            "llm": config.llm.model_copy(update={"model": "the-compilers-model"}),
            "paths": config.paths.model_copy(update={"llm_cache": tmp_path}),
            "openrouter_api_key": SecretStr(key) if key else None,
        }
    )


def test_the_committed_settings_ask_for_translation_by_a_model_of_its_own():
    config = load_config()
    assert config.translation.enabled is True
    assert config.translation.model and config.translation.model != config.llm.model


def test_settings_leave_translation_off_unless_they_ask_for_it(tmp_path):
    assert type(load_config().translation).model_fields["enabled"].default is False
    assert isinstance(build_translator(_config(tmp_path, False, model=None, key=None)), NoOpTranslator)


def test_settings_build_a_cached_translator_for_the_translation_model(tmp_path, monkeypatch):
    sent, built = [], {}

    def client(**settings):
        built.update(settings)
        return _client(_answering(sent))

    monkeypatch.setattr(translation_module, "OpenRouter", client)
    translator = build_translator(_config(tmp_path, True))
    assert isinstance(translator, LlmTranslator) and sent == []
    assert built == {"api_key": "secret", "timeout_ms": load_config().llm.timeout_seconds * 1000, "retry_config": RETRIES}
    assert [translator.translate("日本酒", "ja"), translator.translate("日本酒", "ja")] == ["Sake", "Sake"]
    assert len(sent) == 1
    assert json.loads(sent[0].content)["model"] == "some/model"


def test_retries_give_up_soon_and_then_the_line_fails(monkeypatch):
    """The SDK retries for an hour by default. Time is faked here, so this checks the limit itself."""
    assert RETRIES.backoff.max_elapsed_time <= 60_000
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(500, json={"error": {"code": 500, "message": "Broken"}})

    now = [0.0]
    monkeypatch.setattr("openrouter.utils.retries.time.time", lambda: now[0])
    monkeypatch.setattr("openrouter.utils.retries.time.sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    client = OpenRouter(api_key="k", client=httpx.Client(transport=httpx.MockTransport(handler)), retry_config=RETRIES)
    with pytest.raises(TranslationError, match="translation request failed"):
        LlmTranslator(openrouter_completion(client, "some/model", 0)).translate("Vino", "es")
    assert 1 < len(calls) < 20
    assert now[0] <= RETRIES.backoff.max_elapsed_time / 1000 + RETRIES.backoff.max_interval / 1000


def test_a_failed_request_is_not_cached(tmp_path, monkeypatch):
    answers = [httpx.Response(500, json={"error": {"code": 500, "message": "Broken"}}), None]

    def handler(request):
        return answers.pop(0) or _answering([])(request)

    monkeypatch.setattr(translation_module, "OpenRouter", lambda **settings: _client(handler))
    translator = build_translator(_config(tmp_path, True))
    with pytest.raises(TranslationError):
        translator.translate("日本酒", "ja")
    assert translator.translate("日本酒", "ja") == "Sake"


@pytest.mark.parametrize("settings", [{"model": None}, {"key": None}])
def test_translation_enabled_with_no_way_to_run_it_is_refused(tmp_path, settings):
    with pytest.raises(ValueError, match="translation is enabled"):
        build_translator(_config(tmp_path, True, **settings))


# --- Boundaries ---


def _imports(path):
    found = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module)
    return found


def test_nothing_earlier_or_downstream_imports_normalization():
    for package in ("engine", "policy", "compiler", "ingestion", "offline_eval"):
        for path in (SOURCE / package).rglob("*.py"):
            assert not [name for name in _imports(path) if name.startswith("expense_validator.normalization")], path


def test_normalization_reads_no_answers_and_needs_no_new_dependency():
    project = {"expense_validator.config", "expense_validator.engine.models", "expense_validator.compiler.llm_extract", "expense_validator.exceptions"}
    for path in (SOURCE / "normalization").glob("*.py"):
        for name in _imports(path):
            if name.startswith("expense_validator.normalization"):
                continue
            assert name in project or name.split(".")[0] in {"pydantic", "httpx", "openrouter", *sys.stdlib_module_names}, (path.name, name)


def test_a_row_is_not_changed_on_its_way_through():
    row = copy.deepcopy(ROWS[0])
    item = next(iter_jsonl_lines([json.dumps(row)], IngestionMode.EVALUATION))
    normalize_receipt(item.receipt)
    assert row == ROWS[0]
