"""Against a real MongoDB, when EXPENSE_API_TEST_MONGO_URI names one on this machine; skipped otherwise.

    bash test.sh -q  # starts and cleans up an isolated MongoDB on a random localhost port
"""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest
from conftest import create_ruleset, extract, published_version, review_extraction, upload_documents, validate
from evaluation_support import create, dataset
from pymongo import MongoClient

from expense_api.repositories import EvaluationRepository
from expense_api.services.rulesets import now
from expense_api.worker import Worker

URI = os.environ.get("EXPENSE_API_TEST_MONGO_URI")
pytestmark = pytest.mark.skipif(URI is None, reason="set EXPENSE_API_TEST_MONGO_URI to a disposable local MongoDB")


@pytest.fixture
def real(make_client, settings):
    name = f"expense_api_test_{uuid.uuid4().hex[:8]}"
    mongo = MongoClient(URI, serverSelectionTimeoutMS=2000, tz_aware=True)
    yield make_client(app_settings=settings.model_copy(update={"mongo_database": name}), mongo=mongo), mongo[name]
    mongo.drop_database(name)
    mongo.close()


def test_a_run_round_trips_through_mongodb(real):
    client, database = real
    assert client.get("/health").json()["database"] == "ok"
    ruleset = create_ruleset(client)
    run = validate(client, ruleset["ruleset_id"], receipt_ids=("RCP-013", "RCP-014")).json()

    assert client.get(f"/v1/runs/{run['run_id']}").json() == run
    assert client.get(f"/v1/rulesets/{ruleset['ruleset_id']}").json() == ruleset
    stored = database["runs"].find_one({"_id": run["run_id"]})
    assert isinstance(stored["created_at"], datetime) and stored["created_at"].tzinfo is not None
    assert database["rulesets"].index_information()["name_version"]["unique"] is True


def test_the_lifecycle_runs_on_mongodb(real):
    client, database = real
    published = published_version(client)
    run = validate(client, published["ruleset_id"], preview=False).json()
    assert run["ruleset"]["status"] == "published" and run["ruleset"]["publication_mode"] == "production"
    stored = database["rulesets"].find_one({"_id": published["ruleset_id"]})
    assert [event["to_status"] for event in stored["transitions"]] == ["draft", "in_review", "in_review", "approved", "published"]
    assert stored["approval_reference"] == published["approval_reference"] and len(stored["approval_json"]) > 1000


def test_documents_and_extractions_round_trip_through_mongodb(real):
    client, database = real
    refs = upload_documents(client)
    assert client.get("/v1/documents").json()["total"] == 9
    extraction = extract(client, refs).json()
    review = review_extraction(client, extraction).json()
    assert review["ready_for_approval"] is True
    assert client.get(f"/v1/documents/extractions/{extraction['extraction_id']}").json()["reviews"][0]["review_id"] == review["review_id"]
    version = client.post("/v1/rulesets", json={"name": "from-documents", "extraction": {"extraction_id": extraction["extraction_id"], "review_id": review["review_id"]}})
    assert version.status_code == 201, version.text
    stored = database["extractions"].find_one({"_id": extraction["extraction_id"]})
    assert len(stored["result_json"]) > 100_000 and database["documents"].index_information()["document_revision"]["unique"] is True


def test_history_findings_and_exports_read_from_mongodb(real):
    client, database = real
    published = published_version(client)
    first = validate(client, published["ruleset_id"], receipt_ids=("RCP-013", "RCP-014"), preview=False).json()
    second = validate(client, published["ruleset_id"], receipt_ids=("RCP-001",), preview=False).json()
    history = client.get("/v1/runs", params={"ruleset_id": published["ruleset_id"], "classification_backend": "openrouter"}).json()
    assert [item["run_id"] for item in history["items"]] == [second["run_id"], first["run_id"]]
    assert history["items"][1]["counts"] == {"exception": 4, "review_signal": 3, "open_question": 1, "uncertainty": 2}
    findings = client.get(f"/v1/runs/{first['run_id']}/findings", params={"kind": "exception"}).json()
    assert [item["rule_id"] for item in findings["items"]] == ["GIFT-02", "ALC-03", "DOC-03", "HOUSE-01"]
    exported = client.get(f"/v1/runs/{first['run_id']}/export", params={"format": "csv"})
    assert exported.status_code == 200 and exported.content.count(b"\r\n") == 11  # the header and ten findings
    plan = database["runs"].find({}).sort([("created_at", -1), ("_id", -1)]).explain()
    assert "IXSCAN" in str(plan)  # the history query uses an index


def test_concurrent_workers_never_run_the_same_job(real, settings):
    """MongoDB's atomic findAndModify, not the test double, is what keeps claims apart: eight workers race."""
    client, database = real
    ruleset_id = create_ruleset(client)["ruleset_id"]
    dataset_id = dataset(client, ("RCP-001",))["dataset_id"]
    queued = {create(client, dataset_id, ruleset_id, label=f"job-{index}").json()["evaluation_id"] for index in range(20)}
    jobs = EvaluationRepository(database["evaluations"])

    def claim_all(worker: str) -> list[str]:
        claimed = []
        while (job := jobs.claim(worker, now(), now() + timedelta(seconds=60))) is not None:
            claimed.append(job["_id"])
        return claimed

    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(claim_all, [f"worker-{index}" for index in range(8)]))
    every = [job for claimed in claims for job in claimed]
    assert sorted(every) == sorted(queued) and len(every) == len(set(every))  # each job claimed exactly once
    assert database["evaluations"].count_documents({"status": "running", "attempts": 1}) == 20


def test_an_evaluation_runs_to_completion_on_mongodb(real, settings):
    client, database = real
    ruleset_id = create_ruleset(client)["ruleset_id"]
    job = create(client, dataset(client, ("RCP-001", "RCP-013", "RCP-014"))["dataset_id"], ruleset_id).json()
    worker = Worker(settings.model_copy(update={"mongo_database": database.name}), database)
    while worker.run_once():
        pass
    stored = client.get(f"/v1/evaluations/{job['evaluation_id']}").json()
    assert (stored["status"], stored["result_context"], stored["progress"]["processed_receipts"]) == ("completed", "dataset", 3)
    assert database["evaluation_results"].count_documents({"evaluation_id": job["evaluation_id"]}) == 3
    plan = database["evaluations"].find({}).sort([("created_at", -1), ("_id", -1)]).explain()
    assert "IXSCAN" in str(plan)
