"""The document and compilation workflow's additions: document profiles, change requests, review read-back, the
history of versions made from an extraction, and the models live extraction may use."""

import mongomock
import pytest
from conftest import POLICY_DOCUMENTS, TEST_BUCKET, extract, review_extraction, step, upload_documents

MEALS = next(document for document in POLICY_DOCUMENTS if document["filename"] == "meals-and-entertainment-policy.md")
SCOPE = {"regions": ["EMEA"], "entities": ["DE01"], "expense_types": ["meals"]}


@pytest.fixture
def mongo():
    return mongomock.MongoClient(tz_aware=True)


@pytest.fixture
def app(make_client, mongo):
    return make_client(mongo=mongo)


@pytest.fixture
def reviewed(app):
    extraction = extract(app, upload_documents(app)).json()
    review = review_extraction(app, extraction).json()
    return extraction, review


def code(response):
    return response.status_code, response.json()["error"]["code"]


def version_from(app, extraction, review, name="from-documents"):
    return app.post("/v1/rulesets", json={"name": name, "extraction": {"extraction_id": extraction["extraction_id"], "review_id": review["review_id"]}})


# --- Document profiles ---


def test_a_profile_is_given_at_upload_and_changed_later_without_a_new_revision(app, mongo, s3):
    created = app.post("/v1/documents", json={"filename": MEALS["filename"], "content": MEALS["content"], "title": "Meals", "category": "meals_entertainment", "description": "Business meals.", "scope": SCOPE})
    assert created.status_code == 201, created.text
    document = created.json()
    assert (document["category"], document["description"], document["scope"]) == ("meals_entertainment", "Business meals.", SCOPE)
    before = (mongo["expense_api"]["documents"].find_one({"document_id": document["document_id"]}), s3.list_objects_v2(Bucket=TEST_BUCKET)["KeyCount"])

    changed = app.patch(f"/v1/documents/{document['document_id']}/profile", json={"actor": "e.ditor", "description": "Client and team meals."})
    assert changed.status_code == 200, changed.text
    # Only the given field changes; the revision, its text, hash and object are exactly as they were.
    assert (changed.json()["category"], changed.json()["description"], changed.json()["revision"]) == ("meals_entertainment", "Client and team meals.", 1)
    assert (mongo["expense_api"]["documents"].find_one({"document_id": document["document_id"]}), s3.list_objects_v2(Bucket=TEST_BUCKET)["KeyCount"]) == before
    assert app.get(f"/v1/documents/{document['document_id']}").json()["description"] == "Client and team meals."
    assert "content" not in mongo["expense_api"]["document_profiles"].find_one({"_id": document["document_id"]})

    cleared = app.patch(f"/v1/documents/{document['document_id']}/profile", json={"actor": "e.ditor", "category": None}).json()
    assert cleared["category"] is None  # unclassified


def test_a_document_never_described_is_unclassified(app):
    [ref] = upload_documents(app, documents=[MEALS])
    listed = app.get("/v1/documents").json()["items"][0]
    assert (listed["document_id"], listed["category"], listed["description"], listed["scope"]) == (ref["document_id"], None, None, None)


@pytest.mark.parametrize("body", [
    {"actor": "e.ditor", "category": "lunch"},  # not a category
    {"actor": "e.ditor", "scope": {"regions": [""]}},
    {"category": "gifts_awards"},  # who changed it is recorded
    {"actor": "e.ditor", "content": "# rewritten"},  # a profile never carries text
])
def test_a_refused_profile_change_changes_nothing(app, body):
    [ref] = upload_documents(app, documents=[MEALS])
    assert code(app.patch(f"/v1/documents/{ref['document_id']}/profile", json=body))[0] == 422
    assert app.get(f"/v1/documents/{ref['document_id']}").json()["category"] is None


def test_a_profile_of_an_unknown_document_is_not_found(app):
    assert code(app.patch("/v1/documents/doc_" + "0" * 24 + "/profile", json={"actor": "e.ditor", "category": "gifts_awards"})) == (404, "document_not_found")


