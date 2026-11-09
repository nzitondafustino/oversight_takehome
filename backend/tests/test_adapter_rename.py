"""Historical compatibility across the adapters' rename: openrouter is now chat_completions, and jev decisions.

The historical LangGraph evaluation is a real record, read from the API that stored it before the rename
(tests/fixtures/historical/langgraph_evaluation.json). It is never edited to fit new code: each test here checks new
code against what that API wrote. No test reaches a provider.
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest
from conftest import REPLAY_SETS, REPO
from evaluation_support import LANGGRAPH_RECEIPTS, Workbench, create, upload
from expense_validator import ExpenseValidator

from expense_api.services.evaluation_runner import fingerprint, from_snapshot, stored_fingerprint_version
from expense_api.services.spending import spend_report

HISTORICAL = json.loads((Path(__file__).parent / "fixtures" / "historical" / "langgraph_evaluation.json").read_text())
LEGACY_ORDER = {"backends": [{"name": "primary", "backend": "jev", "model": "typesafe/jev-1.14"}, {"name": "fallback", "backend": "openrouter", "model": "anthropic/claude-haiku-5.5"}]}
CANONICAL_ORDER = {"backends": [{"name": "primary", "backend": "decisions", "model": "typesafe/jev-1.14"}, {"name": "fallback", "backend": "chat_completions", "model": "anthropic/claude-haiku-5.5"}]}


HISTORICAL_DATASET = REPO / "backend" / "examples" / "langgraph-golden-5.yaml"  # what the historical run evaluated


def historical_job(bench, **classification) -> str:
    """Queue a job on the historical run's own dataset file, so its receipts and context are exactly that run's."""
    response = upload(bench.client, HISTORICAL_DATASET.read_bytes(), filename=HISTORICAL_DATASET.name)
    assert response.status_code in (200, 201), response.text
    assert response.json()["canonical_sha256"] == HISTORICAL["dataset"]["canonical_sha256"]
    queued = create(bench.client, response.json()["dataset_id"], bench.ruleset_id, classification=classification)
    assert queued.status_code == 201, queued.text
    return queued.json()["evaluation_id"]


def no_provider(core):
    raise AssertionError("a replay never builds a provider client")


def as_recorded_before_the_rename(bench, evaluation_id: str, record: dict) -> None:
    """Make a queued job hold what the API before the rename stored: its request, plan, snapshot and fingerprint."""
    configuration = record["configuration"]
    bench.database["evaluations"].update_one({"_id": evaluation_id}, {"$set": {
        "classification": record["classification"],
        **{f"configuration.{name}": configuration[name] for name in ("classification_backend", "model_label", "models", "snapshot", "fingerprint")},
    }})


# --- 1. Historical LangGraph configurations keep their fingerprints ---


def test_a_historical_langgraph_configuration_keeps_its_recorded_fingerprint(settings):
    record = HISTORICAL["configuration"]
    assert stored_fingerprint_version({"configuration": record}) == 1  # recorded before versions existed
    core = from_snapshot(settings, "replay", REPLAY_SETS["langgraph"], record["snapshot"], REPO / "rules" / "ruleset.json")
    validator = ExpenseValidator(core)
    # The stored snapshot is read as it is, earlier names and all, and the adapters it names are the renamed ones.
    assert [child.backend for child in core.langgraph.backends] == ["decisions", "chat_completions"] and core.langgraph.reflection.reviewer.backend == "chat_completions"
    assert fingerprint(core, validator, 1) == record["fingerprint"]  # exactly what was recorded, every field
    # The adapters' own names give a different hash: which is why a job is checked under the version it recorded.
    assert validator.prompt_sha256 != record["fingerprint"]["prompt_sha256"] and fingerprint(core, validator)["prompt_identity_version"] == 2


# --- 2 and 7. Historical recordings replay without a provider; a missing one never calls one ---


def test_a_historical_langgraph_evaluation_replays_as_recorded_without_a_provider(make_client, settings):
    bench = Workbench(make_client, settings, receipt_ids=LANGGRAPH_RECEIPTS)
    evaluation_id = historical_job(bench, backend="langgraph", replay_set="langgraph", langgraph={"reflection": {"enabled": True}})
    as_recorded_before_the_rename(bench, evaluation_id, HISTORICAL)
    bench.drain(client_factory=no_provider)
    job = bench.get(evaluation_id)
    assert (job["status"], job["error"], job["progress"]["failed_receipts"]) == ("completed", None, 0)
    assert {name: job["summary"][name] for name in HISTORICAL["summary"]} == HISTORICAL["summary"]  # scored before the summary held more
    # Stored as the earlier API wrote it, and still so: nothing is rewritten.
    stored = bench.database["evaluations"].find_one({"_id": evaluation_id})["configuration"]
    assert {name: stored[name] for name in ("classification_backend", "model_label", "snapshot", "fingerprint")} == {name: HISTORICAL["configuration"][name] for name in ("classification_backend", "model_label", "snapshot", "fingerprint")}
    # Every call replayed, none live, with the recorded history's cost, grouped by each adapter once.
    spend = job["spend"]
    assert (spend["calls"], spend["historical"], spend["new_spend_usd"]) == (HISTORICAL["spend"]["calls"], HISTORICAL["spend"]["historical"], "0")
    assert {name: totals["calls"]["total"] for name, totals in spend["by_backend"].items()} == {"decisions": HISTORICAL["spend"]["by_backend_calls"]["jev"], "chat_completions": HISTORICAL["spend"]["by_backend_calls"]["openrouter"]}
    assert job["configuration"]["setup"]["nodes"] == [
        {"name": "primary", "backend": "jev", "model": "typesafe/jev-1.14", "provider": "openrouter", "protocol": "decisions", "adapter": "decisions"},
        {"name": "fallback", "backend": "openrouter", "model": "anthropic/claude-haiku-5.5", "provider": "openrouter", "protocol": "chat_completions", "adapter": "chat_completions"}]


def test_a_historical_langgraph_evaluation_missing_recordings_never_calls_a_provider(make_client, settings):
    # The LLM's replay set holds none of Jev's answers: Jev's node has nothing to replay.
    bench = Workbench(make_client, settings, receipt_ids=LANGGRAPH_RECEIPTS)
    evaluation_id = historical_job(bench, backend="langgraph", replay_set="openrouter", langgraph={"reflection": {"enabled": True}})
    as_recorded_before_the_rename(bench, evaluation_id, HISTORICAL)
    bench.drain(client_factory=no_provider)
    job = bench.get(evaluation_id)
    assert job["status"] == "completed" and job["spend"]["calls"]["live"] == 0 and job["spend"]["new_spend_usd"] == "0"
    # Each receipt is a replay miss, never scored on the answers the LLM node gave in Jev's place.
    assert job["error_summary"] == {"replay_recording_missing": len(LANGGRAPH_RECEIPTS)}
    assert all(record["mode"] != "live" for record in bench.database["evaluation_usage"].find({"evaluation_id": evaluation_id}))


# --- 3 and 4. Earlier names run the same adapters; new executions record the adapters' own names ---


def test_earlier_and_canonical_names_queue_the_same_execution_and_record_canonical_names(make_client, settings):
    bench = Workbench(make_client, settings, receipt_ids=LANGGRAPH_RECEIPTS)
    jobs = {label: bench.get(bench.queue(label=label, classification={"backend": "langgraph", "replay_set": "langgraph", "langgraph": order})) for label, order in (("earlier", LEGACY_ORDER), ("canonical", CANONICAL_ORDER))}
    earlier, canonical = jobs["earlier"]["configuration"], jobs["canonical"]["configuration"]
    assert earlier["snapshot"] == canonical["snapshot"] and earlier["fingerprint"] == canonical["fingerprint"]
    assert [child["backend"] for child in canonical["snapshot"]["langgraph"]["backends"]] == ["decisions", "chat_completions"]
    assert canonical["model_label"] == "primary=decisions:typesafe/jev-1.14, fallback=chat_completions:anthropic/claude-haiku-5.5"
    assert canonical["fingerprint"]["prompt_identity_version"] == 2
    bench.drain(client_factory=no_provider)
    done = {label: bench.get(job["evaluation_id"]) for label, job in jobs.items()}
    assert done["earlier"]["summary"] == done["canonical"]["summary"] and done["earlier"]["spend"]["calls"] == done["canonical"]["spend"]["calls"]


# --- 5. Spending: earlier and canonical records aggregate as one adapter, each counted once ---


def _record(backend: str, model: str, *, mode: str = "live", cost: str | None = "0.0001", historical: str | None = None) -> dict:
    return {"mode": mode, "purpose": "classification", "backend": backend, "model": model, "success": True, "input_tokens": 10, "output_tokens": 2,
            "actual_cost_usd": cost if mode == "live" else None, "estimated_cost_usd": None, "historical_cost_usd": historical, "pricing_version": None}


def test_earlier_and_canonical_spending_records_aggregate_by_adapter_without_double_counting():
    records = [
        _record("jev", "typesafe/jev-1.14"), _record("decisions", "typesafe/jev-1.14"),
        _record("openrouter", "anthropic/claude-haiku-5.5"), _record("chat_completions", "anthropic/claude-haiku-5.5"),
        _record("jev", "typesafe/jev-1.14", mode="replayed", historical="0.00007"), _record("openrouter", "tencent/hy-mt2-30b-a3b", mode="replayed"),
    ]
    report = spend_report(records)
    assert report["calls"]["total"] == len(records) and report["new_spend_usd"] == "0.0004"
    by_backend = report["by_backend"]
    assert set(by_backend) == {"decisions", "chat_completions"}  # two protocols, never merged
    assert {name: (totals["calls"]["live"], totals["calls"]["replayed"], totals["new_spend_usd"]) for name, totals in by_backend.items()} == {"decisions": (2, 1, "0.0002"), "chat_completions": (2, 1, "0.0002")}
    assert by_backend["decisions"]["historical"]["cost_usd"] == "0.00007"
    assert sum(totals["calls"]["total"] for totals in by_backend.values()) == report["calls"]["total"]
    assert {name: totals["calls"]["total"] for name, totals in report["by_model"].items()} == {"typesafe/jev-1.14": 3, "anthropic/claude-haiku-5.5": 2, "tencent/hy-mt2-30b-a3b": 1}
    assert [record["backend"] for record in records][:2] == ["jev", "decisions"]  # the records keep their own names


def test_a_spending_report_stored_before_the_rename_reads_by_adapter_with_its_totals_unchanged(make_client, settings):
    bench = Workbench(make_client, settings, receipt_ids=("RCP-001", "RCP-008"))  # RCP-008 is French: translations too
    job = bench.run(classification={"backend": "decisions", "model": "typesafe/jev-1.14", "replay_set": "jev"})
    evaluation_id, before = job["evaluation_id"], job["spend"]
    assert set(before["by_backend"]) == {"decisions", "chat_completions"}  # Jev classifies; the translator chats
    earlier = {"decisions": "jev", "chat_completions": "openrouter"}
    # The usage records and the stored report as the earlier API wrote them, under the earlier names.
    for canonical, legacy in earlier.items():
        bench.database["evaluation_usage"].update_many({"evaluation_id": evaluation_id, "backend": canonical}, {"$set": {"backend": legacy}})
    bench.database["evaluations"].update_one({"_id": evaluation_id}, {"$set": {"spend.by_backend": {earlier[name]: totals for name, totals in before["by_backend"].items()}}})
    after = bench.get(evaluation_id)["spend"]
    assert set(after["by_backend"]) == {"decisions", "chat_completions"} and after["by_backend"] == before["by_backend"]
    assert {key: after[key] for key in ("calls", "new_spend_usd", "historical", "by_model", "by_purpose")} == {key: before[key] for key in ("calls", "new_spend_usd", "historical", "by_model", "by_purpose")}
    assert {record["backend"] for record in bench.database["evaluation_usage"].find({"evaluation_id": evaluation_id})} == {"jev", "openrouter"}  # never rewritten
    assert set(bench.database["evaluations"].find_one({"_id": evaluation_id})["spend"]["by_backend"]) == {"jev", "openrouter"}


# --- 6. Comparisons match runs on what ran, whichever names they recorded ---


def test_runs_before_and_after_the_rename_compare_as_the_same_classification(make_client, settings):
    bench = Workbench(make_client, settings)
    selection = {"model": "typesafe/jev-1.14", "replay_set": "jev"}
    earlier = bench.queue(label="before", classification={"backend": "decisions", **selection})
    # As stored before the rename: the earlier name, and a version 1 fingerprint, which for a direct run is the same hash.
    stored = bench.database["evaluations"].find_one({"_id": earlier})["configuration"]
    fingerprint_v1 = {name: value for name, value in stored["fingerprint"].items() if name != "prompt_identity_version"}
    bench.database["evaluations"].update_one({"_id": earlier}, {"$set": {"classification.backend": "jev", "configuration.classification_backend": "jev",
                                                                          "configuration.snapshot.classification_backend": "jev", "configuration.fingerprint": fingerprint_v1}})
    later = bench.queue(label="after", classification={"backend": "decisions", **selection})
    bench.drain(client_factory=no_provider)
    body = bench.client.post("/v1/evaluations/compare", json={"evaluation_ids": [earlier, later]}).json()
    before, after = body["evaluations"]
    assert body["comparability"]["directly_comparable"] is True
    assert (before["backend"], after["backend"]) == ("jev", "decisions")  # each as recorded
    assert before["classification_identity"] == after["classification_identity"] == "decisions:typesafe/jev-1.14"
    assert before["setup"]["adapter"] == after["setup"]["adapter"] == "decisions"
    assert before["category_accuracy"] == after["category_accuracy"] and before["provider_calls"] == after["provider_calls"]


def test_a_historical_langgraph_run_compares_by_its_canonical_identity(make_client, settings):
    bench = Workbench(make_client, settings, receipt_ids=LANGGRAPH_RECEIPTS)
    reflection = {"reflection": {"enabled": True}}
    earlier = historical_job(bench, backend="langgraph", replay_set="langgraph", langgraph=reflection)
    as_recorded_before_the_rename(bench, earlier, HISTORICAL)
    later = historical_job(bench, backend="langgraph", replay_set="langgraph", langgraph=reflection)
    bench.drain(client_factory=no_provider)
    before, after = bench.client.post("/v1/evaluations/compare", json={"evaluation_ids": [earlier, later]}).json()["evaluations"]
    assert before["model_label"] != after["model_label"]  # the labels each recorded name the adapters differently
    assert before["classification_identity"] == after["classification_identity"] == "primary=decisions:typesafe/jev-1.14, fallback=chat_completions:anthropic/claude-haiku-5.5"
    assert before["macro_f1"] == after["macro_f1"] and before["provider_calls"] == after["provider_calls"]


@pytest.mark.parametrize("version", [3, 0])
def test_an_unknown_fingerprint_version_is_refused_not_guessed(settings, version):
    core = from_snapshot(settings, "replay", REPLAY_SETS["langgraph"], HISTORICAL["configuration"]["snapshot"], REPO / "rules" / "ruleset.json")
    with pytest.raises(Exception, match="prompt identity version"):
        fingerprint(core, ExpenseValidator(core), version)
    assert Decimal(HISTORICAL["spend"]["historical"]["cost_usd"]) > 0
