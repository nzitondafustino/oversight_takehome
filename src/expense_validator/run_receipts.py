"""Validate project receipts and export evaluation/final/receipts.jsonl.

Use --replay DIR for offline execution; other runs may call paid providers.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from expense_validator.classification.backends.registry import BACKENDS
from expense_validator.config import LANGGRAPH, LEGACY_BACKENDS, ROOT
from expense_validator.engine.models import Receipt
from expense_validator.exceptions import ReplayAnswerMissing
from expense_validator.ingestion import IngestionMode, iter_jsonl
from expense_validator.validator import ExpenseValidator

CONFIG = ROOT / "config.yaml"
DATA = ROOT / "data" / "receipts.jsonl"
OUTPUT = ROOT / "evaluation" / "final" / "receipts.jsonl"


def load_receipts(path: Path = DATA) -> list[Receipt]:
    """Load the project receipts, dropping their answers."""
    return sorted((item.receipt for item in iter_jsonl(path, IngestionMode.EVALUATION)), key=lambda receipt: receipt.receipt_id)


def write_jsonl(records: Sequence[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in sorted(records, key=lambda record: record["receipt_id"])))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run every receipt end to end and write the per-receipt JSONL.")
    parser.add_argument("--replay", type=Path, help="a folder of saved model answers: answer only from it, and call nothing")
    parser.add_argument("--out", type=Path, default=OUTPUT)
    parser.add_argument("--classification-model", help="an OpenRouter model ID to classify with, instead of config.yaml's")
    parser.add_argument("--translation-model", help="an OpenRouter model ID to translate with, instead of config.yaml's; translation stays on or off as config.yaml says")
    parser.add_argument("--classification-backend", choices=sorted([*BACKENDS, *LEGACY_BACKENDS, LANGGRAPH]), help="the execution adapter, instead of config.yaml's (openrouter and jev are earlier names); decisions needs --classification-model, such as typesafe/jev-1.14; langgraph uses config.yaml's classification.langgraph")
    args = parser.parse_args(argv)
    models = {"classification_model": args.classification_model, "translation_model": args.translation_model, "classification_backend": args.classification_backend}
    if args.replay:
        validator = ExpenseValidator.from_config(CONFIG, mode="replay", cache_dir=args.replay, **models)
    else:
        validator = ExpenseValidator.from_config(CONFIG, mode="live", **models)  # the config's cache, so known answers are not paid for again
    try:
        batch = validator.validate_batch(load_receipts(), context_complete=True)
    except ReplayAnswerMissing as error:
        print(f"not written: {error}", file=sys.stderr)
        for text in error.missing[:10]:
            print(f"   missing: {text[:120]}", file=sys.stderr)
        return 1
    write_jsonl([result.to_record() for result in batch.results], args.out)
    counts = {disposition: sum(result.disposition == disposition for result in batch.results) for disposition in ("flag", "review", "pass")}
    print(f"wrote {len(batch.results)} receipts to {args.out}: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
