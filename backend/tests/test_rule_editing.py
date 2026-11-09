"""Editing rules: a new draft version compiled with reviewed rule overrides, through the same lifecycle; the
version edited, published or not, never changes."""

import mongomock
import pytest
from conftest import ARTIFACT, TEST_BUCKET, create_ruleset, extract, published_version, review_extraction, step, upload_documents

TITLE = {"rule_id": "ALC-01", "set": {"title": "Alcohol needs an entertainment context"}, "rationale": "clearer for reviewers"}


@pytest.fixture
def mongo():
    return mongomock.MongoClient(tz_aware=True)


@pytest.fixture
def app(make_client, mongo):
    return make_client(mongo=mongo)


def code(response):
    return response.status_code, response.json()["error"]["code"]


def edit(app, ruleset_id, *changes, rationale="Make the rules clearer"):
    return app.post(f"/v1/rulesets/{ruleset_id}/edits", json={"actor": "e.ditor", "rationale": rationale, "changes": list(changes)})


def rules(version):
    return {rule["rule_id"]: rule for rule in version["artifact"]["rules"]}


def test_editing_a_published_version_makes_a_new_draft_and_never_changes_the_published_one(app, mongo, s3):
    published = published_version(app)
    stored = mongo["expense_api"]["rulesets"].find_one({"_id": published["ruleset_id"]})
    objects = s3.list_objects_v2(Bucket=TEST_BUCKET)["KeyCount"]
    response = edit(app, published["ruleset_id"], TITLE)
    assert response.status_code == 201, response.text
    draft = response.json()
    assert (draft["status"], draft["name"], draft["version"], draft["based_on_version"]) == ("draft", published["name"], published["version"] + 1, published["version"])
    assert rules(draft)["ALC-01"]["title"] == "Alcohol needs an entertainment context"
    assert {rule_id: rule for rule_id, rule in rules(draft).items() if rule_id != "ALC-01"} == {rule_id: rule for rule_id, rule in rules(published).items() if rule_id != "ALC-01"}
    assert draft["artifact_sha256"] != published["artifact_sha256"]
    # The audit: which fields changed, from what, by whom and why, and what it was based on.
    assert draft["edits"]["changes"] == [{"rule_id": "ALC-01", "field": "title", "before": rules(published)["ALC-01"]["title"], "after": "Alcohol needs an entertainment context"}]
    assert (draft["edits"]["based_on"], draft["edits"]["actor"], draft["edits"]["rationale"]) == (published["ruleset_id"], "e.ditor", "Make the rules clearer")
    assert draft["transitions"][0]["actor"] == {"claimed": "e.ditor", "verified": False}
    assert draft["compilation"]["rule_overrides"] == [{"rule_id": "ALC-01", "set": {"title": "Alcohol needs an entertainment context"}, "reviewer": "e.ditor", "rationale": "clearer for reviewers"}]
    # Same documents, same readings: the source text is referenced, never copied, and the published record is untouched.
    assert [document["sha256"] for document in draft["compilation"]["documents"]] == [document["sha256"] for document in published["compilation"]["documents"]]
    assert s3.list_objects_v2(Bucket=TEST_BUCKET)["KeyCount"] == objects
    assert mongo["expense_api"]["rulesets"].find_one({"_id": published["ruleset_id"]}) == stored


def test_an_edited_draft_goes_through_review_approval_and_publication_like_any_other(app, mongo):
    published = published_version(app)
    draft = edit(app, published["ruleset_id"], TITLE).json()
    readiness = app.get(f"/v1/rulesets/{draft['ruleset_id']}/readiness").json()
    assert {name: readiness[name] for name in ("next_step", "rules_changed", "review_remaining", "validation_errors", "approval", "can_publish")} == {
        "next_step": "submit", "rules_changed": 1, "review_remaining": 0, "validation_errors": 0, "approval": "not_approved", "can_publish": False}
    assert step(app, draft, "submit").status_code == 200
    # The review decisions the published version was accepted with carry over, and the compiler checks them again.
    accepted = app.post(f"/v1/rulesets/{draft['ruleset_id']}/review", json={"actor": "r.eviewer", "decision": "accepted"})
    assert accepted.status_code == 200, accepted.text
    assert app.get(f"/v1/rulesets/{draft['ruleset_id']}/readiness").json()["next_step"] == "approve"
    approved = step(app, draft, "approve")
    assert approved.status_code == 200, approved.text  # the compiler rebuilt the draft, with its overrides, and approved exactly it
    assert app.get(f"/v1/rulesets/{draft['ruleset_id']}/readiness").json()["can_publish"] is True
    final = step(app, draft, "publish")
    assert final.status_code == 200, final.text
    assert final.json()["status"] == "published" and rules(final.json())["ALC-01"]["title"] == "Alcohol needs an entertainment context"
    # Both versions stay, each as it was: publishing the new one overwrote nothing.
    first = app.get(f"/v1/rulesets/{published['ruleset_id']}").json()
    assert (first["status"], first["artifact_sha256"]) == ("published", published["artifact_sha256"])
    assert app.get(f"/v1/rulesets/{draft['ruleset_id']}/readiness").json() | {"blockers": None} == {
        "status": "published", "next_step": "none", "rules_changed": 1, "review_remaining": 0, "validation_errors": 0, "approval": "published", "approval_mode": "production", "can_publish": False, "blockers": None}


