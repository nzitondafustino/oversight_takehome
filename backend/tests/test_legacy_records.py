"""Records stored before the single-company migration: readable for audit, never rewritten, and never a way past
the compiler's safeguards.

tests/fixtures/legacy/pre_migration.json.gz holds real records of the replay demo, exported from MongoDB as stored
before customer_id was removed: the nine policy documents, an extraction with its review, and the version made from
them, taken through review, approval and publication under the scope "ui-demo".
"""

import copy
import gzip
import hashlib
import json
from pathlib import Path

import mongomock
import pytest
from bson import json_util

from expense_api.services.legacy import OBSOLETE

FIXTURE = Path(__file__).parent / "fixtures" / "legacy" / "pre_migration.json.gz"
STORED = json_util.loads(gzip.decompress(FIXTURE.read_bytes()).decode("utf-8"))
EXTRACTION = STORED["extractions"][0]
VERSION = STORED["rulesets"][0]
EXTRACTION_ID, RULESET_ID = EXTRACTION["_id"], VERSION["_id"]


def _mentions_scope(value) -> bool:
    if isinstance(value, dict):
        return OBSOLETE in value or any(_mentions_scope(item) for item in value.values())
    if isinstance(value, list):
        return any(_mentions_scope(item) for item in value)
    return False


@pytest.fixture
def legacy(make_client):
    """A started API over a database holding the pre-migration records exactly as exported."""
    mongo = mongomock.MongoClient(tz_aware=True)
    client = make_client(mongo=mongo)
    database = mongo["expense_api"]
    for name, records in STORED.items():
        database[name].insert_many(copy.deepcopy(records))
    return client, database


def _stored(database) -> dict:
    return {name: list(database[name].find({}, sort=[("_id", 1)])) for name in STORED}


def _code(response):
    return response.status_code, response.json()["error"]["code"]


def test_the_fixture_is_pre_migration_and_its_hashes_hold():
    assert _mentions_scope(json.loads(EXTRACTION["result_json"])) and _mentions_scope(json.loads(VERSION["compilation"]["comparisons_json"]))
    assert _mentions_scope(VERSION["review"]["decisions"])
    assert hashlib.sha256(EXTRACTION["result_json"].encode()).hexdigest() == EXTRACTION["result_sha256"]
    assert hashlib.sha256(VERSION["artifact_json"].encode()).hexdigest() == VERSION["artifact_sha256"]


