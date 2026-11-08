"""Test hash-bound approvals and publication gates with offline fixtures.

Relabeled simulations are test-only; publications use temporary paths.
"""

import json

import pytest
import yaml
from pydantic import ValidationError
from test_publication import RECORDINGS, REVIEW, _retamper, _sign_offs, _workspace

from expense_validator.compiler import agreement, build_rules
from expense_validator.compiler.agreement import Resolution, review_agreement
from expense_validator.compiler.approval import Approval, approval_holds, approve, load_approval, save_approval, why_approval_fails
from expense_validator.compiler.build_rules import prepare_candidate, publish_with_agreement
from expense_validator.compiler.llm_extract import document_sha256, recorded, recording_kind
from expense_validator.compiler.validate import Issue
from expense_validator.config import load_config

NOW = "2026-11-05T15:00:00Z"
PRODUCTION_RULESET = load_config().paths.ruleset


def _candidate(paths, recordings, resolutions, kind="simulation"):
    return prepare_candidate(paths, recorded(recordings), resolutions, kind)


def _approved(paths, recordings, resolutions, kind="simulation"):
    candidate = _candidate(paths, recordings, resolutions, kind)
    return approve(candidate.output, candidate.ruleset_sha256, "approver", NOW)


def _publish(paths, recordings, resolutions, out, approval, mode="simulation", kind="simulation"):
    return publish_with_agreement(paths, recorded(recordings), resolutions, out, reading_kind=kind, approval=approval, mode=mode)


def _as_model_recordings(recordings):
    """Stands in for real model recordings: the same answers, labelled model. Never done to the fixtures."""
    for path in recordings.glob("*.json"):
        path.write_text(json.dumps({**json.loads(path.read_text()), "kind": "model", "origin": "relabelled in a test"}))


# --- The whole workflow, in temporary directories ---


def test_the_whole_workflow_in_simulation(tmp_path):
    paths, recordings, out = _workspace(tmp_path)
    out.parent.mkdir()
    # 1. Mock extraction and agreement: two fields need a reviewer, so the output is not ready.
    first = review_agreement(paths.policy_docs, recorded(recordings), [], "simulation")
    assert first["reading_kind"] == "simulation" and first["summary"]["rules_needing_review"] == 2 and not first["ready_for_approval"]
    # 2. Reconciliation: a reviewer resolves both, checked against the clause text and its hash.
    resolutions = _sign_offs(paths)
    resolutions_file = tmp_path / "resolutions.yaml"
    resolutions_file.write_text(yaml.safe_dump({"resolutions": [entry.model_dump() for entry in resolutions]}))
    # 3. Validation: the candidate ruleset is built and validated, and nothing is written yet.
    candidate = _candidate(paths, recordings, agreement.read_resolutions(resolutions_file.read_text()))
    assert candidate.reasons == [] and candidate.output["ready_for_approval"] and not out.exists()
    # 4. Approval of that exact output and candidate, saved once.
    record = approve(candidate.output, candidate.ruleset_sha256, "approver", NOW, "MCC-03 and REG-01 checked")
    save_approval(record, tmp_path / "approvals" / "simulation.json")
    # 5. Publication, to a temporary path only.
    publication = _publish(paths, recordings, resolutions, out, load_approval(tmp_path / "approvals" / "simulation.json"))
    assert publication.published, publication.reasons
    assert out.read_text() == PRODUCTION_RULESET.read_text()
    assert publication.report["mode"] == "simulation" and publication.report["approval"]["reading_kind"] == "simulation"


def test_production_publishes_model_readings_with_an_approval_of_them(tmp_path):
    paths, recordings, out = _workspace(tmp_path)
    _as_model_recordings(recordings)
    out.parent.mkdir()
    texts = [path.read_text() for path in sorted(paths.policy_docs.glob("*.md"))]
    assert recording_kind(recordings, texts) == "model"
    record = _approved(paths, recordings, _sign_offs(paths), "model")
    publication = _publish(paths, recordings, _sign_offs(paths), out, record, mode="production", kind="model")
    assert publication.published, publication.reasons
    assert out.read_text() == PRODUCTION_RULESET.read_text()


# --- What blocks publication ---


def test_no_approval_blocks(tmp_path):
    paths, recordings, out = _workspace(tmp_path)
    publication = _publish(paths, recordings, _sign_offs(paths), out, None)
    assert publication.reasons == ["no saved approval"] and not out.exists()


