"""Test source-bound reviews and approvals offline in temporary paths."""

import hashlib
import json
import socket
import urllib.request
from pathlib import Path

import pytest
from yaml_cases import load_cases

from expense_validator import ApprovalRefused, InvalidInputError, PolicyCompiler
from expense_validator.compiler.build_rules import prepare_candidate
from expense_validator.compiler.llm_extract import recorded
from expense_validator.config import ROOT, load_config
from expense_validator.models import CompilerSettings, ExtractionResult, ReviewResult

CONFIG = ROOT / "config.yaml"
RECORDINGS = ROOT / "tests" / "fixtures" / "llm_responses"
SCENARIOS = ROOT / "tests" / "fixtures" / "policy_scenarios"
PATHS = load_config().paths
APPROVER = {"approved_by": "test approver", "approved_at": "2026-11-06T10:00:00Z"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a network request was made")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def _documents(folder=PATHS.policy_docs, names=None):
    paths = sorted(folder.glob("*.md")) if names is None else [folder / name for name in names]
    return [{"document_id": f"doc-{path.stem}", "filename": path.name, "content": path.read_text()} for path in paths]


def _compiler(**changes):
    settings = CompilerSettings.from_config(CONFIG, mode="replay", recordings_dir=RECORDINGS)
    return PolicyCompiler(settings.model_copy(update=changes))


COMPILER = _compiler()
EXTRACTION = COMPILER.extract(_documents())


def _find(extraction, rule_id, field):
    return next(c for c in extraction.comparisons if c.rule_id == rule_id and c.field == field)


def _decision(comparison, action, extraction=EXTRACTION, **fields):
    return {"comparison_id": comparison.comparison_id, "extraction_sha256": extraction.extraction_sha256, "action": action, "reviewer": "test reviewer", **fields}


def _sign_offs(extraction=EXTRACTION):
    return [_decision(c, "accept_regex", extraction) for c in extraction.comparisons if c.status != "agreed"]


def _review(decisions, policy="exceptions_only", extraction=EXTRACTION, compiler=COMPILER, documents=None):
    return compiler.review(extraction, decisions, policy=policy, documents=documents or _documents())


def _codes(review):
    return sorted(issue.code for issue in review.issues)


# --- Comparisons ---


def test_every_extracted_field_is_a_comparison():
    candidate = prepare_candidate(PATHS, recorded(RECORDINGS), [], "simulation")
    assert len(EXTRACTION.comparisons) == len(candidate.report["results"]) == sum(EXTRACTION.agreement["summary"]["fields"].values()) == 287
    listed = {(d["policy_doc"], r["rule_id"], item["field"]) for d in EXTRACTION.agreement["documents"] for r in d["rules"] for item in [*r["agreed"], *r["disagreed"]]}
    assert {(c.policy_doc, c.rule_id, c.field) for c in EXTRACTION.comparisons} == listed
    assert sum(c.status == "agreed" for c in EXTRACTION.comparisons) == 285 and sum(c.review_required for c in EXTRACTION.comparisons) == 2


def test_comparisons_keep_typed_values_evidence_and_location():
    meal = _find(EXTRACTION, "MEAL-01", "thresholds")
    assert meal.regex_value == meal.llm_value == meal.agreed_value == [{"value": "75", "unit": "usd"}, {"value": "100", "unit": "usd"}]
    assert "**$75" in meal.evidence and meal.clause_hash == hashlib.sha256(meal.evidence.encode()).hexdigest()
    text = (PATHS.policy_docs / meal.policy_doc).read_text().splitlines()
    assert text[meal.location["line"] - 1].startswith("**MEAL-01 — ") and meal.location["section"]
    signal = _find(EXTRACTION, "MCC-02", "review_signal")
    assert signal.regex_value is True and isinstance(signal.agreed_value, bool)
    assert ExtractionResult.model_validate_json(EXTRACTION.model_dump_json()) == EXTRACTION


def test_comparison_ids_and_the_extraction_identity_are_deterministic():
    again = COMPILER.extract(list(reversed(_documents())))
    ids = [c.comparison_id for c in EXTRACTION.comparisons]
    assert ids == [c.comparison_id for c in again.comparisons]
    assert len(set(ids)) == len(ids) and again.extraction_sha256 == EXTRACTION.extraction_sha256


def test_the_same_rule_id_in_two_documents_gives_two_comparisons():
    extraction = COMPILER.extract(_documents(SCENARIOS, ["agree.md", "severity_conflict.md"]))
    titles = [c for c in extraction.comparisons if c.rule_id == "ZZ-01" and c.field == "title"]
    assert sorted(c.policy_doc for c in titles) == ["agree.md", "severity_conflict.md"] and len({c.comparison_id for c in titles}) == 2


# --- Decisions ---


@pytest.mark.parametrize("case", load_cases("policy_compiler", "review_decisions"))
def test_a_decision_is_applied_only_where_the_source_and_the_rules_support_it(case):
    comparison = _find(EXTRACTION, case["rule_id"], case["field"])
    base = [d for d in _sign_offs() if d["comparison_id"] != comparison.comparison_id]
    review = _review([*base, _decision(comparison, **case["decision"])])
    field = next(f for f in review.fields if f.comparison.comparison_id == comparison.comparison_id)
    assert {"outcome": field.outcome, "codes": _codes(review), "ready": review.ready_for_approval} == case["expected"]


def test_one_sided_readings_are_reviewable():
    extraction = COMPILER.extract(_documents(SCENARIOS, ["llm_added_a_rule.md"]))
    added = _find(extraction, "ZZ-09", "rule")
    assert (added.status, added.regex_value, added.llm_value) == ("only_llm", "absent", "present")
    documents = _documents(SCENARIOS, ["llm_added_a_rule.md"])
    absent = _review([_decision(added, "accept_regex", extraction)], extraction=extraction, documents=documents)
    present = _review([_decision(added, "accept_llm", extraction)], extraction=extraction, documents=documents)
    assert next(f for f in absent.fields if f.comparison == added).outcome == "regex"
    assert [i.code for i in absent.issues] == ["no_candidate"]  # the scenario has no rule logic, so it can never be approved
    assert [i.code for i in present.issues if i.comparison_id == added.comparison_id] == ["unsupported_value"]


@pytest.mark.parametrize("change, code", [
    ({"comparison_id": "cmp-0000"}, "unknown_comparison"),
    ({"extraction_sha256": "0" * 64}, "stale_extraction"),
])
def test_decisions_for_anything_but_this_extraction_are_refused(change, code):
    review = _review([*_sign_offs()[1:], {**_sign_offs()[0], **change}])
    assert _codes(review) == [code] and not review.ready_for_approval and len(review.remaining) == 1


def test_duplicate_decisions_are_both_refused():
    first, second = _sign_offs()
    review = _review([first, second, {**first, "reviewer": "someone else"}])
    assert _codes(review) == ["duplicate_decision", "duplicate_decision"] and review.remaining == (first["comparison_id"],)


def test_reviewing_changes_nothing_in_the_extraction():
    before = EXTRACTION.model_dump_json()
    meal = _find(EXTRACTION, "MEAL-01", "thresholds")
    final = lambda review: next(f for f in review.fields if f.comparison.comparison_id == meal.comparison_id).final_value
    final(_review([*_sign_offs(), _decision(meal, "confirm")])).append({"value": "1", "unit": "usd"})
    assert EXTRACTION.model_dump_json() == before
    assert final(_review([*_sign_offs(), _decision(meal, "confirm")])) == meal.agreed_value == [{"value": "75", "unit": "usd"}, {"value": "100", "unit": "usd"}]


def test_an_altered_extraction_or_other_documents_cannot_be_reviewed():
    meal = _find(EXTRACTION, "MEAL-01", "thresholds")
    forged = meal.model_copy(update={"regex_value": [{"value": "100", "unit": "usd"}]})
    altered = EXTRACTION.model_copy(update={"comparisons": tuple(forged if c == meal else c for c in EXTRACTION.comparisons)})
    with pytest.raises(InvalidInputError, match="has been altered"):
        _review(_sign_offs(), extraction=altered)
    changed = [{**d, "content": d["content"] + " "} if d["filename"] == meal.policy_doc else d for d in _documents()]
    with pytest.raises(InvalidInputError, match="exactly the ones this extraction read"):
        _review(_sign_offs(), documents=changed)
    resolved = COMPILER.extract(_documents(), resolutions=[])
    assert _review(_sign_offs(), extraction=resolved).ready_for_approval


def test_an_extraction_made_with_resolutions_is_not_reviewed_again():
    item = EXTRACTION.review_items[0]
    resolution = {"policy_doc": item.policy_doc, "rule_id": item.rule_id, "field": item.field, "value": [], "clause_hash": item.clause_hash, "reviewer": "r", "rationale": "not a limit"}
    with pytest.raises(InvalidInputError, match="applied resolutions"):
        _review(_sign_offs(), extraction=COMPILER.extract(_documents(), resolutions=[resolution]))


# --- Completion policies and hashes ---


def test_exceptions_only_needs_the_open_fields_and_all_fields_needs_every_field():
    assert _review([]).remaining == tuple(d["comparison_id"] for d in _sign_offs())
    assert _review(_sign_offs()).complete and _review(_sign_offs()).ready_for_approval
    partial = _review(_sign_offs(), policy="all_fields")
    assert not partial.complete and not partial.ready_for_approval and len(partial.remaining) == 285
    assert all(f.outcome == "undecided" for f in partial.fields if f.comparison.status == "agreed")
    confirmed = [_decision(c, "confirm") for c in EXTRACTION.comparisons if c.status == "agreed"]
    halfway = _review([*_sign_offs(), *confirmed[:100]], policy="all_fields")
    assert len(halfway.remaining) == 185 and not halfway.ready_for_approval
    whole = _review([*_sign_offs(), *confirmed], policy="all_fields")
    assert whole.complete and whole.ready_for_approval and whole.issues == ()


def test_review_hashes_are_deterministic_and_bind_decisions_and_policy():
    review = _review(_sign_offs())
    assert _review(list(reversed(_sign_offs()))).review_sha256 == review.review_sha256
    assert ReviewResult.model_validate_json(review.model_dump_json()) == review and json.loads(review.to_json())["review_sha256"] == review.review_sha256
    changed = [{**_sign_offs()[0], "rationale": "a different reason"}, *_sign_offs()[1:]]
    other_reviewer = [{**_sign_offs()[0], "reviewer": "someone else"}, *_sign_offs()[1:]]
    confirmed = [_decision(c, "confirm") for c in EXTRACTION.comparisons if c.status == "agreed"]
    hashes = {review.review_sha256, _review(changed).review_sha256, _review(other_reviewer).review_sha256, _review([*_sign_offs(), *confirmed], policy="all_fields").review_sha256, _review([*_sign_offs(), *confirmed]).review_sha256}
    assert len(hashes) == 5


# --- Approval and publication ---


def _published(compiler, tmp_path, approval, decisions, policy="exceptions_only", name="ruleset.json", mode="simulation"):
    out = tmp_path / name
    result = compiler.publish(_documents(), approval=approval, out=out, mode=mode, decisions=decisions, review_policy=policy)
    assert out.exists() == result.published
    return result


def test_an_approved_review_publishes_the_same_ruleset(tmp_path):
    compiler = _compiler(ruleset_path=tmp_path / "production.json")
    review = _review(_sign_offs(), compiler=compiler)
    approval = compiler.approve(review, **APPROVER)
    assert (approval.output_sha256, approval.ruleset_sha256) == (review.review_sha256, review.candidate_ruleset_sha256)
    assert approval.output["kind"] == "review" and approval.output["policy"] == "exceptions_only"
    result = _published(compiler, tmp_path, approval, _sign_offs())
    assert result.published and (tmp_path / "ruleset.json").read_text() == PATHS.ruleset.read_text()


def test_a_review_that_is_not_ready_or_was_altered_cannot_be_approved():
    for review, message in [
        (_review([]), "2 fields need a decision"),
        (_review([*_sign_offs(), _decision(_find(EXTRACTION, "MEAL-01", "thresholds"), "reject", rationale="unclear")]), "1 issues block it"),
        (_review(_sign_offs()).model_copy(update={"policy": "all_fields"}), "has been altered"),
        (_review([]).model_copy(update={"ready_for_approval": True}), "has been altered"),
    ]:
        with pytest.raises(ApprovalRefused, match=message):
            COMPILER.approve(review, **APPROVER)


def test_publication_refuses_anything_but_the_approved_review(tmp_path):
    compiler = _compiler(ruleset_path=tmp_path / "production.json")
    approval = compiler.approve(_review(_sign_offs(), compiler=compiler), **APPROVER)
    changed = [{**_sign_offs()[0], "rationale": "a different reason"}, *_sign_offs()[1:]]
    confirmed = [_decision(c, "confirm") for c in EXTRACTION.comparisons if c.status == "agreed"]
    cases = {
        "changed decision": (approval, changed, "exceptions_only"),
        "other policy": (approval, [*_sign_offs(), *confirmed], "all_fields"),
        "tampered record": (approval.model_copy(update={"output": {**approval.output, "policy": "all_fields"}}), _sign_offs(), "exceptions_only"),
    }
    reasons = {name: _published(compiler, tmp_path, a, d, p, name=f"{name}.json").reasons for name, (a, d, p) in cases.items()}
    assert reasons["changed decision"] == reasons["other policy"] == ("approval does not hold: the agreement output has changed since it was approved",)
    assert reasons["tampered record"] == ("approval does not hold: the approval record does not match the output it holds",)


def test_an_incomplete_or_invalid_review_never_publishes(tmp_path):
    compiler = _compiler(ruleset_path=tmp_path / "production.json")
    approval = compiler.approve(_review(_sign_offs(), compiler=compiler), **APPROVER)
    incomplete = _published(compiler, tmp_path, approval, _sign_offs(), policy="all_fields", name="incomplete.json")
    assert "review incomplete: 285 fields still need a decision" in incomplete.reasons
    meal = _find(EXTRACTION, "MEAL-01", "thresholds")
    override = [*_sign_offs(), _decision(meal, "override", value=[{"value": "100", "unit": "usd"}], rationale="the cap is 100")]
    invalid = _published(compiler, tmp_path, approval, override, name="invalid.json")
    assert any(reason.startswith("the reviewed interpretation disagrees with the executable rules: error faithful: MEAL-01") for reason in invalid.reasons)


def test_review_and_extraction_approvals_do_not_stand_in_for_each_other(tmp_path):
    compiler = _compiler(ruleset_path=tmp_path / "production.json")
    resolutions = [{"policy_doc": i.policy_doc, "rule_id": i.rule_id, "field": i.field, "value": [], "clause_hash": i.clause_hash, "reviewer": "r", "rationale": "not a limit"} for i in EXTRACTION.review_items]
    extraction_approval = compiler.approve(compiler.extract(_documents(), resolutions=resolutions), **APPROVER)
    review_approval = compiler.approve(_review(_sign_offs(), compiler=compiler), **APPROVER)
    as_review = _published(compiler, tmp_path, extraction_approval, _sign_offs(), name="a.json")
    as_extraction = compiler.publish(_documents(), approval=review_approval, out=tmp_path / "b.json", mode="simulation", resolutions=resolutions)
    assert as_review.reasons == as_extraction.reasons == ("approval does not hold: the agreement output has changed since it was approved",)
    assert compiler.publish(_documents(), approval=extraction_approval, out=tmp_path / "c.json", mode="simulation", resolutions=resolutions).published
    with pytest.raises(InvalidInputError, match="no resolutions"):
        compiler.publish(_documents(), approval=review_approval, out=tmp_path / "d.json", mode="simulation", resolutions=resolutions, decisions=_sign_offs(), review_policy="exceptions_only")


def test_a_review_of_simulated_readings_never_authorizes_production(tmp_path):
    compiler = _compiler(ruleset_path=tmp_path / "production.json")
    approval = compiler.approve(_review(_sign_offs(), compiler=compiler), **APPROVER)
    result = _published(compiler, tmp_path, approval, _sign_offs(), mode="production")
    assert result.reasons == ("production publication needs model readings, and these are simulation", "approval is of simulation readings, which never authorize production")


def test_review_inputs_are_checked():
    with pytest.raises(InvalidInputError, match="policy must be"):
        _review(_sign_offs(), policy="some")
    with pytest.raises(InvalidInputError, match="decisions.0: action"):
        _review([{**_sign_offs()[0], "action": "approve"}])
    with pytest.raises(InvalidInputError, match="decisions.0: reviewer"):
        _review([{**_sign_offs()[0], "reviewer": " "}])
    with pytest.raises(InvalidInputError, match="must be a list"):
        _review(_sign_offs()[0])
