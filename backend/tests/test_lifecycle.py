"""The ruleset lifecycle, decided by the core's PolicyCompiler: draft → in_review → approved → published."""

import hashlib
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import mongomock
import pytest
from conftest import (
    COMPILER_RECORDINGS,
    DECISIONS,
    POLICY_DOCUMENTS,
    REPO,
    case_ids,
    compile_version,
    create_ruleset,
    load_cases,
    published_version,
    review_decisions,
    step,
    validate,
)
from expense_validator import PolicyCompiler

from expense_api.repositories import RulesetRepository

TRANSITIONS = load_cases("lifecycle_transitions")
REPOSITORY_RULESET = hashlib.sha256((REPO / "rules" / "ruleset.json").read_bytes()).hexdigest()


def _get(client, record):
    return client.get(f"/v1/rulesets/{record['ruleset_id']}").json()


def _code(response):
    return response.status_code, response.json()["error"]["code"]


def _core_approval(change=None):
    """An approval made directly with the core's PolicyCompiler, as a reviewer's own tooling would make it.

    change, given, replaces the first decision's fields: a different review of the same extraction.
    """
    compiler = PolicyCompiler.from_config(REPO / "config.yaml", mode="replay", recordings_dir=COMPILER_RECORDINGS["live"])
    extraction = compiler.extract(POLICY_DOCUMENTS)
    wanted = {(item["rule_id"], item["field"]): item for item in DECISIONS["live"]}
    decisions = [
        {"comparison_id": comparison.comparison_id, "extraction_sha256": extraction.extraction_sha256, "reviewer": "r.eviewer",
         **{name: value for name, value in wanted[(comparison.rule_id, comparison.field)].items() if name not in ("rule_id", "field")}}
        for comparison in extraction.comparisons if comparison.review_required
    ]
    if change:
        decisions[0] |= change
    review = compiler.review(extraction, decisions, policy="exceptions_only", documents=POLICY_DOCUMENTS)
    return compiler.approve(review, approved_by="a.pprover", approved_at="2026-11-06T10:00:00Z").model_dump(mode="json")


# --- Drafts and versions ---


def test_a_compiled_draft_holds_the_compilers_candidate_and_its_hashes(client):
    response = compile_version(client)
    assert response.status_code == 201, response.text
    record = response.json()
    assert (record["version"], record["status"], record["source"]["kind"]) == (1, "draft", "policy_compiler")
    assert record["artifact_sha256"] == REPOSITORY_RULESET  # the candidate these documents compile to
    compilation = record["compilation"]
    assert (compilation["recording_set"], compilation["reading_kind"], compilation["ready_for_approval"], compilation["comparisons_needing_review"]) == ("live", "model", False, 4)
    assert [document["filename"] for document in compilation["documents"]] == [document["filename"] for document in POLICY_DOCUMENTS]
    assert [(event["from_status"], event["to_status"]) for event in record["transitions"]] == [(None, "draft")]

    comparisons = client.get(f"/v1/rulesets/{record['ruleset_id']}/comparisons").json()
    assert comparisons["extraction_sha256"] == compilation["extraction_sha256"] and len(comparisons["comparisons"]) == 287


def test_each_new_draft_is_the_next_version_and_may_name_the_one_it_replaces(client):
    assert [compile_version(client).json()["version"] for _ in range(2)] == [1, 2]
    assert _code(compile_version(client, based_on_version=1)) == (409, "version_conflict")  # 2 was made since
    replacement = compile_version(client, based_on_version=2).json()
    assert (replacement["version"], replacement["based_on_version"]) == (3, 2)
    assert create_ruleset(client)["version"] == 4  # uploads share the same version history


def test_two_edits_of_the_same_version_cannot_both_become_the_next_one(client):
    compile_version(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: compile_version(client, based_on_version=1), range(2)))
    assert sorted(response.status_code for response in responses) == [201, 409]


