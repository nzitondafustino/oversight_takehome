"""Test recorded extraction and strict publication offline in temporary paths."""

import ast
import hashlib
import json
import re
import shutil
from pathlib import Path

import pytest
import yaml
from yaml_cases import load_cases

from expense_validator.compiler import build_rules
from expense_validator.compiler.agreement import Resolution, compare_document, counts, read_resolutions, reconcile
from expense_validator.compiler.approval import approve
from expense_validator.compiler.build_rules import prepare_candidate, publish_with_agreement
from expense_validator.compiler.extract import extract_document
from expense_validator.compiler.llm_extract import PROMPT, RecordingMissing, document_sha256, extract_with_llm, recorded, recording
from expense_validator.compiler.validate import Issue
from expense_validator.config import load_config

FIXTURES = Path(__file__).parents[1] / "fixtures"
RECORDINGS = FIXTURES / "llm_responses"
SCENARIOS = FIXTURES / "policy_scenarios"
PATHS = load_config().paths
REVIEW = {"reviewer": "test reviewer", "rationale": "checked against the clause"}


def _scenario(name):
    text = (SCENARIOS / f"{name}.md").read_text()
    return text, extract_document(text, f"{name}.md")


def _summary(results):
    found = {}
    for result in results:
        if result.field == "rule":
            found[result.rule_id] = result.status
        else:
            statuses = found.setdefault(result.rule_id, {})
            if result.status != "agreed":
                statuses.setdefault(result.status, []).append(result.field)
    return found


# --- The recorded provider ---


@pytest.mark.parametrize("case", load_cases("agreement", "recorded"))
def test_a_recorded_reading_goes_through_the_real_parser(case):
    text, document = _scenario(case["scenario"])
    results = compare_document(document, extract_with_llm(text, recorded(RECORDINGS)), text)
    assert {"rules": _summary(results), "counts": counts(results)} == case["expected"]
    json.dumps([result.to_dict() for result in results])


@pytest.mark.parametrize("case", load_cases("agreement", "recorded_refused"))
def test_no_usable_recording_is_an_error(case):
    text, _ = _scenario(case["scenario"])
    with pytest.raises((RecordingMissing, ValueError), match=case["expected"]):
        extract_with_llm(text, recorded(RECORDINGS))


def test_recordings_are_found_by_content_not_order():
    complete = recorded(RECORDINGS)
    texts = [path.read_text() for path in sorted(PATHS.policy_docs.glob("*.md"))]
    forward = [complete(PROMPT, text) for text in texts]
    backward = [complete(PROMPT, text) for text in reversed(texts)]
    assert forward == list(reversed(backward))


def test_a_recording_for_another_prompt_is_refused():
    text, _ = _scenario("agree")
    with pytest.raises(ValueError, match="another prompt"):
        recorded(RECORDINGS)("a changed prompt", text)


def test_a_document_changed_by_one_character_has_no_recording():
    text, _ = _scenario("agree")
    with pytest.raises(RecordingMissing):
        recorded(RECORDINGS)(PROMPT, text + " ")


def test_every_recording_is_named_by_its_document_and_says_where_it_came_from():
    for path in RECORDINGS.glob("*.json"):
        entry = json.loads(path.read_text())
        assert path.stem == entry["document_sha256"]
        assert entry["prompt_sha256"] == document_sha256(PROMPT)
        assert entry["origin"].strip()


def test_the_provider_never_reads_a_regex_reading():
    source = Path(build_rules.__file__).with_name("llm_extract.py")
    imported = {node.module for node in ast.walk(ast.parse(source.read_text())) if isinstance(node, ast.ImportFrom)}
    assert "expense_validator.compiler.extract" not in imported


def test_a_recording_saved_now_replays_the_same_answer(tmp_path):
    record = recording(lambda prompt, text: '{"rules": []}', tmp_path, "doc.md", "test")
    assert record(PROMPT, "some text") == recorded(tmp_path)(PROMPT, "some text") == '{"rules": []}'


# --- Reconciliation ---


