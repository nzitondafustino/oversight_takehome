"""Policy document contents in S3 (mocked), MongoDB holding only metadata and references, and the legacy migration."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import mongomock
import pytest
from botocore.client import BaseClient
from botocore.exceptions import ClientError, ConnectTimeoutError
from conftest import POLICY_DOCUMENTS, TEST_BUCKET, extract, upload_documents
from pymongo.errors import AutoReconnect

from expense_api import migrate_documents
from expense_api.repositories import DocumentRepository
from expense_api.services.document_store import CONTENT_TYPE, DocumentStore

MEALS = next(document for document in POLICY_DOCUMENTS if document["filename"] == "meals-and-entertainment-policy.md")
CHANGED = MEALS["content"] + "\nA new paragraph.\n"


@pytest.fixture
def mongo():
    return mongomock.MongoClient(tz_aware=True)


@pytest.fixture
def app(make_client, mongo):
    return make_client(mongo=mongo)


def records(mongo) -> list[dict]:
    return list(mongo["expense_api"]["documents"].find())


def objects(s3) -> dict[str, bytes]:
    listed = s3.list_objects_v2(Bucket=TEST_BUCKET).get("Contents", [])
    return {item["Key"]: s3.get_object(Bucket=TEST_BUCKET, Key=item["Key"])["Body"].read() for item in listed}


def create(app, content=MEALS["content"], **fields):
    return app.post("/v1/documents", json={"filename": MEALS["filename"], "content": content, "title": "Meals", **fields})


def failing(monkeypatch, operation: str, error: Exception) -> None:
    """Make every S3 client's operation fail with this error, as S3 or the network would."""
    original = BaseClient._make_api_call

    def call(self, name, params):
        if name == operation:
            raise error
        return original(self, name, params)

    monkeypatch.setattr(BaseClient, "_make_api_call", call)


def code(response):
    return response.status_code, response.json()["error"]["code"]


# --- Upload ---


def test_an_upload_puts_the_text_in_one_private_encrypted_object_and_only_metadata_in_mongodb(app, mongo, s3):
    created = create(app)
    assert created.status_code == 201, created.text
    document_id = created.json()["document_id"]
    key = f"policy-documents/{document_id}/revisions/1/document.md"
    head = s3.head_object(Bucket=TEST_BUCKET, Key=key)
    assert (head["ContentType"], head["ServerSideEncryption"], head["Metadata"]) == (CONTENT_TYPE, "AES256", {"content-sha256": created.json()["content_sha256"]})
    assert objects(s3) == {key: MEALS["content"].encode("utf-8")}
    [record] = records(mongo)
    assert "content" not in record
    assert record["storage"] == {"provider": "s3", "bucket": TEST_BUCKET, "region": "us-east-1", "key": key, "version_id": None}
    assert {name: record[name] for name in ("_id", "document_id", "revision", "based_on_revision", "filename", "title", "size_bytes")} == {
        "_id": f"{document_id}:1", "document_id": document_id, "revision": 1, "based_on_revision": None, "filename": MEALS["filename"], "title": "Meals", "size_bytes": len(MEALS["content"].encode())}
    assert record["content_sha256"] == hashlib.sha256(MEALS["content"].encode()).hexdigest()
    # The API's response is as before: the text, and no storage reference.
    assert created.json()["content"] == MEALS["content"] and "storage" not in created.json()
    # The bucket is never made public by an upload.
    assert all(grant["Grantee"].get("URI") is None for grant in s3.get_object_acl(Bucket=TEST_BUCKET, Key=key)["Grants"])


def test_a_versioned_bucket_records_the_version_s3_returned(app, mongo, s3):
    s3.put_bucket_versioning(Bucket=TEST_BUCKET, VersioningConfiguration={"Status": "Enabled"})
    created = create(app).json()
    [record] = records(mongo)
    assert record["storage"]["version_id"] == s3.head_object(Bucket=TEST_BUCKET, Key=record["storage"]["key"])["VersionId"]
    assert app.get(f"/v1/documents/{created['document_id']}").json()["content"] == MEALS["content"]


@pytest.mark.parametrize(("body", "expected"), [
    ({"filename": "travel-policy.pdf", "content": "# Travel"}, (422, "invalid_request")),
    ({"filename": "travel-policy.docx", "content": "# Travel"}, (422, "invalid_request")),
    ({"filename": "../travel-policy.md", "content": "# Travel"}, (422, "invalid_request")),
    ({"filename": "meals.md", "content": "x" * (512 * 1024 + 1)}, (413, "document_too_large")),
])
def test_a_refused_upload_writes_nothing_anywhere(app, mongo, s3, body, expected):
    assert code(app.post("/v1/documents", json=body)) == expected
    assert records(mongo) == [] and objects(s3) == {}