def test_an_approval_goes_stale_when_a_document_changes(tmp_path):
    paths, recordings, out = _workspace(tmp_path)
    record = _approved(paths, recordings, _sign_offs(paths))
    doc = paths.policy_docs / "gifts-and-awards-policy.md"
    original = doc.read_text()
    changed = original.replace("at any amount.", "at any amount at all.", 1)
    doc.write_text(changed)
    entry = json.loads((recordings / f"{document_sha256(original)}.json").read_text())
    (recordings / f"{document_sha256(changed)}.json").write_text(json.dumps({**entry, "document_sha256": document_sha256(changed)}))
    publication = _publish(paths, recordings, _sign_offs(paths), out, record)
    assert "approval does not hold: the agreement output has changed since it was approved" in publication.reasons and not out.exists()


def test_an_approval_goes_stale_when_a_resolution_changes(tmp_path):
    paths, recordings, out = _workspace(tmp_path)
    record = _approved(paths, recordings, _sign_offs(paths))
    reworded = [entry.model_copy(update={"rationale": "a different reason"}) for entry in _sign_offs(paths)]
    publication = _publish(paths, recordings, reworded, out, record)
    assert publication.reasons == ["approval does not hold: the agreement output has changed since it was approved"] and not out.exists()


def test_changing_executable_rule_logic_voids_the_approval(tmp_path):
    paths, recordings, out = _workspace(tmp_path)
    record = _approved(paths, recordings, _sign_offs(paths))
    logic = yaml.safe_load(paths.rule_logic.read_text())
    alc03 = next(rule for rule in logic["rules"] if rule["rule_id"] == "ALC-03")
    alc03["params"]["on_premises"]["values"].append("5814")  # one more merchant code treated as on the premises
    changed = paths.model_copy(update={"rule_logic": tmp_path / "logic.yaml"})
    changed.rule_logic.write_text(yaml.safe_dump(logic))
    candidate = _candidate(changed, recordings, _sign_offs(paths))
    assert candidate.reasons == [] and candidate.output == record.output, "the agreement is the same: only the rules changed"
    publication = _publish(changed, recordings, _sign_offs(paths), out, record)
    assert publication.reasons == ["approval does not hold: the candidate ruleset has changed since it was approved"] and not out.exists()


def test_an_edited_approval_record_does_not_hold(tmp_path):
    paths, recordings, out = _workspace(tmp_path)
    record = _approved(paths, recordings, _sign_offs(paths))
    edited_output = {**record.output, "summary": {**record.output["summary"], "rules_needing_review": 0, "rules_agreed": 41}}
    forged = record.model_copy(update={"output": edited_output})
    assert why_approval_fails(forged, record.output, record.ruleset_sha256) == "the approval record does not match the output it holds"
    relabelled = record.model_copy(update={"reading_kind": "model"})
    assert why_approval_fails(relabelled, record.output, record.ruleset_sha256) == "the approval record does not match the output it holds"
    publication = _publish(paths, recordings, _sign_offs(paths), out, forged)
    assert publication.reasons == ["approval does not hold: the approval record does not match the output it holds"] and not out.exists()


@pytest.mark.parametrize("damage", ["not json", '{"approved_by": "x"}', "extra"])
def test_an_invalid_approval_file_is_refused(tmp_path, damage):
    paths, recordings, _ = _workspace(tmp_path)
    path = tmp_path / "approval.json"
    if damage == "extra":
        path.write_text(json.dumps({**json.loads(_approved(paths, recordings, _sign_offs(paths)).model_dump_json()), "authorized": True}))
    else:
        path.write_text(damage)
    with pytest.raises(ValueError):
        load_approval(path)


def test_mock_readings_never_publish_in_production(tmp_path):
    paths, recordings, out = _workspace(tmp_path)
    record = _approved(paths, recordings, _sign_offs(paths))
    publication = _publish(paths, recordings, _sign_offs(paths), out, record, mode="production")
    assert publication.reasons == [
        "production publication needs model readings, and these are simulation",
        "approval is of simulation readings, which never authorize production",
    ]
    assert not out.exists()


def test_a_mock_approval_never_authorizes_production_even_for_model_readings(tmp_path):
    paths, recordings, out = _workspace(tmp_path)
    mock_record = _approved(paths, recordings, _sign_offs(paths))
    _as_model_recordings(recordings)
    publication = _publish(paths, recordings, _sign_offs(paths), out, mock_record, mode="production", kind="model")
    assert "approval is of simulation readings, which never authorize production" in publication.reasons and not out.exists()