@pytest.mark.parametrize("case", load_cases("agreement", "resolutions"))
def test_a_resolution_is_accepted_only_when_the_text_supports_it(case):
    text, document = _scenario(case["scenario"])
    results = compare_document(document, extract_with_llm(text, recorded(RECORDINGS)), text)
    hashes = {r.rule_id: r.clause_hash for r in results}  # every result of one rule carries the same hash
    stale = hashlib.sha256(b"an older clause").hexdigest()
    resolutions = []
    for entry in case["resolutions"]:
        rule_id = entry.get("rule_id", "ZZ-01")
        current = hashes[rule_id]
        resolutions.append(Resolution(policy_doc=document.policy_doc, rule_id=rule_id, field=entry["field"], value=entry["value"], clause_hash=stale if entry.get("clause_hash") == "stale" else current, **REVIEW))
    outcome = reconcile(results, resolutions, {document.policy_doc: text})
    assert sorted(f"{r['rule_id']} {r['field']}" for r in outcome.resolved) == case["expected"]["resolved"]
    assert sorted(f"{r.rule_id} {r.field}" for r in outcome.unresolved) == case["expected"]["unresolved"]
    assert len(outcome.errors) == len(case["expected"]["errors"])
    for error, pattern in zip(outcome.errors, case["expected"]["errors"]):
        assert re.search(pattern, error), error
    json.dumps(outcome.resolved)


@pytest.mark.parametrize("change", [{"reviewer": " "}, {"rationale": ""}, {"clause_hash": "abc"}, {"extra": 1}])
def test_a_resolution_must_say_who_why_and_which_clause(change):
    entry = {"policy_doc": "d.md", "rule_id": "ZZ-01", "field": "thresholds", "value": [], "clause_hash": "0" * 64, **REVIEW, **change}
    with pytest.raises(ValueError):
        Resolution.model_validate(entry)


# --- The publication gate, on the real policy and logic, in a temporary place ---
#
# These run in simulation mode, with a valid approval wherever one can be made, so each test shows the one
# thing that blocks publication. Approval itself is tested in test_approval.py.


def _workspace(tmp_path):
    docs, recordings = tmp_path / "docs", tmp_path / "recordings"
    shutil.copytree(PATHS.policy_docs, docs)
    shutil.copytree(RECORDINGS, recordings)
    paths = PATHS.model_copy(update={"policy_docs": docs, "ruleset": tmp_path / "production" / "ruleset.json"})
    return paths, recordings, tmp_path / "out" / "ruleset.json"


def _clause_hash(paths, policy_doc, rule_id):
    clause = next(c for c in extract_document((paths.policy_docs / policy_doc).read_text(), policy_doc).clauses if c.rule_id == rule_id)
    return hashlib.sha256(clause.text.encode()).hexdigest()


def _sign_offs(paths):
    """The two fields the stand-in readings get wrong: each takes a merchant code or the word three as a limit."""
    return [
        Resolution(policy_doc="merchant-category-risk.md", rule_id="MCC-03", field="thresholds", value=[], clause_hash=_clause_hash(paths, "merchant-category-risk.md", "MCC-03"), reviewer="test reviewer", rationale="5813 is a merchant code, not a limit"),
        Resolution(policy_doc="regional-addendum.md", rule_id="REG-01", field="thresholds", value=[], clause_hash=_clause_hash(paths, "regional-addendum.md", "REG-01"), reviewer="test reviewer", rationale="three counts rules, not a limit"),
    ]


def _retamper(recordings, policy_doc, edit):
    """Rewrites one real document's recorded response, as a model that read it differently would."""
    for path in recordings.glob("*.json"):
        entry = json.loads(path.read_text())
        if entry["policy_doc"] == policy_doc:
            entry["response"] = edit(entry["response"])
            path.write_text(json.dumps(entry))
            return
    raise AssertionError(policy_doc)


def _approval_for(paths, recordings, resolutions):
    """An approval of what these inputs would publish, as a reviewer would give it, or None if none can be."""
    candidate = prepare_candidate(paths, recorded(recordings), resolutions, "simulation")
    if not candidate.output["ready_for_approval"] or candidate.ruleset_sha256 is None:
        return None
    return approve(candidate.output, candidate.ruleset_sha256, "test approver", "2026-11-05T15:00:00Z")


def _publish(paths, recordings, resolutions, out, approval="approve"):
    """Publishes in simulation mode. By default it approves the current candidate first, when it can."""
    if approval == "approve":
        approval = _approval_for(paths, recordings, resolutions)
    return publish_with_agreement(paths, recorded(recordings), resolutions, out, reading_kind="simulation", approval=approval, mode="simulation")


def _out_is_unwritten(out):
    return not out.exists() and not out.parent.exists()


def _blockers(publication):
    """The reasons other than having no approval, which follows from them when nothing could be approved."""
    return [reason for reason in publication.reasons if reason != "no saved approval"]


