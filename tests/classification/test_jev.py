"""Test Jev contracts, errors, and replay using simulated responses only."""

import copy
import hashlib
import json
import re
import socket

import httpx
import openrouter
import pytest
import yaml
from yaml_cases import load_cases

from expense_validator import ConfigurationError, ExpenseValidator, ReplayAnswerMissing
from expense_validator import validator as validator_module
from expense_validator.classification.backends import jev
from expense_validator.classification.core.common import line_input, load_vocabulary
from expense_validator.config import ROOT, load_config
from expense_validator.engine.models import Receipt
from expense_validator.ingestion import IngestionMode, iter_jsonl
from expense_validator.models import ValidatorSettings
from expense_validator.normalization import normalize_receipt
from expense_validator.normalization.translation import RETRIES
from expense_validator.run_receipts import load_receipts

CONFIG_PATH = ROOT / "config.yaml"
CONFIG = load_config()
VOCABULARY = load_vocabulary(CONFIG.paths.taxonomy, CONFIG.paths.rule_fields)
QUESTIONS = jev.build_questions(VOCABULARY)
GOLDEN = {json.loads(line)["receipt_id"]: json.loads(line) for line in (ROOT / "evaluation" / "final" / "receipts.jsonl").read_text().splitlines()}
MODEL = jev.MODEL
SNAPSHOT = jev.SNAPSHOTS[MODEL]
KEY = "sk-or-test-secret-key"


def _one_line():
    receipt = next(iter_jsonl(ROOT / "data" / "receipts.jsonl", IngestionMode.EVALUATION)).receipt
    line = receipt.line_items[0]
    return normalize_receipt(Receipt.model_validate({**receipt.model_dump(), "line_items": [line], "total_amount": line.amount, "total_amount_usd": line.amount_usd}))


ONE = _one_line()


def jev_response(questions: dict, chosen: dict, model: str = SNAPSHOT) -> dict:
    """Build a simulated Decisions response with complete option distributions.

    Chosen options get their supplied probabilities; remaining mass is uniform.
    Other questions default to not_stated or other.
    """
    answers = {}
    for name, question in questions.items():
        choice, probability = chosen.get(name, ("other" if name == "category" else jev.NOT_STATED, 1.0))
        options = list(question["criteria"])
        others = [option for option in options if option != choice]
        probabilities = {option: (1 - probability) / len(others) for option in others}
        if choice in options:
            probabilities[choice] = probability
        confidence = min(1.0, max(0.0, (len(options) * probability - 1) / (len(options) - 1)))
        answers[name] = {"type": "choice", "choice": choice, "confidence": confidence, "probabilities": probabilities}
    return {"id": "gen-test", "provider": "TypeSafe", "model": model, "answers": answers, "usage": {"input_tokens": 812, "output_tokens": 9, "cost": 0.0000341}}


def _edited(response: dict, edit: dict) -> str:
    if "raw" in edit:
        return edit["raw"]
    response = copy.deepcopy(response)
    for name in edit.get("drop", []):
        del response["answers"][name]
    for name in edit.get("drop_top", []):
        del response[name]
    for path, value in edit.get("set", {}).items():
        *parents, last = path.split(".")
        target = response
        for part in parents:
            target = target[part]
        target[last] = value
    return json.dumps(response)


def _classified(text: str):
    return jev.classify_line(ONE, ONE.lines[0], lambda questions, state: text, MODEL, VOCABULARY)


@pytest.mark.parametrize("case", load_cases("classification", "jev_answers"))
def test_a_jev_answer_becomes_the_canonical_classification(case):
    line = _classified(json.dumps(jev_response(QUESTIONS, {name: tuple(pair) for name, pair in case["answers"].items()})))
    values = {**line._asdict(), "confidence": None if line.confidence is None else str(line.confidence)}
    assert {name: values[name] for name in case["expected"]} == case["expected"]


@pytest.mark.parametrize("case", load_cases("classification", "jev_answers_refused"))
def test_a_jev_answer_that_breaks_the_contract_fails_the_line(case):
    line = _classified(_edited(jev_response(QUESTIONS, {name: tuple(pair) for name, pair in case["answers"].items()}), case["edit"]))
    assert line.category is None and line.confidence is None and line.restricted is None and line.details is None
    assert line.error is not None
    assert re.search(case["expected"], line.error), line.error


