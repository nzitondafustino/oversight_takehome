"""GET /v1/backends and GET /v1/taxonomy: read from the installed core and this deployment, never from a provider."""

import hashlib

import yaml
from conftest import REPO, compile_version, step, validate
from expense_validator.classification.backends.registry import ADAPTERS, BACKENDS, REVIEWERS, REVISERS, backends_for
from expense_validator.classification.core.common import load_taxonomy
from expense_validator.config import LANGGRAPH


def _backends(client):
    response = client.get("/v1/backends")
    assert response.status_code == 200, response.text
    return response.json(), {backend["id"]: backend for backend in response.json()["backends"]}


def test_the_backends_are_the_cores_registry_and_its_orchestrator(client):
    body, backends = _backends(client)
    assert list(backends) == [*BACKENDS, LANGGRAPH]
    assert body["default_backend"] == "chat_completions" and backends["chat_completions"]["default"] and backends["chat_completions"]["default_model"] == "anthropic/claude-haiku-5.5"
    assert {name: backend["legacy_ids"] for name, backend in backends.items()} == {"chat_completions": ["openrouter"], "decisions": ["jev"], LANGGRAPH: []}
    for name, backend in backends.items():
        assert backend["capabilities"]["review"] is (name in REVIEWERS) and backend["capabilities"]["revise"] is (name in REVISERS)
        assert backend["installed"] and backend["enabled"] and backend["available"] and backend["replay"]["supported"]
    order = backends[LANGGRAPH]["capabilities"]["order"]
    assert [(item["backend"], item["model"]) for item in order] == [("decisions", "typesafe/jev-1.14"), ("chat_completions", "anthropic/claude-haiku-5.5")]
    assert backends[LANGGRAPH]["capabilities"]["reflection"]["enabled"] is False


def test_live_is_off_by_default_and_a_key_alone_is_not_availability(make_client, settings, monkeypatch):
    body, backends = _backends(make_client())
    assert body["execution_modes"] == {"default": "replay", "replay": True, "live": False, "deterministic": True}
    assert all(backend["live"] == {"permitted": False, "credential_configured": False, "available": False} for backend in backends.values())

    allowed = make_client(app_settings=settings.model_copy(update={"allow_live": True}))
    _, backends = _backends(allowed)
    assert backends["decisions"]["live"] == {"permitted": True, "credential_configured": False, "available": False}  # no key: missing credentials

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-discovery-secret")
    response = allowed.get("/v1/backends")
    assert "sk-or-discovery-secret" not in response.text
    assert {backend["id"]: backend["live"]["available"] for backend in response.json()["backends"]} == {"chat_completions": True, "decisions": True, LANGGRAPH: True}
    assert response.json()["credential_configured"] is True


def test_a_backend_the_deployment_leaves_out_is_installed_but_not_enabled(make_client, settings):
    client = make_client(app_settings=settings.model_copy(update={"allowed_backends": frozenset({"chat_completions"}), "allow_live": True}))
    _, backends = _backends(client)
    assert (backends["decisions"]["installed"], backends["decisions"]["enabled"], backends["decisions"]["available"], backends["decisions"]["live"]["permitted"]) == (True, False, False, False)
    assert backends["chat_completions"]["enabled"] is True


def test_each_backend_lists_only_the_allowed_models_its_own_check_accepts(make_client, settings):
    client = make_client(app_settings=settings.model_copy(update={"allowed_models": frozenset({"anthropic/claude-haiku-5.5", "typesafe/jev-1.14"})}))
    body, backends = _backends(client)
    assert (backends["chat_completions"]["allowed_models"], backends["decisions"]["allowed_models"]) == (["anthropic/claude-haiku-5.5"], ["typesafe/jev-1.14"])
    assert body["allowed_models"] == ["anthropic/claude-haiku-5.5", "typesafe/jev-1.14"]


def test_replay_sets_show_where_a_backend_has_answers(client):
    _, backends = _backends(client)
    jev = {item["name"]: item for item in backends["decisions"]["replay"]["sets"]}
    assert jev["jev"]["present"] and jev["jev"]["answer_files"] > 0 and jev["jev"]["folder"] == "jev"
    assert (jev["openrouter"]["present"], jev["openrouter"]["answer_files"]) == (False, 0)  # the LLM set holds no Jev answers
    assert {item["name"] for item in backends["chat_completions"]["replay"]["sets"]} == {"openrouter", "jev", "langgraph"}


def test_backends_without_replay_sets_say_replay_has_nothing_to_read(make_client, settings):
    _, backends = _backends(make_client(app_settings=settings.model_copy(update={"replay_sets": {}, "default_replay_set": None})))
    assert all(backend["replay"]["sets"] == [] for backend in backends.values())