def test_the_real_policy_is_blocked_until_its_two_disputed_fields_are_signed_off(tmp_path):
    paths, recordings, out = _workspace(tmp_path)
    publication = _publish(paths, recordings, [], out)
    assert not publication.published and _out_is_unwritten(out)
    assert publication.report["counts"] == {"agreed": 285, "disagree": 2, "unconfirmed": 0, "only_regex": 0, "only_llm": 0}
    assert sorted(_blockers(publication)) == ["unresolved: merchant-category-risk.md MCC-03 thresholds is disagree", "unresolved: regional-addendum.md REG-01 thresholds is disagree"]


def test_signed_off_and_approved_it_publishes_the_same_rules_with_every_condition_and_carve_out(tmp_path):
    paths, recordings, out = _workspace(tmp_path)
    out.parent.mkdir()
    publication = _publish(paths, recordings, _sign_offs(paths), out)
    assert publication.published, publication.reasons
    assert out.read_text() == PATHS.ruleset.read_text()
    published = {rule["rule_id"]: rule for rule in json.loads(out.read_text())["rules"]}
    assert published["PERS.BAGGAGE"]["kind"] == "carve_out" and published["MEAL-01"]["helper"] == "meal_cap"
    assert len(publication.report["resolved"]) == 2 and json.dumps(publication.report)


def test_a_threshold_disagreement_blocks_until_resolved_and_the_llm_value_is_refused(tmp_path):
    paths, recordings, out = _workspace(tmp_path)
    out.parent.mkdir()
    _retamper(recordings, "meals-and-entertainment-policy.md", lambda raw: raw.replace('{"value": "75", "unit": "usd"}', '{"value": "80", "unit": "usd"}', 1))
    blocked = _publish(paths, recordings, _sign_offs(paths), out)
    assert not blocked.published and not out.exists()
    assert _blockers(blocked) == ["unresolved: meals-and-entertainment-policy.md MEAL-01 thresholds is disagree"]
    meal = _clause_hash(paths, "meals-and-entertainment-policy.md", "MEAL-01")
    wrong = Resolution(policy_doc="meals-and-entertainment-policy.md", rule_id="MEAL-01", field="thresholds", value=[{"value": "80", "unit": "usd"}, {"value": "100", "unit": "usd"}], clause_hash=meal, **REVIEW)
    refused = _publish(paths, recordings, [*_sign_offs(paths), wrong], out)
    assert not refused.published and not out.exists() and "does not state the number 80" in " ".join(refused.reasons)
    right = wrong.model_copy(update={"value": [{"value": "75", "unit": "usd"}, {"value": "100", "unit": "usd"}]})
    assert _publish(paths, recordings, [*_sign_offs(paths), right], out).published


def test_a_severity_disagreement_blocks(tmp_path):
    paths, recordings, out = _workspace(tmp_path)
    _retamper(recordings, "technology-and-equipment-policy.md", lambda raw: raw.replace('"severities": ["high"]', '"severities": ["medium"]', 1))
    publication = _publish(paths, recordings, _sign_offs(paths), out)
    assert not publication.published and _out_is_unwritten(out)
    assert _blockers(publication) == ["unresolved: technology-and-equipment-policy.md TECH-01 severities is disagree"]


@pytest.mark.parametrize("side", ["only_regex", "only_llm"])
def test_a_rule_missing_on_either_side_blocks(tmp_path, side):
    paths, recordings, out = _workspace(tmp_path)
    if side == "only_regex":
        _retamper(recordings, "gifts-and-awards-policy.md", lambda raw: json.dumps({"rules": [rule for rule in json.loads(raw)["rules"] if rule["rule_id"] != "GIFT-03"]}))
    else:
        _retamper(recordings, "gifts-and-awards-policy.md", lambda raw: json.dumps({"rules": [*json.loads(raw)["rules"], {**json.loads(raw)["rules"][0], "rule_id": "GIFT-09"}]}))
    publication = _publish(paths, recordings, _sign_offs(paths), out)
    assert not publication.published and _out_is_unwritten(out)
    assert _blockers(publication) == [f"unresolved: gifts-and-awards-policy.md {'GIFT-03' if side == 'only_regex' else 'GIFT-09'} rule is {side}"]


