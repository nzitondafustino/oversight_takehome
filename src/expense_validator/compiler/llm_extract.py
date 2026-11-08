"""Read a policy document with an LLM, independently of the regex, for comparison."""

import hashlib
import json
import os
import re
import threading
import urllib.request
from decimal import Decimal
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, ConfigDict, model_validator

from expense_validator.config import LlmConfig
from expense_validator.exceptions import RecordingConflict, RecordingMissing
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
    """Parse an LLM reading, accepting a JSON code fence."""
    fenced = re.fullmatch(r"\s*```(?:json)?\s*(.*?)\s*```\s*", raw, flags=re.DOTALL)
    return LlmDocument.model_validate_json(fenced.group(1) if fenced else raw)


def extract_with_llm(text: str, complete: Complete) -> LlmDocument:
    return parse_response(complete(PROMPT, text))


PROVIDER = "openrouter"
RESPONSE_FORMAT = {"type": "json_object"}


def openrouter(settings: LlmConfig, api_key: str) -> Complete:
    """Return a completion function for the compiler model. Sends the document to OpenRouter."""

    def complete(prompt: str, text: str) -> str:
        body = {
            "model": settings.model,
            "temperature": settings.temperature,
            "response_format": RESPONSE_FORMAT,
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


def cache_key(model: str, temperature: float) -> str:
    """Return the cache identity of a compiler reading's generation settings, for cached.

    The request timeout changes no answer, and a failed request is never cached, so it is left out.
    """
    return f"{model}\0temperature={float(temperature)!r}"


def cached(complete: Complete, model: str, directory: Path) -> Complete:
    """Return a completion function that reuses answers until the model, prompt or text changes."""

    def lookup(prompt: str, text: str) -> str:
        key = hashlib.sha256("\0".join([model, prompt, text]).encode()).hexdigest()
        path = directory / f"{key}.txt"
        if path.exists():
            return path.read_text()
        answer = complete(prompt, text)
        directory.mkdir(parents=True, exist_ok=True)
        # Written aside and then renamed, so a concurrent reader finds a whole answer or none.
        partial = directory / f".{key}.{os.getpid()}.{threading.get_ident()}.tmp"
        partial.write_text(answer)
        os.replace(partial, path)
        return answer

    return lookup


# simulation: written by hand or by a stand-in, for tests and dry runs. model: a real model's answer.
RECORDING_KINDS = ("simulation", "model")


def document_sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def recorded(directory: Path) -> Complete:
    """Return a completion function that replays recordings by document hash.

    Raises:
        RecordingMissing: If no recording exists for the document.
        ValueError: If the recording is for another prompt or has an invalid kind.
    """

    def complete(prompt: str, text: str) -> str:
        key = document_sha256(text)
        path = directory / f"{key}.json"
        if not path.exists():
            raise RecordingMissing(f"no recorded response for the document with sha256 {key} in {directory}")
        recording = json.loads(path.read_text())
        if recording["document_sha256"] != key:
            raise ValueError(f"{path.name}: records another document")
        if recording.get("kind") not in RECORDING_KINDS:
            raise ValueError(f"{path.name}: kind must be one of {RECORDING_KINDS}")
        if recording["prompt_sha256"] != document_sha256(prompt):
            raise ValueError(f"{path.name}: recorded for another prompt, so its answer may not hold")
        return recording["response"]

    return complete


def recording_kind(directory: Path, texts: list[str]) -> str:
    """Return the single kind of the recordings these documents need."""
    kinds = set()
    for text in texts:
        path = directory / f"{document_sha256(text)}.json"
        if not path.exists():
            raise RecordingMissing(f"no recorded response for the document with sha256 {document_sha256(text)} in {directory}")
        kinds.add(json.loads(path.read_text()).get("kind"))
    if len(kinds) != 1 or not kinds <= set(RECORDING_KINDS):
        raise ValueError(f"the recordings must all be one kind of {RECORDING_KINDS}, found {sorted(map(str, kinds))}")
    return kinds.pop()


def recording(complete: Complete, directory: Path, policy_doc: str, origin: str, kind: str = "model") -> Complete:
    """Return a completion function that saves each answer as a replayable recording, named by document hash.

    Raises:
        RecordingConflict: If the document already has a recording with another answer, prompt or kind.
    """

    def record(prompt: str, text: str) -> str:
        if kind not in RECORDING_KINDS:
            raise ValueError(f"kind must be one of {RECORDING_KINDS}")
        answer = complete(prompt, text)
        entry = {"policy_doc": policy_doc, "document_sha256": document_sha256(text), "prompt_sha256": document_sha256(prompt), "kind": kind, "origin": origin, "response": answer}
        same = ("document_sha256", "prompt_sha256", "kind", "response")
        return write_once(directory / f"{entry['document_sha256']}.json", entry, same)["response"]

    return record


# --- Recordings named by their generation settings, written by live mode ---

# 3 dropped the tenant hash that 2 carried, when customer_id was removed: a format 2 recording never matches.
RECORDING_FORMAT = 3
CONFIGURED_PREFIX = "configured-"


def recording_identity(text: str, prompt: str, *, url: str, model: str, temperature: float) -> dict:
    """Return everything that decides a recorded answer.

    The timeout changes no answer, so it is left out, as are credentials and times.
    """
    return {
        "format": RECORDING_FORMAT,
        "document_sha256": document_sha256(text),
        "prompt_sha256": document_sha256(prompt),
        "provider": PROVIDER,
        "url": url,
        "model": model,
        "temperature": float(temperature),
        "response_format": RESPONSE_FORMAT,
    }


def recording_key(identity: dict) -> str:
    """Return the SHA-256 of an identity's canonical JSON, which names its recording file."""
    return document_sha256(json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


def configured_path(directory: Path, identity: dict) -> Path:
    return directory / f"{CONFIGURED_PREFIX}{recording_key(identity)}.json"


def model_recording(complete: Complete, directory: Path, generation: dict, policy_doc_of: Callable[[str], str], origin: dict) -> Complete:
    """Return a completion function that records each answer under its generation settings, as a model reading.

    An existing matching recording is replayed instead of calling the model.

    Args:
        generation: The keyword arguments of recording_identity, except text and prompt.
        policy_doc_of: The document's file name, from its text.
        origin: Provenance kept with the recording. Never credentials.

    Raises:
        RecordingConflict: If the recording exists with other content.
    """

    def record(prompt: str, text: str) -> str:
        identity = recording_identity(text, prompt, **generation)
        path = configured_path(directory, identity)
        if path.exists():
            return verified_recording(path, identity)["response"]
        entry = {"key": recording_key(identity), "identity": identity, "kind": "model", "policy_doc": policy_doc_of(text), "origin": origin, "response": complete(prompt, text)}
        return write_once(path, entry, ("key", "identity", "kind", "response"))["response"]

    return record


def verified_recording(path: Path, identity: dict) -> dict:
    """Return the recording at path if it is a model recording of exactly this identity.

    Raises:
        RecordingConflict: If it records anything else.
    """
    try:
        entry = json.loads(path.read_text())
    except ValueError:
        raise RecordingConflict(f"{path.name} is not a recording") from None
    key = recording_key(identity)
    if not isinstance(entry, dict) or entry.get("identity") != identity or entry.get("key") != key or path.name != f"{CONFIGURED_PREFIX}{key}.json":
        raise RecordingConflict(f"{path.name} does not record these generation settings, so its answer is not used")
    if entry.get("kind") != "model" or not isinstance(entry.get("response"), str):
        raise RecordingConflict(f"{path.name} is not a model recording")
    return entry


def replay_configured(directory: Path, generation: dict) -> Complete:
    """Return a completion function that replays only recordings of exactly these generation settings.

    Raises:
        RecordingMissing: If there is none for the document.
        RecordingConflict: If the recording found records anything else.
    """

    def complete(prompt: str, text: str) -> str:
        identity = recording_identity(text, prompt, **generation)
        path = configured_path(directory, identity)
        if not path.exists():
            raise RecordingMissing(f"no recording of the document with sha256 {identity['document_sha256']} with these generation settings in {directory}")
        return verified_recording(path, identity)["response"]

    return complete


def recorded_settings(directory: Path, text: str) -> list[dict]:
    """Return the generation settings this document was recorded with, to explain a miss."""
    document = document_sha256(text)
    found = []
    for path in sorted(directory.glob(f"{CONFIGURED_PREFIX}*.json")):
        try:
            identity = json.loads(path.read_text()).get("identity") or {}
        except (ValueError, AttributeError):
            continue
        if identity.get("format") == RECORDING_FORMAT and identity.get("document_sha256") == document:
            found.append({name: identity.get(name) for name in ("model", "temperature", "url", "prompt_sha256")})
    return found


def write_once(path: Path, entry: dict, same: tuple[str, ...]) -> dict:
    """Create a recording atomically without overwriting existing content.

    Write to a temporary file and hard-link it into place. Reuse an existing
    recording only when the fields named by same match.

    Raises:
        RecordingConflict: If existing content differs.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    partial.write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n")
    try:
        os.link(partial, path)
    except FileExistsError:
        try:
            existing = json.loads(path.read_text())
        except ValueError:
            existing = None
        if not isinstance(existing, dict) or any(existing.get(name) != entry.get(name) for name in same):
            raise RecordingConflict(f"{path.name} already holds a different recording, and a recording is never replaced") from None
        return existing
    finally:
        partial.unlink(missing_ok=True)
    return entry
