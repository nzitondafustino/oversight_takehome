"""Provider usage and spending, per call, per receipt and per evaluation.

What the core gives, verified against its code, decides what can be known:

- Live calls go through a MeteredClient, the boundary this module adds. The worker hands the core completions
  built by the core's own public functions (llm.openrouter_completion, jev.openrouter_decisions and
  translation.openrouter_completion) over a MeteredClient instead of the plain OpenRouter client, so the core's
  requests, parsing and checks are unchanged while every response's usage, cost and generation ID is seen here.
  Its SDK retries are off, so one recorded call is one request the provider may bill.
- Replayed and cached answers make no provider call and add no spend. The core keeps no usage beside a recorded
  answer, except that a Jev answer is the whole Decisions response, whose provider-reported usage the core passes
  on in each line's classification_details. Those calls are worked out from the results (expected_calls): one
  per answered line, every LangGraph attempt and review its audit shows, one per translated line. A historical
  cost is shown only where the recording holds one.

Money is Decimal, kept as text in records and responses. A cost that is not known is null, never zero.
"""

import time
from collections import Counter
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import SimpleNamespace

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from expense_api.services.datasets import DataLoader  # the dataset loader: exact decimals, no tags or aliases
from expense_api.services.rulesets import now
from expense_validator.config import CHAT_COMPLETIONS, LEGACY_BACKENDS, canonical_backend

PROVIDER = "openrouter"  # the API every call goes to: never an adapter
MODES = ("live", "cached", "replayed")


