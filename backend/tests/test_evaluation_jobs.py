"""Evaluation jobs: creation, the worker's claims and leases, progress, recovery, cancellation and replay.

Every run here is replay through the real core and the recorded answers, or live against FakeOpenRouter.
"""

import json
from datetime import timedelta
from itertools import count
from types import SimpleNamespace

import pytest
from conftest import REPLAY_SETS, REPO, case_ids, load_cases, published_version, receipt
from evaluation_support import LANGGRAPH_RECEIPTS, FakeOpenRouter, Workbench, dataset, upload
from expense_validator import ExpenseValidator, ValidatorSettings

from expense_api.repositories import EvaluationRepository
from expense_api.services.rulesets import now, sha256
from expense_api.services.runs import canonical_json
from expense_api.worker import Worker
from expense_api.services import evaluation_runner

CASES = load_cases("evaluation_requests")
LIVE_MODELS = ["anthropic/claude-haiku-5.5", "tencent/hy-mt2-30b-a3b"]
LIVE_BODY = {"execution_mode": "live", "confirm_live": True, "budget": {"max_spend_usd": "1", "max_provider_calls": 100, "max_receipts": 10}}


@pytest.fixture
def bench(make_client, settings):
    return Workbench(make_client, settings)


@pytest.fixture
def fixed_receipt_clock(monkeypatch):
    """Measure each receipt as 1 ms without changing real time or worker lease clocks."""
    ticks = count()
    monkeypatch.setattr(evaluation_runner, "time", SimpleNamespace(perf_counter=lambda: next(ticks) / 1000))


@pytest.mark.parametrize("case", CASES, ids=case_ids(CASES))
def test_a_refused_evaluation_is_an_error_and_queues_nothing(make_client, settings, tmp_path, monkeypatch, case):
    changes = {"live_cache_dir": tmp_path / "live", **case.get("settings", {})}
    if case.get("key"):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-never-sent")
    bench = Workbench(make_client, settings, **changes)
    body = {"dataset_id": bench.dataset["dataset_id"], "ruleset_id": bench.ruleset_id, "preview": True, **(LIVE_BODY if case.get("live") else {}), **case.get("request", {})}
    response = bench.client.post("/v1/evaluations", json={key: value for key, value in body.items() if value is not None})
    assert (response.status_code, response.json()["error"]["code"]) == (case["expect"]["status"], case["expect"]["code"]), response.text
    if "details" in case["expect"]:
        assert response.json()["error"]["details"] == case["expect"]["details"]
    assert bench.database["evaluations"].count_documents({}) == 0


def test_a_new_evaluation_is_queued_with_an_immutable_snapshot(bench):
    response = bench.create(label="baseline", classification={"replay_set": "openrouter"})
    assert response.status_code == 201, response.text
    job = response.json()
    assert response.headers["location"] == f"/v1/evaluations/{job['evaluation_id']}"
    assert (job["status"], job["execution_mode"], job["attempts"], job["started_at"], job["label"]) == ("queued", "replay", 0, None, "baseline")
    assert job["progress"] == {"total_receipts": 2, "processed_receipts": 0, "failed_receipts": 0, "percent": 0.0}
    assert job["dataset"]["canonical_sha256"] == bench.dataset["canonical_sha256"] and job["dataset"]["receipts"] == 2
    assert job["ruleset"]["ruleset_id"] == bench.ruleset_id and len(job["ruleset"]["artifact_sha256"]) == 64
    assert job["replay"]["set"] == "openrouter" and len(job["replay"]["sha256"]) == 64 and job["live"] is None and job["budget"] is None
    configuration = job["configuration"]
    assert configuration["classification_backend"] == "chat_completions" and configuration["models"] == LIVE_MODELS
    assert configuration["snapshot"]["classification_model"] == "anthropic/claude-haiku-5.5" and configuration["snapshot"]["translation_enabled"] is True
    assert set(configuration["fingerprint"]) == {"prompt_sha256", "ruleset_sha256", "taxonomy_sha256", "fields_sha256", "core_version", "prompt_identity_version"}
    assert configuration["fingerprint"]["prompt_identity_version"] == 2  # a new job names adapters by their own names
    assert configuration["fingerprint"]["ruleset_sha256"] == job["ruleset"]["artifact_sha256"]
    assert job["spend_summary"] == {"new_spend_usd": "0", "cost_completeness": "complete", "live_calls": 0}
    assert bench.database["runs"].count_documents({}) == 0  # evaluations are kept apart from validation runs


