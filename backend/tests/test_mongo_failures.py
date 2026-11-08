"""MongoDB failures: the app still starts and reports itself, and no request pretends to have succeeded."""

import pytest
from conftest import compile_version, create_ruleset, extract, review_extraction, step, upload_documents, validate
from pymongo import MongoClient
from pymongo.errors import AutoReconnect

from expense_api.repositories import ExtractionRepository, RulesetRepository, RunRepository


@pytest.fixture
def down(make_client):
    """An app whose MongoDB refuses connections: a real client pointed at a closed loopback port."""
    return make_client(mongo=MongoClient("mongodb://127.0.0.1:9", serverSelectionTimeoutMS=200, connectTimeoutMS=200, tz_aware=True))


def test_health_tells_a_running_app_from_an_unavailable_database(client, down):
    assert client.get("/health").status_code == 200
    response = down.get("/health")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "application": "running", "database": "unavailable", "version": "0.1.0"}


def test_every_data_endpoint_is_a_503_when_the_database_is_down(down):
    for response in (
        down.post("/v1/rulesets", json={"name": "default", "artifact": {"rules": []}}),
        down.get("/v1/rulesets"),
        down.get("/v1/rulesets/rs_x"),
        down.get("/v1/runs/run_x"),
        validate(down, "rs_x"),
        down.get("/v1/rulesets/rs_x/comparisons"),
        *(down.post(f"/v1/rulesets/rs_x/{name}", json={}) for name in ("submit", "publish")),
        down.post("/v1/rulesets/rs_x/review", json={"actor": "r", "decision": "rejected", "reason": "x"}),
        down.post("/v1/rulesets/rs_x/approve", json={"actor": "a"}),
        down.post("/v1/documents", json={"filename": "meals.md", "content": "# Meals\n"}),
        down.get("/v1/documents"),
        down.get(f"/v1/documents/doc_{'0' * 24}"),
        down.post(f"/v1/documents/doc_{'0' * 24}/revisions", json={"content": "# Meals\n"}),
        down.post("/v1/documents/extractions", json={"documents": [{"document_id": f"doc_{'0' * 24}", "revision": 1}], "recording_set": "live"}),
        down.get("/v1/documents/extractions/ext_x"),
        down.get("/v1/runs"),
        down.get("/v1/runs/run_x/findings"),
        *(down.get("/v1/runs/run_x/export", params={"format": fmt}) for fmt in ("json", "csv")),
    ):
        assert response.status_code == 503 and response.json()["error"]["code"] == "database_unavailable", response.text


def test_a_completed_validation_that_cannot_be_saved_is_not_reported_as_done(client, monkeypatch):
    ruleset_id = create_ruleset(client)["ruleset_id"]

    def lost(self, document):
        raise AutoReconnect("connection lost")

    monkeypatch.setattr(RunRepository, "insert", lost)
    response = validate(client, ruleset_id)
    assert response.status_code == 503 and response.json()["error"]["code"] == "database_unavailable"


def test_a_transition_the_database_loses_is_not_reported_as_done(client, monkeypatch):
    record = compile_version(client).json()

    def lost(self, *args):
        raise AutoReconnect("connection lost")

    monkeypatch.setattr(RulesetRepository, "transition", lost)
    response = step(client, record, "submit")
    assert response.status_code == 503 and response.json()["error"]["code"] == "database_unavailable"


def test_a_review_the_database_loses_is_not_reported_as_done(client, monkeypatch):
    extraction = extract(client, upload_documents(client)).json()

    def lost(self, review):
        raise AutoReconnect("connection lost")

    monkeypatch.setattr(ExtractionRepository, "insert_review", lost)
    response = review_extraction(client, extraction)
    assert response.status_code == 503 and response.json()["error"]["code"] == "database_unavailable"


def test_discovery_needs_no_database(down):
    assert down.get("/v1/backends").status_code == 200 and down.get("/v1/taxonomy").status_code == 200