@pytest.mark.parametrize("case", load_cases("classification", "jev_responses"))
def test_a_jev_response_envelope_is_read(case):
    line = _classified(_edited(jev_response(QUESTIONS, {"category": ("food_beverage", 0.9)}), case["edit"]))
    assert line.error is None and line.category == "food_beverage"
    assert {name: line.details[name] for name in case["expected"]} == case["expected"]


def test_the_questions_are_bounded_by_the_taxonomy_and_the_catalog():
    assert {name: question["type"] for name, question in QUESTIONS.items()} == {"category": "choice", "recipient_type": "choice", "venue_type": "choice", "item_kind": "choice", "essential_toiletry": "choice"}
    assert QUESTIONS["category"]["criteria"] == {name: category.examples for name, category in VOCABULARY.categories.items()}
    assert set(QUESTIONS["recipient_type"]["criteria"]) == {*VOCABULARY.recipient_types, jev.NOT_STATED}
    assert set(QUESTIONS["venue_type"]["criteria"]) == {*VOCABULARY.venue_types, jev.NOT_STATED}
    assert set(QUESTIONS["item_kind"]["criteria"]) == {*VOCABULARY.item_kinds, jev.NOT_STATED}
    assert all(1 <= len(question["criteria"]) <= 255 for question in QUESTIONS.values())
    assert "restricted" not in json.dumps(QUESTIONS).lower()
    assert json.loads(jev.questions_text(VOCABULARY)) == QUESTIONS and list(json.loads(jev.questions_text(VOCABULARY))) == list(QUESTIONS)
    sent = {}
    jev.classify_line(ONE, ONE.lines[0], lambda questions, state: sent.update(questions=questions, state=state) or "{}", MODEL, VOCABULARY)
    assert sent == {"questions": jev.questions_text(VOCABULARY), "state": line_input(ONE, ONE.lines[0])}
    assert "submitted_expense_type" not in json.loads(sent["state"])


def test_the_details_keep_every_distribution_and_the_provenance():
    line = _classified(json.dumps(jev_response(QUESTIONS, {"category": ("food_beverage", 0.9)})))
    details = line.details
    assert (details["backend"], details["requested_model"], details["model"]) == ("typesafe-jev", MODEL, SNAPSHOT)
    assert set(details) == {"backend", "requested_model", "model", "answers", "usage", "attribute_thresholds"}
    assert set(details["answers"]) == set(QUESTIONS) and details["answers"]["category"]["probabilities"]["food_beverage"] == 0.9
    assert details["answers"]["category"]["confidence"] != 0.9  # Jev's spread statistic is kept, and is not the line's confidence
    assert details["usage"] == {"input_tokens": 812, "output_tokens": 9, "cost": 0.0000341}


# --- The validator, through the real OpenRouter SDK ---


class FakeOpenRouter:
    """Replace OpenRouter network transport with simulated responses or failures."""

    def __init__(self, chosen=lambda state: {"category": ("food_beverage", 0.9)}, failure=None, response=None):
        self.chosen, self.failure, self.response = chosen, failure, response
        self.requests, self.clients = [], []

    def client(self, **settings):
        self.clients.append(settings)
        return openrouter.OpenRouter(**settings, client=httpx.Client(transport=httpx.MockTransport(self._answer)))

    def _answer(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append({"url": str(request.url), "authorization": request.headers.get("authorization"), "body": body})
        if self.failure is not None:
            if "exception" in self.failure:
                raise getattr(httpx, self.failure["exception"])("simulated", request=request)
            if "html" in self.failure:
                return httpx.Response(self.failure["status"], text=self.failure["html"], headers={"content-type": "text/html"})
            return httpx.Response(self.failure["status"], json=self.failure["body"])
        if self.response is not None:
            return httpx.Response(200, json=self.response(body))
        return httpx.Response(200, json=jev_response(body["questions"], self.chosen(body["state"]), jev.SNAPSHOTS.get(body["model"], body["model"])))


@pytest.fixture
def offline(monkeypatch):
    """No network, no client unless a test installs one, and an OpenRouter key in the environment."""
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: pytest.fail("a connection was attempted"))
    monkeypatch.setattr(validator_module, "OpenRouter", lambda *a, **k: pytest.fail("an OpenRouter client was created"))
    return monkeypatch