def test_langgraph_keeps_its_whole_order_models_reflection_and_limits(bench):
    order = {"backends": [{"name": "primary", "backend": "jev", "model": "typesafe/jev-1.14"}, {"name": "fallback", "backend": "openrouter", "model": "anthropic/claude-haiku-5.5"}],
             "routing": {"min_confidence": 0.8}, "reflection": {"enabled": True, "max_retries": 1, "max_total_calls_per_line": 4}}
    job = bench.create(classification={"backend": "langgraph", "replay_set": "langgraph", "langgraph": order}).json()
    snapshot = job["configuration"]["snapshot"]
    assert [child["model"] for child in snapshot["langgraph"]["backends"]] == ["typesafe/jev-1.14", "anthropic/claude-haiku-5.5"]
    assert snapshot["langgraph"]["routing"] == {"min_confidence": 0.8, "fallback_on_error": True, "fallback_on_low_confidence": True}
    assert snapshot["langgraph"]["reflection"] == {"enabled": True, "reviewer": {"backend": "chat_completions", "model": "anthropic/claude-haiku-5.5"}, "max_retries": 1, "max_total_calls_per_line": 4}
    assert snapshot["classification_model"] is None  # no single model overrides the order
    # The order was sent with the adapters' earlier names, and is planned and labelled with their own.
    assert job["configuration"]["model_label"] == "primary=decisions:typesafe/jev-1.14, fallback=chat_completions:anthropic/claude-haiku-5.5"
    assert job["configuration"]["models"] == ["anthropic/claude-haiku-5.5", "tencent/hy-mt2-30b-a3b", "typesafe/jev-1.14"]
    # LangGraph is the orchestrator: no single provider, adapter or model, and each node with its own, read from the snapshot.
    assert job["configuration"]["setup"] == {"orchestrator": "langgraph", "provider": None, "backend": None, "protocol": None, "adapter": None, "model": None, "nodes": [
        {"name": "primary", "backend": "decisions", "model": "typesafe/jev-1.14", "provider": "openrouter", "protocol": "decisions", "adapter": "decisions"},
        {"name": "fallback", "backend": "chat_completions", "model": "anthropic/claude-haiku-5.5", "provider": "openrouter", "protocol": "chat_completions", "adapter": "chat_completions"}]}
    assert job["classification"]["backend"] == "langgraph"  # the request as sent


def test_an_idempotency_key_returns_the_same_evaluation_and_refuses_a_different_request(bench):
    first = bench.create(idempotency_key="nightly-2026-11-06")
    again = bench.create(idempotency_key="nightly-2026-11-06")
    assert first.status_code == 201 and again.status_code == 200 and again.json()["evaluation_id"] == first.json()["evaluation_id"]
    different = bench.create(idempotency_key="nightly-2026-11-06", label="another")
    assert different.status_code == 409 and different.json()["error"]["code"] == "idempotency_conflict"
    assert bench.database["evaluations"].count_documents({}) == 1


def test_a_production_style_evaluation_runs_against_a_published_version(make_client, settings):
    bench = Workbench(make_client, settings, allow_preview_validation=False)
    published = published_version(bench.client)
    response = bench.client.post("/v1/evaluations", json={"dataset_id": bench.dataset["dataset_id"], "ruleset_id": published["ruleset_id"]})
    assert response.status_code == 201, response.text
    bench.drain()
    job = bench.get(response.json()["evaluation_id"])
    assert (job["status"], job["preview"], job["ruleset"]["status"], job["ruleset"]["publication_mode"]) == ("completed", False, "published", "production")


def test_a_replay_run_completes_with_the_cores_own_batch_result(make_client, settings, core_config):
    ids = ("RCP-013", "RCP-014", "RCP-001")
    bench = Workbench(make_client, settings, receipt_ids=ids)
    job = bench.run()
    assert (job["status"], job["result_context"], job["context_complete"], job["error"], job["stop_reason"]) == ("completed", "dataset", True, None, None)
    assert job["progress"] == {"total_receipts": 3, "processed_receipts": 3, "failed_receipts": 0, "percent": 100.0}
    assert job["started_at"] <= job["completed_at"] and job["attempts"] == 1 and job["lease"] is None
    core = ValidatorSettings.from_config(core_config, mode="replay", cache_dir=REPLAY_SETS["openrouter"])
    expected = ExpenseValidator(core.model_copy(update={"ruleset_path": REPO / "rules" / "ruleset.json"})).validate_batch([receipt(receipt_id) for receipt_id in sorted(ids)], context_complete=True)
    page = bench.client.get(f"/v1/evaluations/{job['evaluation_id']}/results").json()
    assert [item["receipt_id"] for item in page["items"]] == ["RCP-001", "RCP-013", "RCP-014"]  # dataset order
    assert {item["receipt_id"]: item["result"] for item in page["items"]} == {result.receipt_id: json.loads(result.model_dump_json()) for result in expected.results}