def test_an_identical_copy_is_marked_a_duplicate_and_restored_and_nothing_is_deleted(app, mongo, s3):
    kept, copy, other = (app.post("/v1/documents", json=body).json() for body in (
        {"filename": MEALS["filename"], "content": MEALS["content"]}, {"filename": MEALS["filename"], "content": MEALS["content"]},
        {"filename": "other.md", "content": MEALS["content"] + "\nMore."}))
    before = (list(mongo["expense_api"]["documents"].find()), s3.list_objects_v2(Bucket=TEST_BUCKET)["KeyCount"])

    marked = app.patch(f"/v1/documents/{copy['document_id']}/profile", json={"actor": "e.ditor", "duplicate_of": kept["document_id"]})
    assert marked.status_code == 200 and marked.json()["duplicate_of"] == kept["document_id"]
    listed = {item["document_id"]: item["duplicate_of"] for item in app.get("/v1/documents").json()["items"]}
    assert listed == {kept["document_id"]: None, copy["document_id"]: kept["document_id"], other["document_id"]: None}
    # Never a deletion: every revision, its text and its S3 object stay.
    assert (list(mongo["expense_api"]["documents"].find()), s3.list_objects_v2(Bucket=TEST_BUCKET)["KeyCount"]) == before
    assert app.get(f"/v1/documents/{copy['document_id']}").json()["content"] == MEALS["content"]

    # Different content, itself, a chain, or the kept copy are refused.
    assert code(app.patch(f"/v1/documents/{other['document_id']}/profile", json={"actor": "e.ditor", "duplicate_of": kept["document_id"]})) == (422, "not_identical")
    assert code(app.patch(f"/v1/documents/{kept['document_id']}/profile", json={"actor": "e.ditor", "duplicate_of": kept["document_id"]})) == (422, "invalid_duplicate")
    assert code(app.patch(f"/v1/documents/{kept['document_id']}/profile", json={"actor": "e.ditor", "duplicate_of": copy["document_id"]})) == (409, "duplicate_chain")
    assert code(app.patch(f"/v1/documents/{kept['document_id']}/profile", json={"actor": "e.ditor", "duplicate_of": "doc_" + "0" * 24})) == (422, "invalid_duplicate")

    restored = app.patch(f"/v1/documents/{copy['document_id']}/profile", json={"actor": "e.ditor", "duplicate_of": None})
    assert restored.status_code == 200 and restored.json()["duplicate_of"] is None


# --- Review read-back and history ---


def test_a_stored_review_is_read_back_with_its_decisions(app, reviewed):
    extraction, review = reviewed
    fetched = app.get(f"/v1/documents/extractions/{extraction['extraction_id']}/reviews/{review['review_id']}")
    assert fetched.status_code == 200 and fetched.json() == review
    assert code(app.get(f"/v1/documents/extractions/{extraction['extraction_id']}/reviews/rev_none")) == (404, "review_not_found")


def test_the_history_names_the_versions_made_from_each_extraction(app, reviewed):
    extraction, review = reviewed
    assert app.get("/v1/documents/extractions").json()["items"][0]["versions"] == []
    version = version_from(app, extraction, review).json()
    [listed] = app.get("/v1/documents/extractions").json()["items"]
    assert listed["versions"] == [{"ruleset_id": version["ruleset_id"], "name": "from-documents", "version": 1, "status": "draft"}]
    assert listed["open_change_requests"] == 0
    detail = app.get(f"/v1/documents/extractions/{extraction['extraction_id']}").json()
    assert detail["rule_titles"] and all(isinstance(title, str) and title for title in detail["rule_titles"].values())
    # Each rule under review with how it applies its params: TIME-03's gift cap is GIFT-01's threshold, split around.
    split = detail["rule_logic"]["TIME-03"]
    assert split["params"]["gift_cap_usd"]["value"] == "50"
    assert split["param_uses"]["gift_cap_usd"]["helper"] == {"measure": "each receipt's client-gift total for one named recipient, in USD", "operator": "each at or below, together above", "applies_to": "client gifts that name their recipient, not gifts to government officials", "threshold_of": "GIFT-01"}
    # Each rule under review, and the rules it replaces or is replaced by, which a decision on it also affects.
    under_review = {comparison["rule_id"] for comparison in detail["review_required"]}
    replaced = {item["rule_id"] for rule_id in under_review for item in detail["rule_logic"][rule_id]["replaces"] or []}
    assert under_review <= set(detail["rule_logic"]) and replaced <= set(detail["rule_logic"])
    assert all(rule_id in under_review | replaced or set(under_review) & {item["rule_id"] for item in logic["replaces"] or []} for rule_id, logic in detail["rule_logic"].items())


# --- Change requests ---


def request_change(app, extraction, comparison_id=None, **fields):
    comparison_id = comparison_id or extraction["review_required"][0]["comparison_id"]
    body = {"actor": "r.eviewer", "comparison_id": comparison_id, "requested_correction": "The threshold is 50 USD, not 75.", "comment": "See section 2.", **fields}
    return app.post(f"/v1/documents/extractions/{extraction['extraction_id']}/change-requests", json=body)


