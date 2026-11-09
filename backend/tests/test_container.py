"""The image runs without the repository: no source checkout, bind mount or .env at runtime.

These read the Dockerfile, its ignore file and compose.yaml. The image itself was built and run as docs/USAGE.md says.
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = (ROOT / "backend" / "Dockerfile").read_text()
COMPOSE = yaml.safe_load((ROOT / "compose.yaml").read_text())
IGNORE = (ROOT / ".dockerignore").read_text().splitlines()
COPIED = re.findall(r"^COPY (\S+) (\S+)$", DOCKERFILE, re.MULTILINE)


def test_the_image_copies_runtime_assets_and_never_source():
    sources = {source for source, _ in COPIED}
    assert {"config.yaml", "rules/fields.yaml", "rules/rule_logic.yaml", "data/category-taxonomy.md", "data/policy-docs", "evaluation/compiler/live"} <= sources
    assert not [source for source in sources if source.startswith(("src", "backend")) or source.endswith(".py")]
    assert "rules/ruleset.json" not in sources  # no default ruleset to fall back to


def test_the_packages_are_installed_from_a_build_time_mount_only():
    assert re.search(r'RUN --mount=type=bind,source=\.,target=/build,rw \\\n\s+pip install "/build\[langgraph\]" /build/backend', DOCKERFILE)
    assert "--factory" in DOCKERFILE and "USER api" in DOCKERFILE


def test_no_secret_enters_the_build():
    assert [line for line in IGNORE if line and not line.startswith("#")][0] == "*"  # everything is excluded unless listed after
    admitted = [line[1:] for line in IGNORE if line.startswith("!")]
    assert not [path for path in admitted if ".env" in path or path.startswith(("tests", ".venv", ".git", ".cache", "data/receipts"))]


def test_compose_runs_the_api_its_worker_and_mongo_with_no_bind_mounted_source():
    assert set(COMPOSE["services"]) == {"api", "worker", "mongo", "frontend", "mongo-express"}
    api, worker = COMPOSE["services"]["api"], COMPOSE["services"]["worker"]
    # Only declared runtime settings enter the containers, rather than the entire local environment file.
    assert "env_file" not in api and "env_file" not in worker
    # The worker is the same image with the same settings, running the worker instead of the HTTP server.
    assert worker["build"] == api["build"] and worker["environment"] == api["environment"] and "ports" not in worker
    # The image's Dockerfile is the backend's own; the context is the repository root, as it holds the core too.
    assert api["build"] == {"context": ".", "dockerfile": "backend/Dockerfile"} and (ROOT / api["build"]["dockerfile"]).is_file()
    assert worker["command"] == ["python", "-m", "expense_api.worker"] and worker["healthcheck"] == {"disable": True}
    named = set(COMPOSE["volumes"])
    for service in COMPOSE["services"].values():
        for volume in service.get("volumes", []):
            assert volume.split(":")[0] in named, volume  # named volumes only: nothing from the host
    assert "ports" not in COMPOSE["services"]["mongo"] and COMPOSE["networks"]["internal"]["internal"] is True
    frontend = COMPOSE["services"]["frontend"]
    assert frontend["build"]["context"] == "./frontend"
    assert frontend["networks"] == ["public"]  # the browser-facing service cannot reach MongoDB
    assert frontend["environment"]["API_UPSTREAM"] == "http://api:8000"
