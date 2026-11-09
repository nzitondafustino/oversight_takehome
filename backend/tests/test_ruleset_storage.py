"""Compiled ruleset versions keep their policy documents in S3 (mocked), by reference, and rebuild exactly from them."""

import hashlib
import json

import mongomock
import pytest
from botocore.client import BaseClient
from botocore.exceptions import ClientError
from conftest import POLICY_DOCUMENTS, TEST_BUCKET, compile_version, extract, published_version, review_extraction, step, upload_documents
from pymongo.errors import AutoReconnect

from expense_api import migrate_rulesets
from expense_api.errors import ApiError
from expense_api.repositories import DocumentRepository, RulesetRepository
from expense_api.services.compilation import Compilation
from expense_api.services.compilation_documents import CompilationDocuments
from expense_api.services.document_store import DocumentStore

TEXTS = {document["filename"]: document["content"] for document in POLICY_DOCUMENTS}


@pytest.fixture
def mongo():
    return mongomock.MongoClient(tz_aware=True)


@pytest.fixture
def app(make_client, mongo):
    return make_client(mongo=mongo)


def stored(mongo, ruleset_id: str) -> dict:
    return mongo["expense_api"]["rulesets"].find_one({"_id": ruleset_id})


def objects(s3, prefix: str = "") -> dict[str, bytes]:
    listed = s3.list_objects_v2(Bucket=TEST_BUCKET, Prefix=prefix).get("Contents", [])
    return {item["Key"]: s3.get_object(Bucket=TEST_BUCKET, Key=item["Key"])["Body"].read() for item in listed}


def failing(monkeypatch, operation: str, error: Exception, after: int = 0) -> None:
    """Make an S3 operation fail, after `after` successful calls of it."""
    original, calls = BaseClient._make_api_call, []

    def call(self, name, params):
        if name == operation:
            calls.append(name)
            if len(calls) > after:
                raise error
        return original(self, name, params)

    monkeypatch.setattr(BaseClient, "_make_api_call", call)


def no_text_anywhere(mongo) -> bool:
    """No policy document's text in any collection, at any depth, under any field name."""
    for name in mongo["expense_api"].list_collection_names():
        dumped = json.dumps(list(mongo["expense_api"][name].find()), default=str)
        if any(json.dumps(text)[1:-1][:200] in dumped for text in TEXTS.values()):
            return False
    return True


def rebuild(settings, record: dict):
    return Compilation(settings, CompilationDocuments(lambda: DocumentStore(settings))).rebuild(record)


def from_extraction(app, refs, name: str = "from-documents"):
    extraction = extract(app, refs).json()
    review = review_extraction(app, extraction).json()
    return app.post("/v1/rulesets", json={"name": name, "extraction": {"extraction_id": extraction["extraction_id"], "review_id": review["review_id"]}})


# --- New versions ---


def test_an_inline_compilation_keeps_each_document_in_s3_and_only_its_reference_in_mongodb(app, mongo, s3):
    created = compile_version(app)
    assert created.status_code == 201, created.text
    ruleset_id = created.json()["ruleset_id"]
    entries = stored(mongo, ruleset_id)["compilation"]["documents"]
    assert len(entries) == len(POLICY_DOCUMENTS) and all("content" not in entry for entry in entries)
    for entry in entries:
        text = TEXTS[entry["filename"]].encode("utf-8")
        assert entry["sha256"] == hashlib.sha256(text).hexdigest() and entry["size_bytes"] == len(text)
        assert entry["storage"] == {"provider": "s3", "bucket": TEST_BUCKET, "region": "us-east-1", "key": f"ruleset-documents/{ruleset_id}/documents/{entry['sha256']}.md", "version_id": None}
        assert objects(s3)[entry["storage"]["key"]] == text
    assert no_text_anywhere(mongo)
    # The API shows each document as before: no text, and no storage reference.
    shown = app.get(f"/v1/rulesets/{ruleset_id}").json()["compilation"]["documents"]
    assert [set(document) for document in shown] == [{"document_id", "filename", "sha256", "revision"}] * len(entries)