def test_a_change_request_names_its_field_and_blocks_a_version_until_resolved(app, reviewed):
    extraction, review = reviewed
    comparison = extraction["review_required"][0]
    created = request_change(app, extraction)
    assert created.status_code == 201, created.text
    record = created.json()
    assert {name: record[name] for name in ("comparison_id", "rule_id", "field", "policy_doc", "document_id", "status", "requested_by")} == {
        "comparison_id": comparison["comparison_id"], "rule_id": comparison["rule_id"], "field": comparison["field"], "policy_doc": comparison["policy_doc"],
        "document_id": comparison["document_id"], "status": "open", "requested_by": {"claimed": "r.eviewer", "verified": False}}
    assert app.get(f"/v1/documents/extractions/{extraction['extraction_id']}").json()["open_change_requests"] == 1

    refused = version_from(app, extraction, review)
    assert code(refused) == (409, "changes_requested")
    assert refused.json()["error"]["details"] == {"change_requests": [{"change_request_id": record["change_request_id"], "rule_id": comparison["rule_id"], "field": comparison["field"]}]}

    resolved = app.post(f"/v1/documents/extractions/{extraction['extraction_id']}/change-requests/{record['change_request_id']}/resolve", json={"actor": "a.uthor", "note": "Confirmed against the source: 75 is right."})
    assert resolved.status_code == 200 and resolved.json()["status"] == "resolved" and resolved.json()["resolved_by"]["claimed"] == "a.uthor"
    assert code(app.post(f"/v1/documents/extractions/{extraction['extraction_id']}/change-requests/{record['change_request_id']}/resolve", json={"actor": "a.uthor", "note": "again"})) == (409, "change_request_resolved")
    assert version_from(app, extraction, review).status_code == 201
    assert [item["status"] for item in app.get(f"/v1/documents/extractions/{extraction['extraction_id']}/change-requests").json()] == ["resolved"]


def test_a_change_request_must_name_a_compared_field(app, reviewed):
    extraction, _ = reviewed
    assert code(request_change(app, extraction, comparison_id="cmp-none")) == (422, "unknown_comparison")
    assert code(request_change(app, extraction, requested_correction=" ")) == (422, "invalid_request")


def test_an_open_change_request_blocks_approval_and_the_published_version_never_changes(app, mongo, reviewed):
    extraction, review = reviewed
    version = version_from(app, extraction, review).json()
    for name in ("submit", "accept"):
        assert step(app, version, name).status_code == 200
    record = request_change(app, extraction).json()
    assert code(step(app, version, "approve")) == (409, "changes_requested")
    assert mongo["expense_api"]["rulesets"].find_one({"_id": version["ruleset_id"]})["status"] == "in_review"
    app.post(f"/v1/documents/extractions/{extraction['extraction_id']}/change-requests/{record['change_request_id']}/resolve", json={"actor": "a.uthor", "note": "Addressed."})
    for name in ("approve", "publish"):
        assert step(app, version, name).status_code == 200
    published = mongo["expense_api"]["rulesets"].find_one({"_id": version["ruleset_id"]})
    # Requests and profile changes made after publication never touch the published version.
    request_change(app, extraction)
    app.patch(f"/v1/documents/{extraction['documents'][0]['document_id']}/profile", json={"actor": "e.ditor", "category": "travel_regional"})
    # A review resolved again afterwards is a new stored review: the published version keeps the one it was made from.
    assert review_extraction(app, extraction).status_code == 201
    assert mongo["expense_api"]["rulesets"].find_one({"_id": version["ruleset_id"]}) == published


# --- Live extraction models ---


def test_live_extraction_offers_and_accepts_only_chat_models_never_jev_or_a_translator(make_client, settings):
    deployment = settings.model_copy(update={
        "allow_live": True, "compiler_live_recording_set": "live",
        "allowed_models": frozenset({"anthropic/claude-haiku-5.5", "typesafe/jev-1.14", "tencent/hy-mt2-30b-a3b"}),
        "allowed_classification_models": frozenset({"anthropic/claude-haiku-5.5", "typesafe/jev-1.14"}),
        "allowed_translation_models": frozenset({"tencent/hy-mt2-30b-a3b"}),
    })
    app = make_client(app_settings=deployment)
    assert app.get("/v1/backends").json()["policy_compiler"]["extraction_models"] == ["anthropic/claude-haiku-5.5"]
    refs = upload_documents(app, documents=[MEALS])
    for model in ("typesafe/jev-1.14", "tencent/hy-mt2-30b-a3b"):
        refused = app.post("/v1/documents/extractions", json={"documents": refs, "execution_mode": "live", "extraction_model": model})
        assert code(refused) == (422, "model_not_allowed") and refused.json()["error"]["details"] == {"allowed": ["anthropic/claude-haiku-5.5"]}
