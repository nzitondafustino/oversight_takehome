"""Compilation telemetry uses fake HTTP responses only; signed outputs remain independent."""
import io
import json
from urllib.error import URLError

import pytest
from conftest import case_ids, load_cases, extract, upload_documents
from expense_validator.compiler.llm_extract import cached, observe_calls, openrouter
from expense_validator.config import LlmConfig
from expense_api.services.compilation_metrics import CompilationMeter
from expense_api.schemas.documents import CompilationAnalytics

CASES = load_cases("compilation_metrics")


@pytest.mark.parametrize("case", CASES, ids=case_ids(CASES))
def test_metrics_keep_exact_costs_and_missing_usage(case):
    meter = CompilationMeter()
    for call in case["calls"]:
        meter.record({"model": "test/model", "document_sha256": "doc", "latency_ms": 5, "failed": False, **call})
    summary = meter.summary([{"content_sha256": "doc"}])
    CompilationAnalytics.model_validate(summary)
    assert {key: summary[key] for key in case["expected"]} == case["expected"]
    assert summary["provider_latency_ms"] == 5 * len(case["calls"])
    assert summary["duration_ms"] >= 0


def test_observer_counts_provider_requests_not_cache_hits(monkeypatch, tmp_path):
    body = {"choices": [{"message": {"content": '{"rules":[]}'}}], "usage": {"cost": 0.0001, "prompt_tokens": 9, "completion_tokens": 3}}
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: io.BytesIO(json.dumps(body).encode()))
    complete = cached(openrouter(LlmConfig(model="test/model", url="https://provider.invalid", temperature=0, timeout_seconds=1), "test-not-a-key"), "test/model", tmp_path)
    meter = CompilationMeter()
    with observe_calls(meter.record):
        assert complete("prompt", "document") == complete("prompt", "document") == '{"rules":[]}'
    assert len(meter.calls) == 1
    assert meter.calls[0]["cost_usd"] == "0.0001"
    openrouter(LlmConfig(model="test/model", url="https://provider.invalid", temperature=0, timeout_seconds=1), "test-not-a-key")("prompt", "another document")
    assert len(meter.calls) == 1  # observation does not leak into another request


def test_failed_provider_requests_report_unknown_cost(monkeypatch):
    def fail(*args, **kwargs):
        raise URLError("fake failure")
    monkeypatch.setattr("urllib.request.urlopen", fail)
    meter = CompilationMeter()
    with observe_calls(meter.record), pytest.raises(URLError):
        openrouter(LlmConfig(model="test/model", url="https://provider.invalid", temperature=0, timeout_seconds=1), "test-not-a-key")("prompt", "document")
    assert meter.calls[0]["failed"] is True
    assert meter.calls[0]["cost_usd"] is None


def test_replay_extraction_exposes_execution_analytics(client):
    result = extract(client, upload_documents(client)).json()
    assert result["analytics"]["live_calls"] == 0
    assert result["analytics"]["new_spend_usd"] == "0"
    assert result["analytics"]["reused_documents"] == 9
    listed = client.get("/v1/documents/extractions").json()["items"][0]
    assert listed["analytics"] == result["analytics"]