def test_invalid_utf8_writes_nothing_anywhere(app, mongo, s3):
    lone = app.post("/v1/documents", content=b'{"filename": "meals.md", "content": "# Meals \\ud800"}', headers={"content-type": "application/json"})
    latin = app.post("/v1/documents", content='{"filename": "meals.md", "content": "Caf\xe9"}'.encode("latin-1"), headers={"content-type": "application/json"})
    assert {code(lone)[0], code(latin)[0]} == {422}
    assert records(mongo) == [] and objects(s3) == {}


@pytest.mark.parametrize(("error", "reason"), [
    (ClientError({"Error": {"Code": "AccessDenied", "Message": "denied"}}, "PutObject"), "S3 denied access"),
    (ConnectTimeoutError(endpoint_url="https://s3.amazonaws.com"), "S3 could not be reached in time"),
    (ClientError({"Error": {"Code": "NoSuchBucket", "Message": "gone"}}, "PutObject"), "S3 refused (NoSuchBucket)"),
])
def test_a_failed_s3_write_creates_no_record(app, mongo, s3, monkeypatch, error, reason):
    failing(monkeypatch, "PutObject", error)
    response = create(app)
    assert code(response) == (503, "document_storage_unavailable") and reason in response.json()["error"]["message"]
    assert "testing" not in response.text  # no credential in an error
    assert records(mongo) == []


def test_an_unconfigured_deployment_refuses_uploads_but_reads_records_not_yet_migrated(make_client, settings, mongo):
    app = make_client(app_settings=settings.model_copy(update={"s3_bucket": None}), mongo=mongo)
    assert code(create(app)) == (503, "document_storage_unavailable")
    legacy = _legacy(mongo, "doc_" + "1" * 24, 1, MEALS["content"])
    assert app.get(f"/v1/documents/{legacy['document_id']}").json()["content"] == MEALS["content"]


def test_a_failed_mongodb_write_removes_the_object_it_left(app, mongo, s3, monkeypatch):
    def fail(self, revision):
        raise AutoReconnect("primary stepped down")

    monkeypatch.setattr(DocumentRepository, "insert", fail)
    assert create(app).status_code == 503
    assert records(mongo) == [] and objects(s3) == {}


def test_an_object_is_kept_when_mongodb_cannot_say_whether_a_record_references_it(app, mongo, s3, monkeypatch):
    def fail(self, *args, **kwargs):
        raise AutoReconnect("no primary")

    original = DocumentRepository.metadata
    calls = []

    def metadata(self, document_id, revision=None):
        calls.append(revision)
        return original(self, document_id, revision) if len(calls) == 1 else fail(self)

    monkeypatch.setattr(DocumentRepository, "insert", fail)
    monkeypatch.setattr(DocumentRepository, "metadata", metadata)
    assert create(app).status_code == 503
    assert len(objects(s3)) == 1  # never deleted while it may be referenced; --orphans reports it


def test_a_write_reported_as_failed_but_made_is_the_requests_own_success(app, mongo, s3, monkeypatch):
    original = DocumentRepository.insert

    def made_then_failed(self, revision):
        original(self, revision)
        raise AutoReconnect("the reply was lost")

    monkeypatch.setattr(DocumentRepository, "insert", made_then_failed)
    response = create(app)
    assert response.status_code == 201, response.text
    [record] = records(mongo)
    assert objects(s3) == {record["storage"]["key"]: MEALS["content"].encode()}


# --- Retrieval ---


def test_a_revision_is_read_from_its_object_and_checked(app, mongo, s3):
    created = create(app).json()
    assert app.get(f"/v1/documents/{created['document_id']}?revision=1").json() == created


def test_a_missing_object_is_a_clear_error(app, mongo, s3):
    created = create(app).json()
    s3.delete_object(Bucket=TEST_BUCKET, Key=records(mongo)[0]["storage"]["key"])
    assert code(app.get(f"/v1/documents/{created['document_id']}")) == (500, "document_content_missing")


def test_unreadable_storage_is_a_503(app, mongo, s3, monkeypatch):
    created = create(app).json()

    failing(monkeypatch, "GetObject", ClientError({"Error": {"Code": "AccessDenied", "Message": "denied"}}, "GetObject"))
    response = app.get(f"/v1/documents/{created['document_id']}")
    assert code(response) == (503, "document_storage_unavailable") and "S3 denied access" in response.json()["error"]["message"]


def test_the_document_list_reads_no_object_and_shows_no_reference(app, mongo, s3, monkeypatch):
    create(app)
    failing(monkeypatch, "GetObject", AssertionError("a list reads no content"))
    [item] = app.get("/v1/documents").json()["items"]
    assert "storage" not in item and "content" not in item


# --- Revisions ---


