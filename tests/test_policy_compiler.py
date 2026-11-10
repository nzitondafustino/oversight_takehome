"""Test compiler extraction, approval, and publication offline in temporary paths."""

import io
import json
import re
import shutil
import socket
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import URLError

import pytest
import yaml
from pydantic import SecretStr, ValidationError
from yaml_cases import load_cases

from expense_validator import ApprovalRefused, ConfigurationError, InvalidInputError, PolicyCompiler, ReplayAnswerMissing
from expense_validator.exceptions import RecordingConflict
from expense_validator.compiler import agreement
from expense_validator.compiler.agreement import output_sha256
from expense_validator.compiler.approval import approve
from expense_validator.compiler.build_rules import prepare_candidate
from expense_validator.compiler.extract import extract_document
from expense_validator.compiler.llm_extract import PROMPT, cache_key, document_sha256, model_recording, recorded, recording, recording_identity, recording_key
from expense_validator.config import ROOT, load_config
from expense_validator.models import CompilerSettings, ExtractionResult
from expense_validator.models.settings import RecordedWith

CONFIG = ROOT / "config.yaml"
RECORDINGS = ROOT / "tests" / "fixtures" / "llm_responses"
SCENARIOS = ROOT / "tests" / "fixtures" / "policy_scenarios"
PATHS = load_config().paths
APPROVER = {"approved_by": "test approver", "approved_at": "2026-11-05T15:00:00Z"}


def _documents(folder=PATHS.policy_docs, names=None):
    paths = sorted(folder.glob("*.md")) if names is None else [folder / name for name in names]
    return [{"document_id": f"doc-{path.stem}", "filename": path.name, "content": path.read_text()} for path in paths]


def _replay(recordings=RECORDINGS, **changes):
    settings = CompilerSettings.from_config(CONFIG, mode="replay", recordings_dir=recordings)
    return PolicyCompiler(settings.model_copy(update=changes))


def _live_settings(**changes):
    base = CompilerSettings.from_config(CONFIG, mode="replay", recordings_dir=RECORDINGS).model_dump()
    return CompilerSettings.model_validate({**base, "mode": "live", "recordings_dir": None, "extraction_model": "vendor/default-model", "openrouter_api_key": "test-key", **changes})


def _sign_offs(extraction: ExtractionResult):
    """Accept the reading the clause supports for each open item, as a reviewer would."""
    return [
        {"policy_doc": item.policy_doc, "rule_id": item.rule_id, "field": item.field, "value": item.regex, "clause_hash": item.clause_hash, "reviewer": "test reviewer", "rationale": "checked against the clause"}
        for item in extraction.review_items
    ]


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a network request was made")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


# --- Inputs and settings ---


@pytest.mark.parametrize("case", load_cases("policy_compiler", "documents_refused"))
def test_invalid_documents_are_refused_without_quoting_them(case, no_network):
    with pytest.raises(InvalidInputError, match=case["expected"]) as error:
        _replay().extract(case["documents"])
    assert "SECRET" not in str(error.value)


@pytest.mark.parametrize("case", load_cases("policy_compiler", "resolutions_refused"))
def test_invalid_resolutions_are_refused(case, no_network):
    with pytest.raises(InvalidInputError, match=case["expected"]):
        _replay().extract(_documents(), resolutions=case["resolutions"])


@pytest.mark.parametrize("case", load_cases("policy_compiler", "settings_refused"))
def test_settings_that_cannot_work_are_refused_at_construction(case):
    base = CompilerSettings.from_config(CONFIG, mode="replay", recordings_dir=RECORDINGS) if case["mode"] == "replay" else _live_settings()
    completion = (lambda prompt, text: "{}") if case["completion"] else None
    with pytest.raises((ConfigurationError, ValidationError), match=case["expected"]):
        PolicyCompiler(CompilerSettings.model_validate({**base.model_dump(), **case["changes"]}), completion=completion)


# --- Extraction ---


def test_in_memory_documents_give_exactly_what_the_file_based_gate_gives(no_network, tmp_path):
    extraction = _replay().extract(_documents())
    from_files = prepare_candidate(PATHS, recorded(RECORDINGS), [], "simulation")
    # With no customer in it, the in-memory output is the file-based one exactly, hash and all.
    assert extraction.output_sha256 == extraction.agreement["output_sha256"] == from_files.output["output_sha256"]
    assert extraction.agreement == from_files.output
    assert extraction.candidate.ruleset_sha256 == from_files.ruleset_sha256
    assert extraction.candidate.ruleset.to_json() == PATHS.ruleset.read_text()
    assert extraction.blockers == tuple(from_files.reasons) and not extraction.ready_for_approval
    assert [(item.document_id, item.rule_id, item.field, item.status, item.resolution) for item in extraction.review_items] == [
        ("doc-merchant-category-risk", "MCC-03", "thresholds", "disagree", None),
        ("doc-regional-addendum", "REG-01", "thresholds", "disagree", None),
    ]
    assert (extraction.mode, extraction.reading_kind, extraction.extraction_model) == ("replay", "simulation", None)
    assert len(extraction.clauses) == 41 and {d.document_id for d in extraction.documents} == {d["document_id"] for d in _documents()}
    assert ExtractionResult.model_validate_json(extraction.model_dump_json()) == extraction


def test_documents_need_no_repository_folder_and_their_order_does_not_matter(no_network):
    documents = _documents()
    assert _replay().extract(list(reversed(documents))) == _replay().extract(documents)


def test_resolutions_settle_review_items_and_make_it_ready(no_network):
    compiler = _replay()
    first = compiler.extract(_documents())
    settled = compiler.extract(_documents(), resolutions=_sign_offs(first))
    assert settled.ready_for_approval and settled.blockers == ()
    assert [item.resolution["reviewer"] for item in settled.review_items] == ["test reviewer", "test reviewer"]
    assert settled.output_sha256 != first.output_sha256


def test_extraction_writes_and_activates_nothing(no_network, tmp_path):
    before = PATHS.ruleset.read_bytes()
    compiler = _replay(ruleset_path=tmp_path / "ruleset.json")
    first = compiler.extract(_documents())
    compiler.extract(_documents(), resolutions=_sign_offs(first))
    assert list(tmp_path.iterdir()) == [] and PATHS.ruleset.read_bytes() == before


