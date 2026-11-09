"""Exact provider-reported costs and measured compilation time; independent of signed policy outputs."""

import time
from decimal import Decimal, InvalidOperation


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() and number >= 0 else None


class CompilationMeter:
    def __init__(self):
        self.started = time.perf_counter()
        self.calls: list[dict] = []

    def record(self, call: dict) -> None:
        usage = call.get("usage") if isinstance(call.get("usage"), dict) else {}
        cost = _number(usage.get("cost"))
        tokens = {}
        for name, key in (("input_tokens", "prompt_tokens"), ("output_tokens", "completion_tokens")):
            number = _number(usage.get(key))
            tokens[name] = int(number) if number is not None and number == int(number) else None
        self.calls.append({"model": call["model"], "document_sha256": call["document_sha256"],
                           "latency_ms": call["latency_ms"], "failed": call["failed"],
                           "cost_usd": None if cost is None else format(cost, "f"), **tokens})

    def summary(self, documents: list[dict]) -> dict:
        priced = [Decimal(call["cost_usd"]) for call in self.calls if call["cost_usd"] is not None]
        called = {call["document_sha256"] for call in self.calls}
        return {
            "duration_ms": round((time.perf_counter() - self.started) * 1000),
            "provider_latency_ms": sum(call["latency_ms"] for call in self.calls),
            "live_calls": len(self.calls),
            "failed_calls": sum(call["failed"] for call in self.calls),
            "reused_documents": sum(document["content_sha256"] not in called and not document.get("reading_error") for document in documents),
            "documents": len(documents),
            "new_spend_usd": format(sum(priced, Decimal(0)), "f"),
            "cost_completeness": "complete" if len(priced) == len(self.calls) else "incomplete",
            "unpriced_calls": len(self.calls) - len(priced),
            **{name: None if any(call[name] is None for call in self.calls) else sum(call[name] for call in self.calls)
               for name in ("input_tokens", "output_tokens")},
            "calls": self.calls,
        }
