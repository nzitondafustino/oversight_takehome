"""Helpers for the evaluation tests: datasets from the project's receipts, a worker, and a stand-in provider.

FakeOpenRouter answers the core's own live requests with fixed, valid answers and the usage a test chooses, so
the live path, the metering and the budgets are real while no provider is reached.
"""

import json
from decimal import Decimal
from pathlib import Path

import httpx
import mongomock
from conftest import REPLAY_SETS, REPO, create_ruleset

from expense_api.config import Settings
from expense_api.worker import Worker

GOLDEN = REPO / "data" / "receipts.jsonl"
LANGGRAPH_RECEIPTS = ("RCP-011", "RCP-017", "RCP-022", "RCP-023", "RCP-028")  # what the langgraph replay set covers
JEV_RECORDING = next((REPLAY_SETS["jev"] / "jev").glob("*.txt")).read_text()
TRANSLATION_PROMPT_END = "without any additional explanation:"


def golden_lines(receipt_ids=None) -> list[str]:
    """The project's labelled receipts as JSONL lines, in file order: the same format as data/receipts.jsonl."""
    lines = GOLDEN.read_text().splitlines()
    return [line for line in lines if receipt_ids is None or json.loads(line)["receipt_id"] in receipt_ids]


def upload(client, content: bytes | str, filename: str = "golden.jsonl", **form):
    data = {**({"name": "golden"} if "name" not in form and filename.endswith(".jsonl") else {}), **form}
    content = content.encode() if isinstance(content, str) else content
    return client.post("/v1/evaluations/datasets", data={key: value for key, value in data.items() if value is not None}, files={"file": (filename, content)})


def dataset(client, receipt_ids=None, name: str = "golden") -> dict:
    response = upload(client, "\n".join(golden_lines(receipt_ids)) + "\n", name=name)
    assert response.status_code in (200, 201), response.text
    return response.json()


def create(client, dataset_id: str, ruleset_id: str, **fields):
    body = {"dataset_id": dataset_id, "ruleset_id": ruleset_id, "preview": True, **fields}
    return client.post("/v1/evaluations", json=body)


def drain(settings, mongo, **worker) -> Worker:
    """Run every queued job to its end with one worker."""
    runner = Worker(settings, mongo["expense_api"], **worker)
    while runner.run_once():
        pass
    return runner


def get(client, evaluation_id: str, path: str = "") -> dict:
    response = client.get(f"/v1/evaluations/{evaluation_id}{path}")
    assert response.status_code == 200, response.text
    return response.json()


class _Response:
    def __init__(self, body: dict):
        self.body = body
        content = body.get("choices", [{}])[0].get("message", {}).get("content")
        self.choices = [type("Choice", (), {"message": type("Message", (), {"content": content})(), "finish_reason": "stop"})()]
        self.usage, self.model, self.id = None, body.get("model"), body.get("id")

    def model_dump(self, **_):
        return self.body

    def model_dump_json(self, **_):
        return json.dumps(self.body)


class FakeOpenRouter:
    """Stands in for openrouter.OpenRouter. usage maps a model to the usage its responses report; fail lists
    models whose requests raise. Every request is kept in calls."""

    CLASSIFICATION = {"category": "food_beverage", "confidence": 0.9, "recipient_name": None, "recipient_type": None, "venue_type": None, "item_kind": None, "essential_toiletry": None}

    def __init__(self, usage: dict | None = None, fail: tuple = ()):
        self.usage, self.fail, self.calls = usage or {}, fail, []
        self.chat = type("Chat", (), {"send": self._chat})()
        self.alpha = type("Alpha", (), {})()
        self.alpha.decisions = type("Decisions", (), {"create": self._decide})()

    def _usage(self, model: str) -> dict:
        return dict(self.usage.get(model, {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.0001}))

    def _chat(self, *, model, messages, **_):
        self.calls.append(("chat", model))
        if model in self.fail:
            raise httpx.ConnectError("connection refused")
        content = messages[-1]["content"]
        if len(messages) == 1:  # translation: one user message, the prompt then the text
            answer = content.split(TRANSLATION_PROMPT_END, 1)[1].strip()
        else:
            answer = json.dumps(self.CLASSIFICATION)
        return _Response({"id": f"gen-{len(self.calls)}", "model": model, "choices": [{"message": {"content": answer}}], "usage": self._usage(model)})

    def _decide(self, *, model, **_):
        self.calls.append(("decisions", model))
        body = json.loads(JEV_RECORDING)
        body["usage"] = {key: value for key, value in self._usage(model).items() if key in ("input_tokens", "output_tokens", "cost")} or body["usage"]
        return _Response(body)


def money(text: str | None) -> Decimal | None:
    return None if text is None else Decimal(text)


def pricing_file(tmp_path: Path, models: dict) -> Path:
    path = tmp_path / "pricing.yaml"
    lines = ["version: test-2026-11", "currency: USD", "models:"]
    for model, (input_price, output_price) in models.items():
        lines.append(f"  {model}: {{input_per_million_tokens: '{input_price}', output_per_million_tokens: '{output_price}'}}")
    path.write_text("\n".join(lines) + "\n")
    return path


def deployment(settings, **changes):
    """The settings with changes, validated as the environment's would be, so a list becomes a set."""
    return Settings.model_validate({**settings.model_dump(), **changes})


class Workbench:
    """An app, its database and deployment settings, one ruleset version and one dataset."""

    def __init__(self, make_client, settings, receipt_ids=("RCP-001", "RCP-002"), **changes):
        self.settings = deployment(settings, **changes)
        self.mongo = mongomock.MongoClient(tz_aware=True)
        self.database = self.mongo["expense_api"]
        self.client = make_client(app_settings=self.settings, mongo=self.mongo)
        self.ruleset_id = create_ruleset(self.client)["ruleset_id"]
        self.dataset = dataset(self.client, receipt_ids)

    def create(self, **fields):
        return create(self.client, self.dataset["dataset_id"], self.ruleset_id, **fields)

    def queue(self, **fields) -> str:
        response = self.create(**fields)
        assert response.status_code == 201, response.text
        return response.json()["evaluation_id"]

    def drain(self, **worker):
        return drain(self.settings, self.mongo, **worker)

    def get(self, evaluation_id: str, path: str = "") -> dict:
        return get(self.client, evaluation_id, path=path)

    def run(self, worker: dict | None = None, **fields) -> dict:
        """Queue one evaluation, run it to its end, and return it as GET /v1/evaluations/{id} does."""
        evaluation_id = self.queue(**fields)
        self.drain(**(worker or {}))
        return self.get(evaluation_id)