# --- Customers and concurrency ---


def test_requests_share_one_compiler_without_sharing_results(no_network):
    compiler = _replay()
    settings = compiler.settings
    alone = compiler.extract(_documents())
    other = compiler.extract(_documents(SCENARIOS, ["agree.md", "llm_added_a_rule.md"]))
    assert compiler.extract(_documents()) == alone
    assert {d.filename for d in other.documents} == {"agree.md", "llm_added_a_rule.md"}
    assert other.output_sha256 != alone.output_sha256 and not other.ready_for_approval
    assert compiler.settings is settings and compiler.settings == _replay().settings


def test_concurrent_requests_get_the_same_results_as_sequential_ones(no_network):
    compiler = _replay()
    requests = {
        "full policy": (_documents(), ()),
        "one document": (_documents(SCENARIOS, ["agree.md"]), ()),
        "with resolutions": (_documents(), _sign_offs(compiler.extract(_documents()))),
    }
    expected = {name: compiler.extract(docs, resolutions=res) for name, (docs, res) in requests.items()}
    order = list(requests) * 8
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda name: compiler.extract(requests[name][0], resolutions=requests[name][1]), order))
    assert all(result == expected[name] for name, result in zip(order, results))


# --- Approval and publication ---


def _approved(compiler):
    first = compiler.extract(_documents())
    resolutions = _sign_offs(first)
    approval = compiler.approve(compiler.extract(_documents(), resolutions=resolutions), **APPROVER)
    return resolutions, approval


def test_an_approved_simulation_publishes_the_same_ruleset_once(no_network, tmp_path):
    compiler = _replay(ruleset_path=tmp_path / "production.json")
    resolutions, approval = _approved(compiler)
    out = tmp_path / "ruleset.json"
    published = compiler.publish(_documents(), approval=approval.model_dump(), out=out, mode="simulation", resolutions=resolutions)
    assert published.published and published.reasons == () and out.read_text() == PATHS.ruleset.read_text()
    again = compiler.publish(_documents(), approval=approval, out=out, mode="simulation", resolutions=resolutions)
    assert not again.published and again.reasons == (f"{out} exists, and publish replaces a ruleset only with replace=True",)
    assert compiler.publish(_documents(), approval=approval, out=out, mode="simulation", resolutions=resolutions, replace=True).published


def test_unresolved_items_block_approval_and_publication(no_network, tmp_path):
    compiler = _replay()
    extraction = compiler.extract(_documents())
    with pytest.raises(ApprovalRefused, match="not ready for approval: 2 rules need review"):
        compiler.approve(extraction, **APPROVER)
    out = tmp_path / "ruleset.json"
    publication = compiler.publish(_documents(), approval=None, out=out, mode="simulation")
    assert not publication.published and not out.exists()
    assert publication.reasons == (*extraction.blockers, "no saved approval")


def test_an_approval_of_anything_else_does_not_hold(no_network, tmp_path):
    compiler = _replay()
    resolutions, approval = _approved(compiler)
    out = tmp_path / "ruleset.json"

    def reasons(approval, resolutions=resolutions):
        publication = compiler.publish(_documents(), approval=approval, out=out, mode="simulation", resolutions=resolutions)
        assert not publication.published and not out.exists()
        return publication.reasons

    other_rationale = [{**resolution, "rationale": "a different reason"} for resolution in resolutions]
    assert reasons(approval, other_rationale) == ("approval does not hold: the agreement output has changed since it was approved",)
    assert reasons(approval.model_copy(update={"ruleset_sha256": "0" * 64})) == ("approval does not hold: the candidate ruleset has changed since it was approved",)
    assert reasons(approval.model_copy(update={"output_sha256": "0" * 64})) == ("approval does not hold: the approval record does not match the output it holds",)
    with pytest.raises(InvalidInputError, match="invalid approval: approved_by"):
        reasons({**approval.model_dump(), "approved_by": " "})


def test_a_forged_extraction_cannot_be_approved_into_publication(no_network, tmp_path):
    compiler = _replay()
    resolutions, _ = _approved(compiler)
    settled = compiler.extract(_documents(), resolutions=resolutions)
    forged = settled.model_copy(update={"agreement": {**settled.agreement, "summary": {**settled.agreement["summary"], "rules": 1}}})
    publication = compiler.publish(_documents(), approval=compiler.approve(forged, **APPROVER), out=tmp_path / "r.json", mode="simulation", resolutions=resolutions)
    assert publication.reasons == ("approval does not hold: the approval record does not match the output it holds",)


def test_simulation_readings_never_authorize_production(no_network, tmp_path):
    compiler = _replay(ruleset_path=tmp_path / "production.json")
    resolutions, approval = _approved(compiler)
    production = compiler.publish(_documents(), approval=approval, out=tmp_path / "ruleset.json", mode="production", resolutions=resolutions)
    assert production.reasons == ("production publication needs model readings, and these are simulation", "approval is of simulation readings, which never authorize production")
    onto_production = compiler.publish(_documents(), approval=approval, out=tmp_path / "production.json", mode="simulation", resolutions=resolutions)
    assert onto_production.reasons == ("a simulation never writes the production ruleset",)
    assert list(tmp_path.iterdir()) == []


def test_model_readings_with_a_model_approval_publish_to_production(no_network, tmp_path):
    recordings = tmp_path / "recordings"
    shutil.copytree(RECORDINGS, recordings)
    for path in recordings.glob("*.json"):
        path.write_text(json.dumps({**json.loads(path.read_text()), "kind": "model"}))
    compiler = _replay(recordings, ruleset_path=tmp_path / "production.json")
    resolutions, approval = _approved(compiler)
    assert approval.reading_kind == "model"
    publication = compiler.publish(_documents(), approval=approval, out=tmp_path / "production.json", mode="production", resolutions=resolutions)
    assert publication.published and (tmp_path / "production.json").read_text() == PATHS.ruleset.read_text()


def test_publication_arguments_are_checked(no_network, tmp_path):
    compiler = _replay()
    with pytest.raises(InvalidInputError, match="mode must be production or simulation"):
        compiler.publish(_documents(), approval=None, out=tmp_path / "r.json", mode="live")
    with pytest.raises(InvalidInputError, match="out: no folder"):
        compiler.publish(_documents(), approval=None, out=tmp_path / "missing" / "r.json", mode="simulation")


