"""GET /v1/runs: runs, newest first, filtered, without their results."""

import time
from datetime import datetime

import mongomock
import pytest
from conftest import ARTIFACT, case_ids, create_ruleset, load_cases, receipt, validate
from expense_api.services.discovery import run_setup

CASES = load_cases("run_history")
INVALID = load_cases("run_history", "invalid")
SETUPS = load_cases("run_history", "setups")
LABELS = load_cases("run_history", "langgraph_labels")


def _classified(receipt_id):
    record = receipt(receipt_id)
    for line in record["line_items"]:
        line.update(category="service_fee" if "Gratuity" in line["description_raw"] else "food_beverage", restricted=False, confidence=0.95)
    return record


@pytest.fixture
def history(make_client):
    """The four runs run_history.yaml describes, a few milliseconds apart, and the mongo client holding them."""
    mongo = mongomock.MongoClient(tz_aware=True)
    client = make_client(mongo=mongo)
    v1 = create_ruleset(client)["ruleset_id"]
    v2 = create_ruleset(client, artifact={**ARTIFACT, "ruleset_version": "v2"})["ruleset_id"]
    made = [
        ("A", lambda: validate(client, v1)),
        ("B", lambda: validate(client, v1, classification={"backend": "jev", "model": "typesafe/jev-1.14", "replay_set": "jev"})),
        ("C", lambda: validate(client, v2, receipt_ids=("RCP-014",))),
        ("D", lambda: client.post("/v1/validate", json={"ruleset_id": v1, "receipts": [_classified("RCP-001")], "context_complete": True, "execution_mode": "deterministic", "preview": True})),
    ]
    runs = {}
    for label, make in made:
        response = make()
        assert response.status_code == 201, response.text
        runs[label] = response.json()
        time.sleep(0.005)  # distinct millisecond timestamps, so time filters have a clear boundary
    return client, mongo, runs, {"v1": v1, "v2": v2}


def _params(case_params, runs, rulesets):
    params = {**case_params}
    for name in ("ruleset_id",):
        if params.get(name) in rulesets:
            params[name] = rulesets[params[name]]
    if "from_run" in params:
        params["created_from"] = runs[params.pop("from_run")]["created_at"]
    if "to_run" in params:
        params["created_to"] = runs[params.pop("to_run")]["created_at"]
    return {name: value for name, value in params.items() if value is not None}


@pytest.mark.parametrize("case", CASES, ids=case_ids(CASES))
def test_history_filters_and_pages(history, case):
    client, _, runs, rulesets = history
    response = client.get("/v1/runs", params=_params(case["params"], runs, rulesets))
    assert response.status_code == 200, response.text
    page = response.json()
    by_id = {run["run_id"]: label for label, run in runs.items()}
    assert [by_id[item["run_id"]] for item in page["items"]] == case["expect"]
    assert page["total"] == case.get("total", len(case["expect"]))


@pytest.mark.parametrize("case", INVALID, ids=case_ids(INVALID))
def test_an_invalid_filter_is_refused(history, case):
    client, _, runs, rulesets = history
    response = client.get("/v1/runs", params=_params(case["params"], runs, rulesets))
    assert (response.status_code, response.json()["error"]["code"]) == (422, case["code"])


def test_a_summary_is_light_and_counts_what_the_core_found(history):
    client, _, runs, _ = history
    items = {item["run_id"]: item for item in client.get("/v1/runs").json()["items"]}
    flagged = items[runs["C"]["run_id"]]
    assert "results" not in flagged and "results_json" not in flagged
    assert (flagged["classification_backend"], flagged["receipt_count"], flagged["dispositions"]) == ("chat_completions", 1, {"flag": 1, "review": 0, "pass": 0})
    assert flagged["counts"] == {"exception": 3, "review_signal": 1, "open_question": 0, "uncertainty": 0}
    assert flagged["ruleset"] == runs["C"]["ruleset"] and flagged["created_at"] == runs["C"]["created_at"]
    assert items[runs["D"]["run_id"]]["classification_backend"] is None  # deterministic mode calls no backend


