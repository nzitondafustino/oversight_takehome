"""Evaluation history, details, results, exports and comparisons, over replay runs of the project's receipts."""

import csv
import io
import json
from datetime import timedelta

import pytest
from conftest import REPLAY_SETS, case_ids, load_cases
from evaluation_support import FakeOpenRouter, Workbench, dataset, golden_lines, upload
from fastapi.testclient import TestClient

from expense_api.config import Settings
from expense_api.main import create_app
from expense_api.services.rulesets import now

JEV = {"backend": "jev", "model": "typesafe/jev-1.14", "replay_set": "jev"}
HISTORY = load_cases("evaluation_history")


@pytest.fixture(scope="module")
def runs(core_config, tmp_path_factory):
    """An openrouter and a Jev replay of the 32 receipts, a third on a smaller dataset, and one left queued.
    Built once for the module, as each run replays every receipt."""
    settings = Settings(core_config=core_config, artifact_dir=tmp_path_factory.mktemp("artifacts"), replay_sets=REPLAY_SETS, default_replay_set="openrouter", allow_preview_validation=True)
    clients = []

    def make_client(app_settings, mongo):
        client = TestClient(create_app(app_settings, mongo_client=mongo), raise_server_exceptions=False)
        client.__enter__()
        clients.append(client)
        return client

    bench = Workbench(make_client, settings, receipt_ids=None)
    ids = {"openrouter": bench.queue(label="openrouter"), "jev": bench.queue(label="jev", classification=JEV)}
    small = dataset(bench.client, ("RCP-001", "RCP-002"), name="small")
    ids["small"] = bench.client.post("/v1/evaluations", json={"dataset_id": small["dataset_id"], "ruleset_id": bench.ruleset_id, "preview": True, "label": "small"}).json()["evaluation_id"]
    bench.drain()
    ids["queued"] = bench.queue(label="queued")
    bench.small = small
    yield bench, ids
    for client in clients:
        client.__exit__(None, None, None)


@pytest.mark.parametrize("case", HISTORY, ids=case_ids(HISTORY))
def test_history_filters(runs, case):
    bench, ids = runs
    params = {**{name: (bench.dataset["dataset_id"] if value == "@golden" else bench.small["dataset_id"] if value == "@small" else value) for name, value in case.get("params", {}).items()}}
    page = bench.client.get("/v1/evaluations", params=params).json()
    assert [item["label"] for item in page["items"]] == case["expect"], page


def test_history_is_newest_first_paged_and_holds_no_results(runs):
    bench, ids = runs
    first = bench.client.get("/v1/evaluations", params={"limit": 2}).json()
    second = bench.client.get("/v1/evaluations", params={"limit": 2, "offset": 2}).json()
    assert [item["label"] for item in first["items"] + second["items"]] == ["queued", "small", "jev", "openrouter"] and first["total"] == 4
    item = first["items"][1]
    assert set(item) >= {"evaluation_id", "dataset", "ruleset", "configuration", "status", "progress", "started_at", "completed_at", "summary", "spend_summary"}
    assert "metrics" not in item and "spend" not in item and "results" not in item
    assert item["summary"]["category_accuracy"] == 1.0 and item["spend_summary"] == {"new_spend_usd": "0", "cost_completeness": "complete", "live_calls": 0}
    window = {"created_from": (now() + timedelta(days=1)).isoformat()}
    assert bench.client.get("/v1/evaluations", params=window).json()["total"] == 0
    reversed_window = {"created_from": now().isoformat(), "created_to": (now() - timedelta(days=1)).isoformat()}
    assert bench.client.get("/v1/evaluations", params=reversed_window).json()["error"]["code"] == "invalid_filter"


def test_details_show_spending_by_model_backend_and_purpose(runs):
    bench, ids = runs
    job = bench.get(ids["jev"])
    assert set(job["spend"]["by_purpose"]) == {"classification", "translation"} and set(job["spend"]["by_backend"]) == {"decisions", "chat_completions"}
    assert job["spend"]["by_model"]["typesafe/jev-1.14"]["calls"] == {"total": 79, "live": 0, "cached": 0, "replayed": 79, "failed": 0}
    assert job["metrics_schema_version"] == "evaluation-metrics/3" and job["result_context"] == "dataset"


def test_metrics_are_the_cores_scores_with_their_denominators(runs):
    bench, ids = runs
    metrics = bench.get(ids["jev"], "/metrics")
    # The same as the project's own record of this run: evaluation/jev/runs/first-run/metrics.json.
    assert metrics["classification"]["accuracy"] == {"numerator": 78, "denominator": 79, "value": 0.987342, "wilson_95": [0.931724, 0.997762]}
    assert metrics["classification"]["macro"] == {"precision": 1.0, "recall": 0.995215, "f1": 0.997494}
    assert metrics["classification"]["outcomes"] == {"correct": 78, "incorrect": 0, "missing": 0, "classification_error": 1, "execution_error": 0}
    assert metrics["disposition"]["accuracy"]["denominator"] == 32 and metrics["findings"]["receipts"] == 32
    assert sum(sum(row.values()) for row in metrics["classification"]["confusion_matrix"]["counts"].values()) == 79


