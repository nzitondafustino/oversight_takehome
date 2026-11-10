"""LangGraph reflection's retries through the API: the deployment's maximums, discovery, and structured feedback context.

The default context, reviewer_feedback, is every earlier run's, and keeps its identity; structured is a new identity.
"""

from conftest import create_ruleset, validate
from evaluation_support import LANGGRAPH_RECEIPTS, Workbench

REFLECT = {"enabled": True}
STRUCTURED = {"enabled": True, "feedback_context": "structured"}


def _never(core):
    raise AssertionError("replay must never build a provider client")


def test_discovery_reports_the_reflection_defaults_and_the_deployments_maximums(client):
    reflection = next(item for item in client.get("/v1/backends").json()["backends"] if item["id"] == "langgraph")["capabilities"]["reflection"]
    assert {key: reflection[key] for key in ("enabled", "max_retries", "max_total_calls_per_line", "feedback_context")} == {
        "enabled": False, "max_retries": 2, "max_total_calls_per_line": 6, "feedback_context": "reviewer_feedback"}
    assert reflection["limits"] == {"max_retries": 3, "max_total_calls_per_line": 12}
    assert reflection["feedback_contexts"] == ["reviewer_feedback", "structured"]
    assert reflection["structured_revisers"] == ["chat_completions"]  # the Decisions API adapter cannot revise at all


def test_the_maximums_themselves_are_allowed_and_zero_retries_is_valid(client):
    ruleset_id = create_ruleset(client)["ruleset_id"]
    for reflection in ({**STRUCTURED, "max_retries": 3, "max_total_calls_per_line": 12}, {**REFLECT, "max_retries": 0}):
        response = validate(client, ruleset_id, receipt_ids=("RCP-017",), classification={"backend": "langgraph", "replay_set": "langgraph", "langgraph": {"reflection": reflection}})
        assert response.status_code == 201, response.text


def test_a_tighter_deployment_maximum_refuses_what_config_yaml_alone_would_ask(make_client, settings):
    client = make_client(app_settings=settings.model_copy(update={"max_reflection_retries": 1, "max_reflection_calls_per_line": 4}))
    response = validate(client, create_ruleset(client)["ruleset_id"], classification={"backend": "langgraph", "replay_set": "langgraph", "langgraph": {"reflection": REFLECT}})
    # config.yaml's 2 retries and 6 calls are each over: both are named, and nothing runs.
    assert response.status_code == 422 and response.json()["error"]["code"] == "reflection_limit_exceeded"
    assert response.json()["error"]["details"] == {"limits": {"max_retries": 1, "max_total_calls_per_line": 4}, "requested": {"max_retries": 2, "max_total_calls_per_line": 6}}
    # Reflection off asks for no retries, so the maximums do not apply.
    plain = validate(client, create_ruleset(client)["ruleset_id"], receipt_ids=("RCP-017",), classification={"backend": "langgraph", "replay_set": "langgraph"})
    assert plain.status_code == 201, plain.text


def test_a_structured_validation_audits_its_stop_reason_and_limits(client):
    run = validate(client, create_ruleset(client)["ruleset_id"], receipt_ids=("RCP-017",), classification={"backend": "langgraph", "replay_set": "langgraph", "langgraph": {"reflection": STRUCTURED}}).json()
    for line in run["results"]["results"][0]["line_items"]:
        reflection = line["classification_details"]["orchestration"]["reflection"]
        assert {key: reflection[key] for key in ("feedback_context", "stop_reason", "max_retries", "max_calls", "unresolved")} == {
            "feedback_context": "structured", "stop_reason": "accepted", "max_retries": 2, "max_calls": 6, "unresolved": False}


def test_structured_context_is_a_new_identity_that_replays_from_the_same_recordings(make_client, settings):
    bench = Workbench(make_client, settings, receipt_ids=LANGGRAPH_RECEIPTS)
    jobs = {name: bench.run(worker={"client_factory": _never}, classification={"backend": "langgraph", "replay_set": "langgraph", "langgraph": {"reflection": reflection}})
            for name, reflection in (("default", REFLECT), ("structured", STRUCTURED))}
    for job in jobs.values():
        assert (job["status"], job["error"], job["progress"]["failed_receipts"]) == ("completed", None, 0)
        assert job["spend"]["calls"]["live"] == 0 and job["spend"]["new_spend_usd"] == "0"
    default, structured = (jobs[name]["configuration"] for name in ("default", "structured"))
    assert default["snapshot"]["langgraph"]["reflection"]["feedback_context"] == "reviewer_feedback"
    assert structured["snapshot"]["langgraph"]["reflection"]["feedback_context"] == "structured"
    assert default["fingerprint"]["prompt_sha256"] != structured["fingerprint"]["prompt_sha256"]

    def lines(job):
        page = bench.client.get(f"/v1/evaluations/{job['evaluation_id']}/results").json()["items"]
        return {line["line_id"]: (line["category"], line["confidence"], line["classification_details"]["orchestration"]) for row in page for line in row["result"]["line_items"]}

    before, after = lines(jobs["default"]), lines(jobs["structured"])
    # Both modes give every line the same category and the same verdicts. RCP-023's line is reviewed where it stands,
    # as confidence never routes a line: the reviewer objects, the reviser keeps other, and the objection is never
    # withdrawn, so both modes leave it unresolved, with its confidence withheld.
    assert {line: value[0] for line, value in before.items()} == {line: value[0] for line, value in after.items()}
    assert sorted(line for line in before if before[line][2]["reflection"]["unresolved"] != after[line][2]["reflection"]["unresolved"]) == []
    assert after["RCP-023-L1"][2]["reflection"]["stop_reason"] == "repeated_objection" and after["RCP-023-L1"][1] is None
    assert before["RCP-023-L1"][2]["reflection"]["unresolved"] is True and before["RCP-023-L1"][1] is None
