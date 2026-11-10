"""Test routing and reflection with recorded answers and isolated fake backends."""

import hashlib
import json
import shutil
import socket
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import openrouter
import pytest
import yaml
from pydantic import SecretStr, ValidationError
from yaml_cases import load_cases

from expense_validator import ExpenseValidator
from expense_validator.classification import orchestrator
from expense_validator.classification.backends import registry
from expense_validator.classification.core import common, models
from expense_validator.config import ROOT, LangGraphConfig, load_config
from expense_validator.exceptions import NOT_RECORDED, ClassificationError, ConfigurationError, ReplayAnswerMissing
from expense_validator.models import ValidatorSettings
from expense_validator.normalization import NoOpTranslator, normalize_receipt
from expense_validator.run_receipts import load_receipts

CONFIG = ROOT / "config.yaml"
RECEIPTS = {receipt.receipt_id: receipt for receipt in load_receipts()}
ROUTED = RECEIPTS["RCP-002"]  # L1 Prix fixe dinner, L2 Chianti Classico bottle, L3 Gratuity
NORMALIZED = normalize_receipt(ROUTED, NoOpTranslator())
SHORT = {common.line_input(NORMALIZED, line): line.line_id.rsplit("-", 1)[1] for line in NORMALIZED.lines}
GOLDEN_REPLAY = ROOT / "evaluation" / "final" / "replay"
JEV_REPLAY = ROOT / "evaluation" / "jev" / "runs" / "first-run" / "replay"
GOLDEN = {record["receipt_id"]: record for record in map(json.loads, (ROOT / "evaluation" / "final" / "receipts.jsonl").read_text().splitlines())}


def effective(config: LangGraphConfig, *, reflection: bool = False) -> dict:
    found = {"backends": [[backend.name, backend.backend, backend.model] for backend in config.backends], "routing": config.routing.model_dump()}
    return found | {"reflection": config.reflection.model_dump()} if reflection else found


def order(*links, routing=None) -> dict:
    found = {"backends": [{"name": name, "backend": backend, "model": model} for name, backend, model in links]}
    return found if routing is None else {**found, "routing": routing}


def stand_ins(monkeypatch, answers: dict) -> tuple[Counter, Counter]:
    """Register alpha, beta and gamma for this test. Each answers by its model, and counts every model call."""
    calls, connects = Counter(), Counter()

    def make(name: str) -> models.Backend:
        def check_model(model):
            if not str(model).startswith(name):
                raise ValueError(f"the {name} backend needs a {name} model")

        def connect(client, settings, vocabulary):
            model = settings.classification_model
            connects[model] += 1

            def complete(prompt, text):
                calls[model] += 1
                answer = answers.get(model, {}).get(SHORT[text])
                if answer is None:
                    raise ClassificationError(f"{model} could not answer")
                if answer == "abstain":
                    return json.dumps({"category": None, "confidence": None})
                return json.dumps({"category": answer[0], "confidence": answer[1]})

            return complete

        def classify_receipt(normalized, complete, settings, vocabulary, only=None):
            lines = []
            for line in normalized.lines:
                if only is not None and line.line_id not in only:
                    continue
                try:
                    answer = json.loads(complete(f"{name} instructions", common.line_input(normalized, line)))
                    category = answer["category"]
                    if category is None:  # an abstention: a valid answer with no category, which finalize keeps
                        result = models.ClassifiedLine(line.line_id, details={"backend": name, "model": settings.classification_model})
                    else:
                        restricted = vocabulary.categories[category].restricted if category in vocabulary.categories else False  # finalize refuses an unknown one
                        result = models.ClassifiedLine(line.line_id, category=category, restricted=restricted, confidence=Decimal(str(answer["confidence"])), details={"backend": name, "model": settings.classification_model})
                except ClassificationError as error:
                    result = models.ClassifiedLine(line.line_id, error=str(error))
                lines.append(common.finalize(result, line, vocabulary))
            return tuple(lines)

        return models.Backend(check_model=check_model, prompt=lambda vocabulary: f"{name} instructions", cache_key=lambda model, vocabulary: model, cache_folder=name, connect=connect, classify_receipt=classify_receipt)

    for name in ("alpha", "beta", "gamma"):
        monkeypatch.setitem(registry.BACKENDS, name, make(name))
    return calls, connects


def live_validator(cache_dir, **overrides) -> ExpenseValidator:
    """A langgraph validator in live mode whose only model calls are stand-ins: translation off, a placeholder key."""
    settings = ValidatorSettings.from_config(CONFIG, mode="live", cache_dir=cache_dir, classification_backend="langgraph", **overrides)
    return ExpenseValidator(settings.model_copy(update={"translation_enabled": False, "openrouter_api_key": SecretStr("sk-or-placeholder")}))


def short(result, line) -> str:
    return line.line_id.rsplit("-", 1)[1] if result.receipt_id == ROUTED.receipt_id else line.line_id


def routed(batch) -> dict:
    """Each line's answering alias (None for a failed line) and category."""
    found = {}
    for result in batch.results:
        for line in result.line_items:
            orchestration = (line.classification_details or {}).get("orchestration")
            found[short(result, line)] = (orchestration["answered_by"] if orchestration and line.classification_error is None else None, line.category)
    return found


def audits(batch) -> dict:
    """Each line's orchestration audit, failed lines included."""
    return {short(result, line): line.classification_details["orchestration"] for result in batch.results for line in result.line_items}


@pytest.fixture
def offline(monkeypatch):
    """Refuse every connection and every OpenRouter client."""

    def refuse(*args, **kwargs):
        raise AssertionError("no network: replay and these tests never call out")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(openrouter.OpenRouter, "__init__", refuse)


def recordings(tmp_path, *, jev: bool = True, llm: bool = True):
    """A temporary replay folder: the golden LLM answers and translations, and the first Jev run's answers."""
    folder = tmp_path / "replay"
    folder.mkdir(parents=True)
    for path in GOLDEN_REPLAY.glob("*.txt"):
        if llm or path.name in {item.name for item in JEV_REPLAY.glob("*.txt")}:  # without llm, only the translations
            shutil.copy(path, folder / path.name)
    if jev:
        shutil.copytree(JEV_REPLAY / "jev", folder / "jev")
    return folder


# --- Configuration ---


@pytest.mark.parametrize("case", load_cases("classification", "langgraph_overrides"))
def test_an_override_changes_only_what_it_gives(case):
    base = load_config().classification.langgraph
    with_reflection = "reflection" in case["expected"]
    assert effective(base.merged(case["override"]), reflection=with_reflection) == case["expected"]
    settings = ValidatorSettings.from_config(CONFIG, mode="replay", classification_backend="langgraph", langgraph_config=case["override"])
    assert effective(settings.langgraph, reflection=with_reflection) == case["expected"]
    assert effective(base) == effective(load_config().classification.langgraph), "the configuration itself never changes"


@pytest.mark.parametrize("case", load_cases("classification", "langgraph_overrides_refused"))
def test_an_invalid_override_is_refused_at_every_entry_point(case, tmp_path):
    validator = ExpenseValidator.from_config(CONFIG, mode="replay", cache_dir=tmp_path)
    with pytest.raises(ConfigurationError, match=case["expected"]):
        validator.validate_batch([], context_complete=False, classification_backend="langgraph", langgraph_config=case["override"])
    with pytest.raises(ConfigurationError, match=case["expected"]):
        ExpenseValidator.from_config(CONFIG, mode="replay", cache_dir=tmp_path, classification_backend="langgraph", langgraph_config=case["override"])


def test_the_config_file_sets_the_defaults_and_the_application_fills_the_rest(tmp_path):
    configured = yaml.safe_load(CONFIG.read_text())
    configured["paths"] = {name: str(ROOT / path) for name, path in configured["paths"].items()}
    configured["classification"] = {"backend": "langgraph", "langgraph": {"backends": [{"name": "p", "backend": "jev", "model": "typesafe/jev-1.14"}]}}
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(configured))
    validator = ExpenseValidator.from_config(tmp_path / "config.yaml", mode="replay", cache_dir=tmp_path)
    assert validator.settings.classification_backend == "langgraph" and validator.settings.classification_model is None
    # The file's backends, and the application's default for the routing it leaves out.
    assert effective(validator.settings.langgraph) == {"backends": [["p", "decisions", "typesafe/jev-1.14"]], "routing": {"fallback_on_error": True}}
    configured["classification"] = {"backend": "langgraph"}
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(configured))
    with pytest.raises(ConfigurationError, match="needs at least one backend"):
        ExpenseValidator.from_config(tmp_path / "config.yaml", mode="replay", cache_dir=tmp_path)