def test_a_version_from_uploaded_revisions_references_their_objects_and_copies_nothing(app, mongo, s3):
    refs = upload_documents(app)
    version = from_extraction(app, refs)
    assert version.status_code == 201, version.text
    entries = stored(mongo, version.json()["ruleset_id"])["compilation"]["documents"]
    revisions = {record["document_id"]: record["storage"] for record in mongo["expense_api"]["documents"].find()}
    assert {entry["document_id"]: entry["storage"] for entry in entries} == revisions
    assert [entry["revision"] for entry in entries] == [1] * len(refs)
    assert objects(s3, "ruleset-documents/") == {}
    # A second version from the same revisions shares the same objects.
    again = from_extraction(app, refs, name="another")
    assert {entry["storage"]["key"] for entry in stored(mongo, again.json()["ruleset_id"])["compilation"]["documents"]} == {storage["key"] for storage in revisions.values()}
    assert len(objects(s3)) == len(refs)
    assert no_text_anywhere(mongo)


def test_the_lifecycle_rebuilds_from_s3_and_publishes_the_same_ruleset(app, mongo, s3, settings):
    published = published_version(app)  # review, approval and publication each rebuild the version from its documents
    assert published["status"] == "published"
    record = stored(mongo, published["ruleset_id"])
    rebuilt = rebuild(settings, record)
    assert rebuilt.extraction.extraction_sha256 == record["compilation"]["extraction_sha256"]
    assert rebuilt.extraction.candidate.ruleset_sha256 == record["artifact_sha256"]
    assert [document["content"] for document in rebuilt.documents] == [TEXTS[entry["filename"]] for entry in record["compilation"]["documents"]]
    assert no_text_anywhere(mongo)


def test_a_changed_or_missing_object_is_refused_never_compiled(app, mongo, s3):
    draft = compile_version(app).json()
    entry = stored(mongo, draft["ruleset_id"])["compilation"]["documents"][0]
    s3.put_object(Bucket=TEST_BUCKET, Key=entry["storage"]["key"] + ".x", Body=b"unrelated")
    s3.delete_object(Bucket=TEST_BUCKET, Key=entry["storage"]["key"])
    assert step(app, draft, "submit").status_code == 200
    response = step(app, draft, "accept")
    assert (response.status_code, response.json()["error"]["code"]) == (500, "document_content_missing")
    s3.put_object(Bucket=TEST_BUCKET, Key=entry["storage"]["key"], Body=b"# edited behind the API")
    response = step(app, draft, "accept")
    assert (response.status_code, response.json()["error"]["code"]) == (500, "integrity_error")


# --- Partial failures ---


def test_a_failed_s3_write_stores_no_version_and_removes_what_it_wrote(app, mongo, s3, monkeypatch):
    failing(monkeypatch, "PutObject", ClientError({"Error": {"Code": "AccessDenied", "Message": "denied"}}, "PutObject"), after=3)
    response = compile_version(app)
    assert (response.status_code, response.json()["error"]["code"]) == (503, "document_storage_unavailable")
    assert mongo["expense_api"]["rulesets"].count_documents({}) == 0 and objects(s3) == {}


def test_a_failed_mongodb_write_removes_the_objects_it_wrote(app, mongo, s3, monkeypatch):
    def fail(self, document, based_on_version=None):
        raise AutoReconnect("primary stepped down")

    monkeypatch.setattr(RulesetRepository, "create", fail)
    assert compile_version(app).status_code == 503
    assert objects(s3) == {}


def test_a_lost_version_number_removes_its_objects_but_never_a_revisions(app, mongo, s3, monkeypatch):
    refs = upload_documents(app)
    revision_objects = objects(s3)

    def taken(self, document, based_on_version=None):
        raise ApiError(409, "version_conflict", "taken")

    monkeypatch.setattr(RulesetRepository, "create", taken)
    assert compile_version(app).status_code == 409
    assert from_extraction(app, refs).status_code == 409
    assert objects(s3) == revision_objects  # the shared revision objects stay; the version's own are gone


def test_a_version_reported_as_failed_but_stored_is_the_requests_own(app, mongo, s3, monkeypatch):
    original = RulesetRepository.create

    def stored_then_failed(self, document, based_on_version=None):
        original(self, document, based_on_version)
        raise AutoReconnect("the reply was lost")

    monkeypatch.setattr(RulesetRepository, "create", stored_then_failed)
    response = compile_version(app)
    assert response.status_code == 201, response.text
    keys = {entry["storage"]["key"] for entry in stored(mongo, response.json()["ruleset_id"])["compilation"]["documents"]}
    assert keys == set(objects(s3))


