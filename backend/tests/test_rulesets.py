"""Creating, reading, listing and versioning rulesets."""

import copy
import hashlib
import json

import pytest
from conftest import ARTIFACT, case_ids, create_ruleset, load_cases, validate
from expense_validator.policy.schema import RuleSet

from expense_api.repositories import RulesetRepository

CANONICAL = RuleSet.model_validate(ARTIFACT).to_json()
INVALID = load_cases("invalid_rulesets")


def _changed(case: dict) -> dict:
    artifact = copy.deepcopy(ARTIFACT)
    rules = {rule["rule_id"]: rule for rule in artifact["rules"]}
    for key in case.get("remove", []):
        del artifact[key]
    artifact.update(case.get("set", {}))
    if "drop_rule" in case:
        artifact["rules"] = [rule for rule in artifact["rules"] if rule["rule_id"] != case["drop_rule"]]
    if "duplicate_rule" in case:
        artifact["rules"].append(copy.deepcopy(rules[case["duplicate_rule"]]))
    if "rule" in case:
        rules[case["rule"]["rule_id"]].update(case["rule"]["set"])
    if "param" in case:
        rules[case["param"]["rule_id"]]["params"][case["param"]["name"]].update(case["param"]["set"])
    return artifact


def test_a_ruleset_is_stored_as_the_cores_canonical_artifact(client):
    created = create_ruleset(client)
    assert (created["version"], created["status"], created["published_at"]) == (1, "draft", None)
    assert created["artifact_sha256"] == hashlib.sha256(CANONICAL.encode()).hexdigest()
    assert created["artifact"] == json.loads(CANONICAL)
    assert (created["rule_count"], created["ruleset_version"], created["schema_version"]) == (len(ARTIFACT["rules"]), "v1", "1")
    assert created["source"] == {"kind": "upload", "description": None, "reference": "rules/ruleset.json"}

    fetched = client.get(f"/v1/rulesets/{created['ruleset_id']}")
    assert fetched.status_code == 200 and fetched.json() == created


def test_an_unknown_ruleset_is_not_found(client):
    create_ruleset(client)
    response = client.get("/v1/rulesets/rs_unknown")
    assert response.status_code == 404 and response.json()["error"]["code"] == "ruleset_not_found"
    assert client.get("/v1/rulesets").json()["total"] == 1


def test_listing_pages_through_the_rulesets_newest_first(client):
    made = [create_ruleset(client, name=name) for name in ("travel", "travel", "meals")]

    first = client.get("/v1/rulesets", params={"limit": 2}).json()
    second = client.get("/v1/rulesets", params={"limit": 2, "offset": 2}).json()
    assert (first["total"], first["limit"], first["offset"]) == (3, 2, 0)
    assert [item["ruleset_id"] for item in first["items"] + second["items"]] == [item["ruleset_id"] for item in reversed(made)]
    assert all("artifact" not in item for item in first["items"])

    travel = client.get("/v1/rulesets", params={"name": "travel"}).json()
    assert [item["version"] for item in travel["items"]] == [2, 1]


def test_listing_refuses_a_page_outside_its_bounds(client):
    for params in ({"limit": 0}, {"limit": 101}, {"offset": -1}):
        response = client.get("/v1/rulesets", params=params)
        assert response.status_code == 422 and response.json()["error"]["code"] == "invalid_request"


@pytest.mark.parametrize("case", INVALID, ids=case_ids(INVALID))
def test_an_invalid_ruleset_is_refused_with_structured_errors(client, case):
    response = client.post("/v1/rulesets", json={"name": "default", "artifact": _changed(case)})
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "invalid_ruleset"
    assert sorted({detail["stage"] for detail in error["details"]}) == sorted(case["stages"])
    if "rule_ids" in case:
        assert [detail["rule_id"] for detail in error["details"]] == case["rule_ids"]
    assert client.get("/v1/rulesets").json()["total"] == 0


def test_a_new_version_never_changes_the_one_a_run_used(client):
    first = create_ruleset(client)
    run = validate(client, first["ruleset_id"]).json()
    second = create_ruleset(client, artifact={**ARTIFACT, "ruleset_version": "v2"})

    assert (second["version"], second["ruleset_version"]) == (2, "v2")
    assert second["ruleset_id"] != first["ruleset_id"] and second["artifact_sha256"] != first["artifact_sha256"]
    assert client.get(f"/v1/rulesets/{first['ruleset_id']}").json() == first
    stored = client.get(f"/v1/runs/{run['run_id']}").json()
    assert stored["ruleset"] == {name: first[name] for name in ("ruleset_id", "name", "version", "status", "publication_mode", "artifact_sha256", "ruleset_version")}
    assert {result["metadata"]["ruleset_version"] for result in stored["results"]["results"]} == {"v1"}


def test_the_repository_offers_no_way_to_change_a_stored_artifact():
    assert not [name for name in vars(RulesetRepository) if name.startswith(("update", "replace", "delete", "save"))]


def test_a_stored_artifact_that_no_longer_matches_its_hash_is_refused(make_client):
    import mongomock

    mongo = mongomock.MongoClient(tz_aware=True)
    client = make_client(mongo=mongo)
    created = create_ruleset(client)
    mongo["expense_api"]["rulesets"].update_one({"_id": created["ruleset_id"]}, {"$set": {"artifact_json": CANONICAL.replace('"v1"', '"v9"')}})

    for response in (client.get(f"/v1/rulesets/{created['ruleset_id']}"), validate(client, created["ruleset_id"])):
        assert response.status_code == 500 and response.json()["error"]["code"] == "integrity_error"


def test_an_artifact_file_that_differs_from_the_database_is_rewritten(client, settings):
    created = create_ruleset(client)
    path = settings.artifact_dir / f"{created['artifact_sha256']}.json"
    assert validate(client, created["ruleset_id"]).status_code == 201
    path.write_text(path.read_text().replace('"v1"', '"v9"'))

    response = validate(client, created["ruleset_id"])
    assert response.status_code == 201 and path.read_text() == CANONICAL
    assert response.json()["results"]["results"][0]["provenance"]["ruleset_sha256"] == created["artifact_sha256"]


def test_a_threshold_keeps_every_digit_it_was_sent_with(client):
    # Sent as a JSON number, which standard decoding would round to a float.
    digits = "30.123456789012345678901"
    artifact = copy.deepcopy(ARTIFACT)
    next(rule for rule in artifact["rules"] if rule["rule_id"] == "ALC-02")["params"]["cap_usd"]["value"] = "__CAP__"
    body = json.dumps({"name": "precise", "artifact": artifact}).replace('"__CAP__"', digits)

    response = client.post("/v1/rulesets", content=body, headers={"content-type": "application/json"})
    assert response.status_code == 201, response.text
    cap = next(rule for rule in response.json()["artifact"]["rules"] if rule["rule_id"] == "ALC-02")["params"]["cap_usd"]
    assert cap["value"] == digits