def test_a_configured_model_for_langgraph_itself_is_refused(tmp_path):
    configured = yaml.safe_load(CONFIG.read_text())
    configured["paths"] = {name: str(ROOT / path) for name, path in configured["paths"].items()}
    configured["classification"]["backend"] = "langgraph"  # the file's model stays: it would be ambiguous
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(configured))
    with pytest.raises(ConfigurationError, match="takes no classification_model"):
        ExpenseValidator.from_config(tmp_path / "config.yaml", mode="replay", cache_dir=tmp_path)


def test_langgraph_settings_are_refused_where_they_cannot_apply(tmp_path):
    with pytest.raises(ConfigurationError, match="applies only to the langgraph backend, and this one is chat_completions"):
        ExpenseValidator.from_config(CONFIG, mode="replay", cache_dir=tmp_path, langgraph_config={"routing": {"fallback_on_error": False}})
    validator = ExpenseValidator.from_config(CONFIG, mode="replay", cache_dir=tmp_path)
    with pytest.raises(ConfigurationError, match="applies only to the langgraph backend, and this request's is chat_completions"):
        validator.validate_batch([], context_complete=False, langgraph_config={"routing": {"fallback_on_error": False}})
    with pytest.raises(ConfigurationError, match="cannot give it a classification_model"):
        validator.validate_batch([], context_complete=False, classification_backend="langgraph", classification_model="typesafe/jev-1.14")
    with pytest.raises(ConfigurationError, match="cannot configure langgraph"):
        ExpenseValidator.from_config(CONFIG, mode="deterministic").validate_batch([], context_complete=False, langgraph_config={})
    settings = ValidatorSettings.from_config(CONFIG, mode="live", cache_dir=tmp_path, classification_backend="langgraph").model_copy(update={"openrouter_api_key": SecretStr("sk-or-placeholder")})
    with pytest.raises(ConfigurationError, match="takes no classifier_model"):
        ExpenseValidator(settings, classifier_model=lambda prompt, text: "{}")


def test_the_effective_configuration_is_frozen():
    config = load_config().classification.langgraph
    with pytest.raises(ValidationError):
        config.routing.fallback_on_error = False
    with pytest.raises(ValidationError):
        config.backends[0].model = "another"
    assert isinstance(config.backends, tuple)
    assert config.merged({"routing": {"fallback_on_error": False}}) is not config and config.routing.fallback_on_error is True


# --- Routing ---


@pytest.mark.parametrize("case", load_cases("classification", "langgraph_routes"))
def test_each_line_goes_only_as_far_as_its_routing_says(case, monkeypatch, tmp_path):
    calls, connects = stand_ins(monkeypatch, case["answers"])
    batch = live_validator(tmp_path).validate_batch([ROUTED], context_complete=False, classification_backend="langgraph", langgraph_config=order(*case["order"], routing=case["routing"]))
    found = routed(batch)
    assert {line: found[line][0] for line in found} == case["expected"]["answered_by"]
    assert {line: found[line][1] for line in found} == case["expected"]["category"]
    assert dict(calls) == case["expected"]["calls"]
    assert set(connects) == set(calls), "a backend that no line reaches is never even connected"
    audit = audits(batch)
    for line, outcomes in case["expected"].get("outcomes", {}).items():
        assert [attempt["outcome"] for attempt in audit[line]["attempts"]] == outcomes, line
    for line, result in case["expected"].get("result", {}).items():
        assert audit[line]["result"] == result, line
    assert all(item["fallback"] is (len(item["attempts"]) > 1) for item in audit.values())


def test_an_answered_line_keeps_its_backends_details_and_every_attempt(monkeypatch, tmp_path):
    stand_ins(monkeypatch, {"alpha-1": {"L1": ["food_beverage", 0.95], "L2": ["alcohol", 0.95]}, "beta-1": {"L3": ["service_fee", 0.97]}})
    batch = live_validator(tmp_path).validate_batch([ROUTED], context_complete=False, langgraph_config=order(["primary", "alpha", "alpha-1"], ["fallback", "beta", "beta-1"]))
    line = batch.results[0].line_items[2]
    assert line.classification_details["backend"] == "beta" and line.classification_details["model"] == "beta-1"
    assert line.classification_details["orchestration"] == {
        "answered_by": "fallback",
        "fallback": True,
        "result": "accepted",
        "attempts": [
            {"name": "primary", "backend": "alpha", "model": "alpha-1", "outcome": "error", "routed_on": True, "answered": False, "confidence": None, "error": "alpha-1 could not answer"},
            {"name": "fallback", "backend": "beta", "model": "beta-1", "outcome": "accepted", "routed_on": False, "answered": True, "confidence": 0.97, "error": None},
        ],
    }
    metadata, provenance = batch.results[0].metadata, batch.results[0].provenance
    assert metadata.model == "primary=alpha:alpha-1, fallback=beta:beta-1" and provenance.classification_backend == "langgraph"
    assert provenance.model_overrides == ("langgraph",)


# --- Isolation ---


def test_one_validator_serves_different_configurations_in_turn_without_any_leaking(monkeypatch, tmp_path):
    calls, _ = stand_ins(monkeypatch, {"alpha-1": {"L1": ["food_beverage", 0.95], "L2": ["alcohol", 0.95]}, "beta-1": {"L3": ["service_fee", 0.97]}})  # alpha fails L3
    validator = live_validator(tmp_path, langgraph_config=order(["primary", "alpha", "alpha-1"], ["fallback", "beta", "beta-1"], routing={"fallback_on_error": True}))
    before, graph, backends = validator.settings, orchestrator.GRAPH, dict(registry.BACKENDS)
    run = lambda **override: routed(validator.validate_batch([ROUTED], context_complete=False, **override))["L3"]
    assert run() == ("fallback", "service_fee")  # from_config's error fallback sends the failed line on
    assert run(langgraph_config={"routing": {"fallback_on_error": False}}) == (None, None)  # this request's keeps the failure
    assert run() == ("fallback", "service_fee")  # and the next request is back to from_config's
    assert run(langgraph_config=order(["only", "beta", "beta-1"])) == ("only", "service_fee")
    assert run() == ("fallback", "service_fee")
    assert validator.settings is before and effective(validator.settings.langgraph)["routing"]["fallback_on_error"] is True
    assert orchestrator.GRAPH is graph and dict(registry.BACKENDS) == backends


def test_concurrent_requests_with_different_configurations_stay_apart(monkeypatch, tmp_path):
    answers = {
        "alpha-1": {"L1": ["food_beverage", 0.95], "L3": ["other", 0.75]},  # alpha fails L2
        "beta-1": {"L1": ["other", 0.99], "L2": ["alcohol", 0.97], "L3": ["service_fee", 0.97]},
    }
    stand_ins(monkeypatch, answers)
    validator = live_validator(tmp_path)
    configs = [
        order(["primary", "alpha", "alpha-1"], ["fallback", "beta", "beta-1"]),
        order(["primary", "beta", "beta-1"], ["fallback", "alpha", "alpha-1"]),
        order(["only", "alpha", "alpha-1"]),
        order(["primary", "alpha", "alpha-1"], ["fallback", "beta", "beta-1"], routing={"fallback_on_error": False}),
    ]
    alone = [routed(validator.validate_batch([ROUTED], context_complete=False, langgraph_config=config)) for config in configs]
    assert len({json.dumps(found, sort_keys=True) for found in alone}) == len(configs), "each configuration gives its own answers"
    with ThreadPoolExecutor(max_workers=8) as pool:
        together = list(pool.map(lambda index: (index, routed(validator.validate_batch([ROUTED], context_complete=False, langgraph_config=configs[index % 4]))), range(48)))
    assert all(found == alone[index % 4] for index, found in together)


# --- Cache identity and replay ---


