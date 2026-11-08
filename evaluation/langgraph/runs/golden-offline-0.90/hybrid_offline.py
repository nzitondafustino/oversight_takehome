"""Offline hybrid evaluation: direct Jev, direct LLM and LangGraph in both orders, from recordings only.

Run with the network blocked. Reads the repository's recordings and writes one new run folder, never overwriting
one. Every request is answered from a recording made for exactly that model, prompt and input; a run with any
missing recording is refused before it is scored. Routing is at the configured threshold, 0.90, and is not tuned.
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

from expense_validator import ExpenseValidator
from expense_validator.classification.backends import jev
from expense_validator.classification.backends.registry import BACKENDS
from expense_validator.classification.core.common import load_vocabulary
from expense_validator.classification.orchestrator import Link, classify_receipt as orchestrate
from expense_validator.compiler.llm_extract import cached
from expense_validator.config import ROOT, load_config
from expense_validator.evaluation.classification import missing_recordings, result_predictions, routing_summary
from expense_validator.evaluation.cli import policy as policy_cli
from expense_validator.evaluation.cli import robustness as rcli
from expense_validator.evaluation.dataset import load_dataset
from expense_validator.evaluation.predictions import Source
from expense_validator.evaluation.robustness import line_receipt, without_placeholders
from expense_validator.exceptions import ClassificationError, ReplayAnswerMissing, TranslationError
from expense_validator.models import ValidatorSettings
from expense_validator.normalization import LlmTranslator, normalize_receipt
from expense_validator.normalization.translation import replay_only
from expense_validator.run_receipts import load_receipts, write_jsonl

CONFIG = ROOT / "config.yaml"
RUN = ROOT / "evaluation" / "langgraph" / "runs" / "golden-offline-0.90"
GOLDEN_REPLAY = ROOT / "evaluation" / "final" / "replay"
JEV_GOLDEN = ROOT / "evaluation" / "jev" / "runs" / "first-run" / "replay"
JEV_ROBUSTNESS = ROOT / "evaluation" / "jev" / "runs" / "robustness-first-run" / "replay"
LLM_MODEL, JEV_MODEL = load_config().classification.model, jev.MODEL
JEV_FIRST = [{"name": "primary", "backend": "jev", "model": JEV_MODEL}, {"name": "fallback", "backend": "openrouter", "model": LLM_MODEL}]
CONFIGURATIONS = {
    "direct-jev": {"classification_backend": "jev", "classification_model": JEV_MODEL},
    "direct-llm": {},
    "langgraph-jev-first": {"classification_backend": "langgraph", "langgraph_config": {"backends": JEV_FIRST}},
    "langgraph-llm-first": {"classification_backend": "langgraph", "langgraph_config": {"backends": [{"name": "primary", "backend": "openrouter", "model": LLM_MODEL}, {"name": "fallback", "backend": "jev", "model": JEV_MODEL}]}},
}


def golden(folder: Path) -> dict:
    receipts = load_receipts()
    validator = ExpenseValidator.from_config(CONFIG, mode="replay", cache_dir=folder)
    found = {}
    for name, overrides in CONFIGURATIONS.items():
        batch = validator.validate_batch("northwind", receipts, context_complete=True, **overrides)  # a direct backend raises on any gap
        gaps = missing_recordings(batch.results)
        if gaps:
            raise SystemExit(f"{name}: {len(gaps)} missing recordings, so it is not scored: {gaps[:3]}")
        write_jsonl([result.to_record() for result in batch.results], RUN / name / "receipts.jsonl")
        model = batch.results[0].metadata.model
        predictions = result_predictions(batch.results, "northwind", Source(kind="actual", name=name, description=f"{name}, replayed from recordings at routing threshold 0.90", model=model))
        (RUN / name / "predictions.json").write_text(predictions.model_dump_json(indent=2, exclude_none=True) + "\n")
        lines = [line for result in batch.results for line in result.line_items]
        found[name] = {
            "model": model,
            "rejected_lines": [line.line_id for line in lines if line.classification_error],
            "routing": routing_summary(batch.results),
            "dispositions": {result.receipt_id: result.disposition for result in batch.results},
            "routed_lines": {
                line.line_id: [(a["name"], a["outcome"], a["confidence"]) for a in line.classification_details["orchestration"]["attempts"]]
                for line in lines if (line.classification_details or {}).get("orchestration", {}).get("fallback")
            },
        }
    return found


def robustness_gaps() -> dict:
    """Jev-first routing at 0.90 on the robustness set, from Jev's recordings and an empty LLM folder: every LLM
    answer the routing would need is a missing recording, listed exactly. Nothing is scored."""
    config = load_config()
    vocabulary = load_vocabulary(config.paths.taxonomy, config.paths.rule_fields)
    base = ValidatorSettings.from_config(CONFIG, mode="replay", cache_dir=JEV_ROBUSTNESS)
    empty = Path(tempfile.mkdtemp())
    misses = {"jev": [], "llm": [], "translation": []}

    def link(name, backend_name, model, folder, strip):
        backend = BACKENDS[backend_name]
        complete = cached(replay_only(misses[name], ClassificationError), backend.cache_key(model, vocabulary), folder / backend.cache_folder)
        own = base.model_copy(update={"classification_backend": backend_name, "classification_model": model})
        return Link(name, backend_name, model, backend, without_placeholders(complete) if strip else complete, own)

    translator = LlmTranslator(cached(replay_only(misses["translation"], TranslationError), config.translation.model, JEV_ROBUSTNESS))
    routing = config.classification.langgraph.routing
    found = {}
    for population, items, strip in (
        ("isolated_lines", [normalize_receipt(line_receipt(case["id"], case["input"]), translator) for case in rcli.load_cases()], True),
        ("receipts", [normalize_receipt(receipt, translator) for receipt in load_dataset(rcli.RECEIPTS, rcli.CUSTOMER).inputs], False),
    ):
        misses["jev"].clear(), misses["llm"].clear()
        chain = (link("jev", "jev", JEV_MODEL, JEV_ROBUSTNESS, strip), link("llm", "openrouter", LLM_MODEL, empty, strip))
        needing = []
        for normalized in items:
            for line in orchestrate(normalized, chain, routing, vocabulary):
                attempts = line.details["orchestration"]["attempts"]
                if len(attempts) > 1:
                    needing.append({"line_id": line.line_id, "jev": attempts[0]["outcome"], "jev_confidence": attempts[0]["confidence"], "llm": attempts[1]["outcome"]})
        lines = sum(len(normalized.lines) for normalized in items)
        assert not misses["jev"] and not misses["translation"], "Jev's recordings and the translations must be complete"
        found[population] = {
            "lines": lines,
            "jev_recorded": lines,
            "llm_recorded_at_current_keys": 0,
            "jev_first_lines_needing_llm": len(needing),
            "llm_first_lines_needing_llm": lines,
            "needing": needing,
        }
    shutil.rmtree(empty)
    return found


def main() -> int:
    if RUN.exists():
        raise SystemExit(f"{RUN} exists, and a run is never overwritten")
    with tempfile.TemporaryDirectory() as temporary:
        folder = Path(temporary) / "replay"
        shutil.copytree(GOLDEN_REPLAY, folder)  # the LLM's golden answers and the translations
        shutil.copytree(JEV_GOLDEN / "jev", folder / "jev")  # Jev's first-run answers
        runs = golden(folder)
    policy_cli.main([arg for name in CONFIGURATIONS for arg in ("--predictions", str(RUN / name / "predictions.json"))] + ["--out", str(RUN / "policy")])
    policy = json.loads((RUN / "policy" / "metrics.json").read_text())["scenarios"]
    summary = {}
    for name, run in runs.items():
        scored = policy[name]
        receipts = [json.loads(line) for line in (RUN / "policy" / "receipts" / f"{name}.jsonl").read_text().splitlines()]
        assert {r["receipt_id"]: r["disposition"] for r in receipts} == run["dispositions"], f"{name}: the harness and the validator disagree"
        summary[name] = {
            "model": run["model"],
            "category_accuracy": scored["classification"]["accuracy"],
            "macro_f1": scored["classification"]["macro"]["f1"],
            "rejected_lines": run["rejected_lines"],
            "routing": run["routing"],
            "routed_lines": run["routed_lines"],
            "dispositions_right": scored["disposition"]["accuracy"],
            "false_flag": scored["disposition"]["false_flag"],
            "false_pass": scored["disposition"]["false_pass"],
            "decided": scored["disposition"]["decided"],
            "silent_misses": scored["rules"]["fn_by_kind"]["silent"],
            "unresolved_findings": sum(len(r["unresolved"]) for r in receipts),
            "open_questions": sum(len(r["open"]) for r in receipts),
            "rules_strict": {k: scored["rules"]["strict"][k] for k in ("tp", "fp", "fn", "precision", "recall", "f1")},
            "rules_excluding_exc_03": {k: scored["rules"]["diagnostic"][k] for k in ("tp", "fp", "fn", "precision", "recall", "f1")},
            "rules_with_errors": {rule: {k: row[k] for k in ("tp", "fp", "fn")} for rule, row in scored["rules"]["rules"].items() if row["fp"] or row["fn"]},
        }
    (RUN / "metrics.json").write_text(json.dumps({"threshold": 0.9, "golden": summary, "robustness_coverage": robustness_gaps()}, indent=2, default=str) + "\n")
    shutil.copy(__file__, RUN / "hybrid_offline.py")
    print("wrote", RUN)
    return 0


if __name__ == "__main__":
    sys.exit(main())
