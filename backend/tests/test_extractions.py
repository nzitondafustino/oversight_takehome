"""Extractions of stored revisions by the core's PolicyCompiler, their reviews, and the candidates made from them."""

import copy
import hashlib
import json

import mongomock
import pytest
from conftest import (
    COMPILER_RECORDINGS,
    POLICY_DOCUMENTS,
    REPO,
    case_ids,
    extract,
    extraction_decisions,
    load_cases,
    review_extraction,
    upload_documents,
    validate,
)

REQUESTS = load_cases("extraction_requests")
REVIEWS = load_cases("extraction_reviews")
REPOSITORY_RULESET = hashlib.sha256((REPO / "rules" / "ruleset.json").read_bytes()).hexdigest()


def _code(response):
    return response.status_code, response.json()["error"]["code"]


def _version(client, extraction, review=None, name="from-documents"):
    source = {"extraction_id": extraction["extraction_id"], **({"review_id": review["review_id"]} if review else {})}
    return client.post("/v1/rulesets", json={"name": name, "extraction": source})


def _lifecycle(client, version, **approve):
    path = f"/v1/rulesets/{version['ruleset_id']}"
    responses = [
        client.post(f"{path}/submit", json={}),
        client.post(f"{path}/review", json={"actor": "r.eviewer", "decision": "accepted"}),  # the prepared decisions
        client.post(f"{path}/approve", json={"actor": "a.pprover", **approve}),
        client.post(f"{path}/publish", json={}),
    ]
    return responses


@pytest.fixture
def reviewed(client):
    """An extraction of the nine stored documents from model readings, with a ready review."""
    extraction = extract(client, upload_documents(client)).json()
    return client, extraction, review_extraction(client, extraction).json()


# --- Extraction ---


def test_an_extraction_keeps_the_cores_result_for_the_exact_revisions(client):
    refs = upload_documents(client)
    response = extract(client, refs)
    assert response.status_code == 201, response.text
    extraction = response.json()
    assert (extraction["status"], extraction["ready_for_approval"], extraction["candidate_ruleset_sha256"]) == ("review_required", False, REPOSITORY_RULESET)
    assert [(item["document_id"], item["revision"]) for item in extraction["documents"]] == sorted(
        [(ref["document_id"], ref["revision"]) for ref in refs], key=lambda pair: next(item["filename"] for item in extraction["documents"] if item["document_id"] == pair[0])
    )
    assert {item["content_sha256"] for item in extraction["documents"]} == {hashlib.sha256(document["content"].encode()).hexdigest() for document in POLICY_DOCUMENTS}
    assert extraction["provenance"] | {"prompt_sha256": None} == {"execution_mode": "replay", "recording_set": "live", "recorded_with": None, "reading_kind": "model", "extraction_model": None, "temperature": None, "timeout_seconds": None, "overrides": [], "rule_overrides_from": None, "prompt_sha256": None}
    assert [(item["rule_id"], item["field"]) for item in extraction["review_required"]] == [("TIME-03", "thresholds")]
    assert len(extraction["blockers"]) == 1 and extraction["agreement_summary"]["rules_needing_review"] == 1
    assert extraction["comparison_count"] == 287 and extraction["reviews"] == []

    fetched = client.get(f"/v1/documents/extractions/{extraction['extraction_id']}")
    assert fetched.status_code == 200 and fetched.json() == extraction


def test_every_comparison_is_kept_and_paged(client):
    extraction = extract(client, upload_documents(client)).json()
    path = f"/v1/documents/extractions/{extraction['extraction_id']}/comparisons"
    pages = [client.get(path, params={"limit": 100, "offset": offset}).json() for offset in (0, 100, 200)]
    assert [len(page["items"]) for page in pages] == [100, 100, 87] and {page["total"] for page in pages} == {287}
    assert len({item["comparison_id"] for page in pages for item in page["items"]}) == 287
    assert all(item["extraction_sha256"] == extraction["extraction_sha256"] for page in pages for item in page["items"])
    needing = client.get(path, params={"review_required": True}).json()
    assert needing["items"] == extraction["review_required"]