def test_each_backend_records_under_its_own_folder_and_model_and_replays_with_no_call(monkeypatch, tmp_path, offline):
    answers = {"alpha-1": {"L1": ["food_beverage", 0.95], "L3": ["service_fee", 0.95]}, "beta-1": {"L2": ["alcohol", 0.97]}}
    calls, _ = stand_ins(monkeypatch, answers)
    config = order(["primary", "alpha", "alpha-1"], ["fallback", "beta", "beta-1"])
    settings = ValidatorSettings.from_config(CONFIG, mode="live", cache_dir=tmp_path, classification_backend="langgraph").model_copy(update={"translation_enabled": False, "openrouter_api_key": SecretStr("k")})
    monkeypatch.setattr("expense_validator.validator._Request._client", lambda self, settings: None)  # the offline fixture refuses any client; stand-ins need none
    live = ExpenseValidator(settings).validate_batch([ROUTED], context_complete=False, langgraph_config=config)
    key = lambda model, prompt, short: hashlib.sha256("\0".join([model, prompt, next(text for text, name in SHORT.items() if name == short)]).encode()).hexdigest() + ".txt"
    assert sorted(path.name for path in (tmp_path / "alpha").iterdir()) == sorted([key("alpha-1", "alpha instructions", "L1"), key("alpha-1", "alpha instructions", "L3")])
    assert [path.name for path in (tmp_path / "beta").iterdir()] == [key("beta-1", "beta instructions", "L2")]
    made = dict(calls)

    replay = ExpenseValidator(settings.model_copy(update={"mode": "replay", "openrouter_api_key": None}))
    again = replay.validate_batch([ROUTED], context_complete=False, langgraph_config=config)
    assert dict(calls) == made, "replay makes no model call"
    assert [r.to_record() | {"metadata": None} for r in again.results] == [r.to_record() | {"metadata": None} for r in live.results]
    # Another model is another cache entry: its answers are missing, and replay never asks for them.
    with pytest.raises(ReplayAnswerMissing):
        replay.validate_batch([ROUTED], context_complete=False, langgraph_config=order(["primary", "alpha", "alpha-2"], ["fallback", "beta", "beta-1"]))
    assert dict(calls) == made


def test_real_recordings_replay_through_the_policy_engine_with_no_network(tmp_path, offline):
    """The configured order, Jev then the LLM when Jev fails, on all 32 receipts from the repository's recordings."""
    validator = ExpenseValidator.from_config(CONFIG, mode="replay", cache_dir=recordings(tmp_path), classification_backend="langgraph")
    batch = validator.validate_batch(list(RECEIPTS.values()), context_complete=True)
    found = routed(batch)
    assert Counter(name for name, _ in found.values()) == {"primary": 78, "fallback": 1}
    assert found["RCP-017-L2"] == ("fallback", "service_fee")  # Jev's rejected line, answered by the LLM's recording
    assert found["RCP-005-L3"][0] == "primary"  # Jev's 0.81 is kept: only EXC-03's min_confidence judges it
    assert {result.receipt_id: result.disposition for result in batch.results} == {receipt_id: record["disposition"] for receipt_id, record in GOLDEN.items()}
    rcp_017 = next(result for result in batch.results if result.receipt_id == "RCP-017")
    assert sorted(item.rule_id for item in rcp_017.exceptions) == ["DOC-03", "MCC-01", "PERS-01"] and rcp_017.decided


def test_the_reversed_order_sends_only_the_lines_the_llm_did_not_classify_to_jev(tmp_path, offline):
    validator = ExpenseValidator.from_config(CONFIG, mode="replay", cache_dir=recordings(tmp_path), classification_backend="langgraph")
    batch = validator.validate_batch(list(RECEIPTS.values()), context_complete=True, langgraph_config=order(["primary", "openrouter", "anthropic/claude-haiku-5.5"], ["fallback", "jev", "typesafe/jev-1.14"]))
    unclassified = {line["line_id"] for record in GOLDEN.values() for line in record["line_items"] if line["category"] is None}
    found = routed(batch)
    assert {line_id for line_id, (name, _) in found.items() if name == "fallback"} == unclassified and all(name for name, _ in found.values())


def test_a_missing_recording_may_follow_the_configured_fallback_but_never_the_network(tmp_path, offline):
    folder = recordings(tmp_path, jev=False)  # the LLM's answers and the translations, and no Jev answer
    validator = ExpenseValidator.from_config(CONFIG, mode="replay", cache_dir=folder, classification_backend="langgraph")
    receipt = [RECEIPTS["RCP-001"]]
    batch = validator.validate_batch(receipt, context_complete=False)
    assert all(name == "fallback" for name, _ in routed(batch).values())
    with pytest.raises(ReplayAnswerMissing):
        validator.validate_batch(receipt, context_complete=False, langgraph_config={"routing": {"fallback_on_error": False}})
    with pytest.raises(ReplayAnswerMissing):
        validator.validate_batch(receipt, context_complete=False, langgraph_config=order(["only", "jev", "typesafe/jev-1.14"]))


# --- The direct backends are unchanged ---


def corrected(record, settled):
    """A first-run record with the open questions the later engine corrections settled taken out."""
    if not settled:
        return record
    assert set(settled) <= {question["rule_id"] for question in record["open_questions"]}, record["receipt_id"]
    reasons = [
        {**reason, "rule_ids": [rule for rule in reason["rule_ids"] if rule not in settled]} if reason["code"] == "incomplete_evidence" else reason
        for reason in record["disposition_reasons"]
    ]
    questions = [question for question in record["open_questions"] if question["rule_id"] not in settled]
    return {**record, "disposition_reasons": reasons, "open_questions": questions}


def _before_attribute_confidences(attributes):
    """Attributes as the first run wrote them: before each one's confidence was passed on for EXC-03 to judge."""
    kept = {key: value for key, value in (attributes or {}).items() if not key.endswith("_confidence")}
    return kept or None


def first_run_shape(record):
    """A record in the shape the first run was written in, before the brief's shape moved the extensions into metadata."""
    metadata = record["metadata"]
    lines = metadata["line_items"]
    quotes = iter(metadata["source_quotes"])
    return {
        **{key: record[key] for key in ("receipt_id", "disposition")},
        "line_items": [{**line, **{key: _before_attribute_confidences(value) if key == "attributes" else value for key, value in lines.get(line["line_id"], {}).items() if key != "classification_details"}} for line in record["line_items"]],
        "exceptions": [{**{key: value for key, value in item.items() if key != "explanation"}, "source_quote": next(quotes)["source_quote"], "explanation": item["explanation"]} for item in record["exceptions"]],
        "review_signals": record["review_signals"],
        **{key: metadata[key] for key in ("decided", "disposition_reasons", "open_questions", "uncertainty")},
        "metadata": {key: metadata[key] for key in ("model", "translation_model", "retriever", "prompt_sha256", "ruleset_version", "replayed", "latency_ms")},
    }


def test_jev_alone_still_replays_its_first_run(tmp_path, offline):
    validator = ExpenseValidator.from_config(CONFIG, mode="replay", cache_dir=JEV_REPLAY, classification_backend="jev", classification_model="typesafe/jev-1.14")
    # The run is written once. Only what the later engine corrections settled is applied over it.
    settled = yaml.safe_load((ROOT / "evaluation" / "engine_corrections.yaml").read_text())["jev_first_run"]
    recorded = map(json.loads, (JEV_REPLAY.parent / "receipts.jsonl").read_text().splitlines())
    first_run = {record["receipt_id"]: corrected(record, settled.get(record["receipt_id"], {}).get("settled", [])) for record in recorded}
    strip = lambda record: {**record, "metadata": {k: v for k, v in record["metadata"].items() if k not in ("replayed", "latency_ms")}}
    batch = validator.validate_batch(list(RECEIPTS.values()), context_complete=True)
    assert [strip(first_run_shape(result.to_record())) for result in batch.results] == [strip(first_run[result.receipt_id]) for result in batch.results]
    assert all("orchestration" not in (line.classification_details or {}) for result in batch.results for line in result.line_items)


# --- Missing recordings are missing evidence, never failures ---


def test_a_missing_recording_is_reported_as_missing_evidence_and_never_as_a_failure(tmp_path, offline):
    from expense_validator.evaluation.classification import missing_recordings

    receipt = [RECEIPTS["RCP-001"]]
    gap = ExpenseValidator.from_config(CONFIG, mode="replay", cache_dir=recordings(tmp_path / "gap", jev=False), classification_backend="langgraph")
    batch = gap.validate_batch(receipt, context_complete=False)
    for audit in audits(batch).values():
        assert [attempt["outcome"] for attempt in audit["attempts"]] == ["missing_recording", "accepted"]
        assert audit["attempts"][0]["error"] == NOT_RECORDED and audit["fallback"] is True
    assert missing_recordings(batch.results) == [{"line_id": line.line_id, "name": "primary", "backend": "decisions", "model": "typesafe/jev-1.14", "call": "classification"} for line in RECEIPTS["RCP-001"].line_items]
    complete = ExpenseValidator.from_config(CONFIG, mode="replay", cache_dir=recordings(tmp_path / "full"), classification_backend="langgraph")
    assert missing_recordings(complete.validate_batch(receipt, context_complete=False).results) == []