def _install(offline, fake: FakeOpenRouter) -> FakeOpenRouter:
    offline.setattr(validator_module, "OpenRouter", fake.client)
    return fake


def _jev_validator(mode="live", cache_dir=None, **changes):
    settings = ValidatorSettings.from_config(CONFIG_PATH, mode=mode, cache_dir=cache_dir, classification_backend="jev", classification_model=MODEL)
    return ExpenseValidator(settings.model_copy(update={"translation_enabled": False, **changes}))


def _receipts(count=2):
    return load_receipts()[:count]


def test_jev_is_chosen_through_config_yaml(offline, tmp_path):
    config = yaml.safe_load(CONFIG_PATH.read_text())
    config["classification"] = {"backend": "jev", "model": MODEL}
    config["paths"] = {name: str(ROOT / value) for name, value in config["paths"].items()}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    settings = ValidatorSettings.from_config(path, mode="live", cache_dir=None)
    assert (settings.classification_backend, settings.classification_model, settings.openrouter_api_key.get_secret_value()) == ("decisions", MODEL, KEY)  # jev is the decisions adapter's earlier name
    default = ValidatorSettings.from_config(CONFIG_PATH, mode="replay", cache_dir=tmp_path)
    assert (default.classification_backend, default.classification_model) == ("chat_completions", "anthropic/claude-haiku-5.5")
    assert KEY not in repr(settings)


def test_a_request_can_choose_jev_and_the_validator_stays_as_it_was(offline, tmp_path):
    _install(offline, FakeOpenRouter())
    settings = ValidatorSettings.from_config(CONFIG_PATH, mode="live", cache_dir=None).model_copy(update={"translation_enabled": False})
    validator = ExpenseValidator(settings)
    batch = validator.validate_batch(_receipts(1), context_complete=False, classification_backend="jev", classification_model=MODEL)
    result = batch.results[0]
    assert result.provenance.classification_backend == "decisions" and result.provenance.model_overrides == ("classification_backend", "classification_model")
    assert result.metadata.model == MODEL and result.metadata.prompt_sha256 == hashlib.sha256(jev.questions_text(VOCABULARY).encode()).hexdigest()
    assert all(line.classification_details["backend"] == "typesafe-jev" for line in result.line_items)
    assert validator.settings.classification_backend == "chat_completions" and validator.settings == settings
    with pytest.raises(ConfigurationError, match="choosing the decisions backend needs a classification_model"):
        validator.validate_batch(_receipts(1), context_complete=False, classification_backend="jev")
    with pytest.raises(ConfigurationError, match="needs a Jev classification_model on OpenRouter, such as typesafe/jev-1.14"):
        validator.validate_batch(_receipts(1), context_complete=False, classification_backend="jev", classification_model="anthropic/claude-haiku-5.5")
    with pytest.raises(ConfigurationError, match="must be chat_completions or decisions"):
        validator.validate_batch(_receipts(1), context_complete=False, classification_backend="gpt")


def test_each_line_is_one_authenticated_decisions_request_with_the_typed_questions(offline):
    fake = _install(offline, FakeOpenRouter())
    batch = _jev_validator().validate_batch(_receipts(1), context_complete=False)
    lines = batch.results[0].line_items
    assert len(fake.requests) == len(lines) and len(fake.clients) == 1
    sent = fake.requests[0]
    assert sent["url"] == "https://openrouter.ai/api/alpha/decisions" and sent["authorization"] == f"Bearer {KEY}"
    normalized = normalize_receipt(_receipts(1)[0])
    assert sent["body"] == {"model": MODEL, "questions": QUESTIONS, "state": json.loads(line_input(normalized, normalized.lines[0])), "provider": {"only": ["typesafe"], "allow_fallbacks": False}}
    # The one client keeps the LLM's timeout and retries; each Jev request turns retries off for itself.
    assert fake.clients[0]["timeout_ms"] == CONFIG.llm.timeout_seconds * 1000 and fake.clients[0]["retry_config"] is RETRIES
    assert all((line.category, line.confidence, line.restricted) == ("food_beverage", 0.9, False) for line in lines)
    assert batch.results[0].metadata.latency_ms is not None and KEY not in batch.model_dump_json()