def test_a_draft_needs_documents_the_recordings_hold(client):
    edited = [dict(document) for document in POLICY_DOCUMENTS]
    edited[0]["content"] = edited[0]["content"].replace("must", "should", 1)
    assert _code(compile_version(client, documents=edited)) == (409, "replay_recording_missing")
    assert _code(compile_version(client, recording_set="nightly")) == (422, "unknown_recording_set")
    both = client.post("/v1/rulesets", json={"name": "default", "compile": {"documents": POLICY_DOCUMENTS, "recording_set": "live"}, "artifact": {"rules": []}})
    assert _code(both) == (422, "invalid_request")


# --- Transitions ---


@pytest.mark.parametrize("case", TRANSITIONS, ids=case_ids(TRANSITIONS))
def test_a_step_out_of_order_is_refused_and_changes_nothing(client, case):
    record = compile_version(client).json()
    for name in case["steps"]:
        assert step(client, record, name).status_code == 200
    before = _get(client, record)
    assert _code(step(client, record, case["step"])) == (case["expect"]["status"], case["expect"]["code"])
    assert _get(client, record) == before


def test_submission_and_rejection_are_recorded_and_the_version_can_return(client):
    record = compile_version(client).json()
    submitted = step(client, record, "submit", actor="a.uthor", reason="ready for review").json()
    assert submitted["status"] == "in_review" and submitted["submitted_at"] is not None

    unexplained = client.post(f"/v1/rulesets/{record['ruleset_id']}/review", json={"actor": "r.eviewer", "decision": "rejected"})
    assert _code(unexplained) == (422, "invalid_request")
    rejected = step(client, record, "reject").json()
    assert rejected["status"] == "draft" and rejected["review"]["decision"] == "rejected"
    assert rejected["review"]["reviewer"] == {"claimed": "r.eviewer", "verified": False}

    again = step(client, record, "submit").json()
    assert again["status"] == "in_review" and again["review"] is None
    history = [(event["from_status"], event["to_status"], event["reason"]) for event in again["transitions"]]
    assert history == [(None, "draft", None), ("draft", "in_review", "ready for review"), ("in_review", "draft", "thresholds need a second look"), ("draft", "in_review", None)]
    assert all(event["content_hash"] == record["artifact_sha256"] for event in again["transitions"])
    assert again["transitions"][1]["actor"] == {"claimed": "a.uthor", "verified": False}


def test_the_compiler_decides_whether_a_review_can_be_accepted(client):
    record = compile_version(client).json()
    step(client, record, "submit")
    decisions = review_decisions(client, record)
    review = f"/v1/rulesets/{record['ruleset_id']}/review"
    attempts = {
        "no decisions, while four fields await one": None,
        "one field left undecided": decisions[1:],
        "a regex reading where there is none": [{**decision, "action": "accept_regex"} for decision in decisions],
    }
    for label, attempt in attempts.items():
        response = client.post(review, json={"actor": "r.eviewer", "decision": "accepted", "decisions": attempt})
        assert _code(response) == (422, "review_not_ready"), label
    assert _get(client, record)["review"] is None
    assert step(client, record, "accept").json()["review"]["review_sha256"]


# --- Approval ---


def test_an_approval_is_the_cores_approval_of_this_exact_review(client):
    record = compile_version(client).json()
    step(client, record, "submit")
    reviewed = step(client, record, "accept").json()
    approved = step(client, record, "approve", note="checked against the documents").json()
    assert approved["status"] == "approved" and approved["approval_mode"] == "production"
    assert len(approved["approval_reference"]) == 64 and approved["transitions"][-1]["approval_reference"] == approved["approval_reference"]
    assert approved["transitions"][-1]["actor"] == {"claimed": "a.pprover", "verified": False}
    assert reviewed["artifact_sha256"] == approved["artifact_sha256"] == REPOSITORY_RULESET


def test_an_approval_made_with_the_core_elsewhere_is_verified_and_accepted(client):
    record = compile_version(client).json()
    step(client, record, "submit")
    decisions = review_decisions(client, record)
    client.post(f"/v1/rulesets/{record['ruleset_id']}/review", json={"actor": "r.eviewer", "decision": "accepted", "decisions": decisions})
    approval = _core_approval()
    response = client.post(f"/v1/rulesets/{record['ruleset_id']}/approve", json={"actor": "a.pprover", "approval": approval})
    assert response.status_code == 200, response.text