def test_recorded_failures_are_failures_and_are_told_apart_from_a_missing_recording(tmp_path, offline):
    """A recorded answer that fails parsing, or one that fails the shared consistency checks, is a classification error."""
    from expense_validator.classification.backends import llm
    from expense_validator.compiler.llm_extract import cached

    folder = recordings(tmp_path)
    validator = ExpenseValidator.from_config(CONFIG, mode="replay", cache_dir=folder, classification_backend="langgraph")
    # Jev's recorded answer for RCP-017-L2 breaks the consistency check: a failure, recorded, so the fallback is earned.
    attempts = audits(validator.validate_batch([RECEIPTS["RCP-017"]], context_complete=False))["RCP-017-L2"]["attempts"]
    assert attempts[0]["outcome"] == "error" and "contradicts" in attempts[0]["error"]
    # A recorded LLM answer that is not JSON is a failure too, and replay does not call it missing.
    receipt = RECEIPTS["RCP-001"]
    normalized = normalize_receipt(receipt, NoOpTranslator())
    model = "anthropic/claude-haiku-5.5"
    for line in normalized.lines:
        (folder / (hashlib.sha256("\0".join([llm.classifier_cache_key(model, validator.vocabulary), llm.build_prompt(validator.vocabulary), common.line_input(normalized, line)]).encode()).hexdigest() + ".txt")).write_text("not json")
    batch = validator.validate_batch([receipt], context_complete=False, langgraph_config=order(["only", "openrouter", model]))
    for audit in audits(batch).values():
        assert audit["attempts"][0]["outcome"] == "error" and audit["attempts"][0]["error"] != NOT_RECORDED and audit["result"] == "exhausted"


def test_a_live_provider_failure_is_a_failure_that_is_neither_recorded_nor_retried(monkeypatch, tmp_path):
    calls, _ = stand_ins(monkeypatch, {"alpha-1": {"L1": ["food_beverage", 0.95], "L3": ["service_fee", 0.95]}})  # L2's call fails
    validator = live_validator(tmp_path)
    audit = audits(validator.validate_batch([ROUTED], context_complete=False, langgraph_config=order(["only", "alpha", "alpha-1"])))
    assert audit["L2"]["attempts"][0]["outcome"] == "error" and audit["L2"]["attempts"][0]["error"] == "alpha-1 could not answer"
    assert calls == {"alpha-1": 3} and len(list((tmp_path / "alpha").iterdir())) == 2, "one call per line, and the failure is not recorded"


# --- Any registered backend, alone or in any order ---


def test_jev_alone_and_the_llm_alone_through_langgraph_give_their_direct_results(tmp_path, offline):
    folder = recordings(tmp_path)
    receipts = list(RECEIPTS.values())
    strip = lambda batch: [{**result.to_record(), "metadata": None} for result in batch.results]
    for backend, model in (("jev", "typesafe/jev-1.14"), ("openrouter", "anthropic/claude-haiku-5.5")):
        direct = ExpenseValidator.from_config(CONFIG, mode="replay", cache_dir=folder, classification_backend=backend, classification_model=model).validate_batch(receipts, context_complete=True)
        alone = ExpenseValidator.from_config(CONFIG, mode="replay", cache_dir=folder, classification_backend="langgraph").validate_batch(receipts, context_complete=True, langgraph_config=order(["only", backend, model]))
        assert strip(alone) == strip(direct), backend
        assert all(audit["answered_by"] == "only" and not audit["fallback"] for audit in audits(alone).values())


def test_routing_and_predictions_are_read_back_from_the_audit_for_evaluation(tmp_path, offline):
    from expense_validator.evaluation.classification import result_predictions, routing_summary
    from expense_validator.evaluation.predictions import Source

    validator = ExpenseValidator.from_config(CONFIG, mode="replay", cache_dir=recordings(tmp_path), classification_backend="langgraph")
    batch = validator.validate_batch(list(RECEIPTS.values()), context_complete=True)
    summary = routing_summary(batch.results)
    assert summary["answered_by"] == {"fallback": 1, "primary": 78} and summary["extra_backend_calls"] == 1
    assert summary["fallback"]["numerator"] == 1 and summary["routed_for"] == {"error": 1}  # confidence never routes a line
    assert summary["results"] == {"accepted": 79}
    predictions = result_predictions(batch.results, Source(kind="actual", name="lg", description="d", model="m"))
    assert len(predictions.predictions) == 79 and predictions.by_line()["RCP-017-L2"].category == "service_fee"
    direct = ExpenseValidator.from_config(CONFIG, mode="replay", cache_dir=recordings(tmp_path / "direct"), classification_backend="jev", classification_model="typesafe/jev-1.14")
    alone = routing_summary(direct.validate_batch(list(RECEIPTS.values()), context_complete=True).results)
    assert alone["extra_backend_calls"] == 0 and alone["answered_by"] == {} and alone["fallback"]["numerator"] == 0


# --- Reflection ---

DESCRIPTION = {json.loads(text)["original_description"]: name for text, name in SHORT.items()}
CRITIC = {"enabled": True, "reviewer": {"backend": "critic", "model": "critic-1"}}
SENT_REVISIONS: list[dict] = []  # each structured revision request a stand-in reviser received, in order


def stand_in_line(line, answer, vocabulary, details) -> models.ClassifiedLine:
    if answer["category"] is None:
        return models.ClassifiedLine(line.line_id, details=details)
    return models.ClassifiedLine(line.line_id, category=answer["category"], restricted=vocabulary.categories[answer["category"]].restricted, confidence=Decimal(str(answer["confidence"])), details=details)


def reflection_stand_ins(monkeypatch, reviews: dict, revisions: dict, revisers, calls: Counter) -> None:
    """Register critic, a reviewer, and revisers for the named stand-in backends, for this test only."""

    def critic_connect(client, settings, vocabulary):
        model = settings.classification_model

        def complete(prompt, text):
            calls[model] += 1
            request = json.loads(text)
            answer = reviews.get(f"{DESCRIPTION[request['line']['original_description']]}:{request['proposed'].get('category')}", ["accept", None, None])
            if answer == "error":
                raise ClassificationError(f"{model} failed")
            if answer == "malformed":
                return "{not json"
            verdict, suggested, feedback = answer
            return json.dumps({"verdict": verdict, "reason": f"{verdict}, by the line's words", "suggested_category": suggested, "feedback": feedback})

        return complete

    def review(normalized, line, proposed, complete, vocabulary):
        return common.parse_reflection(complete("critic instructions", common.review_input(normalized, line, proposed)), vocabulary, proposed)

    monkeypatch.setitem(registry.REVIEWERS, "critic", models.Reviewer(check_model=lambda model: None, prompt=lambda vocabulary: "critic instructions", cache_key=lambda model, vocabulary: model, cache_folder="critic", connect=critic_connect, review=review))

    def make(name):
        def connect(client, settings, vocabulary):
            model = settings.classification_model

            def complete(prompt, text):
                calls[f"{model}+revise"] += 1
                request = json.loads(text)
                key = f"{model}:{DESCRIPTION[request['line']['original_description']]}:{request['previous_classification'].get('category')}"
                if "retry" in request:  # structured: an answer may be given for one retry alone, as key:retry
                    SENT_REVISIONS.append(request)
                    key = f"{key}:{request['retry']['number']}" if f"{key}:{request['retry']['number']}" in revisions else key
                answer = revisions.get(key)
                if answer == "missing":
                    raise ClassificationError(NOT_RECORDED)
                if answer == "invalid":
                    raise ClassificationError("item_kind contradicts category")
                if answer is None:
                    raise ClassificationError(f"{model} could not revise")
                if answer == "abstain":
                    return json.dumps({"category": None, "confidence": None})
                return json.dumps({"category": answer[0], "confidence": answer[1]})

            return complete

        def revise(normalized, line, previous, feedback, complete, vocabulary, context=None):
            text = common.revision_input(normalized, line, previous, feedback) if context is None else common.structured_revision_input(normalized, line, previous, context, vocabulary)
            try:
                answer = json.loads(complete(f"{name} revise", text))
                result = stand_in_line(line, answer, vocabulary, {"backend": name, "revised": True})
            except ClassificationError as error:
                result = models.ClassifiedLine(line.line_id, error=str(error))
            return common.finalize(result, line, vocabulary)

        return models.Reviser(prompt=lambda vocabulary: f"{name} revise", cache_key=lambda model, vocabulary: model, cache_folder=f"{name}-revision", connect=connect, revise=revise,
                              structured_prompt=lambda vocabulary: f"{name} revise, structured")

    for name in revisers:
        monkeypatch.setitem(registry.REVISERS, name, make(name))