def test_each_revision_is_its_own_object_and_earlier_ones_never_change(app, mongo, s3):
    document_id = create(app).json()["document_id"]
    second = app.post(f"/v1/documents/{document_id}/revisions", json={"content": CHANGED, "based_on_revision": 1})
    assert second.status_code == 201, second.text
    prefix = f"policy-documents/{document_id}/revisions"
    assert objects(s3) == {f"{prefix}/1/document.md": MEALS["content"].encode(), f"{prefix}/2/document.md": CHANGED.encode()}
    assert app.get(f"/v1/documents/{document_id}?revision=1").json()["content"] == MEALS["content"]
    assert app.get(f"/v1/documents/{document_id}").json()["content"] == CHANGED
    assert all("content" not in record for record in records(mongo))


def test_unchanged_content_is_refused_without_writing(app, mongo, s3):
    document_id = create(app).json()["document_id"]
    assert code(app.post(f"/v1/documents/{document_id}/revisions", json={"content": MEALS["content"]})) == (409, "revision_unchanged")
    assert len(objects(s3)) == 1 and len(records(mongo)) == 1


def test_an_object_another_upload_holds_is_never_overwritten(app, mongo, s3):
    document_id = create(app).json()["document_id"]
    key = f"policy-documents/{document_id}/revisions/2/document.md"
    s3.put_object(Bucket=TEST_BUCKET, Key=key, Body=b"another upload, on its way to its record")
    assert code(app.post(f"/v1/documents/{document_id}/revisions", json={"content": CHANGED, "based_on_revision": 1})) == (409, "revision_conflict")
    assert objects(s3)[key] == b"another upload, on its way to its record" and len(records(mongo)) == 1


def test_an_object_an_earlier_attempt_left_with_this_text_is_reused(app, mongo, s3):
    document_id = create(app).json()["document_id"]
    key = f"policy-documents/{document_id}/revisions/2/document.md"
    s3.put_object(Bucket=TEST_BUCKET, Key=key, Body=CHANGED.encode())
    assert app.post(f"/v1/documents/{document_id}/revisions", json={"content": CHANGED, "based_on_revision": 1}).status_code == 201
    assert records(mongo)[-1]["storage"]["key"] == key


def test_concurrent_revisions_of_one_revision_store_exactly_one(app, mongo, s3):
    document_id = create(app).json()["document_id"]
    contents = [f"{MEALS['content']}\nEdit {index}.\n" for index in range(6)]
    with ThreadPoolExecutor(6) as pool:
        responses = list(pool.map(lambda content: app.post(f"/v1/documents/{document_id}/revisions", json={"content": content, "based_on_revision": 1}), contents))
    assert sorted(response.status_code for response in responses) == [201] + [409] * 5
    winner = next(response.json() for response in responses if response.status_code == 201)
    stored = objects(s3)
    assert len(stored) == 2 and stored[f"policy-documents/{document_id}/revisions/2/document.md"] == winner["content"].encode()
    assert [record["revision"] for record in records(mongo)] == [1, 2]


# --- Extraction ---


def test_extraction_reads_each_selected_revision_from_s3(app, mongo, s3):
    refs = upload_documents(app)
    extraction = extract(app, refs)
    assert extraction.status_code == 201, extraction.text
    hashes = {record["document_id"]: record["content_sha256"] for record in records(mongo)}
    assert {item["document_id"]: item["content_sha256"] for item in extraction.json()["documents"]} == hashes
    # The compiler reads the object: one changed behind the API is refused, never compiled.
    s3.put_object(Bucket=TEST_BUCKET, Key=records(mongo)[0]["storage"]["key"], Body=b"# edited")
    assert code(extract(app, refs)) == (500, "integrity_error")


# --- Legacy records and their migration ---


def _legacy(mongo, document_id: str, revision: int, content: str, sha256: str | None = None) -> dict:
    """A record as written before S3 storage: its text held in MongoDB."""
    record = {"_id": f"{document_id}:{revision}", "document_id": document_id, "revision": revision, "based_on_revision": revision - 1 or None, "filename": MEALS["filename"], "title": None,
              "content": content, "content_sha256": sha256 or hashlib.sha256(content.encode()).hexdigest(), "size_bytes": len(content.encode()), "source": {"kind": "upload", "description": None, "reference": None},
              "created_at": datetime(2026, 11, 1, 9, 30, tzinfo=UTC)}
    mongo["expense_api"]["documents"].insert_one(record)
    return record


@pytest.fixture
def legacy(mongo):
    ids = ["doc_" + str(index) * 24 for index in range(1, 4)]
    made = [_legacy(mongo, ids[0], 1, MEALS["content"]), _legacy(mongo, ids[0], 2, CHANGED), _legacy(mongo, ids[1], 1, "# Travel\n\nTravel rules.\n")]
    broken = _legacy(mongo, ids[2], 1, "# Gifts\n", sha256="0" * 64)  # its text no longer matches its hash
    return made, broken


