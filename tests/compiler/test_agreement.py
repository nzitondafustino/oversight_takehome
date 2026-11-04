"""Runs the cases in cases/compiler/agreement.yaml, then checks the OpenRouter call and the cache."""

import io
import json
from pathlib import Path

import pytest
from yaml_cases import load_cases

from expense_validator.compiler import llm_extract
from expense_validator.compiler.agreement import agreement_report, compare_document
from expense_validator.compiler.extract import extract_document
from expense_validator.compiler.llm_extract import PROMPT, cached, openrouter, parse_response
from expense_validator.config import LlmConfig

DOCS = Path(__file__).parents[2] / "data" / "policy-docs"


def summary(results) -> dict:
    """Every rule, with the fields that did not agree, by status."""
    found = {}
    for result in results:
        if result.field == "rule":
            found[result.rule_id] = result.status
        else:
            statuses = found.setdefault(result.rule_id, {})
            if result.status != "agreed":
                statuses.setdefault(result.status, []).append(result.field)
    return found


@pytest.mark.parametrize("case", load_cases("agreement", "cases"))
def test_agreement(case):
    response = case["response"]
    raw = response if isinstance(response, str) else json.dumps(response)
    document = extract_document(case["text"], "doc.md")
    assert summary(compare_document(document, parse_response(raw))) == case["expected"]


@pytest.mark.parametrize("case", load_cases("agreement", "refused"))
def test_response_is_refused(case):
    with pytest.raises(ValueError, match=case["expected"]):
        parse_response(case["response"])


def reading_of(text: str) -> str:
    """What a perfect LLM would answer: the regex reading, written as a response."""
    rules = [
        {
            "rule_id": clause.rule_id,
            "title": clause.title,
            "section": clause.section,
            "severities": clause.severities,
            "review_signal": clause.review_signal,
            "thresholds": [{"value": str(t.value), "unit": t.unit} for t in clause.thresholds],
            "references": clause.references,
            "quote": clause.text[:20],
        }
        for clause in extract_document(text, "doc.md").clauses
    ]
    return json.dumps({"rules": rules})


def test_a_matching_reading_of_the_real_policy_has_no_disagreements():
    results = agreement_report(DOCS, lambda prompt, text: reading_of(text))
    assert len({result.rule_id for result in results}) == 41
    assert {result.status for result in results} <= {"agreed", "unconfirmed"}
    assert all(result.field == "thresholds" for result in results if result.status == "unconfirmed")


def test_llm_sees_only_the_prompt_and_the_document():
    seen = []

    def complete(prompt, text):
        seen.append((prompt, text))
        return reading_of(text)

    agreement_report(DOCS, complete)
    assert [text for _, text in seen] == [path.read_text() for path in sorted(DOCS.glob("*.md"))]
    assert all(prompt == PROMPT for prompt, _ in seen)


def test_openrouter_request(monkeypatch):
    sent = {}

    def fake_urlopen(request, timeout):
        sent.update(
            url=request.full_url,
            timeout=timeout,
            auth=request.get_header("Authorization"),
            body=json.loads(request.data),
        )
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": "answer"}}]}).encode())

    monkeypatch.setattr(llm_extract.urllib.request, "urlopen", fake_urlopen)
    settings = LlmConfig(model="some/model", url="https://example.test/chat", temperature=0.5, timeout_seconds=7)

    assert openrouter(settings, "secret")("the prompt", "the text") == "answer"
    assert sent["url"] == "https://example.test/chat"
    assert sent["timeout"] == 7
    assert sent["auth"] == "Bearer secret"
    assert sent["body"]["model"] == "some/model"
    assert sent["body"]["temperature"] == 0.5
    assert sent["body"]["messages"] == [
        {"role": "system", "content": "the prompt"},
        {"role": "user", "content": "the text"},
    ]


def test_cache_calls_once_until_something_changes(tmp_path):
    calls = []

    def complete(prompt, text):
        calls.append(text)
        return f"answer {len(calls)}"

    lookup = cached(complete, "model-a", tmp_path)
    assert lookup("prompt", "text") == "answer 1"
    assert lookup("prompt", "text") == "answer 1"
    assert lookup("prompt", "new text") == "answer 2"
    assert lookup("new prompt", "text") == "answer 3"
    assert cached(complete, "model-b", tmp_path)("prompt", "text") == "answer 4"
    assert len(calls) == 4
