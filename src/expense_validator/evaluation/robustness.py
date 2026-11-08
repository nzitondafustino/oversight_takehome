"""Score the classifier on the developer-authored robustness set. Reads no file.

Two populations: isolated lines, each classified on a one-line receipt and scored by score_rows, and whole
receipts, run through the engine by score_receipts. check_labels checks the labels themselves. The command
line, in cli/robustness.py, loads the set and writes runs.
"""

import json
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from decimal import Decimal

from expense_validator.classification.backends.llm import classify_receipt
from expense_validator.classification.core.models import ClassifiedLine, Complete, Vocabulary
from expense_validator.engine.models import Receipt
from expense_validator.evaluation.classification import ATTRIBUTES, line_prediction, score_lines
from expense_validator.evaluation.dataset import Dataset
from expense_validator.evaluation.metrics import calibration, rate
from expense_validator.evaluation.policy_outputs import evaluate_predictions
from expense_validator.evaluation.predictions import LinePrediction, PredictionSet, Source
from expense_validator.normalization import Translator, normalize_receipt
from expense_validator.policy.schema import RuleSet

EXC_03_THRESHOLD = Decimal("0.5")  # Only for this offline table: the engine reads its threshold from the ruleset.
THRESHOLDS = tuple(Decimal(value) for value in ("0.3", "0.5", "0.7", "0.9"))
HIGH_CONFIDENCE = Decimal("0.7")


PLACEHOLDER_FIELDS = ("quantity", "unit_price", "currency")


def line_receipt(case_id: str, case_input: Mapping) -> Receipt:
    """Build a one-line receipt from a case's input. Its amounts are placeholders."""
    line = {"line_id": case_id, "description_raw": case_input["description"], "language": case_input["language"], "quantity": 1, "unit_price": "10", "amount": "10"}
    return Receipt.model_validate(
        {
            "receipt_id": case_id,
            "employee_id": "E",
            "employee_country": "US",
            "entity": "GLOBAL",
            "submitted_expense_type": "Travel - Other",
            "merchant_name": case_input["merchant_name"],
            "mcc_description": case_input["mcc_description"],
            "itemized": case_input["itemized"],
            "currency": "USD",
            "fx_rate_to_usd": "1",
            "total_amount": "10",
            "line_items": [line],
        }
    )


def scored_row(case: Mapping, classified, translation: object) -> dict:
    gold = case["gold"]
    return {
        "line_id": case["id"],
        "description": case["input"]["description"],
        "translation": translation,
        "gold_category": gold["category"],
        "gold_restricted": gold["restricted"],
        "acceptable": [gold["category"], *gold.get("acceptable", [])],
        "category": classified.category,
        "restricted": classified.restricted,
        "confidence": classified.confidence,
        "error": classified.error,
        "ambiguity": case["ambiguity"],
        "challenges": case["challenges"],
        # EXC-03 exempts a non-itemized `other`, so its confidence decides nothing.
        "exempt": not case["input"]["itemized"] and classified.category == "other",
        "gold_attributes": {name: gold.get(name) for name in ATTRIBUTES if name not in gold.get("unlabelled", [])},
        "predicted_attributes": {name: getattr(classified, name) for name in ATTRIBUTES},
    }


def answer_is_acceptable(row: Mapping) -> bool:
    return row["category"] in row["acceptable"]


def by_group(rows: Sequence[Mapping], groups_of: Callable[[Mapping], Iterable[str]]) -> dict:
    """Count results per group. A failed line is wrong on every count."""
    grouped = defaultdict(list)
    for row in rows:
        for group in groups_of(row):
            grouped[group].append(row)
    return {
        group: {
            "cases": len(members),
            "failed": sum(1 for row in members if row["error"] is not None),
            "strict_right": sum(1 for row in members if row["category"] == row["gold_category"]),
            "acceptable_right": sum(1 for row in members if answer_is_acceptable(row)),
            "restricted_right": sum(1 for row in members if row["restricted"] is row["gold_restricted"]),
        }
        for group, members in sorted(grouped.items())
    }


def confusion_matrix(rows: Sequence[Mapping]) -> dict:
    """Count gold against predicted categories, with failures under FAILED."""
    matrix = defaultdict(Counter)
    for row in rows:
        matrix[row["gold_category"]][row["category"] or "FAILED"] += 1
    return {gold: dict(sorted(found.items())) for gold, found in sorted(matrix.items())}


def confidence_levels(rows: Sequence[Mapping]) -> dict:
    """Return accuracy at each confidence level, over acceptable answers."""
    grouped = defaultdict(list)
    for row in rows:
        grouped[str(row["confidence"]) if row["confidence"] is not None else "none"].append(row)
    return {level: rate(sum(1 for row in members if answer_is_acceptable(row)), len(members)) for level, members in sorted(grouped.items())}