def run(mongo, settings, apply: bool):
    database = mongo["expense_api"]
    lines = []
    counts = migrate_documents.migrate(DocumentRepository(database["documents"], database["document_orphans"]), DocumentStore(settings), apply, lines.append)
    return counts, [json.loads(line) for line in lines]


def test_records_not_yet_migrated_read_and_extract_as_before(app, mongo, legacy):
    made, _ = legacy
    assert app.get(f"/v1/documents/{made[0]['document_id']}?revision=1").json()["content"] == MEALS["content"]


def test_a_dry_run_changes_nothing_and_needs_no_s3(mongo, settings, s3, legacy, monkeypatch):
    failing(monkeypatch, "PutObject", AssertionError("a dry run writes nothing"))
    database = mongo["expense_api"]
    lines_without_store = []
    assert migrate_documents.migrate(DocumentRepository(database["documents"]), None, False, lines_without_store.append) == {"would_move": 3, "hash_mismatch": 1}
    counts, lines = run(mongo, settings, apply=False)
    assert [json.loads(line) for line in lines_without_store] == lines
    assert counts == {"would_move": 3, "hash_mismatch": 1}
    assert objects(s3) == {} and sum("content" in record for record in records(mongo)) == 4
    assert all("content" not in line and "#" not in json.dumps(line) for line in lines)  # names keys, never text


def test_the_migration_moves_each_verified_revision_and_keeps_everything_else(app, mongo, settings, s3, legacy):
    made, broken = legacy
    before = {record["_id"]: record for record in records(mongo)}
    counts, _ = run(mongo, settings, apply=True)
    assert counts == {"moved": 3, "hash_mismatch": 1}
    after = {record["_id"]: record for record in records(mongo)}
    for original in made:
        moved = after[original["_id"]]
        assert "content" not in moved and moved["storage"]["key"] == f"policy-documents/{original['document_id']}/revisions/{original['revision']}/document.md"
        assert {name: value for name, value in moved.items() if name != "storage"} == {name: value for name, value in before[original["_id"]].items() if name != "content"}
        assert objects(s3)[moved["storage"]["key"]] == original["content"].encode()
        assert app.get(f"/v1/documents/{original['document_id']}?revision={original['revision']}").json()["content"] == original["content"]
    assert after[broken["_id"]] == before[broken["_id"]]  # left as it was, text and all, for a person to look at
    # Run again: nothing left to move, nothing written twice.
    assert run(mongo, settings, apply=True)[0] == {"hash_mismatch": 1}
    assert len(objects(s3)) == 3


def test_an_interrupted_migration_resumes_and_reuses_what_it_wrote(mongo, settings, s3, legacy, monkeypatch):
    original = DocumentRepository.migrate_to_storage
    calls = []

    def interrupted(self, *args):
        calls.append(args)
        if len(calls) == 2:
            raise KeyboardInterrupt  # stopped after writing the second object, before recording it
        return original(self, *args)

    monkeypatch.setattr(DocumentRepository, "migrate_to_storage", interrupted)
    with pytest.raises(KeyboardInterrupt):
        run(mongo, settings, apply=True)
    assert len(objects(s3)) == 2 and sum("content" in record for record in records(mongo)) == 3  # nothing removed unverified
    monkeypatch.setattr(DocumentRepository, "migrate_to_storage", original)
    counts, _ = run(mongo, settings, apply=True)
    assert counts == {"moved": 2, "hash_mismatch": 1}
    assert len(objects(s3)) == 3 and sum("content" in record for record in records(mongo)) == 1


def test_an_object_holding_other_text_is_never_taken_for_a_revision(mongo, settings, s3, legacy):
    made, _ = legacy
    s3.put_object(Bucket=TEST_BUCKET, Key=f"policy-documents/{made[2]['document_id']}/revisions/1/document.md", Body=b"something else")
    counts, _ = run(mongo, settings, apply=True)
    assert counts == {"moved": 2, "object_conflict": 1, "hash_mismatch": 1}
    assert "content" in mongo["expense_api"]["documents"].find_one({"_id": made[2]["_id"]})


def test_orphans_are_reported_and_never_deleted(app, mongo, settings, s3):
    create(app)
    s3.put_object(Bucket=TEST_BUCKET, Key="policy-documents/doc_" + "9" * 24 + "/revisions/1/document.md", Body=b"# left behind")
    database = mongo["expense_api"]
    lines = []
    assert migrate_documents.orphans(DocumentRepository(database["documents"], database["document_orphans"]), DocumentStore(settings), lines.append) == 1
    assert lines == [json.dumps({"unreferenced_key": "policy-documents/doc_" + "9" * 24 + "/revisions/1/document.md"})]
    assert len(objects(s3)) == 2