def test_a_fabricated_quote_blocks(tmp_path):
    paths, recordings, out = _workspace(tmp_path)
    _retamper(recordings, "transaction-pattern-rules.md", lambda raw: json.dumps({"rules": [{**rule, "quote": "always allowed"} if rule["rule_id"] == "TIME-01" else rule for rule in json.loads(raw)["rules"]]}))
    publication = _publish(paths, recordings, _sign_offs(paths), out)
    assert _blockers(publication) == ["unresolved: transaction-pattern-rules.md TIME-01 quote is disagree"] and _out_is_unwritten(out)


@pytest.mark.parametrize("damage", ["malformed", "missing"])
def test_an_unusable_reading_blocks(tmp_path, damage):
    paths, recordings, out = _workspace(tmp_path)
    if damage == "malformed":
        _retamper(recordings, "regional-addendum.md", lambda raw: "I could not read this document.")
    else:
        (recordings / f"{document_sha256((paths.policy_docs / 'regional-addendum.md').read_text())}.json").unlink()
    publication = _publish(paths, recordings, _sign_offs(paths), out)
    assert not publication.published and _out_is_unwritten(out)
    assert any(reason.startswith("regional-addendum.md: no usable LLM reading") for reason in publication.reasons)


def test_a_sign_off_goes_stale_when_its_clause_changes(tmp_path):
    paths, recordings, out = _workspace(tmp_path)
    old = _sign_offs(paths)
    doc = paths.policy_docs / "regional-addendum.md"
    original = doc.read_text()
    changed = original.replace("Rules about the merchant, such as MCC-01, still apply.", "Rules about the merchant, such as MCC-01, still apply in full.")
    assert changed != original
    doc.write_text(changed)
    entry = json.loads((recordings / f"{document_sha256(original)}.json").read_text())
    (recordings / f"{document_sha256(changed)}.json").write_text(json.dumps({**entry, "document_sha256": document_sha256(changed)}))
    publication = _publish(paths, recordings, old, out)
    assert not publication.published and _out_is_unwritten(out)
    assert any("REG-01 thresholds: stale" in reason for reason in publication.reasons)


def test_an_invalid_resolution_blocks_even_when_everything_else_is_resolved(tmp_path):
    paths, recordings, out = _workspace(tmp_path)
    junk = Resolution(policy_doc="regional-addendum.md", rule_id="REG-02", field="title", value="Anything", clause_hash="0" * 64, **REVIEW)
    publication = _publish(paths, recordings, [*_sign_offs(paths), junk], out)
    assert _blockers(publication) == ["resolution refused: regional-addendum.md REG-02 title: no unresolved result to resolve"] and _out_is_unwritten(out)


def test_validation_still_blocks_when_agreement_is_complete(tmp_path, monkeypatch):
    paths, recordings, out = _workspace(tmp_path)
    monkeypatch.setattr(build_rules, "validate_ruleset", lambda *args: [Issue("error", "fields", "MEAL-01", "a planted error")])
    publication = _publish(paths, recordings, _sign_offs(paths), out)
    assert _blockers(publication) == ["validation: error fields: MEAL-01: a planted error"] and _out_is_unwritten(out)
    assert publication.report["candidate_ruleset_sha256"] is None


def test_a_build_failure_blocks(tmp_path):
    paths, recordings, out = _workspace(tmp_path)
    logic = yaml.safe_load(PATHS.rule_logic.read_text())
    logic["rules"] = [rule for rule in logic["rules"] if rule["rule_id"] != "ALC-02"]
    (tmp_path / "logic.yaml").write_text(yaml.safe_dump(logic))
    publication = _publish(paths.model_copy(update={"rule_logic": tmp_path / "logic.yaml"}), recordings, _sign_offs(paths), out)
    assert _blockers(publication) == ["build failed: no rule for: ['ALC-02']"] and _out_is_unwritten(out)


def test_the_gate_is_what_blocks(tmp_path, monkeypatch):
    """A mutant whose reconciliation lets everything through publishes: so the real one is what holds it back."""
    paths, recordings, out = _workspace(tmp_path)
    out.parent.mkdir()
    assert not _publish(paths, recordings, [], out).published
    monkeypatch.setattr(build_rules, "reconcile", lambda results, resolutions, documents: reconcile([], [], {}))
    assert _publish(paths, recordings, [], out).published and out.exists()


# --- The command line ---


def test_strict_mode_needs_recordings_and_never_reaches_a_live_model(capsys):
    assert build_rules.main(["--strict-agreement"]) == 2
    assert "not connected" in capsys.readouterr().err