class EvaluationStopped(Exception):
    """Stop before the next receipt or provider call. reason is cancelled, a budget limit, or the worker's."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


BUDGET_REASONS = ("max_spend_usd", "max_provider_calls", "budget_unverifiable")


# --- Pricing: estimates only, from an explicitly versioned table ---


class ModelPrice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    input_per_million_tokens: Decimal = Field(ge=0)
    output_per_million_tokens: Decimal = Field(ge=0)

    @field_validator("input_per_million_tokens", "output_per_million_tokens", mode="before")
    @classmethod
    def _exact(cls, value):
        if isinstance(value, float):  # a float has already lost the price's exact value
            raise ValueError("write a price as text or an exact number, such as '0.80'")
        return value


class Pricing(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: str = Field(pattern=r"^\S{1,64}$")
    currency: str = Field(pattern=r"^USD$")
    models: dict[str, ModelPrice] = {}

    def estimate(self, model: str, input_tokens: int | None, output_tokens: int | None) -> Decimal | None:
        price = self.models.get(model)
        if price is None or input_tokens is None or output_tokens is None:
            return None
        return (input_tokens * price.input_per_million_tokens + output_tokens * price.output_per_million_tokens) / Decimal(1_000_000)


NO_PRICING = Pricing(version="none", currency="USD")


def load_pricing(path: Path | None) -> Pricing:
    if path is None:
        return NO_PRICING
    return Pricing.model_validate(yaml.load(path.read_text(), Loader=DataLoader))


def usd(value: Decimal | None) -> str | None:
    """Money as plain decimal text, never in exponent form, without trailing zeros."""
    return None if value is None else format(value.normalize(), "f")


def _decimal(text: str | None) -> Decimal | None:
    return None if text is None else Decimal(text)


# --- Live calls ---


def provider_usage(response) -> dict:
    """The usage, cost and identity a chat or Decisions response reports, with null for whatever it leaves out."""
    body = response.model_dump(mode="json", by_alias=True)
    usage = body.get("usage") or {}
    other = {}
    for group, name in (("prompt_tokens_details", "cached_tokens"), ("prompt_tokens_details", "cache_write_tokens"), ("completion_tokens_details", "reasoning_tokens")):
        value = (usage.get(group) or {}).get(name)
        if isinstance(value, int) and value:
            other[name] = value
    cost = usage.get("cost")
    try:
        cost = None if cost is None or isinstance(cost, bool) else Decimal(str(cost))
    except InvalidOperation:
        cost = None
    return {
        "provider_request_id": body.get("id"),
        "response_model": body.get("model"),
        "input_tokens": usage.get("prompt_tokens", usage.get("input_tokens")),
        "output_tokens": usage.get("completion_tokens", usage.get("output_tokens")),
        "other_tokens": other,
        # With one's own provider key (BYOK), OpenRouter's cost is its fee alone, not what the provider bills.
        "cost": None if usage.get("is_byok") else cost,
        "byok": bool(usage.get("is_byok")),
    }


class Meter:
    """Accounts for one evaluation's live provider calls, and stops the next call when a limit is reached.

    Only the limits the run was given apply; an uncapped run (budget None) has none. They are best effort: a
    call's cost is known only once it has returned, so the last call before the spending limit can take spend past
    it by up to that call's cost. Under a spending limit, a call whose cost is not known, because the provider
    reported none and the pricing table has no price, leaves the budget unverifiable, so the next call is refused.
    """

    def __init__(self, *, evaluation_id: str, attempt: int, budget: dict | None, pricing: Pricing, guard: Callable[[], None], store: Callable[[dict], bool], sequence: int):
        self.evaluation_id, self.attempt = evaluation_id, attempt
        limits = budget or {}
        self.max_spend = None if limits.get("max_spend_usd") is None else Decimal(limits["max_spend_usd"])
        self.max_calls = limits.get("max_provider_calls")
        self.pricing, self.guard, self.store = pricing, guard, store
        self.sequence = sequence
        self.receipt_id: str | None = None
        self.calls = 0
        self.spent = Decimal(0)
        self.unverifiable = 0
        self.attempted: Counter = Counter()  # (receipt_id, purpose): calls sent, answered or not

    def before_call(self) -> None:
        self.guard()  # cancellation, and the worker's lease
        if self.max_calls is not None and self.calls >= self.max_calls:
            raise EvaluationStopped("max_provider_calls")
        if self.max_spend is not None and self.unverifiable:  # only a spending limit needs every cost known
            raise EvaluationStopped("budget_unverifiable")
        if self.max_spend is not None and self.spent >= self.max_spend:
            raise EvaluationStopped("max_spend_usd")

    def record(self, *, purpose: str, backend: str, model: str, latency_ms: int, response=None, error: Exception | None = None) -> None:
        self.calls += 1
        usage = provider_usage(response) if response is not None else {"provider_request_id": None, "response_model": None, "input_tokens": None, "output_tokens": None, "other_tokens": {}, "cost": None, "byok": False}
        estimated = None if usage["cost"] is not None else self.pricing.estimate(model, usage["input_tokens"], usage["output_tokens"])
        if usage["cost"] is not None:
            self.spent += usage["cost"]
        elif estimated is not None:
            self.spent += estimated
        else:
            self.unverifiable += 1  # a failed call may still be billed, and a call with no cost cannot be bounded
        self.attempted[(self.receipt_id, purpose)] += 1
        self.store({
            "_id": f"{self.evaluation_id}:{self.attempt}:{self.sequence}",
            "evaluation_id": self.evaluation_id,
            "receipt_id": self.receipt_id,
            "sequence": self.sequence,
            "source": "metered",
            "mode": "live",
            "purpose": purpose,
            "provider": PROVIDER,
            "backend": backend,
            "model": model,
            "line_id": None,  # a request names no line ID: calls are attributed to the receipt being evaluated
            "success": error is None,
            "error": None if error is None else type(error).__name__,
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "other_tokens": usage["other_tokens"],
            "actual_cost_usd": usd(usage["cost"]),
            "estimated_cost_usd": usd(estimated),
            "pricing_version": None if estimated is None else self.pricing.version,
            "historical_cost_usd": None,
            "byok": usage["byok"],
            "provider_request_id": usage["provider_request_id"],
            "response_model": usage["response_model"],
            "latency_ms": latency_ms,
            "created_at": now(),
        })
        self.sequence += 1


class MeteredClient:
    """The parts of the OpenRouter SDK client the core's live completions use, each call metered."""

    def __init__(self, client, meter: Meter, *, purpose: str, backend: str):
        self._meter, self._purpose, self._backend = meter, purpose, backend
        self.chat = SimpleNamespace(send=lambda **kwargs: self._call(client.chat.send, kwargs))
        self.alpha = SimpleNamespace(decisions=SimpleNamespace(create=lambda **kwargs: self._call(client.alpha.decisions.create, kwargs)))

    def _call(self, send: Callable, kwargs: dict):
        self._meter.before_call()
        started = time.perf_counter()
        try:
            response = send(**kwargs)
        except Exception as error:  # recorded, then raised unchanged: the core decides what a failure means
            self._meter.record(purpose=self._purpose, backend=self._backend, model=kwargs.get("model"), latency_ms=_ms(started), error=error)
            raise
        self._meter.record(purpose=self._purpose, backend=self._backend, model=kwargs.get("model"), latency_ms=_ms(started), response=response)
        return response


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