# --- Replay and live readings ---


def test_replay_fails_closed_on_a_document_with_no_recording(no_network):
    documents = [*_documents(), {"document_id": "new", "filename": "new-policy.md", "content": "**NEW-01 — New.** A new rule."}]
    with pytest.raises(ReplayAnswerMissing) as error:
        _replay().extract(documents)
    assert error.value.missing == ("new-policy.md",)


def test_replay_takes_no_model_override(no_network):
    with pytest.raises(ConfigurationError, match="replay reads recordings by document"):
        _replay().extract(_documents(), extraction_model="vendor/model")


def test_recordings_of_mixed_kinds_are_refused(no_network, tmp_path):
    recordings = tmp_path / "recordings"
    shutil.copytree(RECORDINGS, recordings)
    first = recordings / f"{document_sha256(_documents()[0]['content'])}.json"
    first.write_text(json.dumps({**json.loads(first.read_text()), "kind": "model"}))
    with pytest.raises(ConfigurationError, match="must all be one kind"):
        _replay(recordings).extract(_documents())


class _FakeOpenRouter:
    """Answers each request with the recorded response for its document, and keeps the bodies sent."""

    def __init__(self, fail=False):
        self.bodies, self.timeouts, self.fail = [], [], fail

    def __call__(self, request, timeout):
        body = json.loads(request.data)
        self.bodies.append(body)
        self.timeouts.append(timeout)
        if self.fail:
            raise URLError("unreachable")
        text = body["messages"][1]["content"]
        answer = json.loads((RECORDINGS / f"{document_sha256(text)}.json").read_text())["response"]
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": answer}}]}).encode())


def test_a_live_request_uses_its_own_model_and_cache_entry(monkeypatch, tmp_path):
    fake = _FakeOpenRouter()
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    compiler = PolicyCompiler(_live_settings(cache_dir=tmp_path / "cache"))
    documents = _documents(SCENARIOS, ["agree.md"])
    default = compiler.extract(documents)
    chosen = compiler.extract(documents, extraction_model="vendor/request-model")
    compiler.extract(documents)
    assert [body["model"] for body in fake.bodies] == ["vendor/default-model", "vendor/request-model"]  # the third was cached
    assert (default.extraction_model, chosen.extraction_model) == ("vendor/default-model", "vendor/request-model")
    assert default.reading_kind == chosen.reading_kind == "model"
    assert len(list((tmp_path / "cache").glob("*.txt"))) == 2
    assert compiler.settings.extraction_model == "vendor/default-model"
    assert "test-key" not in default.model_dump_json() and "test-key" not in repr(compiler.settings)


def test_a_failed_live_request_is_a_reading_error_and_is_not_cached(monkeypatch, tmp_path):
    monkeypatch.setattr(urllib.request, "urlopen", _FakeOpenRouter(fail=True))
    extraction = PolicyCompiler(_live_settings(cache_dir=tmp_path / "cache")).extract(_documents(SCENARIOS, ["agree.md"]))
    assert [d.reading_error for d in extraction.documents] == ["ValueError: the model request failed: URLError"]
    assert not extraction.ready_for_approval and not (tmp_path / "cache").exists()


def test_an_injected_completion_is_simulation_uncached_and_takes_no_override(no_network, tmp_path):
    answers = {document_sha256(d["content"]): json.loads((RECORDINGS / f"{document_sha256(d['content'])}.json").read_text())["response"] for d in _documents()}
    compiler = PolicyCompiler(_live_settings(cache_dir=tmp_path / "cache"), completion=lambda prompt, text: answers[document_sha256(text)])
    extraction = compiler.extract(_documents())
    assert extraction.reading_kind == "simulation" and extraction.output_sha256 == _replay().extract(_documents()).output_sha256
    assert not (tmp_path / "cache").exists()
    with pytest.raises(ConfigurationError, match="injected completion ignores extraction_model"):
        compiler.extract(_documents(), extraction_model="vendor/other")


# --- Generation settings: config.yaml, then initialization, then the request ---


def _config_file(tmp_path, **llm):
    """A config.yaml like the project's, with its paths made absolute and these llm values."""
    config = yaml.safe_load(CONFIG.read_text())
    config["llm"].update(llm)
    config["paths"] = {name: str(ROOT / path) for name, path in config["paths"].items()}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    return path


def _sent(fake):
    return [(body["model"], body["temperature"], timeout) for body, timeout in zip(fake.bodies, fake.timeouts)]


def _generation(result):
    return (result.extraction_model, result.temperature, result.timeout_seconds, result.overrides)


@pytest.mark.parametrize("case", load_cases("policy_compiler", "overrides_refused"))
def test_invalid_or_inapplicable_overrides_are_refused_before_any_request(case, no_network):
    with pytest.raises(ConfigurationError, match=case["expected"]):
        CompilerSettings.from_config(CONFIG, mode=case["mode"], recordings_dir=RECORDINGS, **case["overrides"])
    compiler = _replay() if case["mode"] == "replay" else PolicyCompiler(_live_settings())
    settings = compiler.settings
    with pytest.raises(ConfigurationError, match=case["expected"]):
        compiler.extract(_documents(), **case["overrides"])
    assert compiler.settings is settings