def test_an_approval_that_does_not_cover_this_version_is_refused(client):
    record = compile_version(client).json()
    step(client, record, "submit")
    decisions = review_decisions(client, record)
    client.post(f"/v1/rulesets/{record['ruleset_id']}/review", json={"actor": "r.eviewer", "decision": "accepted", "decisions": decisions})
    good = _core_approval()
    forged = {
        "an approval of a different review": _core_approval(change={"action": "accept_llm"}),  # MCC-03: the LLM's reading instead
        "an approval of the unreviewed extraction": {**good, "output_sha256": hashlib.sha256(b"extraction").hexdigest()},
        "a candidate hash that is not this version": {**good, "ruleset_sha256": "0" * 64},
    }
    for label, approval in forged.items():
        response = client.post(f"/v1/rulesets/{record['ruleset_id']}/approve", json={"actor": "a.pprover", "approval": approval})
        assert _code(response) == (422, "approval_does_not_hold"), (label, response.text)
    not_a_record = client.post(f"/v1/rulesets/{record['ruleset_id']}/approve", json={"actor": "a.pprover", "approval": {"approved": True}})
    assert _code(not_a_record) == (422, "invalid_approval")
    assert _get(client, record)["status"] == "in_review"


def test_simulation_readings_never_authorize_production(make_client, settings):
    client = make_client(app_settings=settings.model_copy(update={"allowed_publication_modes": frozenset({"production", "simulation"})}))
    record = compile_version(client, recording_set="fixtures").json()
    step(client, record, "submit")
    step(client, record, "accept", recording_set="fixtures")
    refused = step(client, record, "approve")
    assert _code(refused) == (422, "approval_does_not_hold")
    assert "production publication needs model readings, and these are simulation" in refused.json()["error"]["details"]["reasons"]

    simulated = step(client, record, "approve", mode="simulation").json()
    published = step(client, record, "publish").json()
    assert (simulated["approval_mode"], published["publication_mode"]) == ("simulation", "simulation")
    assert _code(validate(client, record["ruleset_id"], preview=False)) == (409, "ruleset_not_published")
    assert validate(client, record["ruleset_id"], preview=True).status_code == 201


def test_simulation_publication_is_off_unless_the_deployment_allows_it(client):
    record = compile_version(client, recording_set="fixtures").json()
    step(client, record, "submit")
    step(client, record, "accept", recording_set="fixtures")
    assert _code(step(client, record, "approve", mode="simulation")) == (422, "publication_mode_not_allowed")


def test_an_uploaded_artifact_is_structurally_valid_but_never_approvable(client):
    record = create_ruleset(client)
    assert record["compilation"] is None
    step(client, record, "submit")
    accepted = client.post(f"/v1/rulesets/{record['ruleset_id']}/review", json={"actor": "r.eviewer", "decision": "accepted"})
    assert accepted.status_code == 200
    assert _code(step(client, record, "approve")) == (409, "approval_unverifiable")
    assert _code(client.get(f"/v1/rulesets/{record['ruleset_id']}/comparisons")) == (409, "not_compiled")


# --- Publication ---


def test_publication_checks_the_approval_again_and_is_idempotent(client):
    published = published_version(client)
    assert (published["status"], published["publication_mode"]) == ("published", "production")

    again = step(client, published, "publish")
    same = step(client, published, "publish", approval_reference=published["approval_reference"])
    assert again.status_code == same.status_code == 200 and again.json() == same.json() == _get(client, published)
    assert [event["to_status"] for event in again.json()["transitions"]].count("published") == 1
    assert _code(step(client, published, "publish", approval_reference="f" * 64)) == (409, "publication_conflict")


def test_concurrent_publications_of_one_version_publish_it_once(client):
    record = compile_version(client).json()
    for name in ("submit", "accept", "approve"):
        step(client, record, name)
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: step(client, record, "publish"), range(4)))
    assert [response.status_code for response in responses] == [200] * 4
    assert len({response.json()["published_at"] for response in responses}) == 1
    assert [event["to_status"] for event in _get(client, record)["transitions"]].count("published") == 1


