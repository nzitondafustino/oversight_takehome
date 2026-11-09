"""Document approval per exact revision, and the verified source location of each compared field."""

from types import SimpleNamespace

import mongomock
import pytest
from conftest import POLICY_DOCUMENTS, TEST_BUCKET, compile_version, extract, upload_documents

from expense_api.services.source_locations import _sha256, locate

MEALS = next(document for document in POLICY_DOCUMENTS if document["filename"] == "meals-and-entertainment-policy.md")
CHANGED = MEALS["content"] + "\nA new paragraph.\n"


@pytest.fixture
def mongo():
    return mongomock.MongoClient(tz_aware=True)


@pytest.fixture
def app(make_client, mongo):
    return make_client(mongo=mongo)


def code(response):
    return response.status_code, response.json()["error"]["code"]


def act(app, document, action, sha=None, note=None, revision=None):
    revision = revision or document["revision"]
    body = {"action": action, "actor": "r.eviewer", "content_sha256": sha or document["content_sha256"], **({"note": note} if note else {})}
    return app.post(f"/v1/documents/{document['document_id']}/revisions/{revision}/approval", json=body)


# --- Approval of an exact revision ---


def test_a_revision_moves_from_draft_to_needs_review_to_approved_with_its_history(app):
    document = app.post("/v1/documents", json={"filename": MEALS["filename"], "content": MEALS["content"]}).json()
    assert document["approval"]["status"] == "draft"
    assert act(app, document, "submit").json()["status"] == "needs_review"
    approved = act(app, document, "approve", note="Matches the signed policy.").json()
    assert (approved["status"], approved["content_sha256"], approved["updated_by"]) == ("approved", document["content_sha256"], {"claimed": "r.eviewer", "verified": False})
    assert [(event["action"], event["from_status"], event["to_status"], event["content_sha256"]) for event in approved["history"]] == [
        ("submit", "draft", "needs_review", document["content_sha256"]), ("approve", "needs_review", "approved", document["content_sha256"])]
    assert approved["history"][1]["note"] == "Matches the signed policy." and approved["history"][1]["at"]
    assert app.get(f"/v1/documents/{document['document_id']}").json()["approval"]["status"] == "approved"
    assert app.get("/v1/documents").json()["items"][0]["approval"]["status"] == "approved"


def test_a_new_revision_is_a_draft_whatever_the_last_one_was(app):
    first = app.post("/v1/documents", json={"filename": MEALS["filename"], "content": MEALS["content"]}).json()
    act(app, first, "submit")
    act(app, first, "approve")
    second = app.post(f"/v1/documents/{first['document_id']}/revisions", json={"content": CHANGED, "based_on_revision": 1}).json()
    assert second["approval"]["status"] == "draft"
    assert app.get(f"/v1/documents/{first['document_id']}?revision=1").json()["approval"]["status"] == "approved"
    assert app.get("/v1/documents").json()["items"][0]["approval"]["status"] == "draft"  # the library shows the latest revision's own
    # Revision 1's hash cannot approve revision 2: approval names the exact text.
    act(app, second, "submit")
    assert code(act(app, second, "approve", sha=first["content_sha256"])) == (409, "revision_mismatch")
    assert app.get(f"/v1/documents/{first['document_id']}/revisions/2/approval").json()["status"] == "needs_review"


def test_identical_copies_keep_their_own_approvals(app):
    one = app.post("/v1/documents", json={"filename": MEALS["filename"], "content": MEALS["content"]}).json()
    two = app.post("/v1/documents", json={"filename": "meals-copy.md", "content": MEALS["content"]}).json()
    assert one["content_sha256"] == two["content_sha256"]
    act(app, one, "submit")
    act(app, one, "approve")
    statuses = {item["filename"]: item["approval"]["status"] for item in app.get("/v1/documents").json()["items"]}
    assert statuses == {MEALS["filename"]: "approved", "meals-copy.md": "draft"}


def test_steps_out_of_order_and_a_return_without_a_note_are_refused(app):
    document = app.post("/v1/documents", json={"filename": MEALS["filename"], "content": MEALS["content"]}).json()
    assert code(act(app, document, "approve")) == (409, "approval_conflict")  # a draft is submitted first
    assert code(act(app, document, "return_to_draft", note="no")) == (409, "approval_conflict")
    act(app, document, "submit")
    assert code(act(app, document, "submit")) == (409, "approval_conflict")
    assert code(act(app, document, "return_to_draft")) == (422, "note_required")
    returned = act(app, document, "return_to_draft", note="Section 3 is out of date.").json()
    assert returned["status"] == "draft" and returned["history"][-1]["note"] == "Section 3 is out of date."
    assert code(app.post(f"/v1/documents/{document['document_id']}/revisions/1/approval", json={"action": "approve", "actor": "r.eviewer"})) == (422, "invalid_request")


def test_approval_checks_the_stored_text_itself(app, mongo, s3):
    document = app.post("/v1/documents", json={"filename": MEALS["filename"], "content": MEALS["content"]}).json()
    act(app, document, "submit")
    key = mongo["expense_api"]["documents"].find_one({"document_id": document["document_id"]})["storage"]["key"]
    s3.put_object(Bucket=TEST_BUCKET, Key=key, Body=b"# changed behind the API")
    assert code(act(app, document, "approve")) == (500, "integrity_error")
    assert app.get(f"/v1/documents/{document['document_id']}/revisions/1/approval").json()["status"] == "needs_review"