@pytest.mark.parametrize("case", load_cases("classification", "jev_provider_failures"))
def test_a_provider_failure_fails_its_lines_once_and_falls_back_to_nothing(offline, case):
    fake = _install(offline, FakeOpenRouter(failure=case["failure"]))
    lines = _jev_validator().validate_batch(_receipts(1), context_complete=False).results[0].line_items
    assert len(fake.requests) == len(lines)  # one request per line: never retried
    assert all(line.category is None and line.confidence is None for line in lines)
    assert all(re.search(case["expected"]["error"], line.classification_error) for line in lines), lines[0].classification_error
    assert all(KEY not in line.classification_error for line in lines)


def test_settings_that_cannot_classify_with_jev_are_refused(offline, tmp_path):
    offline.delenv("OPENROUTER_API_KEY")
    offline.setattr("expense_validator.config.dotenv_values", lambda path: {})
    with pytest.raises(ConfigurationError, match="needs OPENROUTER_API_KEY"):
        _jev_validator()
    with pytest.raises(ConfigurationError, match="needs a Jev classification_model"):
        ExpenseValidator(ValidatorSettings.from_config(CONFIG_PATH, mode="replay", cache_dir=tmp_path, classification_backend="jev"))
    with pytest.raises(ConfigurationError, match="needs a Jev classification_model on OpenRouter"):
        ExpenseValidator(ValidatorSettings.from_config(CONFIG_PATH, mode="replay", cache_dir=tmp_path, classification_backend="jev", classification_model="jev-1.14.0"))
    with pytest.raises(ConfigurationError, match="classification_backend must be chat_completions or decisions"):
        ExpenseValidator.from_config(CONFIG_PATH, mode="replay", cache_dir=tmp_path, classification_backend="bert")
    with pytest.raises(ConfigurationError, match="never answers chat completions"):
        ExpenseValidator(ValidatorSettings.from_config(CONFIG_PATH, mode="replay", cache_dir=tmp_path, classification_model=MODEL))


def test_an_injected_model_call_replaces_the_backends_own(offline):
    """classifier_model stands in for the selected backend's model call, as it does for the LLM; no client is built."""
    asked = []

    def stand_in(questions, state):
        asked.append((questions, state))
        return json.dumps(jev_response(json.loads(questions), {"category": ("food_beverage", 0.9)}))

    settings = ValidatorSettings.from_config(CONFIG_PATH, mode="live", cache_dir=None, classification_backend="jev", classification_model=MODEL)
    lines = ExpenseValidator(settings.model_copy(update={"translation_enabled": False}), classifier_model=stand_in).validate_batch(_receipts(1), context_complete=False).results[0].line_items
    assert len(asked) == len(lines) and asked[0][0] == jev.questions_text(VOCABULARY)
    assert all((line.category, line.confidence, line.classification_details["backend"]) == ("food_beverage", 0.9, "typesafe-jev") for line in lines)


# --- Recordings: the LLM classifier's answer cache, in cache_dir/jev ---


def _record(offline, cache, chosen=lambda state: {"category": ("food_beverage", 0.9)}, **changes):
    fake = _install(offline, FakeOpenRouter(chosen))
    batch = _jev_validator(cache_dir=cache, **changes).validate_batch(_receipts(), context_complete=False)
    offline.setattr(validator_module, "OpenRouter", lambda *a, **k: pytest.fail("an OpenRouter client was created"))
    return batch, fake