def test_concurrent_compilations_each_keep_their_own_objects(app, mongo, s3):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(3) as pool:
        responses = list(pool.map(lambda index: compile_version(app, name="same"), range(3)))
    assert sorted(response.status_code for response in responses) == [201] * 3
    ids = [response.json()["ruleset_id"] for response in responses]
    assert sorted(stored(mongo, ruleset_id)["version"] for ruleset_id in ids) == [1, 2, 3]
    assert len(objects(s3)) == 3 * len(POLICY_DOCUMENTS)


# --- Versions stored before, and their migration ---


def as_stored_before(mongo, s3, ruleset_id: str) -> dict:
    """Turn a version back into one stored before S3: each entry with its text, and no object."""
    record = stored(mongo, ruleset_id)
    entries = []
    for entry in record["compilation"]["documents"]:
        if entry["storage"]["key"].startswith("ruleset-documents/"):
            s3.delete_object(Bucket=TEST_BUCKET, Key=entry["storage"]["key"])
        entries.append({name: entry[name] for name in ("document_id", "filename", "sha256", "revision") if name in entry} | {"content": TEXTS[entry["filename"]]})
    mongo["expense_api"]["rulesets"].update_one({"_id": ruleset_id}, {"$set": {"compilation.documents": entries}})
    return stored(mongo, ruleset_id)


def run(mongo, settings, apply: bool):
    database = mongo["expense_api"]
    lines = []
    store = DocumentStore(settings) if apply else None
    counts = migrate_rulesets.migrate(RulesetRepository(database["rulesets"]), DocumentRepository(database["documents"]), store, apply, lines.append)
    return counts, [json.loads(line) for line in lines]


@pytest.fixture
def legacy(app, mongo, s3):
    """A published version and a draft, both inline compilations, stored as before S3."""
    published = published_version(app, name="published")
    draft = compile_version(app, name="draft").json()
    return as_stored_before(mongo, s3, published["ruleset_id"]), as_stored_before(mongo, s3, draft["ruleset_id"])


def test_a_version_stored_before_rebuilds_and_moves_through_its_lifecycle_as_before(app, mongo, s3, settings, legacy):
    published, draft = legacy
    assert objects(s3, "ruleset-documents/") == {}
    assert rebuild(settings, published).extraction.candidate.ruleset_sha256 == published["artifact_sha256"]
    for name in ("submit", "accept", "approve"):
        assert step(app, {"ruleset_id": draft["_id"]}, name).status_code == 200


def test_a_dry_run_changes_nothing(mongo, settings, s3, legacy, monkeypatch):
    failing(monkeypatch, "PutObject", AssertionError("a dry run writes nothing"))
    before = list(mongo["expense_api"]["rulesets"].find())
    counts, lines = run(mongo, settings, apply=False)
    assert counts == {"would_move": 2}
    assert [line["status"] for line in lines] == ["published", "draft"]
    assert list(mongo["expense_api"]["rulesets"].find()) == before
    assert all(text[:40] not in json.dumps(lines) for text in TEXTS.values())  # names, never text


def test_the_migration_moves_text_to_verified_references_and_changes_nothing_else(app, mongo, s3, settings, legacy):
    published, draft = legacy
    assert not no_text_anywhere(mongo)  # the check finds text where there is some
    counts, _ = run(mongo, settings, apply=True)
    assert counts == {"moved": 2}
    for before in (published, draft):
        after = stored(mongo, before["_id"])
        assert {name: value for name, value in after.items() if name != "compilation"} == {name: value for name, value in before.items() if name != "compilation"}
        assert {name: value for name, value in after["compilation"].items() if name != "documents"} == {name: value for name, value in before["compilation"].items() if name != "documents"}
        for old, new in zip(before["compilation"]["documents"], after["compilation"]["documents"], strict=True):
            assert "content" not in new and {name: new[name] for name in ("document_id", "filename", "sha256")} == {name: old[name] for name in ("document_id", "filename", "sha256")}
            assert objects(s3)[new["storage"]["key"]] == old["content"].encode()
    assert no_text_anywhere(mongo)
    # The published version still reproduces, from S3, exactly; its approval and identity are untouched.
    moved = stored(mongo, published["_id"])
    assert (moved["status"], moved["approval_reference"], moved["artifact_sha256"]) == (published["status"], published["approval_reference"], published["artifact_sha256"])
    assert rebuild(settings, moved).extraction.candidate.ruleset_sha256 == published["artifact_sha256"]
    for name in ("submit", "accept", "approve"):
        assert step(app, {"ruleset_id": draft["_id"]}, name).status_code == 200
    # Again: nothing left, nothing written twice.
    assert run(mongo, settings, apply=True)[0] == {}
    assert len(objects(s3, "ruleset-documents/")) == 2 * len(POLICY_DOCUMENTS)


