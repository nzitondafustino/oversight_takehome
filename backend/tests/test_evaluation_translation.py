"""The translator of an evaluation: discovered, chosen per live run, used by the worker, recorded with the job, and
never a way around replay. No test reaches a provider."""

from evaluation_support import FakeOpenRouter, Workbench

DEFAULT = "tencent/hy-mt2-30b-a3b"  # config.yaml's translation.model
HAIKU, JEV = "anthropic/claude-haiku-5.5", "typesafe/jev-1.14"
LIVE = {"execution_mode": "live", "confirm_live": True, "budget": {"max_provider_calls": 100}}


def _live_bench(make_client, settings, tmp_path, monkeypatch, allowed, receipt_ids=("RCP-008",)):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-never-sent")
    return Workbench(make_client, settings, receipt_ids=receipt_ids, allow_live=True, allowed_models=allowed, live_cache_dir=tmp_path / "live")


def test_discovery_names_the_default_translator_its_allowlist_status_and_only_chat_alternatives(make_client, settings):
    translation = make_client().get("/v1/backends").json()["translation"]
    assert translation == {"enabled": True, "default_model": DEFAULT, "models": [{"model": DEFAULT, "provider": "openrouter", "default": True, "allowed": False}]}

    allowed = make_client(app_settings=settings.model_copy(update={"allowed_models": frozenset({HAIKU, JEV, DEFAULT})})).get("/v1/backends").json()["translation"]
    # Jev answers no chat completion, so it is never offered as a translator.
    # Each is a chat model through OpenRouter, chosen apart from the classification model.
    assert allowed["models"] == [{"model": HAIKU, "provider": "openrouter", "default": False, "allowed": True}, {"model": DEFAULT, "provider": "openrouter", "default": True, "allowed": True}]


def test_the_default_translator_is_resolved_from_config_and_recorded_with_the_job(make_client, settings, tmp_path, monkeypatch):
    bench = _live_bench(make_client, settings, tmp_path, monkeypatch, [JEV, DEFAULT])
    job = bench.get(bench.queue(classification={"backend": "jev", "model": JEV}, **LIVE))
    assert job["configuration"]["translation"] == {"enabled": True, "model": DEFAULT, "source": "config"}
    assert job["configuration"]["snapshot"]["translation_model"] == DEFAULT
    assert job["configuration"]["models"] == [DEFAULT, JEV]  # Jev classifies, the default translator translates
    assert job["classification"]["translation_model"] is None  # the request named none


def test_a_live_run_uses_the_translator_it_chose_and_records_where_it_came_from(make_client, settings, tmp_path, monkeypatch):
    bench = _live_bench(make_client, settings, tmp_path, monkeypatch, [JEV, HAIKU])  # the default translator is not listed
    provider = FakeOpenRouter()
    job = bench.run(worker={"client_factory": lambda core: provider}, classification={"backend": "jev", "model": JEV, "translation_model": HAIKU}, **LIVE)
    assert job["status"] == "completed", job.get("error")
    assert job["configuration"]["translation"] == {"enabled": True, "model": HAIKU, "source": "request"}
    assert job["configuration"]["models"] == [HAIKU, JEV]
    # The worker sent every translation to the chosen model, and none to config.yaml's.
    assert ("chat", HAIKU) in provider.calls and not any(model == DEFAULT for _, model in provider.calls)
    assert set(job["spend"]["by_purpose"]["translation"]["calls"].items()) >= {("live", 3)}
    assert set(job["spend"]["by_model"]) == {HAIKU, JEV}
    result = bench.get(job["evaluation_id"], "/results")["items"][0]["result"]
    assert result["metadata"]["translation_model"] == HAIKU


def test_replay_never_falls_back_to_a_live_call_for_a_translator_with_no_recordings(make_client, settings):
    """Recorded translations are keyed by model: another translator has none, so its non-English receipts fail."""
    bench = Workbench(make_client, settings, receipt_ids=("RCP-001", "RCP-008"))
    job = bench.run(classification={"replay_set": "openrouter", "translation_model": HAIKU})
    items = {item["receipt_id"]: item for item in bench.get(job["evaluation_id"], "/results?limit=10")["items"]}
    assert items["RCP-001"]["status"] == "ok"  # English lines need no translation
    assert (items["RCP-008"]["status"], items["RCP-008"]["error"]["code"]) == ("error", "replay_recording_missing")
    assert job["spend"]["calls"]["live"] == 0 and job["configuration"]["translation"]["source"] == "request"


def test_replay_with_the_recorded_translator_reads_its_recordings(make_client, settings):
    bench = Workbench(make_client, settings, receipt_ids=("RCP-008",))
    job = bench.run(classification={"replay_set": "openrouter"})
    item = bench.get(job["evaluation_id"], "/results")["items"][0]
    assert item["status"] == "ok" and item["result"]["metadata"]["translation_model"] == DEFAULT
    assert any(line["english_translation"] for line in item["result"]["line_items"])


def _compare(bench, *ids):
    response = bench.client.post("/v1/evaluations/compare", json={"evaluation_ids": list(ids)})
    assert response.status_code == 200, response.text
    return response.json()


def test_runs_with_the_same_translator_stay_directly_comparable(make_client, settings):
    bench = Workbench(make_client, settings)  # RCP-001 and RCP-002: English lines only, so no translation is needed
    first, second = (bench.run(classification={"replay_set": "openrouter"})["evaluation_id"] for _ in range(2))
    body = _compare(bench, first, second)
    assert body["comparability"]["directly_comparable"] is True and body["comparability"]["issue_codes"] == []
    assert [(row["translation_model"], row["translation_known"]) for row in body["evaluations"]] == [(DEFAULT, True)] * 2


def test_runs_with_different_effective_translators_are_not_directly_comparable(make_client, settings):
    bench = Workbench(make_client, settings)
    default = bench.run(classification={"replay_set": "openrouter"})["evaluation_id"]
    chosen = bench.run(classification={"replay_set": "openrouter", "translation_model": HAIKU})["evaluation_id"]
    body = _compare(bench, default, chosen)
    assert body["comparability"]["directly_comparable"] is False
    assert body["comparability"]["issue_codes"] == ["translation_model"]
    assert body["comparability"]["issues"] == [f"different translation models: {HAIKU}, {DEFAULT}"]
    assert [row["translation_model"] for row in body["evaluations"]] == [DEFAULT, HAIKU]


def test_an_older_run_is_compared_by_the_translator_its_snapshot_ran_with_and_never_assumed_when_unrecorded(make_client, settings):
    bench = Workbench(make_client, settings)
    first, second = (bench.run(classification={"replay_set": "openrouter"})["evaluation_id"] for _ in range(2))
    # Made before configuration.translation was recorded: its snapshot is still the configuration the worker ran with.
    bench.database["evaluations"].update_one({"_id": second}, {"$unset": {"configuration.translation": ""}})
    assert _compare(bench, first, second)["comparability"]["directly_comparable"] is True
    # With no record of its translator at all, it is never taken to match.
    bench.database["evaluations"].update_one({"_id": second}, {"$unset": {"configuration.snapshot.translation_model": "", "configuration.snapshot.translation_enabled": ""}})
    body = _compare(bench, first, second)
    assert body["comparability"]["issue_codes"] == ["translation_model_unknown"]
    assert [(row["translation_model"], row["translation_known"]) for row in body["evaluations"]] == [(DEFAULT, True), (None, False)]
