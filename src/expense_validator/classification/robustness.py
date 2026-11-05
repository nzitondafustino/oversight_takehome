"""Scores the classifier on evaluation/robustness, a developer-authored challenge set. Run from the project root:

    PYTHONPATH=src .venv/bin/python -m expense_validator.classification.robustness --check          # no network
    PYTHONPATH=src .venv/bin/python -m expense_validator.classification.robustness --run first-run  # sends line text

A run is written once to evaluation/robustness/runs/<name>/ and is never overwritten. This reads answers, so
production code must never import it. Only a case's input block reaches the classifier.
"""

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from decimal import Decimal
from pathlib import Path

import httpx
import yaml

from expense_validator.classification.classifier import Complete, Vocabulary, build_classifier, build_prompt, classify_receipt, load_vocabulary
from expense_validator.classification.evaluate import ATTRIBUTES, line_prediction, score_lines
from expense_validator.compiler.validate import read_fields
from expense_validator.config import ROOT, Config, load_config
from expense_validator.engine.models import Receipt
from expense_validator.normalization import Translator, build_translator, normalize_receipt
from expense_validator.offline_eval.dataset import Dataset, load_dataset
from expense_validator.offline_eval.metrics import calibration, rate
from expense_validator.offline_eval.predictions import LinePrediction, PredictionSet, Source
from expense_validator.offline_eval.runner import evaluate_predictions
from expense_validator.policy.schema import RuleSet

FOLDER = ROOT / "evaluation" / "robustness"
LINES = FOLDER / "lines.yaml"
RECEIPTS = FOLDER / "receipts.jsonl"
RECEIPT_ATTRIBUTES = FOLDER / "receipt_attributes.yaml"
CUSTOMER = "robustness"

EXC_03_THRESHOLD = Decimal("0.5")  # Only for this offline table: the engine reads its threshold from the ruleset.
THRESHOLDS = tuple(Decimal(value) for value in ("0.3", "0.5", "0.7", "0.9"))
HIGH_CONFIDENCE = Decimal("0.7")

# The brief caps spending below $5, and the key's own limit is higher. A run costs about a cent.
SPENDING_STOP_USD = 4.95


def line_receipt(case_id: str, case_input: Mapping) -> Receipt:
    """A one-line receipt from a case's input alone. The amounts are placeholders the model never reads."""
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
    """Counts for each group, where a row can be in several. A failed line is wrong on every count here."""
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
    """Gold category to predicted category. A failed line is counted under FAILED, so none disappears."""
    matrix = defaultdict(Counter)
    for row in rows:
        matrix[row["gold_category"]][row["category"] or "FAILED"] += 1
    return {gold: dict(sorted(found.items())) for gold, found in sorted(matrix.items())}


def confidence_levels(rows: Sequence[Mapping]) -> dict:
    """Accuracy at each level the model used, read over acceptable answers. Failed lines have no level."""
    grouped = defaultdict(list)
    for row in rows:
        grouped[str(row["confidence"]) if row["confidence"] is not None else "none"].append(row)
    return {level: rate(sum(1 for row in members if answer_is_acceptable(row)), len(members)) for level, members in sorted(grouped.items())}


def threshold_table(rows: Sequence[Mapping], thresholds: Sequence[Decimal] = THRESHOLDS) -> dict:
    """What each threshold would accept, with EXC-03's rule that the threshold itself is accepted. Offline only."""
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
    """Whether confidence tells right from wrong. Too few errors means this cannot be judged: see `wrong`."""
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
    """A name may carry a title, as in Deputy Mayor T. Nguyen. Every other field must match exactly."""
    if name == "recipient_name" and isinstance(gold, str) and isinstance(predicted, str):
        return gold.casefold() in predicted.casefold()
    return gold is predicted or gold == predicted


def attribute_scores(rows: Sequence[Mapping]) -> dict:
    """Over lines where a field is labelled. A failed line has read nothing, so it is left out and counted apart."""
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
    """The answers EXC-03 would let through wrongly, and those it should have been told to doubt."""

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


def classify_cases(cases: Sequence[Mapping], translator: Translator, complete: Complete, vocabulary: Vocabulary) -> list[dict]:
    rows = []
    for case in cases:
        normalized = normalize_receipt(line_receipt(case["id"], case["input"]), translator)
        classified = classify_receipt(normalized, complete, vocabulary)[0]
        line = normalized.lines[0]
        rows.append(scored_row(case, classified, line.translation or line.translation_error))
    return rows


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