def test_concurrent_approvals_of_one_version_approve_it_once(client):
    record = compile_version(client).json()
    step(client, record, "submit")
    step(client, record, "accept")
    with ThreadPoolExecutor(max_workers=3) as pool:
        responses = list(pool.map(lambda _: step(client, record, "approve"), range(3)))
    assert sorted(response.status_code for response in responses) == [200, 409, 409]


def test_a_candidate_changed_after_approval_is_not_published(make_client):
    mongo = mongomock.MongoClient(tz_aware=True)
    client = make_client(mongo=mongo)
    record = compile_version(client).json()
    for name in ("submit", "accept", "approve"):
        step(client, record, name)
    versions = mongo["expense_api"]["rulesets"]
    stored = versions.find_one({"_id": record["ruleset_id"]})
    changed = stored["artifact_json"].replace('"v1"', '"v2"')
    versions.update_one({"_id": record["ruleset_id"]}, {"$set": {"artifact_json": changed, "artifact_sha256": hashlib.sha256(changed.encode()).hexdigest()}})

    assert _code(step(client, record, "publish")) == (409, "compilation_changed")
    assert _get(client, record)["status"] == "approved"


def test_an_approval_gone_stale_since_it_was_given_is_not_published(make_client, settings, tmp_path):
    # The recordings are the readings the approval covered: changing one means the compiler no longer reproduces them.
    recordings = tmp_path / "recordings"
    shutil.copytree(COMPILER_RECORDINGS["live"], recordings)
    client = make_client(app_settings=settings.model_copy(update={"compiler_recording_sets": {"live": recordings}}))
    record = compile_version(client).json()
    for name in ("submit", "accept", "approve"):
        step(client, record, name)
    reading = next(path for path in recordings.glob("*.json") if "Overnight transactions" in path.read_text())
    reading.write_text(reading.read_text().replace("Overnight transactions", "Night transactions"))

    assert _code(step(client, record, "publish")) == (409, "compilation_changed")
    assert _get(client, record)["status"] == "approved"


def test_a_published_version_never_changes(client):
    published = published_version(client)
    for name in ("submit", "accept", "reject", "approve"):
        assert _code(step(client, published, name)) == (409, "invalid_transition")
    assert _get(client, published) == published
    with pytest.raises(ValueError, match="may not change"):
        RulesetRepository(mongomock.MongoClient()["x"]["rulesets"]).transition(published["ruleset_id"], {}, {"artifact_json": "{}"}, {})


# --- Validation ---


def test_validation_uses_only_a_version_published_for_production_unless_previewing(make_client, settings):
    client = make_client(app_settings=settings.model_copy(update={"allow_preview_validation": False}))
    published = published_version(client)
    draft = compile_version(client).json()
    approved = compile_version(client).json()
    for name in ("submit", "accept", "approve"):
        step(client, approved, name)

    run = validate(client, published["ruleset_id"], preview=False).json()
    assert run["ruleset"] == {name: published[name] for name in ("ruleset_id", "name", "version", "status", "publication_mode", "artifact_sha256", "ruleset_version")}
    assert run["preview"] is False
    for record in (draft, approved):
        response = validate(client, record["ruleset_id"], preview=False)
        assert _code(response) == (409, "ruleset_not_published")
        assert response.json()["error"]["details"]["status"] == _get(client, record)["status"]
    assert _code(validate(client, draft["ruleset_id"], preview=True)) == (403, "preview_disabled")


def test_a_preview_run_says_so(client):
    draft = compile_version(client).json()
    run = validate(client, draft["ruleset_id"], preview=True).json()
    assert run["preview"] is True and run["ruleset"]["status"] == "draft"


def test_a_historical_run_stays_reproducible_as_new_versions_are_published(client):
    first = published_version(client)
    run = validate(client, first["ruleset_id"], receipt_ids=("RCP-013", "RCP-014"), preview=False).json()
    published_version(client)  # version 2, published later

    assert client.get(f"/v1/runs/{run['run_id']}").json() == run
    rerun = validate(client, first["ruleset_id"], receipt_ids=("RCP-013", "RCP-014"), preview=False).json()
    assert rerun["results"] == run["results"] and rerun["ruleset"] == run["ruleset"] == {**run["ruleset"], "ruleset_id": first["ruleset_id"], "version": 1}