def test_the_taxonomy_is_the_cores_catalog_exactly(client):
    response = client.get("/v1/taxonomy")
    assert response.status_code == 200, response.text
    body = response.json()
    fields_text = (REPO / "rules" / "fields.yaml").read_text()
    catalog = yaml.safe_load(fields_text)
    taxonomy = load_taxonomy(REPO / "data" / "category-taxonomy.md")
    assert body["catalog"] == {"path": "fields.yaml", "sha256": hashlib.sha256(fields_text.encode()).hexdigest(), "input_contract_version": catalog["input_contract"]["version"]}
    assert body["taxonomy"]["sha256"] == hashlib.sha256((REPO / "data" / "category-taxonomy.md").read_bytes()).hexdigest()
    assert [category["id"] for category in body["categories"]] == catalog["item.category"]["values"]
    assert {category["id"]: category["restricted"] for category in body["categories"]} == {name: taxonomy[name].restricted for name in catalog["item.category"]["values"]}
    assert body["entities"] == [{"id": name, "countries": catalog["receipt.entity"]["countries"][name]} for name in catalog["receipt.entity"]["values"]]
    assert body["countries"] == [{"id": code, "home_currency": currency} for code, currency in catalog["receipt.employee_country"]["home_currency"].items()]
    assert body["expense_types"] == catalog["receipt.submitted_expense_type"]["values"]
    assert body["currencies"] == catalog["input_contract"]["minor_units"]
    assert body["vocabularies"] == {name: field["values"] for name, field in catalog.items() if isinstance(field, dict) and "values" in field}
    assert body["fields"]["receipt.total_amount_usd"] == {"type": "number", "unit": "usd"}


def test_a_missing_catalog_is_unavailable_not_empty(make_client, settings, tmp_path):
    config = yaml.safe_load(settings.core_config.read_text())
    config["paths"]["rule_fields"] = str(tmp_path / "missing.yaml")
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(config))
    client = make_client(app_settings=settings.model_copy(update={"core_config": tmp_path / "config.yaml"}))
    response = client.get("/v1/taxonomy")
    assert (response.status_code, response.json()["error"]["code"]) == (503, "taxonomy_unavailable")


def test_an_unreadable_core_configuration_is_unavailable(make_client, settings, tmp_path):
    (tmp_path / "config.yaml").write_text("classification: [not, a, mapping]\n")
    client = make_client(app_settings=settings.model_copy(update={"core_config": tmp_path / "config.yaml"}))
    for path in ("/v1/backends", "/v1/taxonomy"):
        response = client.get(path)
        assert (response.status_code, response.json()["error"]["code"]) == (503, "core_configuration_unavailable"), path


def test_the_policy_compiler_lists_its_recording_sets_by_name_only(client):
    body, _ = _backends(client)
    response = client.get("/v1/backends")
    assert body["policy_compiler"]["replay"] == {"supported": True, "sets": [
        {"name": "fixtures", "present": True, "live_target": False},
        {"name": "live", "present": True, "live_target": False},
    ]}
    assert str(REPO) not in response.text  # names, never paths


def test_live_extraction_needs_the_switch_a_recording_set_and_a_key(make_client, settings, monkeypatch, tmp_path):
    body, _ = _backends(make_client())
    assert body["policy_compiler"]["live"] == {"permitted": False, "configured": False, "credential_configured": False, "available": False}

    sets = {**settings.compiler_recording_sets, "recorded": tmp_path / "recorded"}  # not made yet: present is false
    allowed = make_client(app_settings=settings.model_copy(update={"allow_live": True, "compiler_recording_sets": sets, "compiler_live_recording_set": "recorded"}))
    compiler = _backends(allowed)[0]["policy_compiler"]
    assert compiler["live"] == {"permitted": True, "configured": True, "credential_configured": False, "available": False}
    assert {item["name"]: (item["present"], item["live_target"]) for item in compiler["replay"]["sets"]}["recorded"] == (False, True)

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-compiler-secret")
    response = allowed.get("/v1/backends")
    assert "sk-or-compiler-secret" not in response.text
    assert response.json()["policy_compiler"]["live"]["available"] is True

    unconfigured = make_client(app_settings=settings.model_copy(update={"allow_live": True}))
    assert _backends(unconfigured)[0]["policy_compiler"]["live"] == {"permitted": True, "configured": False, "credential_configured": True, "available": False}


def test_without_recording_sets_the_policy_compiler_has_nothing_to_replay(make_client, settings):
    body, _ = _backends(make_client(app_settings=settings.model_copy(update={"compiler_recording_sets": {}})))
    assert body["policy_compiler"]["replay"] == {"supported": False, "sets": []}


def test_ruleset_permissions_are_the_deployments_settings(make_client, settings):
    body, _ = _backends(make_client(app_settings=settings.model_copy(update={"allow_preview_validation": False})))
    assert body["rulesets"] == {"preview_allowed": False, "publication_modes": ["production"]}

    both = settings.model_copy(update={"allow_preview_validation": True, "allowed_publication_modes": frozenset({"simulation", "production"})})
    body, _ = _backends(make_client(app_settings=both))
    assert body["rulesets"] == {"preview_allowed": True, "publication_modes": ["production", "simulation"]}


def test_uncapped_evaluations_are_off_unless_the_deployment_allows_them(make_client, settings):
    assert _backends(make_client())[0]["evaluations"] == {"uncapped_allowed": False}
    assert _backends(make_client(app_settings=settings.model_copy(update={"allow_uncapped_evaluations": True})))[0]["evaluations"] == {"uncapped_allowed": True}


