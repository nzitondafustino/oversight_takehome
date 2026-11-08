"""Policy documents: stored exactly, as immutable revisions."""

import hashlib

import mongomock
import pytest
from conftest import POLICY_DOCUMENTS, case_ids, load_cases

UPLOADS = load_cases("document_uploads")
MEALS = next(document for document in POLICY_DOCUMENTS if document["filename"] == "meals-and-entertainment-policy.md")


def _create(client, **fields):
    return client.post("/v1/documents", json={"filename": MEALS["filename"], "content": MEALS["content"], **fields})


def _code(response):
    return response.status_code, response.json()["error"]["code"]


@pytest.mark.parametrize("case", UPLOADS, ids=case_ids(UPLOADS))
def test_a_refused_document_is_an_error_and_stores_nothing(make_client, case):
    mongo = mongomock.MongoClient(tz_aware=True)
    client = make_client(mongo=mongo)
    body = {"filename": MEALS["filename"], "content": MEALS["content"], **case.get("request", {})}
    if "filename_length" in case:
        body["filename"] = "a" * (case["filename_length"] - 3) + ".md"
    if "content_bytes" in case:
        body["content"] = "x" * case["content_bytes"]
    if "body" in case:
        response = client.post("/v1/documents", content=case["body"], headers={"content-type": "application/json"})
    elif "latin1" in case:
        response = client.post("/v1/documents", content=case["latin1"].encode("latin-1"), headers={"content-type": "application/json"})
    else:
        response = client.post("/v1/documents", json=body)
    assert _code(response) == (case["expect"]["status"], case["expect"]["code"]), response.text
    assert mongo["expense_api"]["documents"].count_documents({}) == 0


def test_a_document_is_stored_exactly_as_sent(client):
    # Line endings, trailing spaces, tabs, a decomposed accent and an emoji: none of it is normalized.
    content = MEALS["content"].replace("\n", "\r\n", 3) + "  \n\tCafé \U0001F37D\n"
    response = _create(client, content=content, title="Meals", source={"kind": "upload", "reference": "intranet/meals"})
    assert response.status_code == 201, response.text
    created = response.json()
    assert (created["revision"], created["filename"], created["title"], created["based_on_revision"]) == (1, MEALS["filename"], "Meals", None)
    assert created["content_sha256"] == hashlib.sha256(content.encode("utf-8")).hexdigest() and created["size_bytes"] == len(content.encode("utf-8"))
    assert response.headers["location"] == f"/v1/documents/{created['document_id']}?revision=1"

    fetched = client.get(f"/v1/documents/{created['document_id']}").json()
    assert fetched == created and fetched["content"] == content


def test_a_change_is_a_new_revision_and_the_old_one_stays(client):
    first = _create(client).json()
    changed = MEALS["content"] + "\nA new paragraph.\n"
    second = client.post(f"/v1/documents/{first['document_id']}/revisions", json={"content": changed, "based_on_revision": 1})
    assert second.status_code == 201, second.text
    assert (second.json()["revision"], second.json()["based_on_revision"], second.json()["filename"]) == (2, 1, MEALS["filename"])

    path = f"/v1/documents/{first['document_id']}"
    assert client.get(path, params={"revision": 1}).json() == first
    assert client.get(path).json()["revision"] == 2
    unchanged = client.post(f"{path}/revisions", json={"content": changed})
    assert _code(unchanged) == (409, "revision_unchanged") and unchanged.json()["error"]["details"] == {"revision": 2}
    stale = client.post(f"{path}/revisions", json={"content": changed + "more", "based_on_revision": 1})
    assert _code(stale) == (409, "revision_conflict")
    assert _code(client.get(path, params={"revision": 3})) == (404, "document_not_found")


def test_listing_shows_each_documents_latest_revision_without_content(client):
    made = [client.post("/v1/documents", json={"filename": document["filename"], "content": document["content"]}).json() for document in POLICY_DOCUMENTS[:3]]
    client.post(f"/v1/documents/{made[0]['document_id']}/revisions", json={"content": made[0]["content"] + "\n"})

    page = client.get("/v1/documents", params={"limit": 2}).json()
    rest = client.get("/v1/documents", params={"limit": 2, "offset": 2}).json()
    assert page["total"] == 3 and len(page["items"]) == 2 and len(rest["items"]) == 1
    listed = {item["document_id"]: item for item in page["items"] + rest["items"]}
    assert [item["document_id"] for item in page["items"] + rest["items"]] == [item["document_id"] for item in reversed(made)]
    assert listed[made[0]["document_id"]]["revision"] == 2 and all("content" not in item for item in listed.values())
    assert client.get("/v1/documents", params={"filename": POLICY_DOCUMENTS[1]["filename"]}).json()["total"] == 1


def test_an_unknown_document_is_not_found_and_takes_no_revision(client):
    created = _create(client).json()
    path = "/v1/documents/doc_000000000000000000000000"
    assert _code(client.get(path)) == (404, "document_not_found")
    assert _code(client.post(f"{path}/revisions", json={"content": "# Mine now\n"})) == (404, "document_not_found")
    assert client.get("/v1/documents").json()["total"] == 1
    assert client.get(f"/v1/documents/{created['document_id']}").json() == created


def test_a_stored_revision_that_no_longer_matches_its_hash_is_refused(make_client):
    mongo = mongomock.MongoClient(tz_aware=True)
    client = make_client(mongo=mongo)
    created = _create(client).json()
    mongo["expense_api"]["documents"].update_one({"document_id": created["document_id"]}, {"$set": {"content": "edited behind the API"}})
    assert _code(client.get(f"/v1/documents/{created['document_id']}")) == (500, "integrity_error")