def test_a_request_wins_over_initialization_which_wins_over_the_config_file(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    fake = _FakeOpenRouter()
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    config = _config_file(tmp_path, model="vendor/file-model", temperature=0.1, timeout_seconds=30)
    from_file = PolicyCompiler.from_config(config, mode="live", cache_dir=None)
    initialized = PolicyCompiler.from_config(config, mode="live", cache_dir=None, extraction_model="vendor/init-model", temperature=0.0)
    documents = _documents(SCENARIOS, ["agree.md"])
    results = [
        from_file.extract(documents),
        initialized.extract(documents),
        initialized.extract(documents, extraction_model="vendor/request-model", temperature=0.2, timeout_seconds=5),
        initialized.extract(documents, timeout_seconds=60),
    ]
    assert _sent(fake) == [("vendor/file-model", 0.1, 30), ("vendor/init-model", 0.0, 30), ("vendor/request-model", 0.2, 5), ("vendor/init-model", 0.0, 60)]
    assert [_generation(result) for result in results] == [
        ("vendor/file-model", 0.1, 30, ()),
        ("vendor/init-model", 0.0, 30, ()),
        ("vendor/request-model", 0.2, 5, ("extraction_model", "temperature", "timeout_seconds")),
        ("vendor/init-model", 0.0, 60, ("timeout_seconds",)),
    ]
    assert (initialized.settings.extraction_model, initialized.settings.temperature, initialized.settings.timeout_seconds) == ("vendor/init-model", 0.0, 30)
    assert "test-key" not in "".join(result.model_dump_json() for result in results)


def test_replay_ignores_the_configured_generation_settings_and_reports_none(no_network, tmp_path):
    config = _config_file(tmp_path, model="vendor/file-model", temperature=0.7)
    extraction = PolicyCompiler.from_config(config, mode="replay", recordings_dir=RECORDINGS).extract(_documents())
    assert _generation(extraction) == (None, None, None, ())
    assert extraction.output_sha256 == _replay().extract(_documents()).output_sha256


def test_concurrent_requests_each_use_their_own_generation_settings(monkeypatch, tmp_path):
    fake = _FakeOpenRouter()
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    compiler = PolicyCompiler(_live_settings(cache_dir=None))
    settings = compiler.settings
    documents = _documents(SCENARIOS, ["agree.md"])
    choices = [(f"vendor/model-{n % 3}", n % 4 / 2, 10 + n) for n in range(24)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda c: compiler.extract(documents, extraction_model=c[0], temperature=c[1], timeout_seconds=c[2]), choices))
    assert [(r.extraction_model, r.temperature, r.timeout_seconds) for r in results] == [(m, float(temp), t) for m, temp, t in choices]
    assert sorted(_sent(fake)) == sorted((m, float(temp), t) for m, temp, t in choices)
    assert compiler.settings is settings and settings == _live_settings(cache_dir=None)


def test_the_cache_separates_models_and_temperatures_but_not_timeouts(monkeypatch, tmp_path):
    fake = _FakeOpenRouter()
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    compiler = PolicyCompiler(_live_settings(cache_dir=tmp_path / "cache"))
    documents = _documents(SCENARIOS, ["agree.md"])
    for overrides in [{}, {"temperature": 0.2}, {"extraction_model": "vendor/other"}, {"temperature": 0}, {"temperature": 0.2, "timeout_seconds": 5}, {"extraction_model": "vendor/other", "timeout_seconds": 9}]:
        compiler.extract(documents, **overrides)
    assert _sent(fake) == [("vendor/default-model", 0.0, 180), ("vendor/default-model", 0.2, 180), ("vendor/other", 0.0, 180)]
    assert len(list((tmp_path / "cache").glob("*.txt"))) == 3


def test_the_cache_key_holds_the_model_and_temperature():
    keys = {cache_key("vendor/a", 0), cache_key("vendor/a", 0.0), cache_key("vendor/a", 0.2), cache_key("vendor/b", 0)}
    assert len(keys) == 3 and cache_key("vendor/a", 0) == cache_key("vendor/a", 0.0)


def test_publication_with_request_overrides_keeps_every_safeguard(monkeypatch, tmp_path):
    monkeypatch.setattr(urllib.request, "urlopen", _FakeOpenRouter())
    compiler = PolicyCompiler(_live_settings(cache_dir=None, ruleset_path=tmp_path / "production.json"))
    chosen = {"extraction_model": "vendor/request-model", "temperature": 0.2, "timeout_seconds": 20}
    resolutions = _sign_offs(compiler.extract(_documents(), **chosen))
    extraction = compiler.extract(_documents(), resolutions=resolutions, **chosen)
    approval = compiler.approve(extraction, **APPROVER)
    assert approval.reading_kind == "model" and approval.output_sha256 == extraction.output_sha256
    unapproved = compiler.publish(_documents(), approval=None, out=tmp_path / "a.json", mode="production", resolutions=resolutions, **chosen)
    assert unapproved.reasons == ("no saved approval",) and not (tmp_path / "a.json").exists()
    out = tmp_path / "ruleset.json"
    published = compiler.publish(_documents(), approval=approval, out=out, mode="production", resolutions=resolutions, **chosen)
    assert published.published and out.read_text() == PATHS.ruleset.read_text()
    assert _generation(published) == ("vendor/request-model", 0.2, 20, ("extraction_model", "temperature", "timeout_seconds"))
    onto_production = compiler.publish(_documents(), approval=approval, out=tmp_path / "production.json", mode="simulation", resolutions=resolutions, **chosen)
    assert onto_production.reasons == ("a simulation never writes the production ruleset",)


def test_the_agreement_command_caches_by_model_and_temperature(monkeypatch, tmp_path, capsys):
    fake = _FakeOpenRouter()
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    for temperature in (0, 0.3, 0.3):
        config = load_config()
        config = config.model_copy(update={"llm": config.llm.model_copy(update={"model": "vendor/cli-model", "temperature": temperature}), "openrouter_api_key": SecretStr("test-key"), "paths": config.paths.model_copy(update={"llm_cache": tmp_path / "cache"})})
        monkeypatch.setattr(agreement, "load_config", lambda config=config: config)
        assert agreement.main([]) == 0
    assert sorted({(model, temperature) for model, temperature, _ in _sent(fake)}) == [("vendor/cli-model", 0.0), ("vendor/cli-model", 0.3)]
    assert len(fake.bodies) == 2 * len(_documents())


def test_a_rule_id_in_two_documents_blocks_instead_of_dropping_one(no_network, tmp_path):
    shadow = {"document_id": "shadow", "filename": "a-shadow-policy.md", "content": "# Shadow\n\n**MEAL-01 — Meals are capped at $5000.** Meals are capped at **$5000** per person, a **low** exception."}

    def agreeing(prompt, text):
        recording = RECORDINGS / f"{document_sha256(text)}.json"
        if recording.exists():
            return json.loads(recording.read_text())["response"]
        clause = extract_document(text, "shadow.md").clauses[0]
        rule = {"rule_id": clause.rule_id, "title": clause.title, "section": None, "severities": clause.severities, "review_signal": False, "thresholds": [{"value": "5000", "unit": "usd"}], "references": [], "quote": "Meals are capped at"}
        return json.dumps({"rules": [rule]})

    compiler = PolicyCompiler(_live_settings(cache_dir=None), completion=agreeing)
    resolutions = _sign_offs(_replay().extract(_documents()))
    extraction = compiler.extract([*_documents(), shadow], resolutions=resolutions)
    assert not extraction.ready_for_approval and extraction.candidate is None
    assert extraction.blockers == ("build failed: rule IDs in more than one document: ['MEAL-01']",)
    with pytest.raises(ApprovalRefused, match="no valid candidate ruleset"):
        compiler.approve(extraction, **APPROVER)


