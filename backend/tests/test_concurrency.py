"""Concurrent requests with different ruleset versions, backends and modes share nothing."""

from concurrent.futures import ThreadPoolExecutor

from conftest import create_ruleset, validate

LANGGRAPH_ORDER = "primary=decisions:typesafe/jev-1.14, fallback=chat_completions:anthropic/claude-haiku-5.5"
REQUESTS = [  # the ruleset version to use, by label, receipt, classification, the model label and backend each receipt must report
    ("first", "RCP-001", {}, "anthropic/claude-haiku-5.5", "chat_completions"),
    # jev, the decisions adapter's earlier name, runs that adapter, and the receipt reports it by its own name.
    ("second", "RCP-001", {"backend": "jev", "model": "typesafe/jev-1.14", "replay_set": "jev"}, "typesafe/jev-1.14", "decisions"),
    ("third", "RCP-017", {"backend": "langgraph", "replay_set": "langgraph"}, LANGGRAPH_ORDER, "langgraph"),
    ("first", "RCP-022", {"backend": "langgraph", "replay_set": "langgraph", "langgraph": {"routing": {"min_confidence": 0.5}}}, LANGGRAPH_ORDER, "langgraph"),
]


def test_concurrent_runs_keep_their_own_backend_and_ruleset(client):
    rulesets = {label: create_ruleset(client)["ruleset_id"] for label in {request[0] for request in REQUESTS}}

    def run(request):
        label, receipt_id, classification, _, _ = request
        return validate(client, rulesets[label], receipt_ids=(receipt_id,), classification=classification)

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(run, REQUESTS * 3))

    for (label, receipt_id, classification, model, backend), response in zip(REQUESTS * 3, responses):
        assert response.status_code == 201, response.text
        run_ = response.json()
        result = run_["results"]["results"][0]
        assert (run_["ruleset"]["ruleset_id"], run_["classification"]["backend"]) == (rulesets[label], classification.get("backend"))
        assert result["receipt_id"] == receipt_id
        assert (result["metadata"]["model"], result["provenance"]["classification_backend"]) == (model, backend)
    assert len({response.json()["run_id"] for response in responses}) == len(responses)


def test_concurrent_versions_of_one_name_get_distinct_numbers(client):
    with ThreadPoolExecutor(max_workers=4) as pool:
        created = list(pool.map(lambda _: create_ruleset(client, name="shared"), range(4)))
    assert sorted(ruleset["version"] for ruleset in created) == [1, 2, 3, 4]