def test_a_simulation_never_writes_the_production_ruleset(tmp_path):
    paths, recordings, _ = _workspace(tmp_path)
    paths.ruleset.parent.mkdir()
    record = _approved(paths, recordings, _sign_offs(paths))
    publication = _publish(paths, recordings, _sign_offs(paths), paths.ruleset, record)
    assert publication.reasons == ["a simulation never writes the production ruleset"] and not paths.ruleset.exists()


def test_an_unknown_mode_is_refused(tmp_path):
    paths, recordings, out = _workspace(tmp_path)
    with pytest.raises(ValueError, match="mode must be one of"):
        _publish(paths, recordings, [], out, None, mode="staging")


def test_the_approval_gate_is_what_blocks(tmp_path, monkeypatch):
    """With approval checking replaced by one that accepts anything, an unapproved candidate publishes."""
    paths, recordings, out = _workspace(tmp_path)
    out.parent.mkdir()
    stale = _approved(paths, recordings, _sign_offs(paths)).model_copy(update={"ruleset_sha256": "0" * 64})
    assert not _publish(paths, recordings, _sign_offs(paths), out, stale).published
    monkeypatch.setattr(build_rules, "why_approval_fails", lambda *args: None)
    assert _publish(paths, recordings, _sign_offs(paths), out, stale).published


# --- Approving ---


def test_unsigned_output_cannot_be_approved(tmp_path):
    paths, recordings, _ = _workspace(tmp_path)
    candidate = _candidate(paths, recordings, [])
    with pytest.raises(ValueError, match="2 rules need review"):
        approve(candidate.output, candidate.ruleset_sha256, "approver", NOW)


def test_a_candidate_that_failed_validation_cannot_be_approved(tmp_path, monkeypatch):
    paths, recordings, _ = _workspace(tmp_path)
    monkeypatch.setattr(build_rules, "validate_ruleset", lambda *args: [Issue("error", "fields", "MEAL-01", "planted")])
    candidate = _candidate(paths, recordings, _sign_offs(paths))
    assert candidate.ruleset_sha256 is None
    with pytest.raises(ValueError, match="no valid candidate ruleset"):
        approve(candidate.output, candidate.ruleset_sha256, "approver", NOW)


def test_a_refused_resolution_or_a_failed_reading_cannot_be_approved(tmp_path):
    paths, recordings, _ = _workspace(tmp_path)
    junk = Resolution(policy_doc="regional-addendum.md", rule_id="REG-02", field="title", value="x", clause_hash="0" * 64, **REVIEW)
    candidate = _candidate(paths, recordings, [*_sign_offs(paths), junk])
    with pytest.raises(ValueError, match="1 resolutions were refused"):
        approve(candidate.output, candidate.ruleset_sha256, "approver", NOW)
    _retamper(recordings, "regional-addendum.md", lambda raw: "not json")
    failed = _candidate(paths, recordings, _sign_offs(paths)).output
    addendum = next(document for document in failed["documents"] if document["policy_doc"] == "regional-addendum.md")
    assert addendum["rules"] == [] and addendum["reading_error"].startswith("ValidationError")
    with pytest.raises(ValueError, match="1 readings failed"):
        approve(failed, "0" * 64, "approver", NOW)


def test_an_approval_is_saved_once_and_reloads_the_same(tmp_path):
    paths, recordings, _ = _workspace(tmp_path)
    record = _approved(paths, recordings, _sign_offs(paths))
    path = tmp_path / "approvals" / "a.json"
    save_approval(record, path)
    assert load_approval(path) == record
    candidate = _candidate(paths, recordings, _sign_offs(paths))
    assert approval_holds(load_approval(path), candidate.output, candidate.ruleset_sha256)
    with pytest.raises(FileExistsError, match="never overwritten"):
        save_approval(record, path)


@pytest.mark.parametrize("change", [{"approved_by": " "}, {"approved_at": "yesterday"}, {"output_sha256": "abc"}, {"ruleset_sha256": "abc"}, {"extra": 1}])
def test_an_approval_says_who_when_and_what(change):
    entry = {"approved_by": "approver", "approved_at": NOW, "reading_kind": "simulation", "output_sha256": "0" * 64, "ruleset_sha256": "0" * 64, "output": {}, **change}
    with pytest.raises(ValidationError):
        Approval.model_validate(entry)


# --- The agreement output ---


def test_the_output_holds_every_field_once_as_agreed_or_not(tmp_path):
    paths, recordings, _ = _workspace(tmp_path)
    output = review_agreement(paths.policy_docs, recorded(recordings), [], "simulation")
    rules = [rule for document in output["documents"] for rule in document["rules"]]
    assert len(output["documents"]) == 9 and len(rules) == 41
    assert sum(len(rule["agreed"]) + len(rule["disagreed"]) for rule in rules) == sum(output["summary"]["fields"].values()) == 287
    assert json.loads(json.dumps(output)) == output
    assert output == review_agreement(paths.policy_docs, recorded(recordings), [], "simulation")


