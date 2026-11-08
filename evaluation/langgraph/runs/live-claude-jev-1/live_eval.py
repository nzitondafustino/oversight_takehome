"""Live LangGraph evaluation: Claude and Jev on 13 lines, five configurations, under a hard budget.

Stages (python live_eval.py STAGE [RUN_DIR]):
  preflight  free: key, models, budget, inputs; seeds the one recorded translation the run needs
  dry-run    no network: the whole pipeline with a stand-in client, into a scratch folder
  live       PAID: configurations A to E, in order, sharing one recording folder, so each unique request is
             sent once; every request passes the budget guard first, and none is retried
  evaluate   offline: scores, reflection analysis, cost and latency, from the recordings
  verify     offline, network blocked: replays every configuration and checks it against the live run

Economy: one shared cache, so a later configuration reuses every identical earlier request; RB-047 is dropped,
as RBX-009 covers event tickets with a policy outcome; the smoke receipt is the first unit of configuration D,
and its answers are reused. Only answers recorded in this run are used, except the one translation.
"""

import dataclasses
import hashlib
import json
import shutil
import sys
import time
from collections import Counter
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from openrouter.utils import RetryConfig

import expense_validator.validator as validator_module
from expense_validator import ExpenseValidator
from expense_validator.classification.backends import jev
from expense_validator.classification.core.common import line_input
from expense_validator.classification.orchestrator import classify_receipt as orchestrate
from expense_validator.config import ROOT, load_config
from expense_validator.evaluation.classification import missing_recordings, result_predictions, routing_summary
from expense_validator.evaluation.cli import robustness as rcli
from expense_validator.evaluation.dataset import load_dataset
from expense_validator.evaluation.policy_outputs import evaluate_predictions
from expense_validator.evaluation.predictions import LinePrediction, PredictionSet, Source
from expense_validator.evaluation.robustness import PLACEHOLDER_FIELDS, line_receipt
from expense_validator.models import ValidatorSettings
from expense_validator.normalization import normalize_receipt

CONFIG = ROOT / "config.yaml"
RUN = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "evaluation" / "langgraph" / "runs" / "live-claude-jev-1"
BUDGET_USD = 0.10
CEILING_USD = 0.09  # the guard's stop: a margin under the approved budget
MAX_REQUESTS = 150
WORST = {"chat": 0.0004, "decision": 0.0001}  # conservative per-request maxima (measured prompts: 0.00036; Jev: 0.000068)
CLAUDE = "anthropic/claude-haiku-5.5"
TRANSLATION = load_config().translation.model
GOLDEN = ("RCP-011", "RCP-017", "RCP-022", "RCP-023", "RCP-028")
ROBUST = ("RBX-001", "RBX-009")
ISOLATED = ("RB-002",)
JEV, LLM = {"name": "jev", "backend": "jev", "model": jev.MODEL}, {"name": "claude", "backend": "openrouter", "model": CLAUDE}
REFLECT = {"enabled": True}  # config.yaml's reviewer (Claude), max_retries 2, max_total_calls_per_line 6
CONFIGS = {
    "A": {"backends": [JEV], "reflection": {"enabled": False}},
    "B": {"backends": [LLM], "reflection": {"enabled": False}},
    "C": {"backends": [JEV, LLM], "reflection": {"enabled": False}},
    "D": {"backends": [JEV, LLM], "reflection": REFLECT},
    "E": {"backends": [LLM, JEV], "reflection": REFLECT},
}
NO_RETRY = RetryConfig("none", None, False)


class BudgetStop(Exception):
    """Not a ClassificationError and not an SDK error, so no backend catches it: the run stops at once."""