def threshold_table(rows: Sequence[Mapping], thresholds: Sequence[Decimal] = THRESHOLDS) -> dict:
    """Return what each threshold would accept. A line at the threshold is accepted, as in EXC-03."""
    table = {}
    for threshold in thresholds:
        accepted = [row for row in rows if row["exempt"] or (row["confidence"] is not None and row["confidence"] >= threshold)]
        accepted_ids = {row["line_id"] for row in accepted}
        set_aside = [row for row in rows if row["line_id"] not in accepted_ids]
        table[str(threshold)] = {
            "coverage": rate(len(accepted), len(rows)),
            "accuracy_when_accepted": rate(sum(1 for row in accepted if answer_is_acceptable(row)), len(accepted)),
            "accepted_wrong": sum(1 for row in accepted if not answer_is_acceptable(row)),
            "set_aside_right": sum(1 for row in set_aside if answer_is_acceptable(row)),
            "set_aside_wrong": sum(1 for row in set_aside if not answer_is_acceptable(row)),
        }
    return table


def confidence_separation(rows: Sequence[Mapping]) -> dict:
    """Return whether confidence separates right from wrong answers."""
    points = [(row["confidence"], answer_is_acceptable(row)) for row in rows if row["confidence"] is not None]
    right = [float(confidence) for confidence, correct in points if correct]
    wrong = [float(confidence) for confidence, correct in points if not correct]
    return {
        "right": len(right),
        "wrong": len(wrong),
        "mean_confidence_right": round(sum(right) / len(right), 3) if right else None,
        "mean_confidence_wrong": round(sum(wrong) / len(wrong), 3) if wrong else None,
        "calibration": calibration(points),
    }


def _attribute_matches(name: str, gold: object, predicted: object) -> bool:
    """A recipient name may carry a title; every other field must match exactly."""
    if name == "recipient_name" and isinstance(gold, str) and isinstance(predicted, str):
        return gold.casefold() in predicted.casefold()
    return gold is predicted or gold == predicted


def attribute_scores(rows: Sequence[Mapping]) -> dict:
    """Score each labelled attribute. Failed lines are excluded."""
    scores = {name: Counter(labelled=0, right=0, fabricated=0, missed=0, wrong_value=0) for name in ATTRIBUTES}
    for row in rows:
        if row["error"] is not None:
            continue
        for name, gold in row["gold_attributes"].items():
            predicted = row["predicted_attributes"][name]
            count = scores[name]
            count["labelled"] += 1
            if _attribute_matches(name, gold, predicted):
                count["right"] += 1
            elif gold is None:
                count["fabricated"] += 1
            elif predicted is None:
                count["missed"] += 1
            else:
                count["wrong_value"] += 1
    return {name: dict(count) for name, count in scores.items()}


def safety_report(rows: Sequence[Mapping], threshold: Decimal = EXC_03_THRESHOLD) -> dict:
    """List wrong answers EXC-03 would accept and other confidence risks."""

    def named(selected: Iterable[Mapping]) -> list[str]:
        return [row["line_id"] for row in selected]

    scored = [row for row in rows if row["confidence"] is not None]
    return {
        "failed": named(row for row in rows if row["error"] is not None),
        "accepted_wrong_by_exc_03": named(row for row in scored if not row["exempt"] and row["confidence"] >= threshold and not answer_is_acceptable(row)),
        "high_confidence_wrong": named(row for row in scored if row["confidence"] >= HIGH_CONFIDENCE and not answer_is_acceptable(row)),
        "low_confidence_right": named(row for row in scored if row["confidence"] < threshold and answer_is_acceptable(row)),
        "indeterminate_at_high_confidence": named(row for row in scored if row["ambiguity"] == "indeterminate" and row["confidence"] >= HIGH_CONFIDENCE),
        "indeterminate_accepted_by_exc_03": named(row for row in scored if row["ambiguity"] == "indeterminate" and row["confidence"] >= threshold),
        "official_without_support": named(
            row for row in rows if row["predicted_attributes"]["recipient_type"] == "government_official" and row["gold_attributes"].get("recipient_type") != "government_official"
        ),
    }


def score_rows(rows: Sequence[Mapping]) -> dict:
    return {
        "scores": score_lines(rows),
        "by_ambiguity": by_group(rows, lambda row: [row["ambiguity"]]),
        "by_challenge": by_group(rows, lambda row: row["challenges"]),
        "confusion_matrix": confusion_matrix(rows),
        "confidence_levels": confidence_levels(rows),
        "confidence_separation": confidence_separation(rows),
        "thresholds": threshold_table(rows),
        "attributes": attribute_scores(rows),
        "safety": safety_report(rows),
    }


def without_placeholders(complete: Complete) -> Complete:
    """Remove placeholder amounts from each request, so the cache keys on what is sent."""

    def send(prompt: str, text: str) -> str:
        line = {name: value for name, value in json.loads(text).items() if name not in PLACEHOLDER_FIELDS}
        return complete(prompt, json.dumps(line, ensure_ascii=False))

    return send