@pytest.mark.parametrize("case", load_cases("classification", "langgraph_reflection"))
def test_reflection_reviews_revises_and_falls_back_as_its_precedence_says(case, monkeypatch, tmp_path):
    SENT_REVISIONS.clear()
    calls, _ = stand_ins(monkeypatch, case["answers"])
    reflected = Counter()
    reflection_stand_ins(monkeypatch, case["reviews"], case["revisions"], case["revisers"], reflected)
    config = {**order(*case["order"]), "reflection": {**CRITIC, **case["reflection"]}}
    batch = live_validator(tmp_path).validate_batch([ROUTED], context_complete=False, langgraph_config=config)
    found, audit, expected = routed(batch), audits(batch), case["expected"]
    assert {line: found[line][0] for line in expected["answered_by"]} == expected["answered_by"]
    assert {line: found[line][1] for line in expected["category"]} == expected["category"]
    assert {line: audit[line]["result"] for line in expected["result"]} == expected["result"]
    assert dict(calls) | dict(reflected) == expected["calls"]
    if not ({**CRITIC, **case["reflection"]})["enabled"]:
        assert all("reflection" not in item for item in audit.values())
        return
    assert {line: audit[line]["reflection"]["revisions"] for line in expected.get("revisions", {})} == expected.get("revisions", {})
    assert {line: [review["verdict"] for review in audit[line]["reflection"]["reviews"]] for line in expected.get("verdicts", {})} == expected.get("verdicts", {})
    limits = batch.results[0].provenance and validator_limits(case["reflection"])
    lines = {short(result, line): line for result in batch.results for line in result.line_items}
    for name, item in audit.items():
        reflection = item["reflection"]
        called = [review for review in reflection["reviews"] if review["outcome"] != "reused"]  # a reused verdict makes no call
        assert reflection["calls"] == len(item["attempts"]) + len(called) <= limits["max_total_calls_per_line"]
        assert reflection["revisions"] == sum(1 for attempt in item["attempts"] if "revision" in attempt) <= limits["max_retries"]
        assert (lines[name].confidence is None) is (reflection["unresolved"] or lines[name].category is None), name
    for line, unresolved in expected.get("unresolved", {}).items():
        assert audit[line]["reflection"]["unresolved"] is unresolved, line
    structured = ({**CRITIC, **case["reflection"]}).get("feedback_context") == "structured"
    for line, reason in expected.get("stop_reason", {}).items():
        assert audit[line]["reflection"]["stop_reason"] == reason == audit[line]["result"], line
    if not structured:
        assert SENT_REVISIONS == [] and all("stop_reason" not in item["reflection"] for item in audit.values())
    # Each structured request: the review it answers, where the retry stands, and a failed revision's error.
    sent = [{"category": request["previous_classification"]["category"], "confidence": request["previous_classification"]["confidence"], "review": request["review"],
             "categories": sorted(request["categories_in_question"]), "retry": request["retry"], **({"failed_revision": request["failed_revision"]} if "failed_revision" in request else {})}
            for request in SENT_REVISIONS]
    assert sent == expected.get("requests", sent)
    for line, attempts in expected.get("revision_attempts", {}).items():
        assert [{key: attempt[key] for key in ("revision", "outcome", "answers_review", "max_retries", "retried_validation_error")} for attempt in audit[line]["attempts"] if "revision" in attempt] == attempts, line


def validator_limits(override: dict) -> dict:
    return load_config().classification.langgraph.merged({"reflection": {**CRITIC, **override}}).reflection.model_dump()


def test_reflection_is_off_by_default_and_off_changes_nothing(tmp_path, offline):
    assert load_config().classification.langgraph.reflection.enabled is False
    validator = ExpenseValidator.from_config(CONFIG, mode="replay", cache_dir=recordings(tmp_path), classification_backend="langgraph")
    receipts = list(RECEIPTS.values())
    plain = validator.validate_batch(receipts, context_complete=True)
    off = validator.validate_batch(receipts, context_complete=True, langgraph_config={"reflection": {"enabled": False, "max_retries": 0}})
    assert [result.model_dump() | {"provenance": None} for result in off.results] == [result.model_dump() | {"provenance": None} for result in plain.results]
    assert all("reflection" not in item for item in audits(plain).values())


def test_the_llm_reviews_and_revises_through_its_own_calls_records_them_apart_and_replays_them(monkeypatch, tmp_path, offline):
    """Verify Jev classification and fake LLM review/revision use separate replay caches."""
    from expense_validator.classification.backends import llm

    folder = recordings(tmp_path)
    sent = Counter()

    def fake_send(client, model, temperature, prompt, text, json_schema, what):
        sent[what] += 1
        request = json.loads(text)
        if what == "reflection":
            if request["proposed"]["category"] == "personal_electronics" and sent["revision"] == 0:
                return json.dumps({"verdict": "revise", "reason": "headphones may be an accessory", "suggested_category": None, "feedback": "check whether these are a computer accessory"})
            return json.dumps({"verdict": "accept", "reason": "the line names the product", "suggested_category": None, "feedback": None})
        assert what == "revision" and request["reviewer_feedback"] == "check whether these are a computer accessory" and request["previous_classification"]["category"] == "personal_electronics"
        return json.dumps({"category": "other", "confidence": 0.9, "recipient_name": None, "recipient_type": None, "venue_type": None, "item_kind": None, "essential_toiletry": None})

    monkeypatch.setattr(llm, "_send", fake_send)
    monkeypatch.setattr("expense_validator.validator._Request._client", lambda self, settings: pytest.fail("a client_factory replaces the shared client"))
    settings = ValidatorSettings.from_config(CONFIG, mode="live", cache_dir=folder, classification_backend="langgraph").model_copy(update={"translation_enabled": False, "openrouter_api_key": SecretStr("k")})
    config = {"reflection": {"enabled": True}}  # config.yaml's reviewer, the LLM
    receipt = [RECEIPTS["RCP-012"]]  # one line, Sony WH-1000XM5 wireless headphones: Jev's answer is recorded
    # Every connection is asked of the caller's factory, named by its purpose and its adapter, as a meter needs.
    connected = []
    live = ExpenseValidator(settings, client_factory=lambda purpose, backend: connected.append((purpose, backend))).validate_batch(receipt, context_complete=False, langgraph_config=config)
    assert connected == [("review", "chat_completions"), ("revision", "chat_completions")]  # Jev's recording answered: no connection
    audit = audits(live)["RCP-012-L1"]
    assert (audit["answered_by"], live.results[0].line_items[0].category, audit["result"]) == ("fallback", "other", "accepted")
    assert [review["verdict"] for review in audit["reflection"]["reviews"]] == ["revise", "accept"]
    assert audit["attempts"][-1] | {"confidence": None} == {"name": "fallback", "backend": "chat_completions", "model": "anthropic/claude-haiku-5.5", "outcome": "accepted", "routed_on": False, "answered": True, "confidence": None, "error": None, "revision": 1}
    assert audit["reflection"]["calls"] == 4 and audit["reflection"]["revisions"] == 1
    assert sent == {"reflection": 2, "revision": 1}, "Jev was answered from its recording, and never asked to revise"
    # Reviews and revisions are recorded apart from classifications, each under its own key.
    assert len(list((folder / "reflection").iterdir())) == 2 and len(list((folder / "revision").iterdir())) == 1
    vocabulary = live and ExpenseValidator(settings).vocabulary
    normalized = normalize_receipt(RECEIPTS["RCP-012"], NoOpTranslator())
    first_pass = hashlib.sha256("\0".join([llm.classifier_cache_key("anthropic/claude-haiku-5.5", vocabulary), llm.build_prompt(vocabulary), common.line_input(normalized, normalized.lines[0])]).encode()).hexdigest()
    assert first_pass + ".txt" not in {path.name for path in (folder / "revision").iterdir()}
    # Replay answers every call from those recordings, with no client and no network.
    replay = ExpenseValidator(settings.model_copy(update={"mode": "replay", "openrouter_api_key": None})).validate_batch(receipt, context_complete=False, langgraph_config=config)
    assert [r.to_record() | {"metadata": None} for r in replay.results] == [r.to_record() | {"metadata": None} for r in live.results]
    assert sent == {"reflection": 2, "revision": 1}