class Ledger:
    """Every paid request, checked before it is sent and logged after, without its prompt or response."""

    def __init__(self, path: Path):
        self.path, self.requests, self.estimate = path, 0, 0.0
        self.unit = "setup"

    def guard(self, kind: str, send):
        def call(**kwargs):
            model = kwargs.get("model")
            if kind == "chat" and model == TRANSLATION:
                raise BudgetStop("a translation was about to be sent: every translation must be recorded already")
            if self.requests >= MAX_REQUESTS or self.estimate + WORST[kind] > CEILING_USD:
                raise BudgetStop(f"budget guard: {self.requests} requests, ${self.estimate:.4f} conservative estimate")
            self.requests += 1
            self.estimate += WORST[kind]
            started = time.perf_counter()
            record = {"unit": self.unit, "kind": kind, "model": model, "ok": False}
            try:
                result = send(**kwargs)
                record["ok"] = True
                usage = getattr(result, "usage", None)
                record["usage"] = usage.model_dump(mode="json", exclude_none=True) if hasattr(usage, "model_dump") else (dict(vars(usage)) if usage is not None else None)
                return result
            except Exception as error:
                record["error"] = type(error).__name__
                raise
            finally:
                record["latency_ms"] = round((time.perf_counter() - started) * 1000)
                with self.path.open("a") as file:
                    file.write(json.dumps(record) + "\n")

        return call


def install(ledger: Ledger, fake=None):
    """Every client the validator builds: no SDK retries, and every paid call through the ledger."""
    validator_module.RETRIES = NO_RETRY
    original = validator_module._Request._client

    def client(self, settings):
        if self._openrouter is not None:
            return self._openrouter  # built and guarded once per request
        real = fake() if fake is not None else original(self, settings)
        real.chat.send = ledger.guard("chat", real.chat.send)
        real.alpha.decisions.create = ledger.guard("decision", real.alpha.decisions.create)
        self._openrouter = real
        return real

    validator_module._Request._client = client


def strip_placeholders(complete):
    """The robustness rule for isolated lines: their made-up amounts never reach a model, in any request."""

    def send(prompt, text):
        body = json.loads(text)
        target = body["line"] if isinstance(body.get("line"), dict) else body
        for name in PLACEHOLDER_FIELDS:
            target.pop(name, None)
        return complete(prompt, json.dumps(body, ensure_ascii=False))

    return send


def datasets():
    golden = load_dataset(ROOT / "data" / "receipts.jsonl", "northwind")
    robust = load_dataset(rcli.RECEIPTS, rcli.CUSTOMER)
    pick = lambda data, ids: replace(data, inputs=tuple(r for r in data.inputs if r.receipt_id in ids), answers={k: v for k, v in data.answers.items() if k in ids})
    return pick(golden, GOLDEN), pick(robust, ROBUST)


def cases():
    return {case["id"]: case for case in rcli.load_cases() if case["id"] in ISOLATED}


def validator(mode: str) -> ExpenseValidator:
    settings = ValidatorSettings.from_config(CONFIG, mode=mode, cache_dir=RUN / "replay", classification_backend="langgraph")
    if mode == "live" and settings.openrouter_api_key is None:
        raise SystemExit("no OPENROUTER_API_KEY")
    return ExpenseValidator(settings)


def run_config(name: str, v: ExpenseValidator, ledger: Ledger | None) -> dict:
    """One configuration over every input. Policy inputs go through the public validator, whole receipts at a time."""
    golden, robust = datasets()
    found = {"batches": {}, "isolated": {}}
    for label, data in (("golden", golden), ("robust", robust)):
        if ledger:
            ledger.unit = f"{name}:{label}"
        started = time.perf_counter()
        batch = v.validate_batch(data.customer_id, list(data.inputs), context_complete=True, langgraph_config=CONFIGS[name])
        found["batches"][label] = {"batch": batch, "wall_ms": round((time.perf_counter() - started) * 1000)}
    # The isolated line, as the robustness harness sends it: the orchestrator with this request's own links.
    settings = v.settings.model_copy(update={"langgraph": v.settings.langgraph.merged(CONFIGS[name])})
    request = validator_module._Request(settings, v.vocabulary, None, None)
    chain = tuple(dataclasses.replace(link, complete=strip_placeholders(link.complete)) for link in request.chain)
    reflection = request.reflection
    if reflection is not None:
        reflection = dataclasses.replace(
            reflection,
            reviewer=dataclasses.replace(reflection.reviewer, complete=strip_placeholders(reflection.reviewer.complete)),
            revisers=tuple(None if r is None else dataclasses.replace(r, complete=strip_placeholders(r.complete)) for r in reflection.revisers),
        )
    for case_id, case in cases().items():
        if ledger:
            ledger.unit = f"{name}:{case_id}"
        started = time.perf_counter()
        normalized = normalize_receipt(line_receipt(case_id, case["input"]), request.translator)
        (line,) = orchestrate(normalized, chain, settings.langgraph.routing, v.vocabulary, reflection)
        found["isolated"][case_id] = {"line": line, "wall_ms": round((time.perf_counter() - started) * 1000), "missing": list(request.chain_missing)}
    return found