def test_ruleset_permissions_say_what_validation_and_approval_then_enforce(make_client, settings):
    client = make_client(app_settings=settings.model_copy(update={"allow_preview_validation": False}))
    permissions = _backends(client)[0]["rulesets"]
    version = compile_version(client).json()
    assert permissions["preview_allowed"] is False
    response = validate(client, version["ruleset_id"], preview=True)
    assert (response.status_code, response.json()["error"]["code"]) == (403, "preview_disabled")

    assert "simulation" not in permissions["publication_modes"]
    for name in ("submit", "accept"):
        assert step(client, version, name).status_code == 200
    refused = step(client, version, "approve", mode="simulation")
    assert (refused.status_code, refused.json()["error"]["code"]) == (422, "publication_mode_not_allowed")


def test_orchestrators_providers_adapters_and_models_are_kept_apart(make_client, settings):
    allowed = frozenset({"anthropic/claude-haiku-5.5", "typesafe/jev-1.14"})
    body, backends = _backends(make_client(app_settings=settings.model_copy(update={"allowed_models": allowed})))
    # Each registered backend is an execution adapter with a provider; LangGraph is an orchestrator with neither.
    assert {name: (item["kind"], item["provider"], item["protocol"]) for name, item in backends.items()} == {
        **{name: ("adapter", adapter.provider, adapter.protocol) for name, adapter in ADAPTERS.items()}, LANGGRAPH: ("orchestrator", None, None)}
    # Jev is a model through OpenRouter, never a provider of its own.
    assert [(item["id"], item["adapters"]) for item in body["providers"]] == [("openrouter", list(BACKENDS))]
    assert [item["id"] for item in body["orchestrators"]] == ["direct", LANGGRAPH]
    direct, langgraph = body["orchestrators"]
    assert direct["default"] and direct["nodes"] is None and not langgraph["default"]
    assert [(node["name"], node["provider"], node["backend"], node["model"]) for node in langgraph["nodes"]] == [
        ("primary", "openrouter", "decisions", "typesafe/jev-1.14"), ("fallback", "openrouter", "chat_completions", "anthropic/claude-haiku-5.5")]
    # Every model is offered with the one adapter its own ID needs, as the core decides.
    models = {item["model"]: item for item in body["classification_models"]}
    assert set(models) == set(allowed)
    for model, item in models.items():
        assert [item["backend"]] == backends_for(model) and item["provider"] == "openrouter" and item["allowed"]
    assert models["typesafe/jev-1.14"]["protocol"] == "decisions" and models["typesafe/jev-1.14"]["sources"] == ["langgraph:primary", "allowed"]
    assert models["anthropic/claude-haiku-5.5"]["default"] and models["anthropic/claude-haiku-5.5"]["capabilities"]["review"]
    assert all(item["provider"] == "openrouter" for item in body["translation"]["models"])


def test_a_model_no_adapter_runs_is_not_offered(make_client, settings):
    body, _ = _backends(make_client(app_settings=settings.model_copy(update={"allowed_models": frozenset({"~typesafe/jev-latest"})})))
    assert "~typesafe/jev-latest" not in {item["model"] for item in body["classification_models"]}


def test_classification_and_translation_models_are_offered_from_their_own_allowlists(make_client, settings):
    roles = {"allowed_classification_models": frozenset({"anthropic/claude-haiku-5.5", "typesafe/jev-1.14"}), "allowed_translation_models": frozenset({"tencent/hy-mt2-30b-a3b"})}
    body, backends = _backends(make_client(app_settings=settings.model_copy(update=roles)))
    assert (body["allowed_classification_models"], body["allowed_translation_models"]) == (["anthropic/claude-haiku-5.5", "typesafe/jev-1.14"], ["tencent/hy-mt2-30b-a3b"])
    # The translator is never offered as a classifier, nor a classifier as a translator.
    assert {item["model"] for item in body["classification_models"]} == {"anthropic/claude-haiku-5.5", "typesafe/jev-1.14"}
    assert [(item["model"], item["allowed"]) for item in body["translation"]["models"]] == [("tencent/hy-mt2-30b-a3b", True)]
    assert backends["chat_completions"]["allowed_models"] == ["anthropic/claude-haiku-5.5"]
    # Without role lists, ALLOWED_MODELS serves both, as before they existed.
    body, _ = _backends(make_client(app_settings=settings.model_copy(update={"allowed_models": frozenset({"anthropic/claude-haiku-5.5", "tencent/hy-mt2-30b-a3b"})})))
    assert body["allowed_classification_models"] == body["allowed_translation_models"] == ["anthropic/claude-haiku-5.5", "tencent/hy-mt2-30b-a3b"]


def test_earlier_adapter_names_in_the_deployment_allowlist_name_the_adapters(make_client, settings):
    _, backends = _backends(make_client(app_settings=settings.model_validate({**settings.model_dump(), "allowed_backends": ["openrouter", "langgraph"]})))
    assert (backends["chat_completions"]["enabled"], backends["decisions"]["enabled"], backends[LANGGRAPH]["enabled"]) == (True, False, True)