def test_the_llm_revises_with_structured_context_through_its_own_request_and_recordings(monkeypatch, tmp_path, offline):
    """A structured revision: the whole review, the previous confidence, the categories in question and the retry."""
    from expense_validator.classification.backends import llm

    folder = recordings(tmp_path)
    requests = []

    def fake_send(client, model, temperature, prompt, text, json_schema, what):
        request = json.loads(text)
        if what == "reflection":
            if request["proposed"]["category"] == "personal_electronics":
                return json.dumps({"verdict": "revise", "reason": "the line names headphones", "suggested_category": "office_supplies", "feedback": "check whether these are office equipment"})
            return json.dumps({"verdict": "accept", "reason": "the line names the product", "suggested_category": None, "feedback": None})
        assert prompt.endswith(llm.STRUCTURED_REVISION_INSTRUCTIONS) and "reviewer_feedback" not in request
        requests.append(request)
        return json.dumps({"category": "office_supplies", "confidence": 0.9, "recipient_name": None, "recipient_type": None, "venue_type": None, "item_kind": None, "essential_toiletry": None})

    monkeypatch.setattr(llm, "_send", fake_send)
    settings = ValidatorSettings.from_config(CONFIG, mode="live", cache_dir=folder, classification_backend="langgraph").model_copy(update={"translation_enabled": False, "openrouter_api_key": SecretStr("k")})
    config = {"reflection": {"enabled": True, "feedback_context": "structured", "max_retries": 3}}
    receipt = [RECEIPTS["RCP-012"]]
    live = ExpenseValidator(settings, client_factory=lambda purpose, backend: object()).validate_batch(receipt, context_complete=False, langgraph_config=config)
    vocabulary = ExpenseValidator(settings).vocabulary
    line = normalize_receipt(RECEIPTS["RCP-012"], NoOpTranslator()).lines[0]
    assert requests == [{
        "line": json.loads(common.line_input(normalize_receipt(RECEIPTS["RCP-012"], NoOpTranslator()), line)),  # the line as classified, nothing more
        "previous_classification": {"category": "personal_electronics", "confidence": requests[0]["previous_classification"]["confidence"]},
        "review": {"verdict": "revise", "reason": "the line names headphones", "feedback": "check whether these are office equipment", "suggested_category": "office_supplies"},
        "categories_in_question": {name: vocabulary.categories[name].examples for name in ("personal_electronics", "office_supplies")},
        "retry": {"number": 1, "max": 3},
    }]
    assert requests[0]["previous_classification"]["confidence"] is not None
    audit = audits(live)["RCP-012-L1"]
    assert (audit["result"], audit["reflection"]["stop_reason"], audit["reflection"]["unresolved"], live.results[0].line_items[0].category) == ("accepted", "accepted", False, "office_supplies")
    assert audit["reflection"] | {"reviews": None, "fallbacks": None} == {"calls": 4, "revisions": 1, "reviews": None, "fallbacks": None, "unresolved": False, "feedback_context": "structured", "stop_reason": "accepted", "max_retries": 3, "max_calls": 6}
    # Its own recording, apart from a feedback-only revision of the same line; replay reads it with no client.
    replay = ExpenseValidator(settings.model_copy(update={"mode": "replay", "openrouter_api_key": None})).validate_batch(receipt, context_complete=False, langgraph_config=config)
    assert [r.to_record() | {"metadata": None} for r in replay.results] == [r.to_record() | {"metadata": None} for r in live.results]
    # The feedback-only request for the same revision was never recorded: replay has no answer for it.
    plain = ExpenseValidator(settings.model_copy(update={"mode": "replay", "openrouter_api_key": None})).validate_batch(receipt, context_complete=False, langgraph_config={"reflection": {"enabled": True, "max_retries": 3}})
    assert [attempt["outcome"] for attempt in audits(plain)["RCP-012-L1"]["attempts"] if "revision" in attempt] == ["missing_recording"]


def test_structured_context_is_its_own_prompt_identity_and_the_default_keeps_the_earlier_one():
    validator = ExpenseValidator.from_config(CONFIG, mode="replay", classification_backend="langgraph")
    settings = validator.settings
    def identity(**reflection):
        merged = settings.model_copy(update={"langgraph": settings.langgraph.merged({"reflection": {"enabled": True, **reflection}})})
        return validator._prompt_sha256(merged, 2)
    # The default is left out of the identity, so a reflection order hashes exactly as it did before the field existed.
    assert identity() == identity(feedback_context="reviewer_feedback")
    assert identity(feedback_context="structured") != identity()


def test_a_missing_review_recording_is_missing_evidence_and_the_classification_stands(tmp_path, offline):
    from expense_validator.evaluation.classification import missing_recordings

    validator = ExpenseValidator.from_config(CONFIG, mode="replay", cache_dir=recordings(tmp_path), classification_backend="langgraph")
    batch = validator.validate_batch([RECEIPTS["RCP-012"]], context_complete=False, langgraph_config={"reflection": {"enabled": True}})
    audit = audits(batch)["RCP-012-L1"]
    assert audit["result"] == "review_missing_recording" and audit["reflection"]["reviews"][0]["outcome"] == "missing_recording"
    assert batch.results[0].line_items[0].category == "personal_electronics", "a missing review never discards or changes a classification"
    assert missing_recordings(batch.results) == [{"line_id": "RCP-012-L1", "name": "primary", "backend": "chat_completions", "model": "anthropic/claude-haiku-5.5", "call": "review"}]


def test_reflection_always_ends_within_its_budgets(monkeypatch, tmp_path):
    """A reviewer that always objects, and a reviser that always changes its answer: every budget still holds."""
    cycle = ["other", "gift", "service_fee", "food_beverage", "alcohol", "office_supplies"]
    stand_ins(monkeypatch, {"alpha-1": {line: ["other", 0.95] for line in ("L1", "L2", "L3")}})
    reviews = {f"{line}:{category}": ["revise", None, f"{category} does not fit {line}"] for line in ("L1", "L2", "L3") for category in cycle}
    revisions = {f"alpha-1:{line}:{category}": [cycle[(index + 1) % len(cycle)], 0.95] for line in ("L1", "L2", "L3") for index, category in enumerate(cycle)}
    reflected = Counter()
    reflection_stand_ins(monkeypatch, reviews, revisions, ["alpha"], reflected)
    validator = live_validator(tmp_path)
    for budget in range(1, 9):
        for retries in range(0, 5):
            reflection = {**CRITIC, "max_retries": retries, "max_total_calls_per_line": budget}
            for item in audits(validator.validate_batch([ROUTED], context_complete=False, langgraph_config={**order(["only", "alpha", "alpha-1"]), "reflection": reflection})).values():
                assert item["reflection"]["calls"] <= budget and item["reflection"]["revisions"] <= retries
                assert item["result"] in {"call_budget", "retries_exhausted", "repeated_objection"}


def test_the_recursion_limit_is_a_second_guard_that_stops_a_runaway(monkeypatch, tmp_path):
    stand_ins(monkeypatch, {"alpha-1": {"L1": ["food_beverage", 0.95], "L2": ["alcohol", 0.95], "L3": ["service_fee", 0.95]}})
    assert orchestrator.recursion_limit(2, 3, None) == 14
    monkeypatch.setattr(orchestrator, "recursion_limit", lambda chain, lines, reflection: 1)
    with pytest.raises(RuntimeError, match="bound on supersteps"):
        live_validator(tmp_path).validate_batch([ROUTED], context_complete=False, langgraph_config=order(["only", "alpha", "alpha-1"]))


def test_concurrent_requests_with_different_reflection_stay_apart(monkeypatch, tmp_path):
    stand_ins(monkeypatch, {"alpha-1": {"L1": ["alcohol", 0.95], "L2": ["alcohol", 0.95], "L3": ["other", 0.95]}})
    reviews = {"L1:alcohol": ["revise", "food_beverage", "the line names a meal"], "L3:other": ["revise", None, "a gratuity is a charge"], "L3:gift": ["revise", "service_fee", "a tip is not a gift"]}
    revisions = {"alpha-1:L1:alcohol": ["food_beverage", 0.95], "alpha-1:L3:other": ["gift", 0.9], "alpha-1:L3:gift": ["service_fee", 0.95]}
    reflection_stand_ins(monkeypatch, reviews, revisions, ["alpha"], Counter())
    validator = live_validator(tmp_path)
    base = order(["only", "alpha", "alpha-1"])
    configs = [base, {**base, "reflection": CRITIC}, {**base, "reflection": {**CRITIC, "max_retries": 1}}, {**base, "reflection": {**CRITIC, "max_retries": 0}}]
    alone = [audits(validator.validate_batch([ROUTED], context_complete=False, langgraph_config=config)) for config in configs]
    assert len({json.dumps(found, sort_keys=True) for found in alone}) == len(configs)
    with ThreadPoolExecutor(max_workers=8) as pool:
        together = list(pool.map(lambda index: (index, audits(validator.validate_batch([ROUTED], context_complete=False, langgraph_config=configs[index % 4]))), range(40)))
    assert all(found == alone[index % 4] for index, found in together)
    assert validator.settings.langgraph.reflection.enabled is False