def test_replay_is_deterministic(bench, fixed_receipt_clock):
    first, second = bench.run(), bench.run()
    assert first["evaluation_id"] != second["evaluation_id"]
    assert bench.get(first["evaluation_id"], "/metrics")["operational"]["latency"] == {
        "receipts_timed": 2, "total_ms": 2, "mean_per_receipt_ms": 1,
    }
    assert bench.get(first["evaluation_id"], "/metrics") | {"evaluation_id": None} == bench.get(second["evaluation_id"], "/metrics") | {"evaluation_id": None}
    stored = lambda job: [(row["receipt_id"], row["result_sha256"]) for row in bench.database["evaluation_results"].find({"evaluation_id": job["evaluation_id"]}).sort("index")]
    assert stored(first) == stored(second)
    assert first["spend"] | {"by_model": None} == second["spend"] | {"by_model": None} and first["spend"]["new_spend_usd"] == "0"


def test_missing_replay_answers_fail_only_their_receipts_and_never_call_a_model(make_client, settings):
    bench = Workbench(make_client, settings, receipt_ids=("RCP-001", *LANGGRAPH_RECEIPTS))
    job = bench.run(worker={"client_factory": _never}, classification={"backend": "langgraph", "replay_set": "langgraph"})
    assert job["status"] == "completed" and job["progress"]["failed_receipts"] == 1 and job["error_summary"] == {"replay_recording_missing": 1}
    assert (job["result_context"], job["context_complete"]) == ("dataset", False)  # the others, together, with a receipt missing
    failed = bench.client.get(f"/v1/evaluations/{job['evaluation_id']}/results", params={"receipt_id": "RCP-001"}).json()["items"][0]
    assert failed["status"] == "error" and failed["error"]["code"] == "replay_recording_missing" and failed["result"] is None
    assert job["spend"]["calls"]["live"] == 0 and job["spend"]["new_spend_usd"] == "0"


def _never(core):
    raise AssertionError("replay must never build a provider client")


def test_a_queued_job_is_claimed_by_exactly_one_worker(bench):
    evaluation_id = bench.queue()
    jobs = EvaluationRepository(bench.database["evaluations"])
    moment = now()
    first = jobs.claim("worker-a", moment, moment + timedelta(seconds=60))
    assert first["_id"] == evaluation_id and first["status"] == "running" and first["lease"]["worker_id"] == "worker-a" and first["attempts"] == 1
    assert jobs.claim("worker-b", moment, moment + timedelta(seconds=60)) is None
    # A worker that does not hold the lease can change nothing.
    assert jobs.owned_update(evaluation_id, "worker-b", {"status": "completed"}) is None
    assert bench.get(evaluation_id)["status"] == "running"


def test_two_workers_take_different_jobs_oldest_first(bench):
    first, second = bench.queue(label="first"), bench.queue(label="second")
    jobs = EvaluationRepository(bench.database["evaluations"])
    moment = now()
    claimed = [jobs.claim(worker, moment, moment + timedelta(seconds=60))["_id"] for worker in ("worker-a", "worker-b")]
    assert claimed == [first, second]


def test_progress_is_reported_while_a_job_runs(make_client, settings):
    bench = Workbench(make_client, settings, receipt_ids=("RCP-001", "RCP-002", "RCP-003", "RCP-004"))
    evaluation_id = bench.queue()
    seen = []
    worker = Worker(bench.settings, bench.database)
    lease = worker._lease

    def watching(job):
        watched = lease(job)
        guard = watched.guard

        def observe():
            seen.append(bench.get(evaluation_id)["progress"]["processed_receipts"])
            guard()

        watched.guard = observe
        return watched

    worker._lease = watching
    worker.run_once()
    assert seen == [0, 1, 2, 3, 4]  # before each receipt, then before the dataset pass
    assert bench.get(evaluation_id)["progress"]["percent"] == 100.0


