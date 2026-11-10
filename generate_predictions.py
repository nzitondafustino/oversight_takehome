"""Generate predictions for the golden receipts with the expense_validator package, one file per model setup.

Reads data/receipts.jsonl and writes each receipt back in the same format, with its labels replaced by what the
validator predicted:
  - each line item's labels: category, restricted and the model's confidence
  - the receipt's labels: expected_exceptions, review_signals and disposition, in the same shape as the answers
The answers' notes are dropped. Each receipt also gets a "metadata" object: the model, adapter and latency.

Writes predictions/receipts_<setup>.jsonl for each setup, beside predictions/receipts_original.jsonl: a copy of
data/receipts.jsonl, with its answers, for comparison.

Setups:
  jev                    the decisions adapter with typesafe/jev-1.14
  haiku                  the chat_completions adapter with anthropic/claude-haiku-5.5
  langgraph_jev_haiku    LangGraph: Jev first, Haiku when Jev fails or abstains (confidence never routes a line)
  langgraph_haiku_jev    LangGraph: Haiku first, then Jev
  langgraph_haiku_haiku  LangGraph: Haiku first, then Haiku again

By default the validator answers only from saved model answers and calls nothing: the local cache (.cache/llm)
and the answers committed with earlier runs (SAVED), gathered into one temporary folder. A setup whose answers are
not all saved is skipped, with how many are missing. --live calls OpenRouter for what is not saved, which costs
money and needs OPENROUTER_API_KEY; its new answers go to .cache/llm.

    python generate_predictions.py                       # every setup, saved answers only
    python generate_predictions.py --setup jev haiku     # some setups
    python generate_predictions.py --live                # call the models for answers not saved
"""

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from expense_validator import ExpenseValidator  # noqa: E402
from expense_validator.exceptions import ReplayAnswerMissing  # noqa: E402
from expense_validator.run_receipts import load_receipts  # noqa: E402

CONFIG = ROOT / "config.yaml"
DATA = ROOT / "data" / "receipts.jsonl"
# Folders of saved model answers, one file per answer, named by a hash of the model, prompt and text (Jev's in a
# jev/ subfolder). Earlier folders win: each committed run is one consistent set, its translations included, so it
# comes before the local cache, whose later live runs may have saved another translation of the same line.
SAVED = [
    ROOT / "evaluation" / "final" / "replay",  # Haiku, with its translations
    ROOT / "evaluation" / "jev" / "runs" / "first-run" / "replay",  # Jev
    ROOT / "evaluation" / "langgraph" / "runs" / "live-claude-jev-2" / "replay",  # LangGraph, Jev then Haiku
    ROOT / ".cache" / "llm",
]
JEV = {"backend": "decisions", "model": "typesafe/jev-1.14"}
HAIKU = {"backend": "chat_completions", "model": "anthropic/claude-haiku-5.5"}


def chain(first: dict, then: dict) -> dict:
    """A LangGraph setup: the first backend, then the second when routing (config.yaml) sends a line on."""
    return {"classification_backend": "langgraph", "langgraph_config": {"backends": [{"name": "first", **first}, {"name": "then", **then}]}}


SETUPS = {
    "jev": {"classification_backend": JEV["backend"], "classification_model": JEV["model"]},
    "haiku": {"classification_backend": HAIKU["backend"], "classification_model": HAIKU["model"]},
    "langgraph_jev_haiku": chain(JEV, HAIKU),
    "langgraph_haiku_jev": chain(HAIKU, JEV),
    "langgraph_haiku_haiku": chain(HAIKU, HAIKU),
}


def predicted(source: dict, record: dict, policy_docs: dict[str, str]) -> dict:
    """The source receipt with its labels replaced by the validator's record of it."""
    lines = {line["line_id"]: line for line in record["line_items"]}
    receipt = {key: value for key, value in source.items() if key not in ("labels", "notes")}
    receipt["line_items"] = [
        {**{key: value for key, value in item.items() if key != "labels"},
         "labels": {name: lines[item["line_id"]][name] for name in ("category", "restricted", "confidence")}}
        for item in source["line_items"]
    ]
    receipt["labels"] = {
        "expected_exceptions": [
            {"rule_id": item["rule_id"], "policy_doc": item["policy_doc"], "severity": item["severity"], "line_ids": item["line_ids"], "reason": item["explanation"]}
            for item in record["exceptions"]
        ],
        "review_signals": [{"rule_id": item["rule_id"], "policy_doc": policy_docs.get(item["rule_id"]), "reason": item["reason"]} for item in record["review_signals"]],
        "disposition": record["disposition"],
    }
    metadata = record["metadata"]
    receipt["metadata"] = {name: metadata.get(name) for name in ("model", "classification_backend", "translation_model", "replayed", "latency_ms")}
    return receipt


def saved_answers(folder: Path) -> Path:
    """Every saved answer in one folder, for replay. Same name means same model, prompt and text, so the same answer."""
    for source in SAVED:
        for path in source.rglob("*") if source.is_dir() else ():
            target = folder / path.relative_to(source)
            if path.is_file() and not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
    return folder


def run(name: str, live: bool, replay: Path | None, out_dir: Path, sources: dict[str, dict], policy_docs: dict[str, str]) -> bool:
    if live:  # the config's cache: saved answers are reused, new ones kept
        validator = ExpenseValidator.from_config(CONFIG, mode="live", **SETUPS[name])
    else:
        validator = ExpenseValidator.from_config(CONFIG, mode="replay", cache_dir=replay, **SETUPS[name])
    try:
        batch = validator.validate_batch(load_receipts(DATA), context_complete=True)
    except ReplayAnswerMissing as error:
        print(f"{name}: skipped, {len(error.missing)} model answers are not saved (run with --live to get them)", file=sys.stderr)
        return False
    records = sorted((result.to_record() for result in batch.results), key=lambda record: record["receipt_id"])
    path = out_dir / f"receipts_{name}.jsonl"
    path.write_text("".join(json.dumps(predicted(sources[record["receipt_id"]], record, policy_docs), ensure_ascii=False) + "\n" for record in records))
    counts = {disposition: sum(record["disposition"] == disposition for record in records) for disposition in ("flag", "review", "pass")}
    print(f"{name}: {len(records)} receipts -> {path.relative_to(ROOT)} {counts}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate predictions for the golden receipts, one file per model setup.")
    parser.add_argument("--setup", nargs="+", choices=sorted(SETUPS), default=list(SETUPS), help="which setups to run (all by default)")
    parser.add_argument("--live", action="store_true", help="call OpenRouter for answers not saved: costs money, needs OPENROUTER_API_KEY")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "predictions")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(DATA, args.out_dir / "receipts_original.jsonl")  # the answers, as given, to compare with
    sources = {receipt["receipt_id"]: receipt for receipt in map(json.loads, DATA.read_text().splitlines())}
    policy_docs = {rule["rule_id"]: rule["policy_doc"] for rule in json.loads((ROOT / "rules" / "ruleset.json").read_text())["rules"]}
    with tempfile.TemporaryDirectory() as folder:
        replay = None if args.live else saved_answers(Path(folder))
        done = [run(name, args.live, replay, args.out_dir, sources, policy_docs) for name in args.setup]
    return 0 if all(done) else 1


if __name__ == "__main__":
    sys.exit(main())