def run_receipts(config: Config, translator: Translator, complete: Complete, vocabulary: Vocabulary, source: Source) -> tuple[dict, PredictionSet]:
    """The whole receipts, through the unchanged engine. These are a separate population from the isolated lines."""
    dataset = load_dataset(RECEIPTS, CUSTOMER)
    classified = []
    for receipt in dataset.inputs:  # Only the receipt reaches the classifier: the answers stay in dataset.answers.
        classified.extend(classify_receipt(normalize_receipt(receipt, translator), complete, vocabulary))
    predictions = PredictionSet(customer_id=CUSTOMER, source=source, predictions=tuple(line_prediction(line) for line in classified if line.error is None))
    labelled = yaml.safe_load(RECEIPT_ATTRIBUTES.read_text())["attributes"]
    ruleset = RuleSet.from_json(config.paths.ruleset.read_text())
    fields = read_fields(config.paths.rule_fields.read_text())
    summary = _engine_summary(evaluate_predictions(dataset, predictions, ruleset, fields))
    summary["attributes"] = attribute_scores(_receipt_attribute_rows(dataset, predictions, labelled))
    summary["failed"] = [line.line_id for line in classified if line.error is not None]
    return summary, predictions


def check_gold(config: Config, vocabulary: Vocabulary) -> list[str]:
    """Problems in the labels themselves. Nothing here uses the network or the model."""
    problems = []
    cases = yaml.safe_load(LINES.read_text())["cases"]
    for case in cases:
        gold = case["gold"]
        for name in [gold["category"], *gold.get("acceptable", [])]:
            if name not in vocabulary.categories:
                problems.append(f"{case['id']}: {name} is not in the taxonomy")
        if gold["category"] in vocabulary.categories and vocabulary.categories[gold["category"]].restricted is not gold["restricted"]:
            problems.append(f"{case['id']}: restricted does not match the taxonomy")
    dataset = load_dataset(RECEIPTS, CUSTOMER)
    for answer in dataset.answers.values():
        for line in answer.lines:
            if vocabulary.categories[line.category].restricted is not line.restricted:
                problems.append(f"{line.line_id}: restricted does not match the taxonomy")
    return problems + _oracle_problems(config, dataset)


def _oracle_problems(config: Config, dataset: Dataset) -> list[str]:
    """Where the engine, given the right categories and attributes, disagrees with an expected result."""
    labelled = yaml.safe_load(RECEIPT_ATTRIBUTES.read_text())["attributes"]
    made = tuple(
        LinePrediction(line_id=line.line_id, category=line.category, restricted=line.restricted, confidence=Decimal("0.9"), **labelled.get(line.line_id, {}))
        for answer in dataset.answers.values()
        for line in answer.lines
    )
    source = Source(kind="oracle", name="gold-check", description="The authored answers, to check them against the engine")
    ruleset = RuleSet.from_json(config.paths.ruleset.read_text())
    run = evaluate_predictions(dataset, PredictionSet(customer_id=CUSTOMER, source=source, predictions=made), ruleset, read_fields(config.paths.rule_fields.read_text()))
    problems = []
    for receipt in run["receipts"]:
        same = receipt["disposition"] == receipt["gold_disposition"] and set(receipt["exceptions"]) == set(receipt["gold_exceptions"]) and set(receipt["signals"]) == set(receipt["gold_signals"])
        if not same:
            problems.append(f"{receipt['receipt_id']}: engine gives {receipt['disposition']} {sorted(receipt['exceptions'])} {receipt['signals']}, authored {receipt['gold_disposition']} {sorted(receipt['gold_exceptions'])} {receipt['gold_signals']}")
    return problems


def key_spend(config: Config) -> float:
    """Total spend on this key so far, which is a project total only if nothing else uses the key."""
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
    rows = classify_cases(yaml.safe_load(LINES.read_text())["cases"], translator, complete, vocabulary)
    source = Source(kind="actual", name=name, description="Robustness receipts: one request per line", model=model)
    receipts, receipt_predictions = run_receipts(config, translator, complete, vocabulary, source)
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
