"""Where settings and secrets come from, and that the key cannot leak through a log line."""

import os

import pytest
from pydantic import ValidationError

from expense_validator.config import ROOT, load_config

SETTINGS = """
llm:
  model: some/model
  url: https://example.test/chat
  temperature: 0
  timeout_seconds: 30
paths:
  policy_docs: docs
  taxonomy: taxonomy.md
  llm_cache: cache
  rule_logic: logic.yaml
  ruleset: ruleset.json
  rule_fields: fields.yaml
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    (tmp_path / "config.yaml").write_text(SETTINGS)
    return tmp_path


def test_settings_come_from_the_config_file(project):
    config = load_config(project)
    assert config.llm.model == "some/model"
    assert config.llm.url == "https://example.test/chat"
    assert config.llm.timeout_seconds == 30


def test_paths_are_relative_to_the_project_root(project):
    config = load_config(project)
    assert config.paths.policy_docs == project / "docs"
    assert config.paths.llm_cache == project / "cache"


def test_key_comes_from_the_env_file(project):
    (project / ".env").write_text("OPENROUTER_API_KEY=from-file\n")
    assert load_config(project).openrouter_api_key.get_secret_value() == "from-file"


def test_a_real_environment_variable_wins(project, monkeypatch):
    (project / ".env").write_text("OPENROUTER_API_KEY=from-file\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "from-environment")
    assert load_config(project).openrouter_api_key.get_secret_value() == "from-environment"


@pytest.mark.parametrize("env_file", [None, "OPENROUTER_API_KEY=\n", "# only a comment\n"])
def test_no_key_is_none(project, env_file):
    if env_file is not None:
        (project / ".env").write_text(env_file)
    assert load_config(project).openrouter_api_key is None


def test_the_key_stays_out_of_printed_config(project):
    (project / ".env").write_text("OPENROUTER_API_KEY=very-secret\n")
    config = load_config(project)
    assert "very-secret" not in repr(config)
    assert "very-secret" not in str(config)
    assert "very-secret" not in config.model_dump_json()


def test_loading_does_not_change_the_environment(project, monkeypatch):
    (project / ".env").write_text("OPENROUTER_API_KEY=from-file\nOTHER_VALUE=x\n")
    load_config(project)
    assert "OTHER_VALUE" not in os.environ


@pytest.mark.parametrize(
    "settings",
    [
        SETTINGS + "unknown: true\n",
        SETTINGS.replace("  temperature: 0", "  temperature: 0\n  tempreture: 1"),
        SETTINGS.replace("  url: https://example.test/chat\n", ""),
        SETTINGS.replace("timeout_seconds: 30", "timeout_seconds: 0"),
        SETTINGS.replace("temperature: 0", "temperature: -1"),
    ],
)
def test_bad_settings_are_refused(project, settings):
    (project / "config.yaml").write_text(settings)
    with pytest.raises(ValidationError):
        load_config(project)


def test_the_real_config_file_loads():
    config = load_config(ROOT)
    assert config.paths.policy_docs.is_dir()
    assert config.llm.url.startswith("https://openrouter.ai/")