def serialize(name: str, found: dict) -> None:
    folder = RUN / "live" / name
    folder.mkdir(parents=True, exist_ok=True)
    out = {}
    for label, item in found["batches"].items():
        out[label] = {"wall_ms": item["wall_ms"], "results": [result.model_dump(mode="json") for result in item["batch"].results]}
    out["isolated"] = {case_id: {"wall_ms": item["wall_ms"], "line": item["line"]._asdict() | {"confidence": None if item["line"].confidence is None else str(item["line"].confidence)}, "missing": item["missing"]} for case_id, item in found["isolated"].items()}
    (folder / "results.json").write_text(json.dumps(out, indent=2, default=str, ensure_ascii=False) + "\n")


def seed_translations() -> None:
    """The one recorded translation the inputs need, copied from the golden replay: no translation is paid for."""
    config = load_config()
    golden, robust = datasets()
    needed = []
    for data in (golden, robust):
        for receipt in data.inputs:
            for line in receipt.line_items:
                if line.language != "en":
                    needed.append(line.description_raw)
    from expense_validator.normalization.translation import PROMPT

    (RUN / "replay").mkdir(parents=True, exist_ok=True)
    for text in needed:
        name = hashlib.sha256("\0".join([config.translation.model, PROMPT, f"{text}\n\n"]).encode()).hexdigest() + ".txt"
        source = ROOT / "evaluation" / "final" / "replay" / name
        if not source.exists():
            raise SystemExit(f"no recorded translation for {text!r}: not running")
        shutil.copy(source, RUN / "replay" / name)
    print(f"translations seeded: {len(needed)}")


def key_usage() -> float:
    import httpx

    key = load_config().openrouter_api_key.get_secret_value()
    return httpx.get("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"}, timeout=30).json()["data"]["usage"]


def preflight() -> None:
    if (RUN / "live").exists():
        raise SystemExit(f"{RUN} already holds a run, and a run is never overwritten")
    golden, robust = datasets()
    lines = sum(len(r.line_items) for r in golden.inputs + robust.inputs) + len(cases())
    print(f"inputs: {len(golden.inputs)} golden and {len(robust.inputs)} robustness receipts, {len(cases())} isolated line: {lines} lines")
    worst_unique = lines * 1 * WORST["decision"] + lines * (1 + 5 + 5) * WORST["chat"]
    print(f"worst case with the shared cache: ${worst_unique:.4f} (every Claude call at its maximum; the guard stops at ${CEILING_USD})")
    validator("live")  # checks every model against its backend and reviewer, with no call
    seed_translations()


def live() -> None:
    preflight()
    ledger = Ledger(RUN / "usage.jsonl")
    install(ledger)
    spend = {"before": key_usage()}
    order = ["D-smoke", "A", "B", "C", "D", "E"]
    v = validator("live")
    try:
        for name in order:
            if name == "D-smoke":
                golden, _ = datasets()
                ledger.unit = "D:smoke"
                smoke = v.validate_batch("northwind", [r for r in golden.inputs if r.receipt_id == "RCP-022"], context_complete=False, langgraph_config=CONFIGS["D"])
                failed = [line.line_id for result in smoke.results for line in result.line_items if line.classification_error]
                print(f"smoke: {ledger.requests} requests, ${ledger.estimate:.4f} estimated, failed lines {failed}")
                if failed:
                    raise BudgetStop(f"smoke test failed on {failed}: not continuing")
                continue
            found = run_config(name, v, ledger)
            serialize(name, found)
            delta = key_usage() - spend["before"]
            print(f"{name}: {ledger.requests} requests so far, ${ledger.estimate:.4f} conservative estimate, key spend ${delta:.4f}")
            if delta > ledger.estimate + 0.005:
                raise BudgetStop(f"the key spent ${delta:.4f}, more than the ${ledger.estimate:.4f} estimate: cost accounting is unreliable")
    finally:
        spend["after"] = key_usage()
        (RUN / "spend.json").write_text(json.dumps({**spend, "delta": spend["after"] - spend["before"], "requests": ledger.requests, "conservative_estimate": ledger.estimate, "budget": BUDGET_USD}, indent=2) + "\n")
        print(f"key spend ${spend['after'] - spend['before']:.4f} for {ledger.requests} requests")


