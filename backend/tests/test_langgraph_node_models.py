"""A model chosen for one LangGraph node, for one run: sent as the whole node list in classification.langgraph.backends.

Each node keeps its configured name and place, routing and reflection stay config.yaml's, and the deployment's own
configuration is never changed. Replay reads only the chosen model's recordings, as answers are keyed by model and prompt.
"""

import mongomock
from conftest import REPLAY_SETS, REPO, create_ruleset, receipt, validate
from evaluation_support import Workbench
from expense_validator import ExpenseValidator, ValidatorSettings


HAIKU, JEV = "anthropic/claude-haiku-5.5", "typesafe/jev-1.14"
SWAPPED = {"backends": [{"name": "primary", "backend": "chat_completions", "model": HAIKU}, {"name": "fallback", "backend": "decisions", "model": JEV}]}
CONFIGURED_NODES = [
    {"name": "primary", "backend": "decisions", "model": JEV, "provider": "openrouter", "protocol": "decisions", "adapter": "decisions"},
    {"name": "fallback", "backend": "chat_completions", "model": HAIKU, "provider": "openrouter", "protocol": "chat_completions", "adapter": "chat_completions"},
]
REFLECTION = {"enabled": True, "reviewer": {"backend": "chat_completions", "model": HAIKU}, "max_retries": 2, "max_total_calls_per_line": 6}


def _core(core_config, receipt_ids, replay_set, langgraph) -> dict:
    """The same batch straight through the core, against the same artifact."""
    settings = ValidatorSettings.from_config(core_config, mode="replay", cache_dir=REPLAY_SETS[replay_set])
    validator = ExpenseValidator(settings.model_copy(update={"ruleset_path": REPO / "rules" / "ruleset.json"}))
    return validator.validate_batch([receipt(receipt_id) for receipt_id in receipt_ids], context_complete=True, classification_backend="langgraph", langgraph_config=langgraph).model_dump(mode="json")


def _untimed(batch: dict) -> dict:
    return batch | {"results": [result | {"metadata": result["metadata"] | {"latency_ms": None}} for result in batch["results"]]}


def _attempts(run: dict) -> list[list[tuple]]:
    return [[(attempt["name"], attempt["backend"], attempt["model"], attempt["outcome"]) for attempt in line["classification_details"]["orchestration"]["attempts"]]
            for line in run["results"]["results"][0]["line_items"]]


def test_each_node_runs_the_model_chosen_for_it_from_that_models_recordings(client, core_config):
    # The openrouter set holds Haiku's answers for every receipt: with Haiku as primary, RCP-001 replays from them.
    choice = {"backend": "langgraph", "replay_set": "openrouter", "langgraph": SWAPPED}
    run = validate(client, create_ruleset(client)["ruleset_id"], classification=choice).json()
    assert run["results"] == _core(core_config, ("RCP-001",), "openrouter", SWAPPED)
    assert _attempts(run) == [[("primary", "chat_completions", HAIKU, "accepted")]] * 4  # confident: the fallback is never reached
    assert run["classification"]["langgraph"] == SWAPPED  # the request as sent
    # The setup is the run's own nodes, each with the adapter its model needs, under its configured name.
    assert run["setup"]["nodes"] == [
        {"name": "primary", "backend": "chat_completions", "model": HAIKU, "provider": "openrouter", "protocol": "chat_completions", "adapter": "chat_completions"},
        {"name": "fallback", "backend": "decisions", "model": JEV, "provider": "openrouter", "protocol": "decisions", "adapter": "decisions"},
    ]


def test_a_node_without_its_models_recording_routes_on_as_before_and_a_line_none_answers_is_refused(make_client, core_config):
    modes = []

    def build(settings):
        modes.append(settings.mode)
        return ExpenseValidator(settings)

    mongo = mongomock.MongoClient(tz_aware=True)
    client = make_client(build_validator=build, mongo=mongo)
    ruleset_id = create_ruleset(client)["ruleset_id"]
    # The jev set has no Haiku answers: each line's primary is a missing recording, audited, and Jev answers it.
    routed = validate(client, ruleset_id, classification={"backend": "langgraph", "replay_set": "jev", "langgraph": SWAPPED})
    assert routed.status_code == 201, routed.text
    # A missed recording still times the receipt, so its wall-clock latency is the one value that may differ.
    assert _untimed(routed.json()["results"]) == _untimed(_core(core_config, ("RCP-001",), "jev", SWAPPED))
    assert _attempts(routed.json()) == [[("primary", "chat_completions", HAIKU, "missing_recording"), ("fallback", "decisions", JEV, "accepted")]] * 4
    # The langgraph set holds neither model's answers for RCP-001: the existing replay miss, with nothing saved.
    missed = validate(client, ruleset_id, classification={"backend": "langgraph", "replay_set": "langgraph", "langgraph": SWAPPED})
    assert missed.status_code == 409 and missed.json()["error"]["code"] == "replay_recording_missing"
    assert missed.json()["error"]["details"] == {"missing_answers": 4}
    assert modes == ["replay", "replay"]  # a miss never builds a live validator
    assert mongo["expense_api"]["runs"].count_documents({}) == 1


