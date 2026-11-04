"""Asks an LLM to read a policy document on its own, so its answer can be compared with the regex.

The LLM only ever sees the document text, never the regex output, otherwise agreement
would mean nothing. Calls go through a `complete` function, so tests can pass recorded
responses and repeated runs can reuse the cache.
"""

import hashlib
import json
import re
import urllib.request
from decimal import Decimal
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, ConfigDict, model_validator

from expense_validator.config import LlmConfig
from expense_validator.policy.vocabulary import Severity, Unit

Complete = Callable[[str, str], str]

PROMPT = """You read an expense policy document and list its rules as JSON. Report only what the text says.

A rule starts with a bold heading such as **ALC-01 — Title.** Its text runs until the next rule, the next
heading, or a new paragraph that opens with a bold note. Italic paragraphs and tables belong to the rule above.

Return one JSON object, with no other text: {"rules": [ ... ]}. Each rule has exactly these keys:
- rule_id: the identifier, such as "ALC-01".
- title: the heading text, without the identifier or the final full stop.
- section: the text of the nearest heading above the rule that starts with ##, or null if there is none.
- severities: every severity in bold in the rule's text: "critical", "high", "medium" or "low".
- review_signal: true if the rule's text contains "review signal" in bold.
- thresholds: every limit in the rule's text, each once, as {"value": "200", "unit": "usd"}. Units:
  "usd" for dollars, "eur" for euros, "nights", "hour" for hours and for clock times (05:00 is 5), and
  "count" for attendee counts and "N or more receipts". Write words as digits: "four" is "4".
- references: the identifiers of the other rules the text mentions, not this rule's own.
- quote: a short phrase copied word for word from the rule's text.
"""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LlmThreshold(_Strict):
    value: Decimal
    unit: Unit


class LlmRule(_Strict):
    rule_id: str
    title: str
    section: str | None
    severities: list[Severity]
    review_signal: bool
    thresholds: list[LlmThreshold]
    references: list[str]
    quote: str


class LlmDocument(_Strict):
    rules: list[LlmRule]

    @model_validator(mode="after")
    def _check_unique_ids(self):
        ids = [rule.rule_id for rule in self.rules]
        duplicates = sorted({rule_id for rule_id in ids if ids.count(rule_id) > 1})
        if duplicates:
            raise ValueError(f"duplicate rule ids: {duplicates}")
        return self


def parse_response(raw: str) -> LlmDocument:
    """Some models wrap their JSON in a code fence even when told not to."""
    fenced = re.fullmatch(r"\s*```(?:json)?\s*(.*?)\s*```\s*", raw, flags=re.DOTALL)
    return LlmDocument.model_validate_json(fenced.group(1) if fenced else raw)


def extract_with_llm(text: str, complete: Complete) -> LlmDocument:
    return parse_response(complete(PROMPT, text))


def openrouter(settings: LlmConfig, api_key: str) -> Complete:
    """A `complete` function for one model. This sends the document text to OpenRouter."""

    def complete(prompt: str, text: str) -> str:
        body = {
            "model": settings.model,
            "temperature": settings.temperature,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": text}],
        }
        request = urllib.request.Request(
            settings.url,
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=settings.timeout_seconds) as response:
            return json.load(response)["choices"][0]["message"]["content"]

    return complete


def cached(complete: Complete, model: str, directory: Path) -> Complete:
    """Reuses an answer until the model, the prompt or the document changes."""

    def lookup(prompt: str, text: str) -> str:
        key = hashlib.sha256("\0".join([model, prompt, text]).encode()).hexdigest()
        path = directory / f"{key}.txt"
        if path.exists():
            return path.read_text()
        answer = complete(prompt, text)
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text(answer)
        return answer

    return lookup