def test_an_interrupted_migration_resumes_and_reuses_what_it_wrote(mongo, settings, s3, legacy, monkeypatch):
    original = RulesetRepository.move_compilation_documents

    def interrupted(self, *args):
        raise KeyboardInterrupt  # stopped after writing every object of the first version, before recording them

    monkeypatch.setattr(RulesetRepository, "move_compilation_documents", interrupted)
    with pytest.raises(KeyboardInterrupt):
        run(mongo, settings, apply=True)
    assert len(objects(s3, "ruleset-documents/")) == len(POLICY_DOCUMENTS)
    assert mongo["expense_api"]["rulesets"].count_documents({"compilation.documents.content": {"$exists": True}}) == 2  # no text removed unverified
    monkeypatch.setattr(RulesetRepository, "move_compilation_documents", original)
    assert run(mongo, settings, apply=True)[0] == {"moved": 2}
    assert len(objects(s3, "ruleset-documents/")) == 2 * len(POLICY_DOCUMENTS)


def test_an_inconsistent_snapshot_is_reported_and_left_exactly_as_it_is(mongo, settings, s3, legacy):
    published, _ = legacy
    entries = published["compilation"]["documents"]
    entries[0] = entries[0] | {"content": entries[0]["content"] + "edited"}
    mongo["expense_api"]["rulesets"].update_one({"_id": published["_id"]}, {"$set": {"compilation.documents": entries}})
    before = stored(mongo, published["_id"])
    counts, lines = run(mongo, settings, apply=True)
    assert counts == {"inconsistent_snapshot": 1, "moved": 1}
    assert lines[0]["documents"] == [entries[0]["filename"]] and stored(mongo, published["_id"]) == before


def test_a_failed_s3_write_during_migration_changes_nothing(mongo, settings, s3, legacy, monkeypatch):
    failing(monkeypatch, "PutObject", ClientError({"Error": {"Code": "RequestTimeout", "Message": "slow"}}, "PutObject"), after=2)
    counts, _ = run(mongo, settings, apply=True)
    assert counts == {"unverified:document_storage_unavailable": 2}
    assert mongo["expense_api"]["rulesets"].count_documents({"compilation.documents.content": {"$exists": True}}) == 2
    assert objects(s3, "ruleset-documents/") == {}  # what it wrote before failing was removed: nothing references it


def test_a_version_from_uploaded_revisions_migrates_onto_their_objects(app, mongo, settings, s3):
    version = from_extraction(app, upload_documents(app)).json()
    before = as_stored_before(mongo, s3, version["ruleset_id"])
    counts, lines = run(mongo, settings, apply=True)
    assert counts == {"moved": 1} and lines[0]["reusing_revision_objects"] == len(POLICY_DOCUMENTS)
    revisions = {record["document_id"]: record["storage"] for record in mongo["expense_api"]["documents"].find()}
    assert {entry["document_id"]: entry["storage"] for entry in stored(mongo, before["_id"])["compilation"]["documents"]} == revisions
    assert objects(s3, "ruleset-documents/") == {}


def test_unreferenced_ruleset_objects_are_reported_and_never_deleted(app, mongo, settings, s3):
    compile_version(app)
    s3.put_object(Bucket=TEST_BUCKET, Key="ruleset-documents/rs_left/documents/abc.md", Body=b"# left")
    lines = []
    assert migrate_rulesets.orphans(RulesetRepository(mongo["expense_api"]["rulesets"]), DocumentStore(settings), lines.append) == 1
    assert lines == [json.dumps({"unreferenced_key": "ruleset-documents/rs_left/documents/abc.md"})]
    assert "ruleset-documents/rs_left/documents/abc.md" in objects(s3)