class _Crash(Exception):
    pass


def _crashing_after(worker: Worker, receipts: int):
    """Make the worker die, as a killed process would, before its receipts-th receipt."""
    lease = worker._lease

    def crashing(job):
        watched = lease(job)
        guard, calls = watched.guard, []

        def die():
            calls.append(1)
            if len(calls) > receipts:
                raise _Crash()
            guard()

        watched.guard = die
        return watched

    worker._lease = crashing


def test_a_replay_job_whose_worker_died_is_requeued_and_resumes_where_it_stopped(make_client, settings, fixed_receipt_clock):
    ids = ("RCP-001", "RCP-002", "RCP-003", "RCP-004")
    bench = Workbench(make_client, settings, receipt_ids=ids)
    evaluation_id = bench.queue()
    dying = Worker(bench.settings, bench.database, worker_id="dying")
    _crashing_after(dying, 2)
    with pytest.raises(_Crash):
        dying.run_once()
    stale = bench.get(evaluation_id)
    assert stale["status"] == "running" and stale["progress"]["processed_receipts"] == 2 and stale["lease"]["worker_id"] == "dying"
    stored = {row["receipt_id"]: row["created_at"] for row in bench.database["evaluation_results"].find({"evaluation_id": evaluation_id})}

    assert Worker(bench.settings, bench.database).recover_stale() == 0  # the lease has not expired yet
    later = lambda: now() + timedelta(seconds=bench.settings.worker_lease_seconds + 1)
    Worker(bench.settings, bench.database, worker_id="healthy", clock=later).run_once()

    job = bench.get(evaluation_id)
    assert (job["status"], job["attempts"], job["progress"]["processed_receipts"], job["result_context"]) == ("completed", 2, 4, "dataset")
    resumed = {row["receipt_id"]: row["created_at"] for row in bench.database["evaluation_results"].find({"evaluation_id": evaluation_id})}
    assert {receipt_id: resumed[receipt_id] for receipt_id in stored} == stored  # the stored receipts were not run again
    clean = bench.run()
    assert bench.get(evaluation_id, "/metrics")["operational"]["latency"] == {
        "receipts_timed": 4, "total_ms": 4, "mean_per_receipt_ms": 1,
    }
    assert bench.get(evaluation_id, "/metrics") | {"evaluation_id": None} == bench.get(clean["evaluation_id"], "/metrics") | {"evaluation_id": None}


def test_a_replay_job_lost_too_often_fails(make_client, settings):
    bench = Workbench(make_client, settings, max_evaluation_attempts=1)
    evaluation_id = bench.queue()
    dying = Worker(bench.settings, bench.database, worker_id="dying")
    _crashing_after(dying, 1)
    with pytest.raises(_Crash):
        dying.run_once()
    later = lambda: now() + timedelta(seconds=bench.settings.worker_lease_seconds + 1)
    assert Worker(bench.settings, bench.database, clock=later).recover_stale() == 1
    job = bench.get(evaluation_id)
    assert job["status"] == "failed" and job["error"]["code"] == "worker_lost" and job["progress"]["processed_receipts"] == 1
    assert bench.get(evaluation_id, "/metrics")["operational"]["evaluated"] == 1  # its completed work is kept


