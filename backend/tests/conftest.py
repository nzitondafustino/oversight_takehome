"""Shared fixtures. No test reaches the network: only loopback connections are allowed, for an opt-in real MongoDB."""

import copy
import json
import socket
from pathlib import Path

import boto3
import mongomock
import pytest
import yaml
from expense_validator import ExpenseValidator
from fastapi.testclient import TestClient
from moto import mock_aws

from expense_api.config import Settings
from expense_api.main import create_app

REPO = Path(__file__).resolve().parents[2]
REPLAY_SETS = {
    "openrouter": REPO / "evaluation" / "final" / "replay",  # every receipt, anthropic/claude-haiku-5.5
    "jev": REPO / "evaluation" / "jev" / "runs" / "first-run" / "replay",  # every receipt, typesafe/jev-1.14
    "langgraph": REPO / "evaluation" / "langgraph" / "runs" / "live-claude-jev-2" / "replay",  # RCP-011, 017, 022, 023 and 028
}
ARTIFACT = json.loads((REPO / "rules" / "ruleset.json").read_text())
COMPILER_RECORDINGS = {
    "live": REPO / "evaluation" / "compiler" / "live",  # model readings of data/policy-docs: can authorize production
    "fixtures": REPO / "tests" / "fixtures" / "llm_responses",  # authored simulation readings: never production
}
POLICY_DOCUMENTS = [{"document_id": path.stem, "filename": path.name, "content": path.read_text()} for path in sorted((REPO / "data" / "policy-docs").glob("*.md"))]
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _receipts() -> dict[str, dict]:
    """The project receipts without their answers, which must never reach the validator."""
    receipts = {}
    for line in (REPO / "data" / "receipts.jsonl").read_text().splitlines():
        record = {name: value for name, value in json.loads(line).items() if name not in {"labels", "notes"}}
        record["line_items"] = [{name: value for name, value in item.items() if name != "labels"} for item in record["line_items"]]
        receipts[record["receipt_id"]] = record
    return receipts


RECEIPTS = _receipts()


def receipt(receipt_id: str) -> dict:
    return copy.deepcopy(RECEIPTS[receipt_id])


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    connect = socket.socket.connect

    def guarded(sock, address):
        host = address[0] if isinstance(address, tuple) else None
        if host is not None and host not in LOOPBACK:
            raise AssertionError(f"a test tried to reach {address}")
        return connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)


@pytest.fixture(scope="session")
def core_config(tmp_path_factory) -> Path:
    """The project's config.yaml with absolute paths, in a folder with no .env, so no test can find a real key."""
    config = yaml.safe_load((REPO / "config.yaml").read_text())
    config["paths"] = {name: str(REPO / path) for name, path in config["paths"].items()}
    path = tmp_path_factory.mktemp("core") / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    return path


TEST_BUCKET = "test-expense-validator"


@pytest.fixture(scope="session")
def mocked_aws():
    """A mocked AWS for the whole run: no test reaches AWS, or needs or sees a real credential."""
    with pytest.MonkeyPatch.context() as patch:
        for name in ("AWS_PROFILE", "AWS_SESSION_TOKEN", "AWS_S3_BUCKET", "EXPENSE_API_S3_BUCKET"):
            patch.delenv(name, raising=False)
        patch.setenv("AWS_ACCESS_KEY_ID", "testing")
        patch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
        patch.setenv("AWS_REGION", "us-east-1")
        with mock_aws():
            yield boto3.client("s3", region_name="us-east-1")


@pytest.fixture(autouse=True)
def s3(mocked_aws):
    """An empty, private, unversioned bucket for each test."""
    mocked_aws.create_bucket(Bucket=TEST_BUCKET)
    yield mocked_aws
    for page in mocked_aws.get_paginator("list_object_versions").paginate(Bucket=TEST_BUCKET):
        for item in page.get("Versions", []) + page.get("DeleteMarkers", []):
            mocked_aws.delete_object(Bucket=TEST_BUCKET, Key=item["Key"], VersionId=item["VersionId"])
    mocked_aws.delete_bucket(Bucket=TEST_BUCKET)


@pytest.fixture
def settings(core_config, tmp_path) -> Settings:
    return Settings(
        s3_bucket=TEST_BUCKET,
        core_config=core_config,
        artifact_dir=tmp_path / "artifacts",
        replay_sets=REPLAY_SETS,
        default_replay_set="openrouter",
        compiler_recording_sets=COMPILER_RECORDINGS,
        allow_preview_validation=True,  # Phase 1's adapter tests validate uploaded drafts, explicitly as previews
    )


@pytest.fixture
def make_client(settings):
    """Build a started app; each keyword replaces one of create_app's defaults."""
    clients = []

    def make(app_settings: Settings | None = None, mongo=None, build_validator=ExpenseValidator) -> TestClient:
        mongo = mongomock.MongoClient(tz_aware=True) if mongo is None else mongo
        client = TestClient(create_app(app_settings or settings, mongo_client=mongo, build_validator=build_validator), raise_server_exceptions=False)
        client.__enter__()
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client) -> TestClient:
    return make_client()


def create_ruleset(client: TestClient, name: str = "default", artifact: dict = ARTIFACT) -> dict:
    response = client.post("/v1/rulesets", json={"name": name, "artifact": artifact, "source": {"kind": "upload", "reference": "rules/ruleset.json"}})
    assert response.status_code == 201, response.text
    return response.json()