def classify_cases(cases: Sequence[Mapping], translator: Translator, complete: Complete, vocabulary: Vocabulary) -> list[dict]:
    """Classify the isolated lines."""
    rows = []
    send = without_placeholders(complete)
    for case in cases:
        normalized = normalize_receipt(line_receipt(case["id"], case["input"]), translator)
        classified = classify_receipt(normalized, send, vocabulary)[0]
        line = normalized.lines[0]
        rows.append(scored_row(case, classified, line.translation or line.translation_error))
    return rows


def classify_receipts(dataset: Dataset, translator: Translator, complete: Complete, vocabulary: Vocabulary) -> list[ClassifiedLine]:
    """Classify every line of the whole receipts, with their real amounts."""
    classified = []
    for receipt in dataset.inputs:  # Only the receipt reaches the classifier: the answers stay in dataset.answers.
        classified.extend(classify_receipt(normalize_receipt(receipt, translator), complete, vocabulary))
    return classified


def _engine_summary(run: Mapping, attributes: Mapping | None = None) -> dict:
    return {
        "disposition": run["disposition"],
        "rules": run["rules"]["strict"],
        "exact_match": run["exact_match"],
        "classification_accuracy": run["classification"]["accuracy"],
        "receipts": [
            {name: receipt[name] for name in ("receipt_id", "gold_disposition", "disposition", "decided", "exceptions", "gold_exceptions", "signals", "gold_signals", "unresolved", "open", "uncertainty")}
            for receipt in run["receipts"]
        ],
    }


def _receipt_attribute_rows(dataset: Dataset, predictions: PredictionSet, labelled: Mapping) -> list[dict]:
    found = predictions.by_line()
    rows = []
    for answer in dataset.answers.values():
        for line in answer.lines:
            predicted = found.get(line.line_id)
            rows.append(
                {
                    "error": None if predicted else "no prediction",
                    "gold_attributes": {name: labelled.get(line.line_id, {}).get(name) for name in ATTRIBUTES},
                    "predicted_attributes": {name: getattr(predicted, name, None) for name in ATTRIBUTES},
                }
            )
    return rows


def score_receipts(dataset: Dataset, classified: Sequence[ClassifiedLine], labelled: Mapping, ruleset: RuleSet, fields: Mapping[str, dict], source: Source) -> tuple[dict, PredictionSet]:
    """Evaluate the whole receipts through the engine, a separate population from the lines.

    Args:
        dataset: The labelled whole receipts.
        classified: Their classified lines.
        labelled: The authored attributes, by line ID.
        ruleset: The ruleset the engine runs.
        fields: The rule fields.
        source: What made the classifications.
    """
    predictions = PredictionSet(source=source, predictions=tuple(line_prediction(line) for line in classified if line.error is None))
    summary = _engine_summary(evaluate_predictions(dataset, predictions, ruleset, fields))
    summary["attributes"] = attribute_scores(_receipt_attribute_rows(dataset, predictions, labelled))
    summary["failed"] = [line.line_id for line in classified if line.error is not None]
    return summary, predictions


def check_labels(cases: Sequence[Mapping], dataset: Dataset, labelled: Mapping, vocabulary: Vocabulary, ruleset: RuleSet, fields: Mapping[str, dict]) -> list[str]:
    """Return problems in the labels of the isolated lines and the whole receipts. Makes no network call."""
    problems = []
    for case in cases:
        gold = case["gold"]
        for name in [gold["category"], *gold.get("acceptable", [])]:
            if name not in vocabulary.categories:
                problems.append(f"{case['id']}: {name} is not in the taxonomy")
        if gold["category"] in vocabulary.categories and vocabulary.categories[gold["category"]].restricted is not gold["restricted"]:
            problems.append(f"{case['id']}: restricted does not match the taxonomy")
    for answer in dataset.answers.values():
        for line in answer.lines:
            if vocabulary.categories[line.category].restricted is not line.restricted:
                problems.append(f"{line.line_id}: restricted does not match the taxonomy")
    return problems + _oracle_problems(dataset, labelled, ruleset, fields)


def _oracle_problems(dataset: Dataset, labelled: Mapping, ruleset: RuleSet, fields: Mapping[str, dict]) -> list[str]:
    """Return receipts where the engine, given the authored answers, disagrees with them."""
    made = tuple(
        LinePrediction(line_id=line.line_id, category=line.category, restricted=line.restricted, confidence=Decimal("0.9"), **labelled.get(line.line_id, {}))
        for answer in dataset.answers.values()
        for line in answer.lines
    )
    source = Source(kind="oracle", name="gold-check", description="The authored answers, to check them against the engine")
    run = evaluate_predictions(dataset, PredictionSet(source=source, predictions=made), ruleset, fields)
    problems = []
    for receipt in run["receipts"]:
        same = receipt["disposition"] == receipt["gold_disposition"] and set(receipt["exceptions"]) == set(receipt["gold_exceptions"]) and set(receipt["signals"]) == set(receipt["gold_signals"])
        if not same:
            problems.append(f"{receipt['receipt_id']}: engine gives {receipt['disposition']} {sorted(receipt['exceptions'])} {receipt['signals']}, authored {receipt['gold_disposition']} {sorted(receipt['gold_exceptions'])} {receipt['gold_signals']}")
    return problems