def test_a_confidently_wrong_jev_answer_is_reviewed_and_revised_with_feedback(monkeypatch, offline):
    """Verify reflection revises the recorded high-confidence Guinness 0.0 error."""
    from expense_validator.classification.backends import jev, llm
    from expense_validator.compiler.llm_extract import cached
    from expense_validator.evaluation.cli import robustness as rcli
    from expense_validator.evaluation.robustness import line_receipt, without_placeholders
    from expense_validator.normalization.translation import replay_only

    case = next(item for item in rcli.load_cases() if item["id"] == "RB-001")
    normalized = normalize_receipt(line_receipt(case["id"], case["input"]), NoOpTranslator())
    config = load_config()
    vocabulary = common.load_vocabulary(config.paths.taxonomy, config.paths.rule_fields)
    folder = ROOT / "evaluation" / "jev" / "runs" / "robustness-first-run" / "replay" / "jev"
    settings = ValidatorSettings.from_config(CONFIG, mode="replay", cache_dir=folder.parent, classification_backend="jev", classification_model=jev.MODEL)
    recorded = without_placeholders(cached(replay_only([], ClassificationError), jev.MODEL, folder))  # as the robustness run sent it
    seen = []

    def model(prompt, text):  # stands in for the LLM: it reads the request, as a model would
        request = json.loads(text)
        seen.append(request)
        if "proposed" in request:
            if request["proposed"]["category"] == "alcohol":
                return json.dumps({"verdict": "revise", "reason": "the description says 0.0", "suggested_category": "food_beverage", "feedback": "0.0 marks an alcohol-free beer: reconsider alcohol"})
            return json.dumps({"verdict": "accept", "reason": "an alcohol-free drink", "suggested_category": None, "feedback": None})
        return json.dumps({"category": "food_beverage", "confidence": 0.9, "recipient_name": None, "recipient_type": None, "venue_type": None, "item_kind": None, "essential_toiletry": None})

    chain = (orchestrator.Link("primary", "jev", jev.MODEL, jev.BACKEND, recorded, settings),)
    reflection = orchestrator.Reflection(
        orchestrator.ReviewerLink("openrouter", "anthropic/claude-haiku-5.5", llm.REVIEWER, without_placeholders(model)),
        (None,),  # Jev cannot take feedback
        max_retries=2,
        max_calls=6,
    )
    # With Jev alone and no reviser, the rejection stays unresolved: the category stands, its confidence is withheld.
    (line,) = orchestrator.classify_receipt(normalized, chain, config.classification.langgraph.routing, vocabulary, reflection)
    assert (line.category, line.confidence, line.details["orchestration"]["result"]) == ("alcohol", None, "no_reviser")
    assert line.details["orchestration"]["reflection"]["withheld_confidence"] == 0.98
    # With the LLM after Jev, the LLM revises Jev's answer from the feedback, and the reviewer accepts the revision.
    llm_settings = settings.model_copy(update={"classification_backend": "openrouter", "classification_model": "anthropic/claude-haiku-5.5"})
    chain += (orchestrator.Link("fallback", "openrouter", "anthropic/claude-haiku-5.5", llm.BACKEND, lambda prompt, text: pytest.fail("no first-pass LLM call"), llm_settings),)
    reflection = orchestrator.Reflection(reflection.reviewer, (None, orchestrator.ReviserLink("fallback", "openrouter", "anthropic/claude-haiku-5.5", llm.REVISER, without_placeholders(model))), 2, 6)
    seen.clear()
    (line,) = orchestrator.classify_receipt(normalized, chain, config.classification.langgraph.routing, vocabulary, reflection)
    audit = line.details["orchestration"]
    assert (line.category, audit["answered_by"], audit["result"], audit["reflection"]["revisions"]) == ("food_beverage", "fallback", "accepted", 1)
    assert [review["verdict"] for review in audit["reflection"]["reviews"]] == ["revise", "accept"]
    revision = seen[1]
    assert revision["previous_classification"]["category"] == "alcohol" and revision["reviewer_feedback"].startswith("0.0 marks")
    assert revision["line"]["original_description"] == "Guinness 0.0 draught pint"


# --- Freeze hardening: unresolved reviews, repeated reviews, line identity, audit ---


def reflect(monkeypatch, tmp_path, answers, reviews, revisions, revisers, chain, reflection=None, cache=True):
    calls, _ = stand_ins(monkeypatch, answers)
    reflected = Counter()
    reflection_stand_ins(monkeypatch, reviews, revisions, revisers, reflected)
    validator = live_validator(tmp_path if cache else None)
    batch = validator.validate_batch([ROUTED], context_complete=False, langgraph_config={**order(*chain), "reflection": {**CRITIC, **(reflection or {})}})
    lines = {short(result, line): line for result in batch.results for line in result.line_items}
    return batch, lines, audits(batch), dict(calls) | dict(reflected)


def test_a_rejection_left_unresolved_withholds_the_confidence_and_keeps_the_category(monkeypatch, tmp_path):
    """Jev-like primary: confident, rejected, and nothing can revise it: the engine must see an unresolved review."""
    batch, lines, audit, _ = reflect(monkeypatch, tmp_path, {"alpha-1": {"L1": ["alcohol", 0.95], "L2": ["alcohol", 0.95], "L3": ["service_fee", 0.95]}},
                                     {"L1:alcohol": ["revise", "food_beverage", "the line names a meal"]}, {}, [], [["only", "alpha", "alpha-1"]])
    assert lines["L1"].category == "alcohol" and lines["L1"].confidence is None, "kept, never replaced, and not passed on as confident"
    assert audit["L1"]["result"] == "no_reviser" and audit["L1"]["reflection"]["unresolved"] is True and audit["L1"]["reflection"]["withheld_confidence"] == 0.95
    assert audit["L1"]["reflection"]["reviews"][0]["feedback"] == "the line names a meal" and audit["L1"]["attempts"][0]["confidence"] == 0.95
    assert lines["L2"].confidence == 0.95 and audit["L2"]["reflection"]["unresolved"] is False
    uncertainty = {(item.line_id, item.state) for item in batch.results[0].uncertainty}
    assert ("RCP-002-L1", "no_confidence") in uncertainty and "EXC-03" in {signal.rule_id for signal in batch.results[0].review_signals}


@pytest.mark.parametrize("revisions, reflection, result", [({"alpha-1:L1:alcohol": ["alcohol", 0.9]}, {}, "revision_unchanged"), ({}, {}, "revision_failed"), ({}, {"max_retries": 0}, "retries_exhausted")])
def test_every_unresolved_rejection_is_exposed_to_the_uncertainty_rules(monkeypatch, tmp_path, revisions, reflection, result):
    _, lines, audit, _ = reflect(monkeypatch, tmp_path, {"alpha-1": {"L1": ["alcohol", 0.95], "L2": ["alcohol", 0.95], "L3": ["service_fee", 0.95]}},
                                 {"L1:alcohol": ["revise", "food_beverage", "the line names a meal"]}, revisions, ["alpha"], [["only", "alpha", "alpha-1"]], reflection)
    assert (lines["L1"].category, lines["L1"].confidence, audit["L1"]["result"], audit["L1"]["reflection"]["unresolved"]) == ("alcohol", None, result, True)


def test_an_uncertain_review_goes_on_to_the_next_backend_and_is_resolved_there(monkeypatch, tmp_path):
    _, lines, audit, calls = reflect(monkeypatch, tmp_path, {"alpha-1": {"L1": ["food_beverage", 0.95], "L2": ["alcohol", 0.95], "L3": ["other", 0.95]}, "beta-1": {"L3": ["service_fee", 0.95]}},
                                     {"L3:other": ["uncertain", None, None]}, {}, [], [["primary", "alpha", "alpha-1"], ["fallback", "beta", "beta-1"]])
    assert (lines["L3"].category, float(lines["L3"].confidence), audit["L3"]["answered_by"], audit["L3"]["result"]) == ("service_fee", 0.95, "fallback", "accepted")
    assert audit["L3"]["reflection"]["fallbacks"] == [{"from": "primary", "reason": "reviewer_uncertain"}] and audit["L3"]["reflection"]["unresolved"] is False
    assert calls == {"alpha-1": 3, "beta-1": 1, "critic-1": 4}