# --- Calls worked out from results: replayed answers, and live answers the run's own cache gave ---


def expected_calls(result: dict) -> list[dict]:
    """Every model call a receipt's result shows was answered, in line order, with any usage its recording holds.

    A translation whose recorded answer failed the core's checks is not shown in a result, so it is not counted.
    """
    calls = []
    translation_model = result["metadata"]["translation_model"]
    langgraph = result["provenance"]["classification_backend"] == "langgraph"
    for line in result["line_items"]:
        if line.get("english_translation") is not None:
            # The translator's adapter, as a live call records it.
            calls.append({"purpose": "translation", "backend": CHAT_COMPLETIONS, "model": translation_model, "line_id": line["line_id"], "usage": None})
        details = line.get("classification_details") or {}
        if not langgraph:
            # One call per line, even one with an error: a replayed receipt has every answer it asked for (a gap
            # raises instead), so an error is the core refusing an answer it was given. Its usage is not kept.
            calls.append({"purpose": "classification", "backend": result["provenance"]["classification_backend"], "model": result["metadata"]["model"], "line_id": line["line_id"], "usage": details.get("usage")})
            continue
        audit = details.get("orchestration") or {}
        attempts = audit.get("attempts", [])
        answer = max((index for index, attempt in enumerate(attempts) if attempt["name"] == audit.get("answered_by") and "revision" not in attempt), default=None)
        for index, attempt in enumerate(attempts):
            if attempt["answered"]:
                usage = details.get("usage") if index == answer else None  # only the answering backend's details survive
                calls.append({"purpose": "revision" if attempt.get("revision") else "classification", "backend": attempt["backend"], "model": attempt["model"], "line_id": line["line_id"], "usage": usage})
        for review in (audit.get("reflection") or {}).get("reviews", []):
            if review["answered"]:  # a reused verdict made no call
                calls.append({"purpose": "review", "backend": review["reviewer"], "model": review["model"], "line_id": line["line_id"], "usage": None})
    return calls


def derived_records(evaluation_id: str, receipt_id: str, calls: list[dict], mode: str) -> list[dict]:
    """Records for calls that reached no provider: replayed from recordings, or answered by the run's cache."""
    records = []
    for index, call in enumerate(calls):
        usage = call["usage"] or {}
        cost = usage.get("cost")
        records.append({
            "_id": f"{evaluation_id}:{receipt_id}:{mode}:{index}",
            "evaluation_id": evaluation_id,
            "receipt_id": receipt_id,
            "sequence": None,
            "source": "derived",
            "mode": mode,
            "purpose": call["purpose"],
            "provider": PROVIDER,
            "backend": call["backend"],
            "model": call["model"],
            "line_id": call["line_id"],
            "success": True,
            "error": None,
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "other_tokens": {},
            "actual_cost_usd": None,
            "estimated_cost_usd": None,
            "pricing_version": None,
            # What the provider reported for the recorded call when it was made: history, never new spend.
            "historical_cost_usd": None if cost is None or isinstance(cost, bool) else usd(Decimal(str(cost))),
            "byok": False,
            "provider_request_id": None,
            "response_model": None,
            "latency_ms": None,
            "created_at": now(),
        })
    return records