def test_replay_answers_from_recordings_offline_and_the_same_every_time(offline, tmp_path):
    live, fake = _record(offline, tmp_path)
    recordings = sorted((tmp_path / "jev").glob("*.txt"))
    assert len(recordings) == len(fake.requests) > 0 and not list(tmp_path.glob("*.txt"))  # apart from LLM answers
    assert all(json.loads(path.read_text())["model"] == SNAPSHOT and KEY not in path.read_text() for path in recordings)
    replays = [_jev_validator(mode="replay", cache_dir=tmp_path).validate_batch(_receipts(), context_complete=False) for _ in range(2)]
    assert replays[0] == replays[1]
    strip = lambda batch: [{**r.to_record(), "metadata": None} for r in batch.results]
    assert strip(replays[0]) == strip(live)


def test_the_recorded_response_keeps_every_field_classification_reads(offline, tmp_path):
    """Verify recordings retain SDK-modeled fields and omit unmodeled usage fields."""
    sent = {}

    def respond(body):
        sent["response"] = {**jev_response(body["questions"], {"category": ("food_beverage", 0.873216)}), "extra": 1}
        sent["response"]["usage"] |= {"is_byok": False}
        return sent["response"]

    _install(offline, FakeOpenRouter(response=respond))
    _jev_validator(cache_dir=tmp_path).validate_batch(_receipts(1), context_complete=False)
    expected = copy.deepcopy(sent["response"])
    del expected["extra"], expected["usage"]["is_byok"]
    assert all(json.loads(path.read_text()) == expected for path in (tmp_path / "jev").iterdir())  # every line got the same answer


def test_replay_without_a_recording_fails_closed(offline, tmp_path):
    with pytest.raises(ReplayAnswerMissing) as missing:
        _jev_validator(mode="replay", cache_dir=tmp_path).validate_batch(_receipts(), context_complete=False)
    assert len(missing.value.missing) == sum(len(r.line_items) for r in _receipts())


def test_recordings_are_kept_apart_by_model_and_questions_and_reused(offline, tmp_path):
    _record(offline, tmp_path)
    first = {path.name: path.read_bytes() for path in (tmp_path / "jev").iterdir()}
    _, again = _record(offline, tmp_path)
    assert again.requests == [] and {path.name: path.read_bytes() for path in (tmp_path / "jev").iterdir()} == first
    _, other = _record(offline, tmp_path, classification_model="typesafe/jev-1.13")
    assert len(other.requests) == len(first) and len(list((tmp_path / "jev").iterdir())) == 2 * len(first)
    offline.setattr(jev, "CATEGORY_INSTRUCTIONS", jev.CATEGORY_INSTRUCTIONS + " Changed.")
    with pytest.raises(ReplayAnswerMissing):
        _jev_validator(mode="replay", cache_dir=tmp_path).validate_batch(_receipts(), context_complete=False)


def test_a_damaged_recording_fails_its_line_without_a_request(offline, tmp_path):
    _record(offline, tmp_path)
    for path in (tmp_path / "jev").iterdir():
        path.write_text("<html>not an answer</html>")
    lines = [line for result in _jev_validator(mode="replay", cache_dir=tmp_path).validate_batch(_receipts(), context_complete=False).results for line in result.line_items]
    assert all(line.category is None and line.classification_error == "jev response is not JSON" for line in lines)


@pytest.mark.parametrize("failure", [{"status": 503, "body": {"error": {"code": 503, "message": "no provider"}}}, {"status": 200, "body": "probabilities that do not sum to 1"}])
def test_a_failed_or_invalid_answer_is_never_recorded_and_is_asked_again(offline, tmp_path, failure):
    def respond(body):
        response = jev_response(body["questions"], {"category": ("food_beverage", 0.9)})
        response["answers"]["category"]["probabilities"]["other"] = 0.5
        return response

    fake = FakeOpenRouter(failure=failure) if failure["status"] != 200 else FakeOpenRouter(response=respond)
    for attempt in (1, 2):
        _install(offline, fake)
        lines = _jev_validator(cache_dir=tmp_path).validate_batch(_receipts(1), context_complete=False).results[0].line_items
        assert all(line.category is None and line.classification_error for line in lines)
        assert len(fake.requests) == attempt * len(lines) and not (tmp_path / "jev").exists()


# --- Policy findings ---