def test_an_uncertain_review_with_no_backend_left_stays_unresolved(monkeypatch, tmp_path):
    _, lines, audit, calls = reflect(monkeypatch, tmp_path, {"alpha-1": {"L1": ["food_beverage", 0.95], "L2": ["alcohol", 0.95], "L3": ["other", 0.95]}},
                                     {"L3:other": ["uncertain", None, None]}, {"alpha-1:L3:other": ["service_fee", 0.95]}, ["alpha"], [["only", "alpha", "alpha-1"]])
    assert (lines["L3"].category, lines["L3"].confidence, audit["L3"]["result"], audit["L3"]["reflection"]["unresolved"]) == ("other", None, "reviewer_uncertain", True)
    assert calls == {"alpha-1": 3, "critic-1": 3}, "uncertain is not revise: nothing is asked to revise"


@pytest.mark.parametrize("budget, result, unresolved, calls", [
    (1, "call_budget", False, {"alpha-1": 3}),  # spent just before the first review: never reviewed, kept as classified
    (2, "call_budget", True, {"alpha-1": 3, "critic-1": 3}),  # spent just before the revision the review asked for
    (3, "call_budget", True, {"alpha-1": 3, "critic-1": 3, "alpha-1+revise": 1}),  # spent just before reviewing the revision
    (4, "accepted", False, {"alpha-1": 3, "critic-1": 4, "alpha-1+revise": 1}),  # enough to finish
])
def test_the_call_budget_at_each_boundary(monkeypatch, tmp_path, budget, result, unresolved, calls):
    _, lines, audit, made = reflect(monkeypatch, tmp_path, {"alpha-1": {"L1": ["alcohol", 0.95], "L2": ["alcohol", 0.95], "L3": ["service_fee", 0.95]}},
                                    {"L1:alcohol": ["revise", "food_beverage", "the line names a meal"]}, {"alpha-1:L1:alcohol": ["food_beverage", 0.95]}, ["alpha"], [["only", "alpha", "alpha-1"]], {"max_total_calls_per_line": budget})
    assert (audit["L1"]["result"], audit["L1"]["reflection"]["unresolved"], audit["L1"]["reflection"]["calls"]) == (result, unresolved, min(budget, 4))
    assert (lines["L1"].confidence is None) is unresolved and made == calls


def test_a_proposal_already_reviewed_is_never_sent_for_review_again(monkeypatch, tmp_path):
    """The same classification, from another backend: its earlier verdict stands, with no call and no budget spent."""
    _, lines, audit, calls = reflect(monkeypatch, None, {"alpha-1": {"L1": ["alcohol", 0.95], "L2": ["alcohol", 0.95], "L3": ["service_fee", 0.95]}, "beta-1": {"L1": ["alcohol", 0.97]}},
                                     {"L1:alcohol": ["revise", "food_beverage", "the line names a meal"]}, {}, [], [["primary", "alpha", "alpha-1"], ["fallback", "beta", "beta-1"]], cache=False)
    reviews = audit["L1"]["reflection"]["reviews"]
    assert [(review["of"], review["outcome"], review["verdict"]) for review in reviews] == [("primary", "verdict", "revise"), ("fallback", "reused", "revise")]
    assert calls["critic-1"] == 3 and audit["L1"]["reflection"]["calls"] == 3, "alpha, its review and beta: the repeat costs nothing"
    assert (audit["L1"]["result"], lines["L1"].confidence) == ("repeated_objection", None)


def test_lines_with_identical_inputs_are_routed_by_their_own_identity(monkeypatch):
    """Two lines that send the same text, and only one needs the fallback: only that one reaches it."""
    twin = ROUTED.model_copy(update={"line_items": (ROUTED.line_items[0], ROUTED.line_items[1], ROUTED.line_items[0].model_copy(update={"line_id": "RCP-002-L3"}))})
    normalized = normalize_receipt(twin, NoOpTranslator())
    assert common.line_input(normalized, normalized.lines[0]) == common.line_input(normalized, normalized.lines[2])
    for line in normalized.lines[:2]:
        monkeypatch.setitem(SHORT, common.line_input(normalized, line), line.line_id.rsplit("-", 1)[1])
    calls, _ = stand_ins(monkeypatch, {"beta-1": {"L1": ["food_beverage", 0.97]}})
    first = []

    def flaky(client, settings, vocabulary):  # alpha's first call fails, as a live provider may; every later one answers
        def complete(prompt, text):
            calls[settings.classification_model] += 1
            first.append(text)
            if len(first) == 1:
                raise ClassificationError("alpha-1 provider failed")
            return json.dumps({"category": "food_beverage" if SHORT[text] == "L1" else "alcohol", "confidence": 0.95})

        return complete

    monkeypatch.setitem(registry.BACKENDS, "alpha", registry.BACKENDS["alpha"]._replace(connect=flaky))
    batch = live_validator(None).validate_batch([twin], context_complete=False, langgraph_config=order(["primary", "alpha", "alpha-1"], ["fallback", "beta", "beta-1"]))
    audit = {line.line_id: line.classification_details["orchestration"] for line in batch.results[0].line_items}
    assert calls == {"alpha-1": 3, "beta-1": 1}, "the twin that alpha answered is never sent to beta"
    assert (audit["RCP-002-L1"]["answered_by"], audit["RCP-002-L3"]["answered_by"]) == ("fallback", "primary")
    assert [attempt["name"] for attempt in audit["RCP-002-L3"]["attempts"]] == ["primary"] and len(audit["RCP-002-L1"]["attempts"]) == 2


def test_an_attempt_says_whether_the_model_answered(monkeypatch, tmp_path):
    """A failed call (no answer) is told apart from an answer the backend rejected, for any backend."""
    stand_ins(monkeypatch, {"alpha-1": {"L1": ["food_beverage", 0.95], "L2": ["not_a_category", 0.95]}})  # L3: the call fails
    batch = live_validator(tmp_path).validate_batch([ROUTED], context_complete=False, langgraph_config=order(["only", "alpha", "alpha-1"]))
    attempts = {line: audit["attempts"][0] for line, audit in audits(batch).items()}
    assert (attempts["L1"]["outcome"], attempts["L1"]["answered"]) == ("accepted", True)
    assert (attempts["L2"]["outcome"], attempts["L2"]["answered"]) == ("error", True), "answered, then rejected by the shared checks"
    assert (attempts["L3"]["outcome"], attempts["L3"]["answered"]) == ("error", False), "no answer: the call itself failed"


def test_a_missing_recording_did_not_answer(tmp_path, offline):
    validator = ExpenseValidator.from_config(CONFIG, mode="replay", cache_dir=recordings(tmp_path, jev=False), classification_backend="langgraph")
    attempts = audits(validator.validate_batch([RECEIPTS["RCP-012"]], context_complete=False))["RCP-012-L1"]["attempts"]
    assert [(a["outcome"], a["answered"]) for a in attempts] == [("missing_recording", False), ("accepted", True)]


def test_earlier_adapter_names_classify_exactly_as_the_adapters_own(tmp_path):
    """openrouter and jev, the adapters' earlier names, run the same adapters from the same recordings: the same
    answers, recorded under the adapters' own names, and the same prompt hash."""
    folder = recordings(tmp_path)
    batches = {}
    for label, links in (("earlier", (["primary", "jev", "typesafe/jev-1.14"], ["fallback", "openrouter", "anthropic/claude-haiku-5.5"])),
                         ("canonical", (["primary", "decisions", "typesafe/jev-1.14"], ["fallback", "chat_completions", "anthropic/claude-haiku-5.5"]))):
        validator = ExpenseValidator.from_config(CONFIG, mode="replay", cache_dir=folder, classification_backend="langgraph")
        batches[label] = validator.validate_batch(list(RECEIPTS.values())[:5], context_complete=True, langgraph_config=order(*links))
    assert [result.to_record() for result in batches["earlier"].results] == [result.to_record() for result in batches["canonical"].results]
    assert {attempt["backend"] for audit in audits(batches["earlier"]).values() for attempt in audit["attempts"]} <= {"decisions", "chat_completions"}
    assert batches["earlier"].results[0].metadata.prompt_sha256 == batches["canonical"].results[0].metadata.prompt_sha256