def cached_calls(calls: list[dict], attempted: Counter, receipt_id: str) -> list[dict]:
    """The calls a live receipt's result shows beyond those sent to the provider: the run's cache answered them,
    as when two lines read the same. Which lines they were is not known, so none is named. A translation that
    failed is not in the result, so it can hide a cached one: the count of cached calls is a lower bound."""
    shown = Counter(call["purpose"] for call in calls)
    surplus = []
    for purpose, count in sorted(shown.items()):
        sample = next(call for call in calls if call["purpose"] == purpose)
        surplus.extend({**sample, "line_id": None, "usage": None} for _ in range(max(0, count - attempted[(receipt_id, purpose)])))
    return surplus


# --- Totals ---


def summarize(records: list[dict]) -> dict:
    """Spending of a set of calls, each counted once by its record. Only live calls are new spend."""
    live = [record for record in records if record["mode"] == "live"]
    actual = [_decimal(record["actual_cost_usd"]) for record in live if record["actual_cost_usd"] is not None]
    estimated = [_decimal(record["estimated_cost_usd"]) for record in live if record["actual_cost_usd"] is None and record["estimated_cost_usd"] is not None]
    unpriced = [record for record in live if record["actual_cost_usd"] is None and record["estimated_cost_usd"] is None]
    past = [record for record in records if record["mode"] != "live"]
    known = [_decimal(record["historical_cost_usd"]) for record in past if record["historical_cost_usd"] is not None]
    return {
        "calls": {"total": len(records), **{mode: sum(1 for record in records if record["mode"] == mode) for mode in MODES}, "failed": sum(1 for record in records if not record["success"])},
        "tokens": {"input": _tokens(live, "input_tokens"), "output": _tokens(live, "output_tokens")},
        "actual_cost_usd": usd(sum(actual, Decimal(0))),
        "estimated_cost_usd": usd(sum(estimated, Decimal(0))),
        "unpriced_usage": {"calls": len(unpriced), "input_tokens": _tokens(unpriced, "input_tokens"), "output_tokens": _tokens(unpriced, "output_tokens")},
        "new_spend_usd": usd(sum(actual, Decimal(0)) + sum(estimated, Decimal(0))),
        # complete: every live call has a provider-reported or estimated cost, so new_spend_usd is the total.
        # incomplete: some have neither, so new_spend_usd is a lower bound.
        "cost_completeness": "incomplete" if unpriced else "complete",
        "historical": {"cost_usd": usd(sum(known, Decimal(0))) if known else None, "calls_with_cost": len(known), "calls_without_cost": len(past) - len(known)},
    }


def spend_report(records: list[dict]) -> dict:
    """Totals, and the same by model, adapter and purpose. Every record counts once, in each breakdown.

    A record keeps the adapter name its call was made under; by_backend groups by the adapter itself, so calls stored
    under an earlier name (jev, openrouter) and under the adapter's own (decisions, chat_completions) count together,
    and no two protocols ever share a group.
    """
    report = summarize(records)
    keys = {"model": lambda record: record["model"], "backend": lambda record: canonical_backend(record["backend"]), "purpose": lambda record: record["purpose"]}
    for name, key in keys.items():
        values = sorted({key(record) for record in records if key(record) is not None})
        report[f"by_{name}"] = {value: summarize([record for record in records if key(record) == value]) for value in values}
    report["pricing_versions"] = sorted({record["pricing_version"] for record in records if record["pricing_version"]})
    return report


def _tokens(records: list[dict], field: str) -> int | None:
    values = [record[field] for record in records if record[field] is not None]
    return sum(values) if values else None


def names_earlier_adapters(spend: dict | None) -> bool:
    """Whether a stored spending report groups calls under an adapter's earlier name, as reports made before the
    rename do: such a report is rebuilt from its own usage records when read, never rewritten where it is stored."""
    return spend is not None and any(name in LEGACY_BACKENDS for name in spend.get("by_backend", {}))