def test_a_change_the_policy_text_does_not_state_is_refused_naming_the_rule(app):
    published = published_version(app)
    refused = edit(app, published["ruleset_id"], {"rule_id": "ALC-02", "set": {"params": {"cap_usd": {"type": "number", "value": "40", "unit": "usd"}}}})
    assert code(refused) == (422, "rule_edit_invalid")
    assert {item["rule_id"] for item in refused.json()["error"]["details"]["blockers"]} == {"ALC-02"}
    assert "cap_usd is 40 usd, not in its clause" in refused.json()["error"]["message"]
    assert app.get("/v1/rulesets", params={"name": published["name"]}).json()["total"] == 1


@pytest.mark.parametrize(("change", "expected"), [
    ({"rule_id": "ALC-01", "set": {"source_quote": "anything"}}, (422, "invalid_rule_change")),
    ({"rule_id": "ZZZ-01", "set": {"title": "x"}}, (422, "invalid_rule_change")),
    ({"rule_id": "ALC-02", "set": {"params": {"cap_usd": {"type": "number", "value": "30", "unit": "eur"}}}}, (422, "invalid_rule_change")),
    ({"rule_id": "ALC-01", "set": {}}, (422, "invalid_request")),
])
def test_an_unsupported_change_is_refused_and_stores_nothing(app, change, expected):
    published = published_version(app)
    assert code(edit(app, published["ruleset_id"], change)) == expected
    assert app.get("/v1/rulesets", params={"name": published["name"]}).json()["total"] == 1


def test_a_change_to_what_the_rule_already_is_is_no_edit(app):
    published = published_version(app)
    assert code(edit(app, published["ruleset_id"], {"rule_id": "ALC-01", "set": {"title": rules(published)["ALC-01"]["title"]}})) == (422, "no_change")


def test_an_uploaded_artifact_cannot_be_edited(app):
    uploaded = create_ruleset(app, artifact=ARTIFACT)
    assert code(edit(app, uploaded["ruleset_id"], TITLE)) == (409, "not_compiled")


def test_edits_build_on_each_other_and_the_audit_is_against_the_version_edited(app):
    published = published_version(app)
    first = edit(app, published["ruleset_id"], TITLE).json()
    condition = {"rule_id": "REG-01", "set": {"applies_when": {"op": "in", "field": "receipt.entity", "values": ["APAC"]}}}
    second = edit(app, first["ruleset_id"], condition).json()
    assert [item["rule_id"] for item in second["compilation"]["rule_overrides"]] == ["ALC-01", "REG-01"]
    assert rules(second)["ALC-01"]["title"] == "Alcohol needs an entertainment context"
    assert [(change["rule_id"], change["field"]) for change in second["edits"]["changes"]] == [("REG-01", "applies_when")]
    assert second["version"] == published["version"] + 2 and app.get(f"/v1/rulesets/{first['ruleset_id']}").json()["status"] == "draft"


def test_an_open_change_request_blocks_publication_readiness(app):
    extraction = extract(app, upload_documents(app)).json()
    review = review_extraction(app, extraction).json()
    version = app.post("/v1/rulesets", json={"name": "from-documents", "extraction": {"extraction_id": extraction["extraction_id"], "review_id": review["review_id"]}}).json()
    for name in ("submit", "accept", "approve"):
        assert step(app, version, name).status_code == 200
    comparison = extraction["review_required"][0]
    app.post(f"/v1/documents/extractions/{extraction['extraction_id']}/change-requests", json={"actor": "r.eviewer", "comparison_id": comparison["comparison_id"], "requested_correction": "Check it."})
    readiness = app.get(f"/v1/rulesets/{version['ruleset_id']}/readiness").json()
    assert readiness["can_publish"] is False
    assert readiness["blockers"] == [{"message": f"A change was requested on {comparison['rule_id']} ({comparison['field']}) and is still open.", "rule_id": comparison["rule_id"], "comparison_id": comparison["comparison_id"]}]