def dry_run() -> None:
    """No network: a stand-in client that answers like the real ones, through the real guard and every stage."""
    preflight()
    ledger = Ledger(RUN / "usage.jsonl")

    def chat_send(**kwargs):
        name = kwargs["response_format"]["json_schema"]["name"]
        if name == "classification_review":
            body = {"verdict": "accept", "reason": "stand-in", "suggested_category": None, "feedback": None}
        else:
            body = {"category": "other", "confidence": 0.9, "recipient_name": None, "recipient_type": None, "venue_type": None, "item_kind": None, "essential_toiletry": None}
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(body)))], usage=SimpleNamespace(prompt_tokens=1700, completion_tokens=60))

    def decisions_create(**kwargs):
        answers = {}
        for question, spec in kwargs["questions"].items():
            options = list(spec["criteria"])
            choice = "other" if question == "category" else jev.NOT_STATED
            answers[question] = {"type": "choice", "choice": choice, "confidence": 1.0, "probabilities": {o: 1.0 if o == choice else 0.0 for o in options}}
        body = {"model": jev.SNAPSHOTS[jev.MODEL], "answers": answers, "usage": {"cost": 0.000067}}
        return SimpleNamespace(model_dump_json=lambda by_alias=True: json.dumps(body))

    fake = lambda: SimpleNamespace(chat=SimpleNamespace(send=chat_send), alpha=SimpleNamespace(decisions=SimpleNamespace(create=decisions_create)))
    install(ledger, fake)
    v = validator("live")
    for name in CONFIGS:
        serialize(name, run_config(name, v, ledger))
    print(f"dry run: {ledger.requests} stand-in requests, ${ledger.estimate:.4f} conservative estimate")


def load(name: str) -> dict:
    return json.loads((RUN / "live" / name / "results.json").read_text())


