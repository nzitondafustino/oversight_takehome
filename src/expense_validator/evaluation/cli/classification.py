"""Measure the classifier on the labelled receipts, and write evaluation/classification. Sends line text to OpenRouter.

Run: PYTHONPATH=src .venv/bin/python -m expense_validator.classification.evaluate
"""

import json
import sys
from collections.abc import Mapping
from pathlib import Path

from expense_validator.classification.backends.llm import build_classifier, classify_receipt
from expense_validator.classification.core.common import load_vocabulary
from expense_validator.config import ROOT, load_config
from expense_validator.evaluation.classification import classification_metrics, classification_predictions
from expense_validator.evaluation.dataset import load_dataset
from expense_validator.evaluation.predictions import PredictionSet
from expense_validator.normalization import build_translator, normalize_receipt

DATA = ROOT / "data" / "receipts.jsonl"
OUTPUT = ROOT / "evaluation" / "classification"


def main() -> int:
    config = load_config()
    vocabulary = load_vocabulary(config.paths.taxonomy, config.paths.rule_fields)
    translator = build_translator(config)
    complete = build_classifier(config, vocabulary)
    dataset = load_dataset(DATA)

    classified = []
    for receipt in dataset.inputs:  # Only the receipt reaches the classifier: the answers stay in dataset.answers.
        normalized = normalize_receipt(receipt, translator)
        classified.append((normalized, classify_receipt(normalized, complete, vocabulary)))

    metrics = classification_metrics(dataset, classified, model=config.classification.model, translation_model=config.translation.model)
    write_results(OUTPUT, metrics, classification_predictions(classified, config.classification.model))
    _print_summary(metrics)
    return 0


def write_results(folder: Path, metrics: Mapping, predictions: PredictionSet) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False, default=str) + "\n")
    (folder / "predictions.json").write_text(predictions.model_dump_json(indent=2, exclude_none=True) + "\n")


def _print_summary(metrics: Mapping) -> None:
    scores = metrics["scores"]
    print(f"model {metrics['model']} | translations {metrics['translations']}")
    for label, name in (("category accuracy", "accuracy"), ("restricted accuracy", "restricted_accuracy"), ("failures", "failures")):
        print(f"{label}: {scores[name]['numerator']}/{scores[name]['denominator']}")
    print(f"macro F1: {scores['macro']['f1']} | restricted F1: {scores['restricted']['f1']}")
    print(f"confidence levels: {metrics['confidence_levels']}")
    for line in metrics["failed"] + metrics["wrong"]:
        print(f"{line['line_id']:12} {line['description'][:44]:44} gold={line['gold_category']} got={line['category']} {line['error'] or ''}")
    for read in metrics["attributes_read"]:
        print(read)


if __name__ == "__main__":
    sys.exit(main())