def test_ties_in_time_keep_one_order(history):
    client, mongo, runs, _ = history
    moment = datetime.fromisoformat(runs["A"]["created_at"].replace("Z", "+00:00"))
    mongo["expense_api"]["runs"].update_many({}, {"$set": {"created_at": moment}})
    first = [item["run_id"] for item in client.get("/v1/runs").json()["items"]]
    assert first == sorted(first, reverse=True)  # the unique, increasing run ID breaks the tie
    pages = [item["run_id"] for offset in (0, 2) for item in client.get("/v1/runs", params={"limit": 2, "offset": offset}).json()["items"]]
    assert pages == first


def test_a_run_stored_before_summaries_shows_them_as_unknown(history):
    client, mongo, runs, _ = history
    # A Phase 1 run: no summary, no results hash, no preview field. Its result is untouched.
    mongo["expense_api"]["runs"].update_one({"_id": runs["A"]["run_id"]}, {"$unset": {"summary": "", "results_sha256": "", "preview": ""}})
    item = next(item for item in client.get("/v1/runs").json()["items"] if item["run_id"] == runs["A"]["run_id"])
    assert (item["classification_backend"], item["counts"], item["preview"]) == (None, None, False)
    assert item["dispositions"] == {"flag": 0, "review": 0, "pass": 1} and item["receipt_count"] == 1
    assert runs["A"]["run_id"] in [item["run_id"] for item in client.get("/v1/runs", params={"preview": False}).json()["items"]]
    stored = client.get(f"/v1/runs/{runs['A']['run_id']}")
    assert stored.status_code == 200 and stored.json()["results"] == runs["A"]["results"]
    assert item["setup"] is None  # nothing stored to read it from
    assert stored.json()["setup"]["model"] == "anthropic/claude-haiku-5.5"  # the detail reads the core's own result


def test_a_run_stored_before_its_model_was_kept_shows_it_as_not_recorded(history):
    client, mongo, runs, _ = history
    # A Jev run whose summary predates classification_model: its adapter is read as recorded, its model never guessed.
    mongo["expense_api"]["runs"].update_one({"_id": runs["B"]["run_id"]}, {"$unset": {"summary.classification_model": ""}})
    item = next(item for item in client.get("/v1/runs").json()["items"] if item["run_id"] == runs["B"]["run_id"])
    assert item["classification_backend"] == "decisions"
    assert item["setup"] == {"orchestrator": "direct", "provider": "openrouter", "backend": "decisions", "protocol": "decisions", "adapter": "decisions", "model": None, "nodes": None}
    # Filtering by the adapter, under either name, still finds it.
    for name in ("decisions", "jev"):
        assert [run["run_id"] for run in client.get("/v1/runs", params={"classification_backend": name}).json()["items"]] == [runs["B"]["run_id"]]


def test_a_run_recorded_before_the_adapters_were_renamed_reads_as_recorded(history):
    client, mongo, runs, _ = history
    # The summary as the earlier API stored it, with the adapter's earlier name: never rewritten.
    mongo["expense_api"]["runs"].update_one({"_id": runs["B"]["run_id"]}, {"$set": {"summary.classification_backend": "jev"}})
    item = next(item for item in client.get("/v1/runs").json()["items"] if item["run_id"] == runs["B"]["run_id"])
    assert item["classification_backend"] == "jev"
    assert item["setup"] == {"orchestrator": "direct", "provider": "openrouter", "backend": "jev", "protocol": "decisions", "adapter": "decisions", "model": "typesafe/jev-1.14", "nodes": None}
    for name in ("decisions", "jev"):
        assert runs["B"]["run_id"] in [run["run_id"] for run in client.get("/v1/runs", params={"classification_backend": name}).json()["items"]]


@pytest.mark.parametrize("case", SETUPS, ids=[case["run"] for case in SETUPS])
def test_each_run_reads_as_orchestrator_provider_adapter_and_model(history, case):
    client, _, runs, _ = history
    run = runs[case["run"]]
    item = next(item for item in client.get("/v1/runs").json()["items"] if item["run_id"] == run["run_id"])
    assert item["setup"] == case["expect"] and run["setup"] == case["expect"]
    assert client.get(f"/v1/runs/{run['run_id']}").json()["setup"] == case["expect"]


@pytest.mark.parametrize("case", LABELS, ids=case_ids(LABELS))
def test_a_langgraph_label_reads_into_its_nodes(case):
    setup = run_setup("langgraph", case["label"])
    assert setup["orchestrator"] == "langgraph" and setup["provider"] is None and setup["model"] is None
    assert setup["nodes"] == case["expect"]