RESULT_FILTERS = [
    ({"classification_correct": False}, 1),
    ({"classification_correct": True}, 31),
    ({"error": True}, 1),
    ({"receipt_id": ["RCP-001", "RCP-002"]}, 2),
    ({"category": "alcohol"}, None),
    ({"disposition": ["pass"]}, None),
    ({"uncertain": True}, 2),
]


@pytest.mark.parametrize(("filters", "total"), RESULT_FILTERS, ids=[json.dumps(filters) for filters, _ in RESULT_FILTERS])
def test_results_filter_and_keep_the_cores_result(runs, filters, total):
    bench, ids = runs
    page = bench.client.get(f"/v1/evaluations/{ids['jev']}/results", params={"limit": 100, **filters}).json()
    if total is not None:
        assert page["total"] == total
    for item in page["items"]:
        result = item["result"]
        if filters.get("classification_correct") is False:
            assert any(line["outcome"] != "correct" for line in item["scoring"]["lines"].values())
        if "category" in filters:
            assert filters["category"] in {line["category"] for line in result["line_items"]}
        if "disposition" in filters:
            assert result["disposition"] in filters["disposition"]
        if "uncertain" in filters:
            assert result["uncertainty"]
        assert {"exceptions", "review_signals", "open_questions", "uncertainty", "metadata", "provenance"} <= set(result)  # evidence kept
        assert item["spend"]["new_spend_usd"] == "0"
    indexes = [item["index"] for item in page["items"]]
    assert indexes == sorted(indexes)


def test_results_are_paged_in_dataset_order(runs):
    bench, ids = runs
    pages = [bench.client.get(f"/v1/evaluations/{ids['openrouter']}/results", params={"limit": 10, "offset": offset}).json() for offset in (0, 10, 20, 30)]
    order = [item["receipt_id"] for page in pages for item in page["items"]]
    assert order == [json.loads(line)["receipt_id"] for line in golden_lines()] and pages[0]["total"] == 32


def test_the_json_export_is_deterministic_and_whole(runs):
    bench, ids = runs
    params = {"format": "json"}
    first = bench.client.get(f"/v1/evaluations/{ids['openrouter']}/export", params=params)
    second = bench.client.get(f"/v1/evaluations/{ids['openrouter']}/export", params=params)
    assert first.status_code == 200 and first.content == second.content
    assert first.headers["content-disposition"] == f'attachment; filename="{ids["openrouter"]}.json"'
    body = json.loads(first.content)
    assert body["export_schema"] == "evaluation-export/2" and len(body["results"]) == 32 and len(body["usage"]) == 90
    assert all(list(item["result"]) == ["receipt_id", "disposition", "line_items", "exceptions", "review_signals", "metadata"] for item in body["results"])  # the brief's shape
    assert body["metrics"] == {key: value for key, value in bench.get(ids["openrouter"], "/metrics").items() if key not in ("evaluation_id", "status", "provisional", "scored")}
    assert body["evaluation"]["dataset"]["canonical_sha256"] == bench.dataset["canonical_sha256"]


def test_the_csv_export_has_one_row_per_line_in_order(runs):
    bench, ids = runs
    response = bench.client.get(f"/v1/evaluations/{ids['jev']}/export", params={"format": "csv"})
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert response.headers["content-type"].startswith("text/csv") and len(rows) == 79
    assert [row["line_id"] for row in rows] == [line["line_id"] for record in map(json.loads, golden_lines()) for line in record["line_items"]]
    assert {row["line_outcome"] for row in rows} == {"correct", "classification_error"}
    assert bench.client.get(f"/v1/evaluations/{ids['jev']}/export", params={"format": "csv"}).content == response.content


def test_csv_cells_cannot_run_as_formulas(make_client, settings, tmp_path, monkeypatch):
    # No recording holds this description, so it is classified live, by the stand-in provider.
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-never-sent")
    bench = Workbench(make_client, settings, receipt_ids=("RCP-001",), allow_live=True, allowed_models=["anthropic/claude-haiku-5.5", "tencent/hy-mt2-30b-a3b"], live_cache_dir=tmp_path / "live")
    record = json.loads(golden_lines(("RCP-001",))[0])
    record["receipt_id"], record["line_items"] = "RCP-901", [{**record["line_items"][0], "line_id": "RCP-901-L1", "description_raw": "=HYPERLINK(\"http://x\")"}]
    record["total_amount"] = record["total_amount_usd"] = record["line_items"][0]["amount"]
    record["itemization_complete"] = True
    response = upload(bench.client, json.dumps(record), name="formula")
    assert response.status_code == 201, response.text
    live = {"execution_mode": "live", "confirm_live": True, "budget": {"max_spend_usd": "1", "max_provider_calls": 10, "max_receipts": 1}}
    job = bench.client.post("/v1/evaluations", json={"dataset_id": response.json()["dataset_id"], "ruleset_id": bench.ruleset_id, "preview": True, **live}).json()
    bench.drain(client_factory=lambda core: FakeOpenRouter())
    text = bench.client.get(f"/v1/evaluations/{job['evaluation_id']}/export", params={"format": "csv"}).text
    row = next(csv.DictReader(io.StringIO(text)))
    assert row["description_raw"] == "'=HYPERLINK(\"http://x\")"