def test_a_pre_migration_extraction_reads_for_audit_without_its_scope(legacy):
    client, database = legacy
    before = _stored(database)
    response = client.get(f"/v1/documents/extractions/{EXTRACTION_ID}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert not _mentions_scope(body)
    stored = json.loads(EXTRACTION["result_json"])
    assert (body["extraction_sha256"], body["result_sha256"]) == (EXTRACTION["extraction_sha256"], EXTRACTION["result_sha256"])  # as stored, never recomputed
    assert [item["comparison_id"] for item in body["review_required"]] == [item["comparison_id"] for item in stored["comparisons"] if item["review_required"]]
    assert all(item["extraction_sha256"] == EXTRACTION["extraction_sha256"] for item in body["review_required"])
    assert [item["extraction_id"] for item in client.get("/v1/documents/extractions").json()["items"]] == [EXTRACTION_ID]
    assert _stored(database) == before  # nothing written back


def test_its_comparisons_page_through_every_stored_field(legacy):
    client, _ = legacy
    path = f"/v1/documents/extractions/{EXTRACTION_ID}/comparisons"
    stored = json.loads(EXTRACTION["result_json"])["comparisons"]
    pages = [client.get(path, params={"limit": 100, "offset": offset}).json() for offset in (0, 100, 200, 300)]
    assert [len(page["items"]) for page in pages] == [100, 100, len(stored) - 200, 0] and {page["total"] for page in pages} == {len(stored)}
    assert [item["comparison_id"] for page in pages for item in page["items"]] == [item["comparison_id"] for item in stored]
    assert not any(_mentions_scope(page) for page in pages)
    needing = client.get(path, params={"review_required": True}).json()
    assert needing["total"] == sum(item["review_required"] for item in stored) == 4
    # Every value is the stored one; only the obsolete field is left out.
    assert pages[0]["items"][0] == {name: value for name, value in stored[0].items() if name != OBSOLETE}


def test_a_version_compiled_before_the_migration_shows_its_comparisons_and_review_without_scope(legacy):
    client, database = legacy
    before = _stored(database)
    comparisons = client.get(f"/v1/rulesets/{RULESET_ID}/comparisons")
    assert comparisons.status_code == 200, comparisons.text
    stored = json.loads(VERSION["compilation"]["comparisons_json"])
    assert [item["comparison_id"] for item in comparisons.json()["comparisons"]] == [item["comparison_id"] for item in stored]
    assert len(client.get(f"/v1/rulesets/{RULESET_ID}/comparisons", params={"review_required": True}).json()["comparisons"]) == 4

    detail = client.get(f"/v1/rulesets/{RULESET_ID}").json()
    assert not _mentions_scope(detail) and not _mentions_scope(client.get("/v1/rulesets").json())
    assert detail["review"]["decisions"] == [
        {name: value for name, value in decision.items() if name != OBSOLETE} for decision in VERSION["review"]["decisions"]
    ]
    # Provenance, approval and audit events, exactly as stored.
    assert (detail["artifact_sha256"], detail["approval_reference"], detail["review"]["review_sha256"]) == (VERSION["artifact_sha256"], VERSION["approval_reference"], VERSION["review"]["review_sha256"])
    assert [(event["from_status"], event["to_status"], event["content_hash"], event["approval_reference"]) for event in detail["transitions"]] == [
        (event["from_status"], event["to_status"], event["content_hash"], event["approval_reference"]) for event in VERSION["transitions"]
    ]
    assert _stored(database) == before


def test_integrity_is_checked_before_any_compatibility_read(legacy):
    client, database = legacy
    tampered = EXTRACTION["result_json"].replace('"MCC-03"', '"MCC-09"', 1)
    database["extractions"].update_one({"_id": EXTRACTION_ID}, {"$set": {"result_json": tampered}})
    for path in (f"/v1/documents/extractions/{EXTRACTION_ID}", f"/v1/documents/extractions/{EXTRACTION_ID}/comparisons"):
        assert _code(client.get(path)) == (500, "integrity_error"), path
    database["rulesets"].update_one({"_id": RULESET_ID}, {"$set": {"artifact_json": VERSION["artifact_json"].replace("ALC-01", "ALC-09", 1)}})
    assert _code(client.get(f"/v1/rulesets/{RULESET_ID}")) == (500, "integrity_error")


def test_a_pre_migration_extraction_starts_no_new_review_or_version(legacy):
    client, database = legacy
    before = _stored(database)
    review = STORED["extraction_reviews"][0]
    decisions = [{name: value for name, value in decision.items() if name != OBSOLETE} for decision in review["decisions"]]
    response = client.post(f"/v1/documents/extractions/{EXTRACTION_ID}/review", json={"actor": "r.eviewer", "extraction_sha256": EXTRACTION["extraction_sha256"], "decisions": decisions})
    assert _code(response) == (409, "legacy_extraction")
    response = client.post("/v1/rulesets", json={"name": "from-legacy", "extraction": {"extraction_id": EXTRACTION_ID, "review_id": review["_id"]}})
    assert _code(response) == (409, "legacy_extraction")
    assert _stored(database) == before


@pytest.mark.parametrize("status, step, body", [("in_review", "approve", {"actor": "a.pprover"}), ("approved", "publish", {})])
def test_a_pre_migration_version_cannot_be_approved_or_published_again(legacy, status, step, body):
    """Its approval and review are readable, but the compiler no longer reproduces its scoped hashes."""
    client, database = legacy
    reset = {"status": status, "publication_mode": None, "published_at": None} | ({"approval_json": None, "approval_reference": None, "approval_mode": None, "approved_at": None} if status == "in_review" else {})
    database["rulesets"].update_one({"_id": RULESET_ID}, {"$set": reset})
    before = _stored(database)
    assert _code(client.post(f"/v1/rulesets/{RULESET_ID}/{step}", json=body)) == (409, "compilation_changed")
    assert _stored(database) == before