def evaluate() -> None:
    golden, robust = datasets()
    config = load_config()
    ruleset, fields = rcli._rules(config)
    gold_lines = {line.line_id: line for data in (golden, robust) for answer in data.answers.values() for line in answer.lines}
    gold_lines |= {case_id: SimpleNamespace(category=case["gold"]["category"]) for case_id, case in cases().items()}
    usage = [json.loads(line) for line in (RUN / "usage.jsonl").read_text().splitlines()]
    report = {}
    for name in CONFIGS:
        found = load(name)
        lines = [line for label in ("golden", "robust") for result in found[label]["results"] for line in result["line_items"]]
        lines += [{"line_id": case_id, "category": item["line"]["category"], "confidence": item["line"]["confidence"], "classification_error": item["line"]["error"], "classification_details": item["line"]["details"]} for case_id, item in found["isolated"].items()]
        audits = {line["line_id"]: (line["classification_details"] or {}).get("orchestration", {}) for line in lines}
        policy = {}
        for label, data in (("golden", golden), ("robust", robust)):
            results = [SimpleNamespace(line_items=[SimpleNamespace(**item) for item in result["line_items"]]) for result in found[label]["results"]]
            predictions = result_predictions(results, data.customer_id, Source(kind="actual", name=name, description=f"live configuration {name}", model="live"))
            run = evaluate_predictions(data, predictions, ruleset, fields)
            policy[label] = {
                "dispositions_right": run["disposition"]["accuracy"], "decided": run["disposition"]["decided"], "false_flag": run["disposition"]["false_flag"], "false_pass": run["disposition"]["false_pass"],
                "silent_misses": run["rules"]["fn_by_kind"]["silent"], "rules_strict": {k: run["rules"]["strict"][k] for k in ("tp", "fp", "fn", "precision", "recall")},
                "exc_03": sum("EXC-03" in r["signals"] for r in run["receipts"]), "unresolved": sum(len(r["unresolved"]) for r in run["receipts"]), "open": sum(len(r["open"]) for r in run["receipts"]),
                "receipts": {r["receipt_id"]: {"gold": r["gold_disposition"], "got": r["disposition"], "exceptions": sorted(r["exceptions"]), "gold_exceptions": sorted(r["gold_exceptions"]), "signals": r["signals"], "decided": r["decided"]} for r in run["receipts"]},
            }
        per_line = {
            line["line_id"]: {
                "gold": gold_lines[line["line_id"]].category, "got": line["category"], "right": line["category"] == gold_lines[line["line_id"]].category,
                "confidence": line["confidence"], "error": line["classification_error"], "answered_by": audits[line["line_id"]].get("answered_by"),
                "result": audits[line["line_id"]].get("result"), "attempts": [(a["name"], a.get("revision", 0), a["outcome"], a["confidence"]) for a in audits[line["line_id"]].get("attempts", [])],
                "reviews": [(r["of"], r["revision"], r["outcome"], r["verdict"], r.get("suggested_category"), r.get("feedback")) for r in (audits[line["line_id"]].get("reflection") or {}).get("reviews", [])],
                "unresolved": (audits[line["line_id"]].get("reflection") or {}).get("unresolved"), "calls": (audits[line["line_id"]].get("reflection") or {}).get("calls", len(audits[line["line_id"]].get("attempts", []))),
            }
            for line in lines
        }
        mine = [u for u in usage if u["unit"].startswith(f"{name}:")]
        report[name] = {
            "category_accuracy": f"{sum(p['right'] for p in per_line.values())}/{len(per_line)}",
            "classified": f"{sum(p['got'] is not None and p['error'] is None for p in per_line.values())}/{len(per_line)}",
            "logical_calls": sum(p["calls"] for p in per_line.values()),
            "new_paid_requests": len(mine), "new_paid_by_kind": dict(Counter(u["kind"] for u in mine)),
            "wall_ms": {label: found[label]["wall_ms"] for label in ("golden", "robust")} | {case_id: item["wall_ms"] for case_id, item in found["isolated"].items()},
            "policy": policy, "lines": per_line,
        }
    report["usage"] = {"requests": len(usage), "by_kind": dict(Counter(u["kind"] for u in usage)), "failed": [u for u in usage if not u["ok"]],
                       "latency_ms_by_kind": {kind: sorted(u["latency_ms"] for u in usage if u["kind"] == kind) for kind in ("chat", "decision")},
                       "tokens": {kind: [u.get("usage") for u in usage if u["kind"] == kind][:3] for kind in ("chat", "decision")}}
    (RUN / "metrics.json").write_text(json.dumps(report, indent=2, default=str, ensure_ascii=False) + "\n")
    print("evaluated:", {name: (report[name]["category_accuracy"], report[name]["logical_calls"], report[name]["new_paid_requests"]) for name in CONFIGS})


def verify() -> None:
    """Replay every configuration from the recordings with no client and no network, and compare it to the live run."""
    import socket

    import openrouter

    def refuse(*args, **kwargs):
        raise AssertionError("replay tried to call out")

    socket.socket.connect = refuse
    openrouter.OpenRouter.__init__ = refuse
    v = validator("replay")
    strip = lambda record: {k: val for k, val in record.items() if k != "metadata"} | {"metadata": {k: val for k, val in record["metadata"].items() if k not in ("replayed", "latency_ms")}, "provenance": None}
    for name in CONFIGS:
        found, live_found = run_config(name, v, None), load(name)
        for label in ("golden", "robust"):
            again = [strip(result.model_dump(mode="json")) for result in found["batches"][label]["batch"].results]
            assert again == [strip(result) for result in live_found[label]["results"]], f"{name} {label}: replay differs from the live run"
            assert missing_recordings(found["batches"][label]["batch"].results) == [], f"{name} {label}: missing recordings"
        for case_id, item in found["isolated"].items():
            assert item["line"]._asdict() | {"confidence": None if item["line"].confidence is None else str(item["line"].confidence)} == live_found["isolated"][case_id]["line"], f"{name} {case_id}: replay differs"
            assert not item["missing"], f"{name} {case_id}: missing recordings"
        print(f"{name}: replay matches the live run, with no provider call")


if __name__ == "__main__":
    {"preflight": preflight, "dry-run": dry_run, "live": live, "evaluate": evaluate, "verify": verify}[sys.argv[1]]()