def test_documents_used_by_a_published_version_are_never_approved_by_it(app, mongo):
    from conftest import published_version

    upload_documents(app)
    published_version(app)
    assert {item["approval"]["status"] for item in app.get("/v1/documents", params={"limit": 100}).json()["items"]} == {"draft"}
    assert mongo["expense_api"]["document_approvals"].count_documents({}) == 0


# --- Verified source locations ---


@pytest.fixture
def compared(app):
    extraction = extract(app, upload_documents(app)).json()
    items = app.get(f"/v1/documents/extractions/{extraction['extraction_id']}/comparisons", params={"limit": 500}).json()["items"]
    find = lambda rule, field: next(item for item in items if (item["rule_id"], item["field"]) == (rule, field))
    source = lambda item: app.get(f"/v1/documents/extractions/{extraction['extraction_id']}/comparisons/{item['comparison_id']}/source")
    return extraction, find, source


def test_a_clause_is_located_on_its_own_lines_in_the_exact_revision(compared):
    _, find, source = compared
    comparison = find("TECH-01", "quote")
    found = source(comparison).json()
    lines = found["document"]["content"].split("\n")
    assert (found["revision_verified"], found["clause_verified"], found["reason"]) == (True, True, None)
    assert "\n".join(lines[found["location"]["from_line"] - 1:found["location"]["to_line"]]).find(comparison["evidence"]) >= 0
    assert lines[found["heading_line"] - 1].startswith("**TECH-01 — ")
    assert found["highlights"] == []  # both readings agree on the quote, so nothing in it is marked


def test_a_disputed_limit_is_marked_where_the_clause_states_it_even_in_words(compared):
    _, find, source = compared
    found = source(find("TIME-03", "thresholds")).json()  # only the text pattern reading has 2, written "Two or more"
    lines = found["document"]["content"].split("\n")
    assert [(item["text"], item["side"]) for item in found["highlights"]] == [("Two", "regex")]
    for item in found["highlights"]:
        assert lines[item["line"] - 1][item["start"]:item["end"]] == item["text"]
        assert found["location"]["from_line"] <= item["line"] <= found["location"]["to_line"]


def test_an_agreed_field_marks_nothing(compared):
    _, find, source = compared
    assert source(find("EXC-01", "thresholds")).json()["highlights"] == []


def test_a_changed_stored_revision_is_refused_not_located(compared, app, mongo, s3):
    _, find, source = compared
    comparison = find("TECH-01", "quote")
    key = mongo["expense_api"]["documents"].find_one({"document_id": comparison["document_id"]})["storage"]["key"]
    s3.put_object(Bucket=TEST_BUCKET, Key=key, Body=b"# changed")
    assert code(source(comparison)) == (500, "integrity_error")


def test_a_version_locates_its_sources_in_the_text_it_was_compiled_from(app):
    version = compile_version(app).json()
    comparisons = app.get(f"/v1/rulesets/{version['ruleset_id']}/comparisons").json()["comparisons"]
    item = next(comparison for comparison in comparisons if comparison["evidence"])
    found = app.get(f"/v1/rulesets/{version['ruleset_id']}/comparisons/{item['comparison_id']}/source").json()
    assert (found["revision_verified"], found["clause_verified"], found["document"]["revision"]) == (True, True, None)
    assert found["location"]["from_line"] <= found["location"]["to_line"]


@pytest.mark.parametrize(("content", "comparison", "reason"), [
    ("# A\n\nClause text.\n", {"evidence": "Clause text.", "clause_hash": "0" * 64}, "The quoted clause does not match its recorded hash, so its position is not shown."),
    ("# A\n\nOther text.\n", {"evidence": "Clause text.", "clause_hash": _sha256("Clause text.")}, "The clause is not found word for word in this revision."),
    ("Clause text.\nClause text.\n", {"evidence": "Clause text.", "clause_hash": _sha256("Clause text.")}, "The clause occurs more than once in this revision, so its position is ambiguous."),
    ("# A\n", {"evidence": None, "clause_hash": None}, "The compiler located no clause for this field."),
])
def test_an_unverifiable_location_is_reported_never_approximated(content, comparison, reason):
    comparison = SimpleNamespace(rule_id="X-01", field="title", status="disagree", regex_value="a", llm_value="b", location={"line": 1, "section": None}, **comparison)
    found = locate(content, _sha256(content), None, comparison)
    assert (found["location"], found["highlights"], found["reason"]) == (None, [], reason)


def test_a_value_is_marked_only_as_a_whole_number_inside_the_clause():
    text = "Intro 75.\n\n**X-01 — Cap**\nMeals up to 75 USD, never 750 or 75.50.\n"
    clause = "**X-01 — Cap**\nMeals up to 75 USD, never 750 or 75.50."
    comparison = SimpleNamespace(rule_id="X-01", field="thresholds", status="disagree", regex_value=[{"unit": "usd", "value": "75"}], llm_value=[], evidence=clause, clause_hash=_sha256(clause), location={"line": 3, "section": None})
    found = locate(text, _sha256(text), None, comparison)
    assert found["heading_line"] == 3
    assert found["highlights"] == [{"line": 4, "start": 12, "end": 14, "text": "75", "side": "regex"}]
    assert locate(text, _sha256(text), "f" * 64, comparison)["reason"] == "This revision's text is not the text the compiler read, so no position in it is shown."