def test_a_rule_needing_review_shows_both_readings_and_no_resolution(tmp_path):
    paths, recordings, _ = _workspace(tmp_path)
    _retamper(recordings, "meals-and-entertainment-policy.md", lambda raw: raw.replace('{"value": "75", "unit": "usd"}', '{"value": "80", "unit": "usd"}', 1))
    output = review_agreement(paths.policy_docs, recorded(recordings), _sign_offs(paths), "simulation")
    meals = next(document for document in output["documents"] if document["policy_doc"] == "meals-and-entertainment-policy.md")
    rule = next(rule for rule in meals["rules"] if rule["rule_id"] == "MEAL-01")
    assert rule["status"] == "needs_review"
    assert rule["disagreed"] == [{"field": "thresholds", "status": "disagree", "regex": [{"value": "75", "unit": "usd"}, {"value": "100", "unit": "usd"}], "llm": [{"value": "80", "unit": "usd"}, {"value": "100", "unit": "usd"}], "resolution": None}]


def test_the_reading_kind_is_part_of_what_is_approved(tmp_path):
    paths, recordings, _ = _workspace(tmp_path)
    as_simulation = review_agreement(paths.policy_docs, recorded(recordings), [], "simulation")
    as_model = review_agreement(paths.policy_docs, recorded(recordings), [], "model")
    assert as_simulation["output_sha256"] != as_model["output_sha256"]


def test_recordings_must_be_one_kind(tmp_path):
    paths, recordings, _ = _workspace(tmp_path)
    texts = [path.read_text() for path in sorted(paths.policy_docs.glob("*.md"))]
    assert recording_kind(recordings, texts) == "simulation"
    one = recordings / f"{document_sha256(texts[0])}.json"
    one.write_text(json.dumps({**json.loads(one.read_text()), "kind": "model"}))
    with pytest.raises(ValueError, match="all be one kind"):
        recording_kind(recordings, texts)


def test_every_fixture_recording_is_a_simulation():
    assert {json.loads(path.read_text())["kind"] for path in RECORDINGS.glob("*.json")} == {"simulation"}


# --- The command line ---


def test_the_command_line_prepares_then_publishes_a_simulation_only_where_told(tmp_path):
    before = PRODUCTION_RULESET.read_text()
    out, report, sign_offs = tmp_path / "ruleset.json", tmp_path / "report.json", tmp_path / "resolutions.yaml"
    paths = load_config().paths
    sign_offs.write_text(yaml.safe_dump({"resolutions": [entry.model_dump() for entry in _sign_offs(paths)]}))
    args = ["--strict-agreement", "--recordings", str(RECORDINGS), "--resolutions", str(sign_offs), "--out", str(out), "--report", str(report)]
    assert build_rules.main([*args, "--mode", "simulation"]) == 1  # prepared for review: no approval yet
    prepared = json.loads(report.read_text())
    assert prepared["reasons"] == ["no saved approval"] and prepared["candidate_ruleset_sha256"] and not out.exists()
    approval_path = tmp_path / "approval.json"
    save_approval(approve(prepared["agreement"], prepared["candidate_ruleset_sha256"], "approver", NOW), approval_path)
    assert build_rules.main([*args, "--mode", "simulation", "--approval", str(approval_path)]) == 0
    assert out.read_text() == before == PRODUCTION_RULESET.read_text()
    out.unlink()
    assert build_rules.main([*args, "--approval", str(approval_path)]) == 1  # production is the default, and refuses mock readings
    assert not out.exists() and PRODUCTION_RULESET.read_text() == before


def test_the_offline_build_still_works_without_any_of_this(tmp_path, monkeypatch):
    config = load_config()
    target = tmp_path / "ruleset.json"
    monkeypatch.setattr(build_rules, "load_config", lambda: config.model_copy(update={"paths": config.paths.model_copy(update={"ruleset": target})}))
    assert build_rules.main([]) == 0
    assert target.read_text() == PRODUCTION_RULESET.read_text()


def test_the_review_command_writes_the_output_with_its_reading_kind(tmp_path):
    path = tmp_path / "agreement.json"
    assert agreement.main(["--recordings", str(RECORDINGS), "--json", str(path)]) == 0
    output = json.loads(path.read_text())
    assert output["reading_kind"] == "simulation" and output["ready_for_approval"] is False