def test_one_document_alone_is_extracted_but_builds_no_candidate(client):
    extraction = extract(client, upload_documents(client, documents=POLICY_DOCUMENTS[:1])).json()
    assert (extraction["status"], extraction["candidate_ruleset_sha256"]) == ("no_candidate", None)
    assert any(blocker.startswith("build failed") for blocker in extraction["blockers"])
    assert _code(_version(client, extraction)) == (409, "no_candidate")


def test_the_selected_revision_is_what_the_compiler_reads(client):
    refs = upload_documents(client)
    meals = next(ref for ref, document in zip(refs, POLICY_DOCUMENTS) if document["filename"] == "meals-and-entertainment-policy.md")
    text = client.get(f"/v1/documents/{meals['document_id']}").json()["content"]
    client.post(f"/v1/documents/{meals['document_id']}/revisions", json={"content": text.replace("$75", "$90")})

    edited = [ref if ref is not meals else {**ref, "revision": 2} for ref in refs]
    missed = extract(client, edited)  # no reading was ever recorded for the edited text, and nothing falls back to the original
    assert _code(missed) == (409, "replay_recording_missing") and missed.json()["error"]["details"]["missing_documents"] == ["meals-and-entertainment-policy.md"]
    assert extract(client, refs).status_code == 201


@pytest.mark.parametrize("case", REQUESTS, ids=case_ids(REQUESTS))
def test_a_refused_extraction_request_stores_nothing(make_client, case):
    mongo = mongomock.MongoClient(tz_aware=True)
    client = make_client(mongo=mongo)
    refs = upload_documents(client)
    selections = {"repeat_first": [refs[0], refs[0]], "unknown": [{"document_id": "doc_" + "0" * 24, "revision": 1}], "revision_2": [{**refs[0], "revision": 2}]}
    body = {"documents": selections.get(case.get("select"), refs), "recording_set": "live", **case.get("request", {})}
    response = client.post("/v1/documents/extractions", json={name: value for name, value in body.items() if value is not None})
    assert _code(response) == (case["expect"]["status"], case["expect"]["code"]), response.text
    assert mongo["expense_api"]["extractions"].count_documents({}) == 0


def test_an_unknown_extraction_is_not_found(reviewed):
    client, _, _ = reviewed
    path = "/v1/documents/extractions/ext_000000000000000000000000"
    assert _code(client.get(path)) == (404, "extraction_not_found")
    assert _code(client.get(f"{path}/comparisons")) == (404, "extraction_not_found")


def test_a_stored_extraction_that_no_longer_matches_its_hash_is_refused(make_client):
    mongo = mongomock.MongoClient(tz_aware=True)
    client = make_client(mongo=mongo)
    extraction = extract(client, upload_documents(client)).json()
    stored = mongo["expense_api"]["extractions"].find_one({"_id": extraction["extraction_id"]})
    mongo["expense_api"]["extractions"].update_one({"_id": extraction["extraction_id"]}, {"$set": {"result_json": stored["result_json"].replace("MCC-03", "MCC-09", 1)}})
    for response in (client.get(f"/v1/documents/extractions/{extraction['extraction_id']}/comparisons"), review_extraction(client, extraction)):
        assert _code(response) == (500, "integrity_error")


# --- Review ---


