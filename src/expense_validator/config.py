"""Loads the project's settings: config.yaml for what is safe to commit, .env for secrets.

A real environment variable wins over .env, so CI can set the key without a file.
"""

import os
from pathlib import Path

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr

ROOT = Path(__file__).parents[2]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LlmConfig(_Strict):
    model: str | None = None
    url: str
    temperature: float = Field(ge=0)
    timeout_seconds: int = Field(gt=0)


class PathsConfig(_Strict):
    policy_docs: Path
    llm_cache: Path
    rule_logic: Path
    ruleset: Path
    rule_fields: Path


class Config(_Strict):
    llm: LlmConfig
    paths: PathsConfig
    openrouter_api_key: SecretStr | None = None


def load_config(root: Path = ROOT) -> Config:
    """Paths come back absolute, so the code works from any directory."""
    settings = yaml.safe_load((root / "config.yaml").read_text())
    config = Config(**settings, openrouter_api_key=_api_key(root))
    paths = PathsConfig(**{name: root / path for name, path in config.paths})
    return config.model_copy(update={"paths": paths})


def _api_key(root: Path) -> str | None:
    name = "OPENROUTER_API_KEY"
    return os.environ.get(name) or dotenv_values(root / ".env").get(name) or None
