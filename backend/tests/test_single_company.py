"""Upgrade and public-contract checks for the single-company migration. No provider calls."""

import json

import mongomock
import pytest
from conftest import published_version, receipt, validate
from pymongo.errors import OperationFailure

from expense_api.repositories import ensure_indexes
from expense_api.errors import ApiError
from expense_api.services.evaluations import EvaluationService
from expense_api.services.rulesets import sha256
from expense_api.services.runs import load_results


def test_legacy_indexes_are_replaced_without_rewriting_history():
    database = mongomock.MongoClient()["upgrade"]
    collection = database["rulesets"]
    collection.create_index([("customer_id", 1), ("name", 1), ("version", 1)], unique=True, name="customer_name_version")
    original = {"_id": "old", "customer_id": "old-scope", "name": "policies", "version": 1, "artifact_json": "immutable"}
    collection.insert_one(original.copy())
    ensure_indexes(database)
    ensure_indexes(database)  # startup/retry is idempotent
    assert collection.find_one({"_id": "old"}) == original
    assert "customer_name_version" not in collection.index_information()
    assert collection.index_information()["name_version"]["unique"]


def test_conflicting_legacy_versions_leave_history_and_indexes_untouched():
    database = mongomock.MongoClient()["upgrade"]
    collection = database["rulesets"]
    collection.create_index([("customer_id", 1), ("name", 1), ("version", 1)], unique=True, name="customer_name_version")
    collection.insert_one({"_id": "first", "customer_id": "first", "name": "policies", "version": 1})
    collection.insert_one({"_id": "second", "customer_id": "second", "name": "policies", "version": 1})
    before, indexes = list(collection.find()), collection.index_information()
    with pytest.raises(OperationFailure, match="Single-company index migration blocked"):
        ensure_indexes(database)
    assert list(collection.find()) == before
    assert collection.index_information() == indexes


def test_swagger_and_published_replay_have_no_customer_contract(client):
    assert "customer_id" not in json.dumps(client.get("/openapi.json").json())
    assert client.get("/docs").status_code == 200
    ruleset = published_version(client)
    response = client.post("/v1/validate", json={"ruleset_id": ruleset["ruleset_id"], "receipts": [receipt("RCP-001")]})
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["ruleset"]["status"] == "published"
    assert run["ruleset"]["publication_mode"] == "production"
    assert run["context_complete"] is True
    assert "customer_id" not in json.dumps(run)
    result = run["results"]["results"][0]
    assert (result["disposition"], result["decided"], result["open_questions"]) == ("pass", True, [])


def test_legacy_run_results_keep_original_bytes_and_hash(client):
    run = validate(client, published_version(client)["ruleset_id"], preview=False).json()
    legacy = run["results"]
    legacy["customer_id"] = "legacy"
    legacy["results"][0]["provenance"]["customer_id"] = "legacy"
    text = json.dumps(legacy)
    record = {"_id": run["run_id"], "results_json": text, "results_sha256": sha256(text)}
    result = load_results(record)
    assert "customer_id" not in result.model_dump_json()
    assert result.results[0].disposition == "pass"
    assert record["results_json"] == text and record["results_sha256"] == sha256(text)
    receipt_text = json.dumps(legacy["results"][0])
    row = {"receipt_id": "RCP-001", "customer_id": "legacy", "result_json": receipt_text, "result_sha256": sha256(receipt_text)}
    public = EvaluationService.__new__(EvaluationService)._row(row)
    assert "customer_id" not in json.dumps(public["result"])
    assert "customer_id" not in public
    assert row["result_json"] == receipt_text and row["result_sha256"] == sha256(receipt_text)
    record["results_json"] += " "
    with pytest.raises(ApiError, match="stored hash"):
        load_results(record)
