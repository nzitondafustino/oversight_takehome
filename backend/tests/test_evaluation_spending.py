"""Spending per call, receipt and run: live calls metered against FakeOpenRouter, replayed calls worked out from
results, budgets, and no call counted twice. No test reaches a provider."""

from collections import Counter
from decimal import Decimal

import pytest
from conftest import case_ids, load_cases
from evaluation_support import LANGGRAPH_RECEIPTS, FakeOpenRouter, Workbench, pricing_file

from expense_api.repositories import UsageRepository
from expense_api.services.spending import cached_calls, derived_records, spend_report

CASES = load_cases("evaluation_spending")
MODELS = ["anthropic/claude-haiku-5.5", "tencent/hy-mt2-30b-a3b", "typesafe/jev-1.14"]
BUDGET = {"max_spend_usd": "1", "max_provider_calls": 100, "max_receipts": 10}


@pytest.mark.parametrize("case", CASES, ids=case_ids(CASES))
def test_live_spending(make_client, settings, tmp_path, monkeypatch, case):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-never-sent")
    changes = {"allow_live": True, "allowed_models": MODELS, "live_cache_dir": tmp_path / "live", "allow_uncapped_evaluations": bool(case.get("uncapped"))}
    if "pricing" in case:
        changes["pricing_table"] = pricing_file(tmp_path, case["pricing"])
    bench = Workbench(make_client, settings, receipt_ids=("RCP-001", "RCP-008"), **changes)
    provider = FakeOpenRouter(usage=case.get("usage"), fail=tuple(case.get("fail", ())))
    limits = {"confirm_uncapped": True} if case.get("uncapped") else {"budget": case.get("budget", BUDGET)}
    job = bench.run(worker={"client_factory": lambda core: provider}, execution_mode="live", confirm_live=True, classification=case.get("classification", {}), **limits)
    expect, spend = case["expect"], job["spend"]

    assert (job["status"], job["stop_reason"], job["progress"]["processed_receipts"], job["result_context"]) == (expect["status"], expect["stop_reason"], expect["processed"], expect["result_context"])
    got = {"live": spend["calls"]["live"], "failed": spend["calls"]["failed"], "unpriced_calls": spend["unpriced_usage"]["calls"],
           **{name: spend[name] for name in ("new_spend_usd", "actual_cost_usd", "estimated_cost_usd", "cost_completeness")}}
    assert got == expect["spend"]
    assert len(provider.calls) == spend["calls"]["live"]  # every request the provider saw is recorded, once
    assert job["spend_summary"] == {"new_spend_usd": spend["new_spend_usd"], "cost_completeness": spend["cost_completeness"], "live_calls": spend["calls"]["live"]}
    if "purposes" in expect:
        assert {purpose: totals["calls"]["live"] for purpose, totals in spend["by_purpose"].items()} == expect["purposes"]
    if "by_model_new_spend" in expect:
        assert {model: totals["new_spend_usd"] for model, totals in spend["by_model"].items() if totals["calls"]["live"]} == expect["by_model_new_spend"]
    if "by_backend_live" in expect:
        assert {backend: totals["calls"]["live"] for backend, totals in spend["by_backend"].items()} == expect["by_backend_live"]
    if "pricing_versions" in expect:
        assert spend["pricing_versions"] == expect["pricing_versions"]