def test_a_live_job_whose_worker_died_is_never_retried(make_client, settings, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-never-sent")
    bench = Workbench(make_client, settings, allow_live=True, allowed_models=LIVE_MODELS, live_cache_dir=tmp_path / "live")
    evaluation_id = bench.queue(**LIVE_BODY)
    provider = FakeOpenRouter()
    dying = Worker(bench.settings, bench.database, worker_id="dying", client_factory=lambda core: provider)
    _crashing_after(dying, 2)  # the guard runs before the receipt and before each provider call: one call is paid
    with pytest.raises(_Crash):
        dying.run_once()
    paid = len(provider.calls)
    assert paid > 0
    later = lambda: now() + timedelta(seconds=bench.settings.worker_lease_seconds + 1)
    Worker(bench.settings, bench.database, clock=later, client_factory=lambda core: provider).run_once()
    job = bench.get(evaluation_id)
    assert job["status"] == "failed" and job["error"]["code"] == "worker_lost" and job["attempts"] == 1
    assert len(provider.calls) == paid  # nothing was paid for again
    assert job["spend"]["calls"]["live"] == paid  # and what was paid for is still accounted


def test_a_queued_job_is_cancelled_at_once(bench):
    evaluation_id = bench.queue()
    response = bench.client.post(f"/v1/evaluations/{evaluation_id}/cancel", json={})
    assert response.status_code == 200 and response.json()["status"] == "cancelled" and response.json()["stop_reason"] == "cancelled"
    assert bench.drain().run_once() is False  # nothing left to run
    again = bench.client.post(f"/v1/evaluations/{evaluation_id}/cancel", json={})
    assert again.status_code == 409 and again.json()["error"]["code"] == "not_cancellable"


def test_a_running_job_stops_before_its_next_receipt_and_keeps_its_work(make_client, settings):
    bench = Workbench(make_client, settings, receipt_ids=("RCP-001", "RCP-002", "RCP-003", "RCP-004"))
    evaluation_id = bench.queue()
    worker = Worker(bench.settings, bench.database)
    lease = worker._lease

    def cancelling(job):
        watched = lease(job)
        guard, calls = watched.guard, []

        def cancel_after_two():
            calls.append(1)
            if len(calls) == 3:
                response = bench.client.post(f"/v1/evaluations/{evaluation_id}/cancel", json={})
                assert response.json()["status"] == "cancel_requested"
            guard()

        watched.guard = cancel_after_two
        return watched

    worker._lease = cancelling
    worker.run_once()
    job = bench.get(evaluation_id)
    assert (job["status"], job["stop_reason"], job["progress"]["processed_receipts"], job["result_context"]) == ("cancelled", "cancelled", 2, "isolated_receipts")
    metrics = bench.get(evaluation_id, "/metrics")
    assert metrics["provisional"] is False and metrics["operational"]["evaluated"] == 2
    assert (metrics["operational"]["receipts"], metrics["operational"]["not_run"]) == (4, 2)  # not run is neither an error nor wrong
    assert (metrics["disposition"]["receipts"], metrics["disposition"]["execution_errors"]) == (2, 0)


def test_an_unknown_evaluation_is_not_found(bench):
    bench.queue()
    unknown = "eval_000000000000000000000000"
    for response in (bench.client.get(f"/v1/evaluations/{unknown}"),
                     bench.client.get(f"/v1/evaluations/{unknown}/results"),
                     bench.client.post(f"/v1/evaluations/{unknown}/cancel")):
        assert response.status_code == 404 and response.json()["error"]["code"] == "evaluation_not_found"
    assert bench.client.get("/v1/evaluations").json()["total"] == 1


def test_a_job_refuses_to_run_when_its_recorded_inputs_changed(bench):
    evaluation_id = bench.queue()
    bench.database["evaluations"].update_one({"_id": evaluation_id}, {"$set": {"replay.sha256": "0" * 64}})
    bench.drain()
    job = bench.get(evaluation_id)
    assert job["status"] == "failed" and job["error"]["code"] == "replay_set_changed" and job["progress"]["processed_receipts"] == 0


def test_metrics_are_provisional_until_the_job_finishes(bench):
    evaluation_id = bench.queue()
    metrics = bench.get(evaluation_id, "/metrics")
    assert metrics["provisional"] is True and metrics["operational"]["evaluated"] == 0 and metrics["classification"]["available"] is False


def test_a_dataset_saying_its_context_is_incomplete_leaves_cross_receipt_totals_open(make_client, settings):
    bench = Workbench(make_client, settings)
    lines = (REPO / "data" / "receipts.jsonl").read_text().splitlines()[:2]
    record = upload(bench.client, "\n".join(lines), name="partial-context", context_complete="false").json()
    job = bench.client.post("/v1/evaluations", json={"dataset_id": record["dataset_id"], "ruleset_id": bench.ruleset_id, "preview": True}).json()
    bench.drain()
    assert bench.get(job["evaluation_id"])["context_complete"] is False


@pytest.mark.parametrize("fields, budget, uncapped", [
    ({"budget": {"max_provider_calls": 5}}, {"max_spend_usd": None, "max_provider_calls": 5, "max_receipts": None}, False),
    ({"budget": {"max_spend_usd": "0.50", "max_receipts": 2}}, {"max_spend_usd": "0.50", "max_provider_calls": None, "max_receipts": 2}, False),
    ({"confirm_uncapped": True}, None, True),
], ids=["one limit", "two limits, dollars exact", "uncapped"])
def test_a_live_job_keeps_exactly_the_limits_it_was_given(make_client, settings, tmp_path, monkeypatch, fields, budget, uncapped):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-never-sent")
    bench = Workbench(make_client, settings, allow_live=True, allow_uncapped_evaluations=True, allowed_models=LIVE_MODELS, live_cache_dir=tmp_path / "live")
    job = bench.get(bench.queue(execution_mode="live", confirm_live=True, **fields))
    assert (job["budget"], job["live"]["uncapped"], job["status"]) == (budget, uncapped, "queued")


def test_a_request_made_before_optional_limits_keeps_its_idempotency_hash(make_client, settings, tmp_path, monkeypatch):
    """The fields added since (confirm_uncapped, and limits left out) are not part of an older request's hash."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-never-sent")
    bench = Workbench(make_client, settings, allow_live=True, allowed_models=LIVE_MODELS, live_cache_dir=tmp_path / "live")
    evaluation_id = bench.queue(idempotency_key="before-optional-limits", **LIVE_BODY)
    stored = bench.database["evaluations"].find_one({"_id": evaluation_id})["request_sha256"]
    older = {"dataset_id": bench.dataset["dataset_id"], "ruleset_id": bench.ruleset_id, "preview": True, "execution_mode": "live",
             "classification": {"backend": None, "model": None, "translation_model": None, "langgraph": None, "replay_set": None},
             "confirm_live": True, "budget": LIVE_BODY["budget"], "label": None}
    assert stored == sha256(canonical_json(older))


def test_an_evaluation_recorded_before_the_adapters_were_renamed_reads_and_replays_as_recorded(bench):
    """A job stored with backend jev, as every Jev evaluation was before the rename, is neither rewritten nor refused."""
    evaluation_id = bench.queue(label="recorded as jev", classification={"backend": "decisions", "model": "typesafe/jev-1.14", "replay_set": "jev"})
    # The record as the earlier API wrote it: the earlier name in the request, the plan and the snapshot.
    bench.database["evaluations"].update_one({"_id": evaluation_id}, {"$set": {
        "classification.backend": "jev", "configuration.classification_backend": "jev", "configuration.snapshot.classification_backend": "jev"}})
    bench.drain()
    job = bench.get(evaluation_id)
    assert job["status"] == "completed" and job["progress"]["failed_receipts"] == 0  # replayed from the stored snapshot
    assert (job["classification"]["backend"], job["configuration"]["classification_backend"], job["configuration"]["snapshot"]["classification_backend"]) == ("jev", "jev", "jev")
    assert job["configuration"]["setup"] == {"orchestrator": "direct", "provider": "openrouter", "backend": "jev", "protocol": "decisions", "adapter": "decisions", "model": "typesafe/jev-1.14", "nodes": None}
    # The same answers as a run recorded under the adapter's own name, from the same recordings.
    fresh = bench.run(label="recorded as decisions", classification={"backend": "decisions", "model": "typesafe/jev-1.14", "replay_set": "jev"})
    assert fresh["summary"] == job["summary"] and fresh["configuration"]["fingerprint"]["prompt_sha256"] == job["configuration"]["fingerprint"]["prompt_sha256"]
    for name in ("jev", "decisions"):
        listed = [item["evaluation_id"] for item in bench.client.get("/v1/evaluations", params={"backend": name}).json()["items"]]
        assert evaluation_id in listed and fresh["evaluation_id"] in listed


def test_each_role_is_checked_against_its_own_allowlist_and_unset_roles_keep_allowed_models(make_client, settings, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-never-sent")
    roles = {"allowed_classification_models": ["typesafe/jev-1.14"], "allowed_translation_models": ["tencent/hy-mt2-30b-a3b"]}
    bench = Workbench(make_client, settings, allow_live=True, live_cache_dir=tmp_path / "live", **roles)
    # Jev classifies and the configured translator translates: each allowed for its own role only.
    job = bench.get(bench.queue(classification={"backend": "decisions", "model": "typesafe/jev-1.14"}, **LIVE_BODY))
    assert job["configuration"]["models"] == ["tencent/hy-mt2-30b-a3b", "typesafe/jev-1.14"]
    # Without role lists, ALLOWED_MODELS still covers both roles, as before.
    legacy = Workbench(make_client, settings, allow_live=True, live_cache_dir=tmp_path / "legacy", allowed_models=["typesafe/jev-1.14", "tencent/hy-mt2-30b-a3b"])
    assert legacy.create(classification={"backend": "decisions", "model": "typesafe/jev-1.14"}, **LIVE_BODY).status_code == 201
