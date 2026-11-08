"""POST /v1/validate and GET /v1/runs/{run_id}, through the real validator and the recorded answers."""

import mongomock
import pytest
from conftest import REPLAY_SETS, REPO, case_ids, create_ruleset, load_cases, receipt, validate
from expense_validator import ExpenseValidator, ValidatorSettings
from pydantic import SecretStr

CASES = load_cases("validation_requests")


def _core_result(core_config, receipt_ids, replay_set="openrouter", **choices) -> dict:
    """The same batch straight through the core, against the same artifact."""
    settings = ValidatorSettings.from_config(core_config, mode="replay", cache_dir=REPLAY_SETS[replay_set])
    validator = ExpenseValidator(settings.model_copy(update={"ruleset_path": REPO / "rules" / "ruleset.json"}))
    return validator.validate_batch([receipt(receipt_id) for receipt_id in receipt_ids], context_complete=True, **choices).model_dump(mode="json")


@pytest.mark.parametrize("case", CASES, ids=case_ids(CASES))
def test_a_refused_request_is_an_error_and_persists_nothing(make_client, case):
    mongo = mongomock.MongoClient(tz_aware=True)
    client = make_client(mongo=mongo)
    ruleset_id = create_ruleset(client)["ruleset_id"]
    if "body" in case:
        response = client.post("/v1/validate", content=case["body"], headers={"content-type": "application/json"})
    else:
        body = {"ruleset_id": ruleset_id, "receipts": [receipt("RCP-001")], "context_complete": True, "preview": True}
        body["receipts"][0].update(case.get("receipt", {}))
        body["receipts"][0]["line_items"][0].update(case.get("line", {}))
        body.update(case.get("request", {}))
        for name in case.get("remove", []):
            del body[name]
        response = client.post("/v1/validate", json=body)
    assert (response.status_code, response.json()["error"]["code"]) == (case["expect"]["status"], case["expect"]["code"]), response.text
    if "details" in case["expect"]:
        assert response.json()["error"]["details"] == case["expect"]["details"]
    assert mongo["expense_api"]["runs"].count_documents({}) == 0


def test_a_run_holds_the_cores_result_unchanged(client, core_config):
    ruleset = create_ruleset(client)
    response = validate(client, ruleset["ruleset_id"], receipt_ids=("RCP-014", "RCP-001"))
    assert response.status_code == 201, response.text
    run = response.json()

    assert run["results"] == _core_result(core_config, ("RCP-014", "RCP-001"))
    assert (run["status"], run["execution_mode"], run["context_complete"]) == ("completed", "replay", True)
    assert "customer_id" not in run and "customer_id" not in run["results"]
    assert run["classification"] == {"backend": None, "model": None, "translation_model": None, "langgraph": None, "replay_set": "openrouter"}
    assert run["ruleset"]["ruleset_id"] == ruleset["ruleset_id"] and run["ruleset"]["version"] == 1
    assert [[line["line_id"] for line in result["line_items"]] for result in run["results"]["results"]] == [
        [line["line_id"] for line in receipt(receipt_id)["line_items"]] for receipt_id in ("RCP-001", "RCP-014")
    ]
    flagged = run["results"]["results"][1]
    assert flagged["disposition"] == "flag" and flagged["exceptions"]  # findings and their evidence, as the core gave them
    assert run["execution"]["receipt_count"] == 2 and run["execution"]["completed_at"] >= run["execution"]["started_at"]
    assert response.headers["location"] == f"/v1/runs/{run['run_id']}"

    stored = client.get(f"/v1/runs/{run['run_id']}")
    assert stored.status_code == 200 and stored.json() == run


def test_uncertainty_signals_and_open_questions_are_kept_as_the_core_gave_them(client, core_config):
    # RCP-013's gift card names no recipient type: EXC-03 asks for review and GIFT-04 stays open.
    run = validate(client, create_ruleset(client)["ruleset_id"], receipt_ids=("RCP-013",)).json()
    assert run["results"] == _core_result(core_config, ("RCP-013",))
    result = run["results"]["results"][0]
    assert "EXC-03" in [signal["rule_id"] for signal in result["review_signals"]]
    assert {item["reason"] for item in result["uncertainty"]} == {"secondary_field_uncertain"}
    assert [question["rule_id"] for question in result["open_questions"]] == ["GIFT-04"]


def test_an_unknown_run_or_ruleset_is_not_found(client):
    ruleset = create_ruleset(client)
    validate(client, ruleset["ruleset_id"])
    unknown = validate(client, "rs_unknown")
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "ruleset_not_found"
    response = client.get("/v1/runs/run_000000000000000000000000")
    assert response.status_code == 404 and response.json()["error"]["code"] == "run_not_found"


def test_replay_is_the_default_and_a_miss_never_builds_a_live_validator(make_client):
    modes = []

    def build(settings):
        modes.append(settings.mode)
        return ExpenseValidator(settings)

    client = make_client(build_validator=build)
    ruleset_id = create_ruleset(client)["ruleset_id"]
    unrecorded = receipt("RCP-001")
    unrecorded["line_items"][0]["description_raw"] = "Something nobody recorded"

    assert validate(client, ruleset_id).json()["execution_mode"] == "replay"
    missed = client.post("/v1/validate", json={"ruleset_id": ruleset_id, "receipts": [unrecorded], "context_complete": True, "preview": True})
    assert missed.status_code == 409 and missed.json()["error"]["details"] == {"missing_answers": 1}
    assert "Something nobody recorded" not in missed.text
    assert modes == ["replay", "replay"]