def validate(client: TestClient, ruleset_id: str, receipt_ids=("RCP-001",), preview: bool = True, **fields):
    """Validate receipts; a preview unless preview=False, which needs a version published for production."""
    body = {"ruleset_id": ruleset_id, "receipts": [receipt(receipt_id) for receipt_id in receipt_ids], "context_complete": True, "preview": preview, **fields}
    return client.post("/v1/validate", json=body)


def load_cases(name: str, section: str = "cases") -> list[dict]:
    return yaml.safe_load((Path(__file__).parent / "cases" / f"{name}.yaml").read_text())[section]


def case_ids(cases: list[dict]) -> list[str]:
    return [case["name"] for case in cases]


DECISIONS = yaml.safe_load((Path(__file__).parent / "cases" / "review_decisions.yaml").read_text())["sets"]


def compile_version(client: TestClient, name: str = "default", recording_set: str = "live", documents=None, **fields):
    body = {"name": name, "compile": {"documents": documents or POLICY_DOCUMENTS, "recording_set": recording_set}, **fields}
    return client.post("/v1/rulesets", json=body)


def review_decisions(client: TestClient, record: dict, recording_set: str = "live") -> list[dict]:
    """The core's ReviewDecision for each field the recording set leaves undecided, from review_decisions.yaml."""
    page = client.get(f"/v1/rulesets/{record['ruleset_id']}/comparisons", params={"review_required": True}).json()
    wanted = {(item["rule_id"], item["field"]): item for item in DECISIONS[recording_set]}
    decisions = []
    for comparison in page["comparisons"]:
        choice = {name: value for name, value in wanted[(comparison["rule_id"], comparison["field"])].items() if name not in ("rule_id", "field")}
        decisions.append({"comparison_id": comparison["comparison_id"], "extraction_sha256": page["extraction_sha256"], "reviewer": "r.eviewer", **choice})
    return decisions


def step(client: TestClient, record: dict, name: str, recording_set: str = "live", **fields):
    """One lifecycle step on a version: submit, accept, reject, approve or publish."""
    body = {**fields}
    if name == "accept":
        body = {"actor": "r.eviewer", "decision": "accepted", "decisions": review_decisions(client, record, recording_set), **body}
        return client.post(f"/v1/rulesets/{record['ruleset_id']}/review", json=body)
    if name == "reject":
        return client.post(f"/v1/rulesets/{record['ruleset_id']}/review", json={"actor": "r.eviewer", "decision": "rejected", "reason": "thresholds need a second look", **body})
    if name == "approve":
        body = {"actor": "a.pprover", **body}
    return client.post(f"/v1/rulesets/{record['ruleset_id']}/{name}", json=body)


def published_version(client: TestClient, name: str = "default") -> dict:
    """A version compiled from the model recordings and taken through review, approval and production publication."""
    record = compile_version(client, name=name).json()
    for name_ in ("submit", "accept", "approve", "publish"):
        response = step(client, record, name_)
        assert response.status_code == 200, response.text
    return response.json()


def upload_documents(client: TestClient, documents=None) -> list[dict]:
    """Store each policy document as revision 1; return the exact revisions to select."""
    refs = []
    for document in documents or POLICY_DOCUMENTS:
        response = client.post("/v1/documents", json={"filename": document["filename"], "content": document["content"], "title": document["document_id"]})
        assert response.status_code == 201, response.text
        refs.append({"document_id": response.json()["document_id"], "revision": 1})
    return refs


def extract(client: TestClient, refs: list[dict], recording_set: str = "live", **fields):
    body = {"documents": refs, **({"recording_set": recording_set} if recording_set else {}), **fields}
    return client.post("/v1/documents/extractions", json=body)


def extraction_decisions(extraction: dict, recording_set: str = "live") -> list[dict]:
    """The core's ReviewDecision for each field the extraction leaves undecided, from review_decisions.yaml."""
    wanted = {(item["rule_id"], item["field"]): item for item in DECISIONS[recording_set]}
    return [
        {"comparison_id": comparison["comparison_id"], "extraction_sha256": extraction["extraction_sha256"], "reviewer": "r.eviewer",
         **{name: value for name, value in wanted[(comparison["rule_id"], comparison["field"])].items() if name not in ("rule_id", "field")}}
        for comparison in extraction["review_required"]
    ]


def review_extraction(client: TestClient, extraction: dict, decisions=None, **fields):
    body = {"actor": "r.eviewer", "extraction_sha256": extraction["extraction_sha256"], "decisions": extraction_decisions(extraction) if decisions is None else decisions, **fields}
    return client.post(f"/v1/documents/extractions/{extraction['extraction_id']}/review", json=body)


@pytest.fixture
def findings_run(make_client):
    """One run of RCP-013, RCP-014 and RCP-001 (see cases/run_findings.yaml), with the mongo client holding it."""
    mongo = mongomock.MongoClient(tz_aware=True)
    client = make_client(mongo=mongo)
    response = validate(client, create_ruleset(client)["ruleset_id"], receipt_ids=("RCP-013", "RCP-014", "RCP-001"))
    assert response.status_code == 201, response.text
    return client, mongo, response.json()