@pytest.mark.parametrize("case", REVIEWS, ids=case_ids(REVIEWS))
def test_the_cores_review_decides_whether_an_extraction_is_ready(client, case):
    extraction = extract(client, upload_documents(client)).json()
    by_rule = {decision_rule: decision for decision_rule, decision in zip([item["rule_id"] for item in extraction["review_required"]], extraction_decisions(extraction))}
    for rule_id in case.get("drop", []):
        del by_rule[rule_id]
    for rule_id, fields in case.get("change", {}).items():
        by_rule[rule_id] = {name: value for name, value in {**by_rule[rule_id], **fields}.items() if value is not None}
    decisions = list(by_rule.values())
    if "add_agreed" in case:
        page = client.get(f"/v1/documents/extractions/{extraction['extraction_id']}/comparisons", params={"limit": 500}).json()
        agreed = next(item for item in page["items"] if item["rule_id"] == case["add_agreed"] and item["status"] == "agreed")
        decisions.append({"comparison_id": agreed["comparison_id"], "extraction_sha256": extraction["extraction_sha256"], "reviewer": "r.eviewer", "action": "confirm"})

    response = review_extraction(client, extraction, decisions, review_policy=case.get("policy", "exceptions_only"))
    assert response.status_code == 201, response.text
    review = response.json()
    assert (review["ready_for_approval"], len(review["remaining"])) == (case["expect"]["ready"], case["expect"]["remaining"])
    assert sorted({issue["code"] for issue in review["issues"] if issue["level"] == "error"}) == case["expect"]["issues"]
    assert review["decisions"] == decisions and review["extraction_sha256"] == extraction["extraction_sha256"]
    assert review["candidate_ruleset_sha256"] == REPOSITORY_RULESET and len(review["review_sha256"]) == 64
    listed = client.get(f"/v1/documents/extractions/{extraction['extraction_id']}").json()["reviews"]
    assert [(item["review_id"], item["ready_for_approval"]) for item in listed] == [(review["review_id"], case["expect"]["ready"])]


def test_a_review_names_the_exact_snapshot_and_the_cores_decisions(client):
    extraction = extract(client, upload_documents(client)).json()
    assert _code(review_extraction(client, extraction, extraction_sha256="0" * 64)) == (409, "stale_extraction")
    invented = [{**extraction_decisions(extraction)[0], "action": "approve"}]
    assert _code(review_extraction(client, extraction, invented)) == (422, "invalid_review_decisions")
    other = extract(client, upload_documents(client), recording_set="fixtures").json()  # the same documents, other readings
    foreign = extraction_decisions(other, "fixtures")
    reviewed = review_extraction(client, extraction, foreign).json()
    assert reviewed["ready_for_approval"] is False and {issue["code"] for issue in reviewed["issues"]} >= {"stale_extraction"}


# --- Candidate versions ---


def test_only_a_ready_review_makes_a_candidate_and_it_starts_as_a_draft(reviewed):
    client, extraction, review = reviewed
    assert _code(_version(client, extraction)) == (409, "review_required")
    partial = review_extraction(client, extraction, extraction_decisions(extraction)[1:]).json()
    assert _code(_version(client, extraction, partial)) == (409, "review_not_ready")
    assert _code(_version(client, extraction, {"review_id": "rev_" + "0" * 24})) == (404, "review_not_found")

    version = _version(client, extraction, review)
    assert version.status_code == 201, version.text
    version = version.json()
    assert (version["status"], version["artifact_sha256"], version["source"]["kind"]) == ("draft", extraction["candidate_ruleset_sha256"], "policy_compiler")
    compilation = version["compilation"]
    assert (compilation["extraction_id"], compilation["extraction_sha256"], compilation["reading_kind"]) == (extraction["extraction_id"], extraction["extraction_sha256"], "model")
    assert compilation["prepared_review"] == {"review_id": review["review_id"], "policy": "exceptions_only", "review_sha256": review["review_sha256"]}
    assert [(item["document_id"], item["revision"]) for item in compilation["documents"]] == [(item["document_id"], item["revision"]) for item in extraction["documents"]]
    assert _code(validate(client, version["ruleset_id"], preview=False)) == (409, "ruleset_not_published")


