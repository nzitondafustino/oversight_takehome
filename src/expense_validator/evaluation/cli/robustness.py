"""Score the classifier on the robustness set in evaluation/robustness.

Run with --check (no network) or --run NAME (sends line text). A run is written once and never overwritten.
Run: PYTHONPATH=src .venv/bin/python -m expense_validator.classification.robustness --check
"""

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path

import httpx
import yaml

from expense_validator.classification.backends.llm import build_classifier, build_prompt
from expense_validator.classification.core.common import load_vocabulary
from expense_validator.classification.core.models import Vocabulary
from expense_validator.compiler.validate import read_fields
from expense_validator.config import ROOT, Config, load_config
from expense_validator.evaluation.dataset import load_dataset
from expense_validator.evaluation.predictions import Source
from expense_validator.evaluation.robustness import check_labels, classify_cases, classify_receipts, score_receipts, score_rows
from expense_validator.normalization import build_translator
from expense_validator.policy.schema import RuleSet

FOLDER = ROOT / "evaluation" / "robustness"
LINES = FOLDER / "lines.yaml"
RECEIPTS = FOLDER / "receipts.jsonl"
RECEIPT_ATTRIBUTES = FOLDER / "receipt_attributes.yaml"

# The brief caps spending below $5, and the key's own limit is higher. A run costs about a cent.
SPENDING_STOP_USD = 4.95


def load_cases() -> list[dict]:
    return yaml.safe_load(LINES.read_text())["cases"]


def load_receipt_attributes() -> Mapping:
    return yaml.safe_load(RECEIPT_ATTRIBUTES.read_text())["attributes"]


def _rules(config: Config) -> tuple[RuleSet, dict]:
    return RuleSet.from_json(config.paths.ruleset.read_text()), read_fields(config.paths.rule_fields.read_text())


def check_gold(config: Config, vocabulary: Vocabulary) -> list[str]:
    """Return problems in the labels. Makes no network call."""
    ruleset, fields = _rules(config)
    return check_labels(load_cases(), load_dataset(RECEIPTS), load_receipt_attributes(), vocabulary, ruleset, fields)


def key_spend(config: Config) -> float:
    """Return the total spend recorded for the configured OpenRouter key."""
    response = httpx.get("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {config.openrouter_api_key.get_secret_value()}"}, timeout=30)
    response.raise_for_status()
    return response.json()["data"]["usage"]


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(name: str, config: Config, vocabulary: Vocabulary) -> int:
    folder = FOLDER / "runs" / name
    if folder.exists():
        raise SystemExit(f"{folder} exists, and a run is never overwritten: choose another name")
    spend_before = key_spend(config)
    if spend_before >= SPENDING_STOP_USD:
        raise SystemExit(f"this key has spent ${spend_before:.3f}, at or above the ${SPENDING_STOP_USD} stop: not running")
    model = config.classification.model
    translator = build_translator(config)
    complete = build_classifier(config, vocabulary)
    rows = classify_cases(load_cases(), translator, complete, vocabulary)
    source = Source(kind="actual", name=name, description="Robustness receipts: one request per line", model=model)
    dataset = load_dataset(RECEIPTS)
    ruleset, fields = _rules(config)
    receipts, receipt_predictions = score_receipts(dataset, classify_receipts(dataset, translator, complete, vocabulary), load_receipt_attributes(), ruleset, fields, source)
    metrics = {
        "run": name,
        "model": model,
        "translation_model": config.translation.model,
        "prompt_sha256": hashlib.sha256(build_prompt(vocabulary).encode()).hexdigest(),
        "lines_sha256": _hash(LINES),
        "receipts_sha256": _hash(RECEIPTS),
        "key_spend_usd": {"before": spend_before, "after": key_spend(config)},
        "lines": score_rows(rows),
        "receipts": receipts,
    }
    folder.mkdir(parents=True)
    (folder / "metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False, default=str) + "\n")
    (folder / "line_rows.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False, default=str) + "\n")
    (folder / "receipt_predictions.json").write_text(receipt_predictions.model_dump_json(indent=2, exclude_none=True) + "\n")
    print(f"wrote {folder}: spend ${spend_before:.4f} -> ${metrics['key_spend_usd']['after']:.4f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score the classifier on the robustness set.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="check the labels: no network")
    action.add_argument("--run", metavar="NAME", help="classify and write a new run: sends line text to OpenRouter")
    args = parser.parse_args(argv)
    config = load_config()
    vocabulary = load_vocabulary(config.paths.taxonomy, config.paths.rule_fields)
    if args.run:
        return run(args.run, config, vocabulary)
    problems = check_gold(config, vocabulary)
    print("\n".join(problems) or "the labels hold together")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