# --- Approval safety: thresholds that mean what they say, and approvals of one exact output ---


def _resolution(extraction, rule_id, value):
    item = next(i for i in extraction.review_items if i.rule_id == rule_id)
    return {"policy_doc": item.policy_doc, "rule_id": rule_id, "field": item.field, "value": value, "clause_hash": item.clause_hash, "reviewer": "test reviewer", "rationale": "probe"}


@pytest.mark.parametrize("rule_id, value, message", [
    ("MCC-03", [{"value": "5813", "unit": "count"}], "MCC-03 thresholds: the clause states 5813, but not as a limit with unit count. Its limits are: none"),
    ("REG-01", [{"value": "3", "unit": "hour"}], "REG-01 thresholds: the clause states 3, but not as a limit with unit hour. Its limits are: none"),
    ("REG-01", [{"value": "3", "unit": "count"}], "REG-01 thresholds: the clause states 3, but not as a limit with unit count"),
])
def test_a_number_that_is_not_a_limit_cannot_be_resolved_as_one(no_network, rule_id, value, message):
    compiler = _replay()
    first = compiler.extract(_documents())
    resolutions = [_resolution(first, rule_id, value) if r["rule_id"] == rule_id else r for r in _sign_offs(first)]
    extraction = compiler.extract(_documents(), resolutions=resolutions)
    assert not extraction.ready_for_approval
    assert any(message in blocker for blocker in extraction.blockers), extraction.blockers
    with pytest.raises(ApprovalRefused):
        compiler.approve(extraction, **APPROVER)


def _meal_disagreement(tmp_path):
    """A replay compiler whose MEAL-01 reading says $80 where the clause says $75."""
    recordings = tmp_path / "recordings"
    shutil.copytree(RECORDINGS, recordings)
    meals = next(d for d in _documents() if d["filename"] == "meals-and-entertainment-policy.md")
    path = recordings / f"{document_sha256(meals['content'])}.json"
    entry = json.loads(path.read_text())
    entry["response"] = entry["response"].replace('{"value": "75", "unit": "usd"}', '{"value": "80", "unit": "usd"}', 1)
    path.write_text(json.dumps(entry))
    return _replay(recordings, ruleset_path=tmp_path / "production.json")


def test_a_supported_threshold_resolves_and_publishes(no_network, tmp_path):
    compiler = _meal_disagreement(tmp_path)
    first = compiler.extract(_documents())
    assert [(i.rule_id, i.status) for i in first.review_items if i.rule_id == "MEAL-01"] == [("MEAL-01", "disagree")]
    stated = [{"value": "75", "unit": "usd"}, {"value": "100.00", "unit": "usd"}]
    resolutions = [_resolution(first, "MEAL-01", stated) if r["rule_id"] == "MEAL-01" else r for r in _sign_offs(first)]
    extraction = compiler.extract(_documents(), resolutions=resolutions)
    assert extraction.ready_for_approval, extraction.blockers
    out = tmp_path / "ruleset.json"
    published = compiler.publish(_documents(), approval=compiler.approve(extraction, **APPROVER), out=out, mode="simulation", resolutions=resolutions)
    assert published.published and out.read_text() == PATHS.ruleset.read_text()


@pytest.mark.parametrize("value, held", [
    ([{"value": "100", "unit": "usd"}], "[100 usd], but the rules hold [75 usd, 100 usd]"),
    ([], "[], but the rules hold [75 usd, 100 usd]"),
])
def test_a_threshold_the_executable_rules_do_not_hold_rejects_the_candidate(no_network, tmp_path, value, held):
    compiler = _meal_disagreement(tmp_path)
    first = compiler.extract(_documents())
    resolutions = [_resolution(first, "MEAL-01", value) if r["rule_id"] == "MEAL-01" else r for r in _sign_offs(first)]
    extraction = compiler.extract(_documents(), resolutions=resolutions)
    assert extraction.candidate is None and not extraction.ready_for_approval
    assert extraction.blockers == (f"resolution conflicts with the executable rules: meals-and-entertainment-policy.md MEAL-01 thresholds resolved as {held}. Update rules/rule_logic.yaml and validate again, or resolve to what the rules hold",)
    with pytest.raises(ApprovalRefused, match="no valid candidate ruleset"):
        compiler.approve(extraction, **APPROVER)


def test_an_approval_publishes_only_the_output_it_holds(no_network, tmp_path):
    compiler = _replay(ruleset_path=tmp_path / "production.json")
    resolutions, approval = _approved(compiler)
    assert "customer_id" not in approval.output

    def reasons(name, approval):
        out = tmp_path / f"{name}.json"
        result = compiler.publish(_documents(), approval=approval, out=out, mode="simulation", resolutions=resolutions)
        assert result.published == (not result.reasons) and out.exists() == result.published
        return result.reasons

    rewritten = approval.model_copy(update={"output": {**approval.output, "reading_kind": "model"}})
    assert reasons("rewritten", rewritten) == ("approval does not hold: the approval record does not match the output it holds",)
    assert reasons("approved", approval) == ()


def test_an_approval_made_the_cli_way_publishes_the_same_output(no_network, tmp_path):
    compiler = _replay()
    resolutions, approval = _approved(compiler)
    cli_style = approve(approval.output, approval.ruleset_sha256, "cli approver", APPROVER["approved_at"])
    result = compiler.publish(_documents(), approval=cli_style, out=tmp_path / "r.json", mode="simulation", resolutions=resolutions)
    assert result.published and result.reasons == ()