def test_a_candidate_from_documents_goes_through_the_unchanged_lifecycle(reviewed):
    client, extraction, review = reviewed
    version = _version(client, extraction, review).json()
    responses = _lifecycle(client, version)
    assert [response.status_code for response in responses] == [200] * 4, [response.text for response in responses]
    published = responses[-1].json()
    assert (published["status"], published["publication_mode"]) == ("published", "production")
    assert published["review"]["review_sha256"] == review["review_sha256"]  # the lifecycle review re-ran the same decisions
    run = validate(client, version["ruleset_id"], preview=False)
    assert run.status_code == 201 and run.json()["ruleset"]["artifact_sha256"] == extraction["candidate_ruleset_sha256"]


def test_simulation_readings_from_documents_never_publish_for_production(client):
    extraction = extract(client, upload_documents(client), recording_set="fixtures").json()
    review = review_extraction(client, extraction, extraction_decisions(extraction, "fixtures")).json()
    version = _version(client, extraction, review).json()
    submit, accept, approve, publish = _lifecycle(client, version)
    assert (submit.status_code, accept.status_code) == (200, 200)
    assert _code(approve) == (422, "approval_does_not_hold") and _code(publish) == (409, "invalid_transition")


# --- Live ---


@pytest.fixture
def live(make_client, settings, tmp_path, monkeypatch):
    """A deployment allowed to extract live, whose provider answers from the core's authored fixture readings.

    The fake replaces only the core's OpenRouter request function, so the core's live path, its recording of
    model readings and every later replay run as they would. Nothing leaves the machine.
    """
    import expense_validator.policy_compiler as policy_compiler

    fixtures = {}
    for path in COMPILER_RECORDINGS["fixtures"].glob("*.json"):
        recording = json.loads(path.read_text())
        fixtures[recording["document_sha256"]] = recording["response"]
    calls = []

    def openrouter(llm, api_key):
        def complete(prompt, text):
            calls.append(llm.model)
            return fixtures[hashlib.sha256(text.encode()).hexdigest()]
        return complete

    monkeypatch.setattr(policy_compiler, "openrouter", openrouter)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-not-a-key")
    recorded = tmp_path / "recorded"
    recorded.mkdir()
    sets = {**COMPILER_RECORDINGS, "recorded": recorded}
    client = make_client(app_settings=settings.model_copy(update={"allow_live": True, "allowed_models": frozenset({"vendor/model-a"}), "compiler_recording_sets": sets, "compiler_live_recording_set": "recorded"}))
    return client, calls, recorded


def test_a_live_extraction_records_model_readings_that_every_later_step_replays(live):
    client, calls, recorded = live
    response = extract(client, upload_documents(client), recording_set=None, execution_mode="live", extraction_model="vendor/model-a", temperature=0.0)
    assert response.status_code == 201, response.text
    extraction = response.json()
    assert calls == ["vendor/model-a"] * 9 and len(list(recorded.glob("*.json"))) == 9
    provenance = extraction["provenance"]
    assert (provenance["execution_mode"], provenance["recording_set"], provenance["reading_kind"]) == ("live", "recorded", "model")
    assert provenance["recorded_with"] == {"extraction_model": "vendor/model-a", "temperature": 0.0, "reading_id": None}

    review = review_extraction(client, extraction, extraction_decisions(extraction, "fixtures")).json()
    version = _version(client, extraction, review).json()
    responses = _lifecycle(client, version)
    assert [response.status_code for response in responses] == [200] * 4, [response.text for response in responses]
    assert calls == ["vendor/model-a"] * 9  # review, approval and publication replayed the recorded readings
    assert validate(client, version["ruleset_id"], preview=False).status_code == 201