def test_a_node_override_for_one_run_leaves_the_deployments_configuration_alone(client):
    before = client.get("/v1/backends").json()
    ruleset_id = create_ruleset(client)["ruleset_id"]
    assert validate(client, ruleset_id, classification={"backend": "langgraph", "replay_set": "openrouter", "langgraph": SWAPPED}).status_code == 201
    assert client.get("/v1/backends").json() == before
    assert [node["model"] for node in before["orchestrators"][1]["nodes"]] == [JEV, HAIKU]
    # The next run names no nodes, so it runs config.yaml's.
    plain = validate(client, ruleset_id, receipt_ids=("RCP-017",), classification={"backend": "langgraph", "replay_set": "langgraph"}).json()
    assert plain["classification"]["langgraph"] is None and plain["setup"]["nodes"] == CONFIGURED_NODES


def test_reflection_keeps_its_reviewer_when_no_chosen_node_can_revise(client, core_config):
    # Jev on both nodes: neither adapter can revise. The order is accepted, and reflection runs as the core defines it.
    nodes = [{"name": "primary", "backend": "decisions", "model": JEV}, {"name": "fallback", "backend": "decisions", "model": JEV}]
    langgraph = {"backends": nodes, "reflection": REFLECTION}
    response = validate(client, create_ruleset(client)["ruleset_id"], receipt_ids=("RCP-017",), classification={"backend": "langgraph", "replay_set": "langgraph", "langgraph": langgraph})
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["results"] == _core(core_config, ("RCP-017",), "langgraph", langgraph)
    review = run["results"]["results"][0]["line_items"][0]["classification_details"]["orchestration"]["reflection"]["reviews"][0]
    assert (review["reviewer"], review["model"], review["verdict"]) == ("chat_completions", HAIKU, "accept")


def test_an_evaluation_keeps_its_chosen_node_models_in_its_snapshot_and_identity(make_client, settings):
    bench = Workbench(make_client, settings, receipt_ids=("RCP-001", "RCP-014"))
    job = bench.run(worker={"client_factory": _never}, classification={"backend": "langgraph", "replay_set": "openrouter", "langgraph": SWAPPED})
    # The worker rebuilds the validator from the stored snapshot and refuses it as configuration_changed unless it
    # reproduces the stored fingerprint: completing proves the chosen order is the job's identity, run after run.
    assert (job["status"], job["error"], job["progress"]["failed_receipts"]) == ("completed", None, 0)
    configuration = job["configuration"]
    assert [(child["name"], child["backend"], child["model"]) for child in configuration["snapshot"]["langgraph"]["backends"]] == [("primary", "chat_completions", HAIKU), ("fallback", "decisions", JEV)]
    assert configuration["snapshot"]["langgraph"]["routing"] == {"fallback_on_error": True}
    assert configuration["snapshot"]["langgraph"]["reflection"]["enabled"] is False
    assert configuration["model_label"] == f"primary=chat_completions:{HAIKU}, fallback=decisions:{JEV}"
    assert configuration["fingerprint"]["prompt_identity_version"] == 2
    assert job["spend"]["calls"]["live"] == 0 and job["spend"]["new_spend_usd"] == "0"

    # A different order is a different identity; the configured one is the deployment's.
    plain = Workbench(make_client, settings, receipt_ids=("RCP-017",)).create(classification={"backend": "langgraph", "replay_set": "langgraph"}).json()
    assert plain["configuration"]["fingerprint"]["prompt_sha256"] != configuration["fingerprint"]["prompt_sha256"]
    assert plain["configuration"]["model_label"] == f"primary=decisions:{JEV}, fallback=chat_completions:{HAIKU}"


def _never(core):
    raise AssertionError("replay must never build a provider client")



def test_an_evaluation_never_scores_a_later_nodes_answer_given_in_place_of_a_missing_recording(make_client, settings):
    # The jev set lacks Haiku's answers. Validation lets Jev answer for the primary node; an evaluation fails the
    # receipt as a replay miss instead, so no metric credits Haiku with Jev's answers.
    bench = Workbench(make_client, settings, receipt_ids=("RCP-001", "RCP-014"))
    job = bench.run(worker={"client_factory": _never}, classification={"backend": "langgraph", "replay_set": "jev", "langgraph": SWAPPED})
    assert (job["status"], job["progress"]["failed_receipts"], job["error_summary"]) == ("completed", 2, {"replay_recording_missing": 2})
    rows = bench.client.get(f"/v1/evaluations/{job['evaluation_id']}/results").json()["items"]
    assert [(row["status"], row["error"]["code"], row["result"]) for row in rows] == [("error", "replay_recording_missing", None)] * 2
    assert rows[0]["error"]["message"] == "4 model answers are not in the recordings"
    assert job["spend"]["calls"]["live"] == 0 and job["spend"]["new_spend_usd"] == "0"