def test_an_approval_saved_with_a_customer_is_read_but_no_longer_covers_the_output(no_network, tmp_path):
    """An approval from before customer_id was removed binds an output that held one, so it never matches now."""
    compiler = _replay()
    resolutions, approval = _approved(compiler)
    output = {**{k: v for k, v in approval.output.items() if k != "output_sha256"}, "customer_id": "customer-a"}
    legacy = {**approval.model_dump(mode="json"), "customer_id": "customer-a", "output": {**output, "output_sha256": output_sha256(output)}, "output_sha256": output_sha256(output)}
    result = compiler.publish(_documents(), approval=legacy, out=tmp_path / "r.json", mode="simulation", resolutions=resolutions)
    assert result.reasons == ("approval does not hold: the agreement output has changed since it was approved",)


def test_concurrent_publications_of_one_approval_all_publish(no_network, tmp_path):
    compiler = _replay(ruleset_path=tmp_path / "production.json")
    resolutions = _sign_offs(compiler.extract(_documents()))
    approval = compiler.approve(compiler.extract(_documents(), resolutions=resolutions), **APPROVER)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda n: compiler.publish(_documents(), approval=approval, out=tmp_path / f"r-{n}.json", mode="simulation", resolutions=resolutions), range(12)))
    assert all(result.published and result.reasons == () for result in results)


# --- Live recordings ---


def _recorded_live(monkeypatch, recordings, **overrides):
    fake = _FakeOpenRouter()
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    extraction = PolicyCompiler(_live_settings(cache_dir=None, recordings_dir=recordings)).extract(_documents(), **overrides)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("a request was made"))
    return extraction, fake


def _replay_recorded(recordings, model="vendor/live-model", temperature=0.2):
    return _replay(recordings, recorded_with=RecordedWith(extraction_model=model, temperature=temperature))


def test_live_answers_are_recorded_under_their_settings_and_replay_offline(monkeypatch, tmp_path):
    recordings = tmp_path / "live"
    extraction, _ = _recorded_live(monkeypatch, recordings, extraction_model="vendor/live-model", temperature=0.2)
    entries = [json.loads(path.read_text()) for path in sorted(recordings.glob("*.json"))]
    assert len(entries) == 9 and all(path.name.startswith("configured-") for path in recordings.iterdir())
    assert {e["kind"] for e in entries} == {"model"} and {e["policy_doc"] for e in entries} == {d["filename"] for d in _documents()}
    identity = entries[0]["identity"]
    assert (identity["provider"], identity["model"], identity["temperature"], identity["prompt_sha256"]) == ("openrouter", "vendor/live-model", 0.2, document_sha256(PROMPT))
    assert "test-key" not in json.dumps(entries)
    assert all(entries_path.name == f"configured-{recording_key(e['identity'])}.json" for entries_path, e in zip(sorted(recordings.glob("*.json")), entries))
    replayed = _replay_recorded(recordings).extract(_documents())
    assert (replayed.reading_kind, replayed.extraction_model, replayed.temperature) == ("model", "vendor/live-model", 0.2)
    assert replayed.output_sha256 == extraction.output_sha256


def test_replay_with_other_settings_fails_closed(monkeypatch, tmp_path):
    recordings = tmp_path / "live"
    _recorded_live(monkeypatch, recordings, extraction_model="vendor/live-model", temperature=0.2)
    for model, temperature in [("vendor/other-model", 0.2), ("vendor/live-model", 0.0)]:
        with pytest.raises(ConfigurationError, match="recorded only with other settings, which replay never uses"):
            _replay_recorded(recordings, model, temperature).extract(_documents())
    with pytest.raises(ConfigurationError, match="replay reads recordings by document"):
        _replay_recorded(recordings).extract(_documents(), temperature=0.2)


def test_a_recording_in_the_format_that_held_a_customer_never_replays(monkeypatch, tmp_path):
    """Format 2 recordings carried a tenant hash. Their identity is not today's, so replay neither uses nor offers them."""
    recordings = tmp_path / "live"
    _recorded_live(monkeypatch, recordings, extraction_model="vendor/live-model", temperature=0.2)
    for path in list(recordings.glob("configured-*.json")):
        entry = json.loads(path.read_text())
        identity = {**entry["identity"], "format": 2, "tenant_sha256": document_sha256("customer\0customer-a")}
        (recordings / f"configured-{recording_key(identity)}.json").write_text(json.dumps({**entry, "identity": identity}))
        path.unlink()
    with pytest.raises(ReplayAnswerMissing) as error:
        _replay_recorded(recordings).extract(_documents())
    assert len(error.value.missing) == 9


def test_new_settings_add_recordings_and_never_replace_old_ones(monkeypatch, tmp_path):
    recordings = tmp_path / "live"
    _recorded_live(monkeypatch, recordings, extraction_model="vendor/live-model", temperature=0.2)
    before = {path.name: path.read_bytes() for path in recordings.iterdir()}
    _, again = _recorded_live(monkeypatch, recordings, extraction_model="vendor/live-model", temperature=0.2)
    assert again.bodies == [] and {path.name: path.read_bytes() for path in recordings.iterdir()} == before
    _, other = _recorded_live(monkeypatch, recordings, extraction_model="vendor/live-model", temperature=0.7)
    assert len(other.bodies) == 9 and len(list(recordings.iterdir())) == 18
    assert all((recordings / name).read_bytes() == content for name, content in before.items())


def test_a_fresh_reading_calls_the_model_again_and_replays_by_its_own_id(monkeypatch, tmp_path):
    recordings = tmp_path / "live"
    _recorded_live(monkeypatch, recordings, extraction_model="vendor/live-model", temperature=0.2)
    before = {path.name: path.read_bytes() for path in recordings.iterdir()}
    fake = _FakeOpenRouter()
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    settings = _live_settings(cache_dir=tmp_path / "cache", recordings_dir=recordings, reading_id="ext_fresh")
    fresh = PolicyCompiler(settings).extract(_documents(), extraction_model="vendor/live-model", temperature=0.2)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("a request was made"))
    assert len(fake.bodies) == 9  # every document read again, though each was recorded with these settings
    assert len(list(recordings.iterdir())) == 18 and all((recordings / name).read_bytes() == content for name, content in before.items())
    chosen = RecordedWith(extraction_model="vendor/live-model", temperature=0.2, reading_id="ext_fresh")
    assert _replay(recordings, recorded_with=chosen).extract(_documents()).output_sha256 == fresh.output_sha256
    with pytest.raises(ConfigurationError, match="recorded only with other settings"):
        _replay(recordings, recorded_with=chosen.model_copy(update={"reading_id": "ext_other"})).extract(_documents())


