"""Startup, the documented API and its schema, and the error shape."""

import mongomock

from expense_api.config import Settings

PATHS = {
    "/health": {"get"},
    "/v1/rulesets": {"get", "post"},
    "/v1/rulesets/{ruleset_id}": {"get"},
    "/v1/rulesets/{ruleset_id}/comparisons": {"get"},
    **{f"/v1/rulesets/{{ruleset_id}}/{step}": {"post"} for step in ("submit", "review", "approve", "publish")},
    "/v1/documents": {"get", "post"},
    "/v1/documents/{document_id}": {"get"},
    "/v1/documents/{document_id}/revisions": {"post"},
    "/v1/documents/extractions": {"get", "post"},
    "/v1/documents/extractions/{extraction_id}": {"get"},
    "/v1/documents/extractions/{extraction_id}/comparisons": {"get"},
    "/v1/documents/extractions/{extraction_id}/review": {"post"},
    "/v1/validate": {"post"},
    "/v1/runs": {"get"},
    "/v1/runs/{run_id}": {"get"},
    "/v1/runs/{run_id}/findings": {"get"},
    "/v1/runs/{run_id}/export": {"get"},
    "/v1/backends": {"get"},
    "/v1/taxonomy": {"get"},
    "/v1/evaluations": {"get", "post"},
    "/v1/evaluations/datasets": {"get", "post"},
    "/v1/evaluations/datasets/{dataset_id}": {"get"},
    "/v1/evaluations/compare": {"post"},
    "/v1/evaluations/{evaluation_id}": {"get"},
    **{f"/v1/evaluations/{{evaluation_id}}/{part}": {"get"} for part in ("metrics", "results", "export")},
    "/v1/evaluations/{evaluation_id}/cancel": {"post"},
}
NOT_CREDENTIALS = {"idempotency_key"}  # names a request, as an HTTP Idempotency-Key does: no secret


def test_startup_creates_the_indexes(make_client):
    mongo = mongomock.MongoClient(tz_aware=True)
    make_client(mongo=mongo)
    rulesets = mongo["expense_api"]["rulesets"].index_information()
    assert rulesets["name_version"]["unique"] is True and rulesets["name_version"]["key"] == [("name", 1), ("version", 1)]
    assert {"created", "name_status", "artifact_sha256"} <= set(rulesets)
    runs = mongo["expense_api"]["runs"].index_information()
    assert {"ruleset_id", "created", "created_id", "ruleset_created", "ruleset_version_created", "backend_created", "mode_created"} <= set(runs)
    assert runs["created_id"]["key"] == [("created_at", -1), ("_id", -1)]
    documents = mongo["expense_api"]["documents"].index_information()
    assert documents["document_revision"]["unique"] is True and {"created", "filename"} <= set(documents)
    assert {"created", "extraction_sha256", "documents"} <= set(mongo["expense_api"]["extractions"].index_information())
    assert "extraction_created" in mongo["expense_api"]["extraction_reviews"].index_information()
    # No index reads a customer: none is kept on any record.
    for name in ("rulesets", "runs", "documents", "extractions", "extraction_reviews", "evaluation_datasets", "evaluations"):
        assert not any("customer_id" in dict(index["key"]) for index in mongo["expense_api"][name].index_information().values()), name
    database = mongo["expense_api"]
    assert database["evaluation_datasets"].index_information()["name_version"]["unique"] is True
    evaluations = database["evaluations"].index_information()
    assert evaluations["idempotency"]["unique"] is True and {"queue", "leases", "created_id"} <= set(evaluations)
    assert database["evaluation_results"].index_information()["evaluation_index"]["unique"] is True
    assert "evaluation_sequence" in database["evaluation_usage"].index_information()


def test_the_endpoints_are_documented(client):
    schema = client.get("/openapi.json").json()
    assert {path: set(methods) for path, methods in schema["paths"].items()} == PATHS
    assert client.get("/docs").status_code == 200


def test_request_bodies_refuse_unknown_fields_and_take_no_credentials(client):
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    for name in ("ValidationRequest", "ClassificationSelection", "RulesetCreate", "RulesetSource", "CompileSource", "TransitionRequest", "ReviewRequest", "ApproveRequest", "PublishRequest", "ExtractionSource",
                 "DocumentCreate", "RevisionCreate", "DocumentSource", "ExtractionCreate", "RevisionRef", "RecordedWith", "ReviewCreate",
                 "EvaluationCreate", "Budget", "CompareRequest"):
        assert schemas[name]["additionalProperties"] is False, name
        assert not [field for field in schemas[name]["properties"] if field not in NOT_CREDENTIALS and any(word in field for word in ("key", "secret", "token", "password", "credential"))], name
    assert schemas["EvaluationCreate"]["properties"]["execution_mode"]["default"] == "replay"
    assert schemas["ValidationRequest"]["properties"]["execution_mode"]["default"] == "replay"
    assert "context_complete" not in schemas["ValidationRequest"]["required"]
    assert schemas["ValidationRequest"]["properties"]["context_complete"]["default"] is True


def test_every_error_has_one_shape(client):
    for response in (client.get("/v1/nowhere"), client.post("/v1/validate", json={}), client.delete("/v1/rulesets")):
        assert set(response.json()) == {"error"} and set(response.json()["error"]) == {"code", "message", "details"}
    assert client.get("/v1/nowhere").json()["error"]["code"] == "not_found"


def test_live_is_off_and_replay_is_named_by_default(core_config, monkeypatch):
    for name in ("EXPENSE_API_ALLOW_LIVE", "EXPENSE_API_ALLOWED_MODELS", "EXPENSE_API_REPLAY_SETS"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(core_config=core_config)
    assert settings.allow_live is False and settings.allowed_models == frozenset() and settings.replay_sets == {}


def test_settings_read_the_environment(core_config, monkeypatch):
    monkeypatch.setenv("EXPENSE_API_REPLAY_SETS", '{"openrouter": "/srv/replay/openrouter"}')
    monkeypatch.setenv("EXPENSE_API_DEFAULT_REPLAY_SET", "openrouter")
    monkeypatch.setenv("EXPENSE_API_ALLOWED_MODELS", '["anthropic/claude-haiku-5.5"]')
    settings = Settings(core_config=core_config)
    assert str(settings.replay_sets["openrouter"]) == "/srv/replay/openrouter"
    assert settings.allowed_models == frozenset({"anthropic/claude-haiku-5.5"})
