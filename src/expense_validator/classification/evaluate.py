"""Measures the classifier on the labelled receipts. Run from the project root:

    PYTHONPATH=src .venv/bin/python -m expense_validator.classification.evaluate

This sends line text to OpenRouter, and cached answers are reused. It writes metrics and a prediction
file to evaluation/classification/, and that file also works with offline_eval --predictions. This is
the only classification module that reads answers, and production code must never import it.

The answers hold no attributes such as recipients, so those are listed for reading, and not scored here.
"""

import json
from collections import Counter
from collections.abc import Mapping, Sequence

from expense_validator.classification.classifier import ClassifiedLine, build_classifier, classify_receipt, load_vocabulary
from expense_validator.config import ROOT, load_config
from expense_validator.normalization import build_translator, normalize_receipt
from expense_validator.offline_eval.dataset import load_dataset
from expense_validator.offline_eval.metrics import classification_scores, rate
from expense_validator.offline_eval.predictions import LinePrediction, PredictionSet, Source

CUSTOMER = "northwind"
DATA = ROOT / "data" / "receipts.jsonl"
OUTPUT = ROOT / "evaluation" / "classification"
ATTRIBUTES = ("recipient_name", "recipient_type", "venue_type", "item_kind", "essential_toiletry")


def score_lines(lines: Sequence[Mapping]) -> dict:
    """The shared classification scores, plus restricted accuracy and failures. A failed line counts as wrong on both."""
    total = len(lines)
    return {
        **classification_scores(lines),
        "restricted_accuracy": rate(sum(1 for line in lines if line["restricted"] is line["gold_restricted"]), total),
        "failures": rate(sum(1 for line in lines if line["error"] is not None), total),
    }


def line_prediction(classified: ClassifiedLine) -> LinePrediction:
    """A failed line has no prediction, so it stays visibly unclassified. Nothing stands in for what it lacks."""
    return LinePrediction(**{name: value for name, value in classified._asdict().items() if name != "error"})


def _scored_line(classified: ClassifiedLine, description: str, gold_category: str, gold_restricted: bool) -> dict:
    return {
        "line_id": classified.line_id,
        "description": description,
        "gold_category": gold_category,
        "gold_restricted": gold_restricted,
        "category": classified.category,
        "restricted": classified.restricted,
        "confidence": classified.confidence,
        "error": classified.error,
    }


def _attributes_read(classified: ClassifiedLine, description: str) -> dict | None:
    found = {name: getattr(classified, name) for name in ATTRIBUTES if getattr(classified, name) is not None}
    return {"line_id": classified.line_id, "description": description, "category": classified.category, **found} if found else None


def main() -> int:
    config = load_config()
    vocabulary = load_vocabulary(config.paths.taxonomy, config.paths.rule_fields)
    translator = build_translator(config)
    complete = build_classifier(config, vocabulary)
    dataset = load_dataset(DATA, CUSTOMER)

    lines, classified_lines, attributes, translations = [], [], [], Counter()
    for receipt in dataset.inputs:  # Only the receipt reaches the classifier: the answers stay in dataset.answers.
        normalized = normalize_receipt(receipt, translator)
        classified = classify_receipt(normalized, complete, vocabulary)
        translations.update(line.translation_status for line in normalized.lines)
        classified_lines.extend(classified)
        answers = {answer.line_id: answer for answer in dataset.answers[receipt.receipt_id].lines}
        for line, result in zip(normalized.lines, classified):
            answer = answers[line.line_id]
            lines.append(_scored_line(result, line.description_normalized, answer.category, answer.restricted))
            read = _attributes_read(result, line.description_normalized)
            if read:
                attributes.append(read)

    metrics = {
        "model": config.classification.model,
        "translation_model": config.translation.model,
        "translations": dict(sorted(translations.items())),
        "scores": score_lines(lines),
        "confidence_levels": dict(sorted(Counter(str(line.confidence) for line in classified_lines).items())),
        "attributes_read": attributes,
        "failed": [line for line in lines if line["error"] is not None],
        "wrong": [line for line in lines if line["error"] is None and line["category"] != line["gold_category"]],
    }
    source = Source(kind="actual", name="baseline-llm", description="One request per line: category, stated attributes, coarse model-reported confidence", model=config.classification.model)
    predictions = PredictionSet(
        customer_id=CUSTOMER,
        source=source,
        predictions=tuple(line_prediction(line) for line in classified_lines if line.error is None),
    )
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False, default=str) + "\n")
    (OUTPUT / "predictions.json").write_text(predictions.model_dump_json(indent=2, exclude_none=True) + "\n")
    _print_summary(metrics)
    return 0


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
    raise SystemExit(main())