def test_a_fresh_reading_is_live_and_recorded_only(tmp_path):
    with pytest.raises(ConfigurationError, match="reading_id names a fresh model reading"):
        PolicyCompiler(_live_settings(reading_id="ext_fresh"))  # nowhere to record it
    with pytest.raises(ConfigurationError, match="reading_id names a fresh model reading"):
        PolicyCompiler(_live_settings(recordings_dir=tmp_path / "live", reading_id="ext_fresh"), completion=lambda prompt, text: "{}")
    with pytest.raises(ValidationError):
        RecordedWith(extraction_model="vendor/m", temperature=0.0, reading_id="not a valid id")


def test_legacy_recordings_replay_as_before_and_record_no_settings(no_network, tmp_path):
    legacy = _replay().extract(_documents())
    assert (legacy.reading_kind, legacy.extraction_model, legacy.temperature) == ("simulation", None, None)
    with pytest.raises(ConfigurationError, match="only a recording by document hash exists, which records no generation settings"):
        _replay_recorded(RECORDINGS).extract(_documents())
    with pytest.raises(ReplayAnswerMissing):
        _replay_recorded(tmp_path).extract(_documents())


def test_a_tampered_or_simulated_configured_recording_is_refused(monkeypatch, tmp_path):
    recordings = tmp_path / "live"
    _recorded_live(monkeypatch, recordings, extraction_model="vendor/live-model", temperature=0.2)
    path = sorted(recordings.glob("*.json"))[0]
    entry = json.loads(path.read_text())
    for tampered, message in [
        ({**entry, "kind": "simulation"}, "is not a model recording"),
        ({**entry, "identity": {**entry["identity"], "model": "vendor/other"}}, "does not record these generation settings"),
        ({k: v for k, v in entry.items() if k != "identity"}, "does not record these generation settings"),
    ]:
        path.write_text(json.dumps(tampered))
        with pytest.raises(RecordingConflict, match=message):
            _replay_recorded(recordings).extract(_documents())
    simulation = json.loads(next(RECORDINGS.glob("*.json")).read_text())
    path.write_text(json.dumps(simulation))
    with pytest.raises(RecordingConflict, match="does not record these generation settings"):
        _replay_recorded(recordings).extract(_documents())


def test_a_recording_is_never_replaced_by_a_different_answer(tmp_path):
    first = recording(lambda prompt, text: '{"rules": []}', tmp_path, "doc.md", "test")
    first(PROMPT, "some text")
    assert first(PROMPT, "some text") == '{"rules": []}'
    with pytest.raises(RecordingConflict, match="already holds a different recording"):
        recording(lambda prompt, text: '{"rules": [1]}', tmp_path, "doc.md", "test")(PROMPT, "some text")
    assert json.loads(next(tmp_path.glob("*.json")).read_text())["response"] == '{"rules": []}'


@pytest.mark.parametrize("distinct_answers", [False, True])
def test_concurrent_writers_leave_one_whole_recording(tmp_path, distinct_answers):
    generation = {"url": "https://example.test", "model": "vendor/m", "temperature": 0.0}
    gate = threading.Barrier(16)

    def write(n):
        def complete(prompt, text):
            gate.wait()
            return json.dumps({"rules": [], "n": n if distinct_answers else 0})

        try:
            return model_recording(complete, tmp_path, generation, lambda text: "doc.md", {})(PROMPT, "text")
        except RecordingConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(write, range(16)))
    files = list(tmp_path.glob("configured-*.json"))
    assert len(files) == 1 and not list(tmp_path.glob(".*.tmp"))
    kept = json.loads(files[0].read_text())["response"]
    assert all(result in (kept, "conflict") for result in results) and kept in results
    assert results.count("conflict") == (15 if distinct_answers else 0)


@pytest.mark.parametrize("case", load_cases("policy_compiler", "recording_keys"))
def test_recording_keys_change_with_everything_that_decides_an_answer(case):
    base = {"text": "a policy", "prompt": PROMPT, "url": "https://openrouter.ai/api/v1/chat/completions", "model": "vendor/m", "temperature": 0.0}
    key = lambda arguments: recording_key(recording_identity(arguments.pop("text"), arguments.pop("prompt"), **arguments))
    assert (key(dict(base)) == key({**base, **case["changes"]})) == case["expected"]["same"]


def test_live_recordings_never_mix_with_simulations_or_stand_ins(tmp_path):
    with pytest.raises(ConfigurationError, match="not model readings: record live answers in a folder of their own"):
        PolicyCompiler(_live_settings(recordings_dir=RECORDINGS))
    with pytest.raises(ConfigurationError, match="injected completion's answers are not a model's"):
        PolicyCompiler(_live_settings(recordings_dir=tmp_path / "live"), completion=lambda prompt, text: "{}")
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "x.json").write_text("not json")
    with pytest.raises(ConfigurationError, match="not a recording"):
        PolicyCompiler(_live_settings(recordings_dir=tmp_path / "broken"))


def test_live_mode_takes_no_recorded_with_and_replay_checks_it(tmp_path):
    with pytest.raises(ConfigurationError, match="recorded_with chooses recordings to replay, so live mode takes none"):
        PolicyCompiler(_live_settings(recorded_with=RecordedWith(extraction_model="vendor/m", temperature=0.0)))
    for bad in ({"extraction_model": " ", "temperature": 0.0}, {"extraction_model": "vendor/m", "temperature": 3.0}, {"extraction_model": "vendor/m", "temperature": True}):
        with pytest.raises(ValidationError):
            RecordedWith(**bad)


# --- Reviewed rule overrides ---


@pytest.fixture(scope="module")
def signed_off():
    compiler = _replay()
    resolutions = _sign_offs(compiler.extract(_documents()))
    return compiler, resolutions, compiler.extract(_documents(), resolutions=resolutions)