def test_jev_replays_from_its_own_recordings(client, core_config):
    choice = {"backend": "jev", "model": "typesafe/jev-1.14", "replay_set": "jev"}
    run = validate(client, create_ruleset(client)["ruleset_id"], classification=choice).json()
    assert run["results"] == _core_result(core_config, ("RCP-001",), replay_set="jev", classification_backend="jev", classification_model="typesafe/jev-1.14")
    assert run["results"]["results"][0]["provenance"]["classification_backend"] == "decisions"  # requested as jev, its earlier name


def test_langgraph_keeps_its_routing_and_reflection_audit(client, core_config):
    ruleset_id = create_ruleset(client)["ruleset_id"]
    reflection = {"reflection": {"enabled": True, "reviewer": {"backend": "openrouter", "model": "anthropic/claude-haiku-5.5"}, "max_retries": 2, "max_total_calls_per_line": 6}}
    run = validate(client, ruleset_id, receipt_ids=("RCP-017",), classification={"backend": "langgraph", "replay_set": "langgraph", "langgraph": reflection}).json()

    assert run["results"] == _core_result(core_config, ("RCP-017",), replay_set="langgraph", classification_backend="langgraph", langgraph_config=reflection)
    audit = run["results"]["results"][0]["line_items"][0]["classification_details"]["orchestration"]
    assert audit["attempts"] and "reflection" in audit
    assert run["classification"]["langgraph"] == reflection
    # The orchestrator, with config.yaml's nodes as the core labelled them: each its own provider, adapter and model.
    assert run["setup"] == {"orchestrator": "langgraph", "provider": None, "backend": None, "protocol": None, "adapter": None, "model": None, "nodes": [
        {"name": "primary", "backend": "decisions", "model": "typesafe/jev-1.14", "provider": "openrouter", "protocol": "decisions", "adapter": "decisions"},
        {"name": "fallback", "backend": "chat_completions", "model": "anthropic/claude-haiku-5.5", "provider": "openrouter", "protocol": "chat_completions", "adapter": "chat_completions"}]}


def test_a_request_without_context_completeness_is_a_complete_context(client):
    ruleset_id = create_ruleset(client)["ruleset_id"]
    body = {"ruleset_id": ruleset_id, "receipts": [receipt("RCP-001")], "preview": True}
    run = client.post("/v1/validate", json=body).json()
    assert run["context_complete"] is True and run["results"]["context_complete"] is True


def test_a_partial_context_is_passed_on_and_recorded(client):
    ruleset_id = create_ruleset(client)["ruleset_id"]
    run = validate(client, ruleset_id, context_complete=False).json()
    assert run["context_complete"] is False and run["results"]["context_complete"] is False
    assert run["results"]["results"][0]["provenance"]["context_complete"] is False


def test_deterministic_mode_takes_already_classified_receipts(client):
    classified = receipt("RCP-001")
    for line, category in zip(classified["line_items"], ("food_beverage", "food_beverage", "food_beverage", "service_fee")):
        line.update(category=category, restricted=False, confidence=0.95)
    body = {"ruleset_id": create_ruleset(client)["ruleset_id"], "receipts": [classified], "context_complete": True, "execution_mode": "deterministic", "preview": True}

    run = client.post("/v1/validate", json=body).json()
    assert run["execution_mode"] == "deterministic" and run["classification"]["replay_set"] is None
    result = run["results"]["results"][0]
    assert result["disposition"] == "pass" and result["metadata"]["model"] is None


def test_a_run_record_holds_no_credentials(make_client, settings, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-never-stored")
    mongo = mongomock.MongoClient(tz_aware=True)
    client = make_client(app_settings=settings.model_copy(update={"mongo_uri": SecretStr("mongodb://user:db-password@mongo:27017")}), mongo=mongo)
    validate(client, create_ruleset(client)["ruleset_id"])

    stored = str(list(mongo["expense_api"]["runs"].find())) + str(list(mongo["expense_api"]["rulesets"].find()))
    assert "sk-or-never-stored" not in stored and "db-password" not in stored


def test_a_result_from_any_other_ruleset_is_refused_and_not_saved(make_client, tmp_path):
    # A validator that quietly read another ruleset, such as the repository's default, must never pass as this run.
    other = tmp_path / "other.json"
    other.write_text((REPO / "rules" / "ruleset.json").read_text().replace('"ruleset_version": "v1"', '"ruleset_version": "v9"'))
    mongo = mongomock.MongoClient(tz_aware=True)
    client = make_client(mongo=mongo, build_validator=lambda settings: ExpenseValidator(settings.model_copy(update={"ruleset_path": other})))

    response = validate(client, create_ruleset(client)["ruleset_id"])
    assert response.status_code == 500 and response.json()["error"]["code"] == "integrity_error"
    assert mongo["expense_api"]["runs"].count_documents({}) == 0
