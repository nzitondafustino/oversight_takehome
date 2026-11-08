"""Live inference needs the deployment's switch and the request's execution_mode. No test calls a provider.

Where a live run must succeed, a stand-in validator answers from the recordings while reporting live mode, so
the API's handling of a live request is real and no paid model is reached.
"""

import httpx
from conftest import REPLAY_SETS, create_ruleset, validate
from expense_validator import ExpenseValidator


class _RecordedLive:
    """A live-mode validator that answers from the recordings instead of a provider."""

    def __init__(self, settings):
        assert settings.mode == "live" and settings.ruleset_path.name.endswith(".json")
        self.replay = ExpenseValidator(settings.model_copy(update={"mode": "replay", "cache_dir": REPLAY_SETS["openrouter"], "openrouter_api_key": None}))

    def validate_batch(self, *args, **kwargs):
        return self.replay.validate_batch(*args, **kwargs).model_copy(update={"mode": "live"})


class _FailingProvider:
    def __init__(self, settings):
        pass

    def validate_batch(self, *args, **kwargs):
        raise httpx.ConnectError("connection refused by https://openrouter.ai with key sk-or-secret")


def _live(settings, **changes):
    return settings.model_copy(update={"allow_live": True, **changes})


def test_live_is_refused_when_the_deployment_does_not_allow_it(make_client):
    built = []
    client = make_client(build_validator=lambda settings: built.append(settings) or ExpenseValidator(settings))
    response = validate(client, create_ruleset(client)["ruleset_id"], execution_mode="live")
    assert response.status_code == 403 and response.json()["error"]["code"] == "live_inference_disabled"
    assert built == []


def test_an_allowed_live_request_runs_in_live_mode(make_client, settings):
    client = make_client(app_settings=_live(settings), build_validator=_RecordedLive)
    run = validate(client, create_ruleset(client)["ruleset_id"], execution_mode="live").json()
    assert run["execution_mode"] == "live" and run["results"]["mode"] == "live"
    assert run["classification"]["replay_set"] is None


def test_live_without_a_key_is_unavailable_and_calls_nothing(make_client, settings):
    client = make_client(app_settings=_live(settings))  # the real validator, with no OPENROUTER_API_KEY anywhere
    response = validate(client, create_ruleset(client)["ruleset_id"], execution_mode="live")
    assert response.status_code == 503 and response.json()["error"]["code"] == "live_inference_unavailable"
    assert "OPENROUTER_API_KEY" in response.json()["error"]["message"]


def test_a_live_request_may_choose_only_listed_models(make_client, settings):
    unlisted = make_client(app_settings=_live(settings), build_validator=_RecordedLive)
    ruleset_id = create_ruleset(unlisted)["ruleset_id"]
    for classification in ({"model": "anthropic/claude-haiku-5.5"}, {"translation_model": "tencent/hy-mt2-30b-a3b"}, {"backend": "langgraph", "langgraph": {"backends": [{"name": "only", "backend": "openrouter", "model": "anthropic/claude-haiku-5.5"}]}}):
        response = validate(unlisted, ruleset_id, execution_mode="live", classification=classification)
        assert response.status_code == 422 and response.json()["error"]["code"] == "model_not_allowed", classification

    listed = make_client(app_settings=_live(settings, allowed_models=frozenset({"anthropic/claude-haiku-5.5"})), build_validator=_RecordedLive)
    response = validate(listed, create_ruleset(listed)["ruleset_id"], execution_mode="live", classification={"model": "anthropic/claude-haiku-5.5"})
    assert response.status_code == 201, response.text


def test_a_listed_set_of_models_also_bounds_replay(make_client, settings):
    client = make_client(app_settings=settings.model_copy(update={"allowed_models": frozenset({"typesafe/jev-1.14"})}))
    response = validate(client, create_ruleset(client)["ruleset_id"], classification={"model": "anthropic/claude-opus-5.5"})
    assert response.status_code == 422 and response.json()["error"]["code"] == "model_not_allowed"


def test_a_provider_failure_is_a_502_that_reveals_nothing(make_client, settings):
    client = make_client(app_settings=_live(settings), build_validator=_FailingProvider)
    response = validate(client, create_ruleset(client)["ruleset_id"], execution_mode="live")
    assert response.status_code == 502 and response.json() == {"error": {"code": "provider_failure", "message": "a model provider failed", "details": None}}