def test_an_unfinished_evaluation_is_not_exported(runs):
    bench, ids = runs
    response = bench.client.get(f"/v1/evaluations/{ids['queued']}/export")
    assert response.status_code == 409 and response.json()["error"]["code"] == "evaluation_not_finished"
    assert bench.client.get(f"/v1/evaluations/{ids['openrouter']}/export", params={"format": "xlsx"}).json()["error"]["code"] == "unsupported_export_format"


def test_runs_on_one_dataset_and_ruleset_compare_side_by_side(runs):
    bench, ids = runs
    response = bench.client.post("/v1/evaluations/compare", json={"evaluation_ids": [ids["openrouter"], ids["jev"]]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["comparability"]["directly_comparable"] is True and "not a controlled head-to-head benchmark" in body["comparability"]["note"]
    openrouter, jev = body["evaluations"]
    assert (openrouter["backend"], jev["backend"]) == ("chat_completions", "decisions")
    # The recorded backend stays the identity; the setup beside it says what that backend is: an adapter, not a provider.
    assert jev["setup"] == {"orchestrator": "direct", "provider": "openrouter", "backend": "decisions", "protocol": "decisions", "adapter": "decisions", "model": "typesafe/jev-1.14", "nodes": None}
    assert openrouter["setup"]["provider"] == "openrouter" and openrouter["setup"]["protocol"] == "chat_completions"
    assert openrouter["category_accuracy"]["value"] == 1.0 and jev["category_accuracy"]["value"] == 0.987342
    assert openrouter["dataset"]["canonical_sha256"] == jev["dataset"]["canonical_sha256"] and openrouter["ruleset"]["artifact_sha256"] == jev["ruleset"]["artifact_sha256"]
    assert jev["provider_calls"]["replayed"] == 90 and jev["new_spend_usd"] == "0"  # 79 classifications and 11 translations
    assert jev["cost_per_receipt_usd"] is None and jev["cost_per_correct_prediction_usd"] is None  # replay adds no spend to divide
    assert jev["historical_cost_usd"]["calls_with_cost"] == 78 and openrouter["mean_latency_per_receipt_ms"] is not None


def test_runs_on_different_datasets_are_marked_not_directly_comparable(runs):
    bench, ids = runs
    body = bench.client.post("/v1/evaluations/compare", json={"evaluation_ids": [ids["openrouter"], ids["small"]]}).json()
    assert body["comparability"]["directly_comparable"] is False and "different datasets" in body["comparability"]["issues"]


def test_only_completed_known_evaluations_are_compared(runs):
    bench, ids = runs
    unfinished = bench.client.post("/v1/evaluations/compare", json={"evaluation_ids": [ids["openrouter"], ids["queued"]]})
    assert unfinished.status_code == 409 and unfinished.json()["error"]["details"] == {ids["queued"]: "queued"}
    unknown = bench.client.post("/v1/evaluations/compare", json={"evaluation_ids": [ids["openrouter"], "eval_000000000000000000000000"]})
    assert unknown.status_code == 404
    repeated = bench.client.post("/v1/evaluations/compare", json={"evaluation_ids": [ids["jev"], ids["jev"]]})
    assert repeated.status_code == 422


def test_a_jev_run_reads_back_with_its_provider_and_adapter_apart(runs):
    bench, ids = runs
    job = bench.client.get(f"/v1/evaluations/{ids['jev']}").json()
    # The request is kept as sent, with jev, the decisions adapter's earlier name; the plan names the adapter.
    assert job["classification"]["backend"] == "jev" and job["configuration"]["classification_backend"] == "decisions"
    assert job["configuration"]["snapshot"]["classification_model"] == "typesafe/jev-1.14" and job["replay"]["set"] == "jev"
    assert job["configuration"]["setup"] == {"orchestrator": "direct", "provider": "openrouter", "backend": "decisions", "protocol": "decisions", "adapter": "decisions", "model": "typesafe/jev-1.14", "nodes": None}
    # Either name finds it.
    for name in ("decisions", "jev"):
        assert ids["jev"] in [item["evaluation_id"] for item in bench.client.get("/v1/evaluations", params={"backend": name}).json()["items"]]