@pytest.mark.parametrize("case", load_cases("policy_compiler", "rule_overrides_accepted"), ids=lambda case: case["id"])
def test_a_reviewed_logic_change_is_built_and_validated_by_the_compiler(case, signed_off, no_network):
    compiler, resolutions, plain = signed_off
    overridden = compiler.extract(_documents(), resolutions=resolutions, rule_overrides=case["overrides"])
    assert overridden.ready_for_approval and overridden.blockers == ()
    assert overridden.candidate.ruleset_sha256 != plain.candidate.ruleset_sha256
    assert overridden.extraction_sha256 == plain.extraction_sha256  # the readings are the same: only the logic changed
    rule = next(rule for rule in overridden.candidate.ruleset.rules if rule.rule_id == case["expected"]["rule_id"]).model_dump(mode="json", exclude_none=True)
    held = rule
    for part in case["expected"]["field"].split("."):
        held = held[part]
    assert held == case["expected"]["value"]


@pytest.mark.parametrize("case", load_cases("policy_compiler", "rule_overrides_blocked"), ids=lambda case: case["id"])
def test_a_logic_change_the_policy_does_not_state_is_blocked(case, signed_off, no_network):
    compiler, resolutions, _ = signed_off
    blocked = compiler.extract(_documents(), resolutions=resolutions, rule_overrides=case["overrides"])
    assert blocked.candidate is None and not blocked.ready_for_approval
    assert case["expected"]["blocker"] in blocked.blockers
    # The same difference, as data: what the clause states beside what the rule does.
    assert [item.model_dump() for item in blocked.discrepancies] == case["expected"]["discrepancies"]


def _amended(tmp_path, case) -> tuple[list[dict], "Path"]:
    """The policy documents with one revised, and recordings that hold an authored reading of the revised text."""
    documents = _documents()
    target = next(document for document in documents if document["filename"] == case["document"])
    original = target["content"]
    if "append" in case:  # a new clause
        target["content"] = original.rstrip("\n") + case["append"]
    else:
        assert case["replace"][0] in original
        target["content"] = original.replace(*case["replace"], 1)
    recordings = tmp_path / "recordings"
    shutil.copytree(RECORDINGS, recordings)
    entry = json.loads((RECORDINGS / f"{document_sha256(original)}.json").read_text())
    response = json.loads(entry["response"])
    if case.get("append"):
        response["rules"].append(case["reading"])
    elif case["reading"]:
        for rule in response["rules"]:
            if rule["rule_id"] == case["reading"]["rule_id"]:
                rule["thresholds"] = case["reading"]["thresholds"]
    revised = {**entry, "document_sha256": document_sha256(target["content"]), "origin": "authored in a test: the reading of a revised document", "response": json.dumps(response, ensure_ascii=False)}
    (recordings / f"{revised['document_sha256']}.json").write_text(json.dumps(revised))
    return documents, recordings


@pytest.mark.parametrize("case", load_cases("policy_compiler", "amendments"), ids=lambda case: case["id"])
def test_a_revised_policy_is_compared_with_the_rule_logic_not_with_earlier_versions(case, tmp_path, no_network):
    documents, recordings = _amended(tmp_path, case)
    extraction = _replay(recordings).extract(documents, rule_overrides=case["overrides"])
    assert (extraction.candidate is not None) == case["expected"]["candidate"]
    assert [item.model_dump() for item in extraction.discrepancies] == case["expected"]["discrepancies"]


@pytest.mark.parametrize("case", load_cases("policy_compiler", "additions"), ids=lambda case: case["id"])
def test_a_new_clause_needs_a_rule_and_a_reviewers_addition_is_built_and_checked_like_any_rule(case, tmp_path, no_network):
    documents, recordings = _amended(tmp_path, case)
    extraction = _replay(recordings).extract(documents, rule_overrides=case["overrides"])
    assert (extraction.candidate is not None) == case["expected"]["candidate"]
    assert [item.model_dump() for item in extraction.discrepancies] == case["expected"]["discrepancies"]
    if extraction.candidate is not None:
        added = next(rule for rule in extraction.candidate.ruleset.rules if rule.rule_id == case["overrides"][0]["rule_id"])
        assert (added.kind, added.policy_doc, added.title) == ("check", case["document"], case["reading"]["title"])  # from its clause, not the reviewer


@pytest.mark.parametrize("case", load_cases("policy_compiler", "additions_refused"), ids=lambda case: case["id"])
def test_an_addition_that_cannot_be_a_rule_is_refused_or_blocks_the_build(case, no_network):
    try:
        extraction = _replay().extract(_documents(), rule_overrides=case["overrides"])
    except InvalidInputError as error:
        assert re.search(case["expected"], str(error))
    else:
        assert extraction.candidate is None and any(re.search(case["expected"], reason) for reason in extraction.blockers)


@pytest.mark.parametrize("case", load_cases("policy_compiler", "rule_overrides_refused"), ids=lambda case: case["id"])
def test_an_invalid_override_is_refused_before_anything_is_built(case, no_network):
    with pytest.raises(InvalidInputError, match=case["expected"]):
        _replay().extract(_documents(), rule_overrides=case["overrides"])


def test_an_approval_binds_the_overridden_artifact_and_publication_needs_the_same_overrides(signed_off, no_network, tmp_path):
    compiler, resolutions, _ = signed_off
    overrides = load_cases("policy_compiler", "rule_overrides_accepted")[0].values[0]["overrides"]
    approval = compiler.approve(compiler.extract(_documents(), resolutions=resolutions, rule_overrides=overrides), **APPROVER)
    out = tmp_path / "ruleset.json"
    without = compiler.publish(_documents(), approval=approval, out=out, mode="simulation", resolutions=resolutions)
    assert not without.published and any("approval does not hold" in reason for reason in without.reasons) and not out.exists()
    published = compiler.publish(_documents(), approval=approval, out=out, mode="simulation", resolutions=resolutions, rule_overrides=overrides)
    assert published.published
    rules = {rule["rule_id"]: rule for rule in json.loads(out.read_text())["rules"]}
    assert rules["ALC-01"]["title"] == "Alcohol needs an entertainment context"
    assert rules["ALC-02"] == {rule["rule_id"]: rule for rule in json.loads(PATHS.ruleset.read_text())["rules"]}["ALC-02"]  # nothing else changed