def test_each_live_call_keeps_its_provider_identity_tokens_and_latency(make_client, settings, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-never-sent")
    bench = Workbench(make_client, settings, receipt_ids=("RCP-008",), allow_live=True, allowed_models=MODELS, live_cache_dir=tmp_path / "live")
    provider = FakeOpenRouter(usage={"anthropic/claude-haiku-5.5": {"prompt_tokens": 300, "completion_tokens": 40, "cost": 0.0001, "completion_tokens_details": {"reasoning_tokens": 12}}})
    job = bench.run(worker={"client_factory": lambda core: provider}, execution_mode="live", confirm_live=True, budget=BUDGET)
    records = UsageRepository(bench.database["evaluation_usage"]).all(job["evaluation_id"])
    live = [record for record in records if record["mode"] == "live"]
    assert [record["purpose"] for record in live] == ["translation"] * 3 + ["classification"] * 3  # the core translates first
    first = live[3]
    assert (first["provider"], first["backend"], first["model"], first["receipt_id"], first["success"]) == ("openrouter", "chat_completions", "anthropic/claude-haiku-5.5", "RCP-008", True)  # the provider, then the adapter
    assert (first["input_tokens"], first["output_tokens"], first["other_tokens"], first["actual_cost_usd"]) == (300, 40, {"reasoning_tokens": 12}, "0.0001")
    assert first["provider_request_id"].startswith("gen-") and isinstance(first["latency_ms"], int)
    assert [record["sequence"] for record in live] == list(range(6)) and len({record["_id"] for record in records}) == len(records)
    receipt = bench.client.get(f"/v1/evaluations/{job['evaluation_id']}/results").json()["items"][0]
    assert receipt["spend"]["new_spend_usd"] == "0.0006" and receipt["spend"]["calls"]["live"] == 6  # per receipt


def test_a_live_run_can_be_replayed_from_its_own_answers_at_no_cost(make_client, settings, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-never-sent")
    bench = Workbench(make_client, settings, receipt_ids=("RCP-001", "RCP-008"), allow_live=True, allowed_models=MODELS, live_cache_dir=tmp_path / "live")
    live = bench.run(worker={"client_factory": lambda core: FakeOpenRouter()}, execution_mode="live", confirm_live=True, budget=BUDGET)
    answers = tmp_path / "live" / "evaluations" / live["evaluation_id"]
    assert live["live"]["answers"] == f"evaluations/{live['evaluation_id']}" and any(answers.rglob("*.txt"))
    replay = Workbench(make_client, settings, receipt_ids=("RCP-001", "RCP-008"), replay_sets={"that-run": answers}, default_replay_set="that-run")
    replayed = replay.run(worker={"client_factory": _never})
    assert replayed["status"] == "completed" and replayed["spend"]["new_spend_usd"] == "0" and replayed["spend"]["calls"]["replayed"] == 10
    assert replay.get(replayed["evaluation_id"], "/metrics")["classification"] == bench.get(live["evaluation_id"], "/metrics")["classification"]


def _never(core):
    raise AssertionError("replay must never build a provider client")


def test_replay_adds_no_spend_and_shows_known_historical_cost_apart(make_client, settings):
    bench = Workbench(make_client, settings, receipt_ids=("RCP-001", "RCP-008"))
    jev = bench.run(worker={"client_factory": _never}, classification={"backend": "jev", "model": "typesafe/jev-1.14", "replay_set": "jev"})
    spend = jev["spend"]
    assert (spend["new_spend_usd"], spend["cost_completeness"], spend["calls"]["live"], spend["calls"]["replayed"]) == ("0", "complete", 0, 10)
    jev_calls = spend["by_model"]["typesafe/jev-1.14"]["historical"]
    assert jev_calls["calls_with_cost"] == 7 and Decimal(jev_calls["cost_usd"]) > 0  # Jev's recordings hold what the provider reported
    translations = spend["by_model"]["tencent/hy-mt2-30b-a3b"]["historical"]
    assert (translations["cost_usd"], translations["calls_without_cost"]) == (None, 3)  # a chat recording holds no usage: unknown, never zero
    openrouter = bench.run(worker={"client_factory": _never})
    assert openrouter["spend"]["historical"] == {"cost_usd": None, "calls_with_cost": 0, "calls_without_cost": 10}


def test_langgraph_spending_counts_every_call_of_its_order_and_reflection(make_client, settings):
    bench = Workbench(make_client, settings, receipt_ids=LANGGRAPH_RECEIPTS)
    job = bench.run(worker={"client_factory": _never}, classification={"backend": "langgraph", "replay_set": "langgraph", "langgraph": {"reflection": {"enabled": True}}})
    assert job["status"] == "completed" and job["progress"]["failed_receipts"] == 0
    results = bench.client.get(f"/v1/evaluations/{job['evaluation_id']}/results").json()["items"]
    shown = Counter()
    for item in results:
        for line in item["result"]["line_items"]:
            audit = line["classification_details"]["orchestration"]
            shown["classification"] += sum(1 for attempt in audit["attempts"] if attempt["answered"] and not attempt.get("revision"))
            shown["revision"] += sum(1 for attempt in audit["attempts"] if attempt["answered"] and attempt.get("revision"))
            shown["review"] += sum(1 for review in audit["reflection"]["reviews"] if review["answered"])
            shown["translation"] += line.get("english_translation") is not None
    spend = job["spend"]
    assert {purpose: totals["calls"]["replayed"] for purpose, totals in spend["by_purpose"].items()} == {purpose: count for purpose, count in shown.items() if count}
    assert shown["review"] > 0 and set(spend["by_model"]) == {"typesafe/jev-1.14", "anthropic/claude-haiku-5.5", "tencent/hy-mt2-30b-a3b"}
    assert set(spend["by_backend"]) == {"decisions", "chat_completions"} and spend["new_spend_usd"] == "0" and spend["calls"]["live"] == 0


def test_a_call_is_recorded_once_however_often_it_is_stored(make_client, settings):
    bench = Workbench(make_client, settings)
    usage = UsageRepository(bench.database["evaluation_usage"])
    record = derived_records("eval_x", "RCP-001", [{"purpose": "classification", "backend": "jev", "model": "typesafe/jev-1.14", "line_id": "RCP-001-L1", "usage": {"cost": 0.00007}}], "replayed")[0]
    assert usage.record(record) is True and usage.record(dict(record)) is False
    assert spend_report(usage.all("eval_x"))["calls"]["total"] == 1 and spend_report(usage.all("eval_x"))["historical"]["cost_usd"] == "0.00007"


def test_a_resumed_replay_job_records_no_call_twice(make_client, settings):
    bench = Workbench(make_client, settings, receipt_ids=("RCP-001", "RCP-008"))
    job = bench.run()
    records = UsageRepository(bench.database["evaluation_usage"]).all(job["evaluation_id"])
    assert len(records) == len({record["_id"] for record in records}) == 10


def test_answers_the_run_cached_are_counted_as_cached_not_as_live():
    calls = [{"purpose": "classification", "backend": "openrouter", "model": "m", "line_id": f"L{index}", "usage": None} for index in range(3)]
    cached = cached_calls(calls, Counter({("RCP-1", "classification"): 2}), "RCP-1")
    assert len(cached) == 1 and cached[0]["line_id"] is None  # which line it was is not known
    assert cached_calls(calls, Counter({("RCP-1", "classification"): 3}), "RCP-1") == []


def test_incomplete_spending_keeps_verified_estimated_and_unknown_costs_apart_and_never_divides_a_lower_bound(make_client, settings, tmp_path, monkeypatch):
    """The translator is priced from the table (estimated). In the first run Haiku reports no cost and has no price
    (unknown); in the second Jev reports its cost (actual)."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-never-sent")
    bench = Workbench(make_client, settings, receipt_ids=("RCP-001", "RCP-008"), allow_live=True, allow_uncapped_evaluations=True, allowed_models=MODELS,
                      live_cache_dir=tmp_path / "live", pricing_table=pricing_file(tmp_path, {"tencent/hy-mt2-30b-a3b": ["0.10", "0.20"]}))
    usage = {"anthropic/claude-haiku-5.5": {"prompt_tokens": 300, "completion_tokens": 40}, "tencent/hy-mt2-30b-a3b": {"prompt_tokens": 50, "completion_tokens": 10},
             "typesafe/jev-1.14": {"prompt_tokens": 10, "completion_tokens": 1, "cost": 0.0002}}
    unknown, known = [bench.run(worker={"client_factory": lambda core: FakeOpenRouter(usage=usage)}, execution_mode="live", confirm_live=True, confirm_uncapped=True, classification=classification)
                      for classification in ({}, {"backend": "jev", "model": "typesafe/jev-1.14"})]

    spend = unknown["spend"]
    assert (unknown["status"], spend["cost_completeness"], unknown["spend_summary"]["cost_completeness"]) == ("completed", "incomplete", "incomplete")
    assert (spend["actual_cost_usd"], spend["unpriced_usage"]["calls"]) == ("0", 7) and Decimal(spend["estimated_cost_usd"]) > 0
    assert spend["new_spend_usd"] == spend["estimated_cost_usd"]  # only what is known: a lower bound, and reported as one
    assert spend["by_model"]["anthropic/claude-haiku-5.5"]["cost_completeness"] == "incomplete" and spend["by_model"]["tencent/hy-mt2-30b-a3b"]["cost_completeness"] == "complete"
    spend = known["spend"]
    assert spend["cost_completeness"] == "complete" and Decimal(spend["actual_cost_usd"]) > 0 and Decimal(spend["estimated_cost_usd"]) > 0
    assert Decimal(spend["new_spend_usd"]) == Decimal(spend["actual_cost_usd"]) + Decimal(spend["estimated_cost_usd"])

    rows = {row["evaluation_id"]: row for row in bench.client.post("/v1/evaluations/compare", json={"evaluation_ids": [unknown["evaluation_id"], known["evaluation_id"]]}).json()["evaluations"]}
    assert (rows[unknown["evaluation_id"]]["cost_per_receipt_usd"], rows[unknown["evaluation_id"]]["cost_per_correct_prediction_usd"]) == (None, None)  # a lower bound is never divided
    assert rows[known["evaluation_id"]]["cost_per_receipt_usd"] is not None