def _golden_choices():
    """Jev answers that repeat the golden classifications, by line input. Names come from shared deterministic extraction, never the fixture."""
    choices = {}
    for receipt in load_receipts():
        normalized = normalize_receipt(receipt)
        golden = {line["line_id"]: line for line in GOLDEN[receipt.receipt_id]["line_items"]}
        for line in normalized.lines:
            gold = golden[line.line_id]
            attributes = gold.get("attributes") or {}
            chosen = {"category": (gold["category"], gold["confidence"])}
            for name in ("recipient_type", "venue_type", "item_kind"):
                if attributes.get(name) not in (None, "unidentified"):
                    chosen[name] = (attributes[name], 1.0)
            if "essential_toiletry" in attributes:
                chosen["essential_toiletry"] = ("essential" if attributes["essential_toiletry"] else "not_essential", 1.0)
            choices[json.dumps(json.loads(line_input(normalized, line)), sort_keys=True)] = chosen
    return choices


def test_the_same_classifications_from_jev_give_the_same_policy_findings(offline):
    choices = _golden_choices()
    _install(offline, FakeOpenRouter(lambda state: choices[json.dumps(state, sort_keys=True)]))
    batch = _jev_validator().validate_batch(load_receipts(), context_complete=True)
    named = {rid for rid, record in GOLDEN.items() if any((line.get("attributes") or {}).get("recipient_name") or (line.get("attributes") or {}).get("recipient_type") == "unidentified" for line in record["line_items"])}
    assert named == {"RCP-018", "RCP-022"}
    compared = 0
    for result in batch.results:
        record, golden = result.to_record(), GOLDEN[result.receipt_id]
        for field in ("disposition", "decided", "exceptions", "review_signals", "disposition_reasons", "open_questions", "uncertainty"):
            assert record[field] == golden[field], (result.receipt_id, field)
        assert [{k: line[k] for k in ("line_id", "category", "restricted", "confidence")} for line in record["line_items"]] == [{k: line[k] for k in ("line_id", "category", "restricted", "confidence")} for line in golden["line_items"]]
        assert [line.get("attributes") for line in record["line_items"]] == [line.get("attributes") for line in golden["line_items"]]
        compared += 1
    assert compared == 32


def test_jev_results_are_saved_on_their_own_in_the_same_jsonl_as_the_llm(offline, tmp_path):
    """Each backend writes its own file through run_receipts; any comparison of the files happens elsewhere."""
    import shutil
    from expense_validator.run_receipts import main

    replay = tmp_path / "replay"
    shutil.copytree(ROOT / "evaluation" / "final" / "replay", replay)  # the LLM run's cached translations
    choices = _golden_choices()
    untranslated = lambda state: json.dumps({k: v for k, v in state.items() if k != "english_translation"}, sort_keys=True)
    _install(offline, FakeOpenRouter(lambda state: choices[untranslated(state)]))
    ExpenseValidator.from_config(CONFIG_PATH, mode="live", cache_dir=replay, classification_backend="jev", classification_model=MODEL).validate_batch(load_receipts(), context_complete=True)
    offline.setattr(validator_module, "OpenRouter", lambda *a, **k: pytest.fail("replay created an OpenRouter client"))
    out = tmp_path / "jev.jsonl"
    assert main(["--replay", str(replay), "--classification-backend", "jev", "--classification-model", MODEL, "--out", str(out)]) == 0
    saved = [json.loads(line) for line in out.read_text().splitlines()]
    assert [record["receipt_id"] for record in saved] == sorted(GOLDEN) and all(set(record) == set(GOLDEN[record["receipt_id"]]) for record in saved)
    for record in saved:
        golden = GOLDEN[record["receipt_id"]]
        assert record["metadata"]["model"] == MODEL and golden["metadata"]["model"] != MODEL
        for field in ("disposition", "exceptions", "uncertainty", "open_questions"):
            assert record[field] == golden[field], (record["receipt_id"], field)
        assert [{k: line.get(k) for k in ("category", "restricted", "confidence", "attributes")} for line in record["line_items"]] == [{k: line.get(k) for k in ("category", "restricted", "confidence", "attributes")} for line in golden["line_items"]]