def test_live_extraction_needs_an_allowed_model_and_a_key(live, monkeypatch):
    client, calls, _ = live
    refs = upload_documents(client)
    live_body = {"recording_set": None, "execution_mode": "live"}
    assert _code(extract(client, refs, extraction_model="vendor/model-b", **live_body)) == (422, "model_not_allowed")
    assert _code(extract(client, refs, extraction_model="vendor/model-a", temperature=5, **live_body)) == (422, "unsupported_configuration")
    monkeypatch.delenv("OPENROUTER_API_KEY")
    assert _code(extract(client, refs, extraction_model="vendor/model-a", **live_body)) == (503, "live_extraction_unavailable")
    assert calls == []


def test_live_extraction_is_unavailable_without_a_recording_set(make_client, settings):
    client = make_client(app_settings=settings.model_copy(update={"allow_live": True, "allowed_models": frozenset({"vendor/model-a"})}))
    response = extract(client, upload_documents(client), recording_set=None, execution_mode="live", extraction_model="vendor/model-a")
    assert _code(response) == (503, "live_extraction_unavailable")


# --- Listing ---


def _list(client, **params):
    response = client.get("/v1/documents/extractions", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_no_extraction_lists_as_an_empty_page(client):
    assert _list(client) == {"items": [], "total": 0, "limit": 20, "offset": 0}
    assert _list(client, limit=5, offset=40) == {"items": [], "total": 0, "limit": 5, "offset": 40}


def test_extractions_list_newest_first_a_page_at_a_time(client):
    refs = upload_documents(client, documents=POLICY_DOCUMENTS[:3])
    made = [extract(client, [ref]).json()["extraction_id"] for ref in refs]
    newest_first = made[::-1]
    pages = [_list(client, limit=2, offset=offset) for offset in (0, 2, 4)]
    assert [[item["extraction_id"] for item in page["items"]] for page in pages] == [newest_first[:2], newest_first[2:], []]
    assert {(page["total"], page["limit"]) for page in pages} == {(3, 2)}


def test_a_listed_extraction_is_its_stored_summary_without_the_cores_result(reviewed):
    client, extraction, review = reviewed
    [listed] = _list(client)["items"]
    fetched = client.get(f"/v1/documents/extractions/{extraction['extraction_id']}").json()
    assert "review_required" not in listed and "rule_titles" not in listed and "rule_logic" not in listed and "discrepancies" not in listed and "result_json" not in listed
    assert listed == {name: value for name, value in fetched.items() if name not in ("review_required", "rule_titles", "rule_logic", "discrepancies")}  # read from the core's result: the single view only
    assert [(item["review_id"], item["ready_for_approval"], item["remaining"]) for item in listed["reviews"]] == [(review["review_id"], True, 0)]


def test_listing_never_reads_the_stored_result(make_client):
    mongo = mongomock.MongoClient(tz_aware=True)
    client = make_client(mongo=mongo)
    extraction = extract(client, upload_documents(client, documents=POLICY_DOCUMENTS[:1])).json()
    mongo["expense_api"]["extractions"].update_one({"_id": extraction["extraction_id"]}, {"$set": {"result_json": "not the core's result"}})
    assert _code(client.get(f"/v1/documents/extractions/{extraction['extraction_id']}")) == (500, "integrity_error")
    assert [item["extraction_id"] for item in _list(client)["items"]] == [extraction["extraction_id"]]


def test_extractions_list_by_a_document_they_read(client):
    first, second = upload_documents(client, documents=POLICY_DOCUMENTS[:2])
    only_first = extract(client, [first]).json()["extraction_id"]
    both = extract(client, [first, second]).json()["extraction_id"]
    assert [item["extraction_id"] for item in _list(client, document_id=first["document_id"])["items"]] == [both, only_first]
    assert [item["extraction_id"] for item in _list(client, document_id=second["document_id"])["items"]] == [both]
    assert _list(client, document_id="doc_" + "0" * 24)["total"] == 0


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"document_id": "not-a-document"}])
def test_a_malformed_listing_request_is_refused(client, params):
    assert client.get("/v1/documents/extractions", params=params).status_code == 422
