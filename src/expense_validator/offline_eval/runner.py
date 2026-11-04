"""Runs one set of classifications through evaluate_receipts, and scores what comes back.

Each run has its own customer, context and result: nothing is shared or cached between runs.
"""

import hashlib
import json
from collections.abc import Mapping

from expense_validator.engine.evaluation import ReceiptEvaluation, evaluate_receipts
from expense_validator.engine.inputs import admit_receipt
from expense_validator.offline_eval import metrics
from expense_validator.offline_eval.dataset import Dataset
from expense_validator.offline_eval.predictions import PredictionSet, apply_predictions
from expense_validator.policy.schema import CheckRule, ProcessRule, RuleSet


def _threshold(ruleset: RuleSet):
    return next(rule.params["min_confidence"].value for rule in ruleset.rules if isinstance(rule, ProcessRule) and rule.directive == "uncertainty")


def critical_rules(ruleset: RuleSet) -> list[str]:
    return sorted(rule.rule_id for rule in ruleset.rules if isinstance(rule, CheckRule) and rule.severity == "critical")


def predictions_hash(predictions: PredictionSet) -> str:
    return hashlib.sha256(json.dumps(predictions.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()


def outcome_of(result: ReceiptEvaluation, answer, expense_type: str) -> dict:
    report = result.report
    return {
        "receipt_id": result.receipt_id,
        "expense_type": expense_type,
        "disposition": result.disposition,
        "gold_disposition": answer.disposition,
        "decided": result.decided,
        "reason_codes": sorted({reason.code for reason in result.reasons}),
        "exceptions": {
            group.rule_id: {"line_ids": sorted(group.line_ids), "severity": group.severity, "policy_doc": group.policy_doc}
            for group in result.active_exceptions
        },
        "gold_exceptions": {rule: dict(value) for rule, value in sorted(answer.exceptions.items())},
        "signals": sorted({group.rule_id for group in result.review_signals}),
        "gold_signals": sorted(answer.signals),
        "monetary": report.monetary.model_dump(mode="json", exclude={"problems"}),
        "uncertain": result.uncertainty is not None,
        "uncertainty": [
            {"line_id": item.line_id, "field": item.field, "state": item.state, "reason": item.reason}
            for item in sorted(result.uncertainty.uncertainty if result.uncertainty else (), key=lambda item: (item.line_id, item.field))
        ],
        "unresolved": sorted({group.rule_id for group in result.unresolved}),
        "open": sorted({item.rule_id for item in report.open_questions}),
        "audit": [{"rule_id": group.rule_id, "resolution": group.resolution, "line_ids": sorted(group.line_ids)} for group in result.audit_history],
        "proof": None
        if result.inevitable is None
        else {
            "rule_ids": list(result.inevitable.rule_ids),
            "readings": len(result.inevitable.readings),
            "explanation": result.inevitable.explanation,
        },
    }


def _line_rows(dataset: Dataset, predictions: PredictionSet) -> list[dict]:
    by_line = predictions.by_line()
    rows = []
    for receipt_id in sorted(dataset.answers):
        for line in dataset.answers[receipt_id].lines:
            predicted = by_line.get(line.line_id)
            rows.append(
                {
                    "line_id": line.line_id,
                    "receipt_id": receipt_id,
                    "gold_category": line.category,
                    "gold_restricted": line.restricted,
                    "category": None if predicted is None else predicted.category,
                    "restricted": None if predicted is None else predicted.restricted,
                    "confidence": None if predicted is None else predicted.confidence,
                }
            )
    return rows


def _calibration(predictions: PredictionSet, lines: list[dict]) -> dict:
    """Only on actual predictions: a score on made-up confidence would describe the generator."""
    if not predictions.source.measured:
        return {"status": "not_evaluated", "reason": f"these classifications are {predictions.source.kind}, so their confidence measures nothing"}
    points = [(line["confidence"], line["category"] == line["gold_category"]) for line in lines if line["confidence"] is not None]
    if not points:
        return {"status": "not_evaluated", "reason": "no line has a confidence"}
    return {"status": "evaluated", "field": "category", **metrics.calibration(points)}


def _warnings(predictions: PredictionSet, lines: list[dict]) -> list[str]:
    found = []
    predicted = [line for line in lines if line["category"] is not None]
    if predictions.source.measured and predicted and all(line["category"] == line["gold_category"] for line in predicted):
        found.append("Every prediction matches the answers exactly. Check that the answers did not reach the classifier.")
    return found


def evaluate_predictions(
    dataset: Dataset, predictions: PredictionSet, ruleset: RuleSet, fields: Mapping[str, dict], *, complete: bool = True
) -> dict:
    """The scores and per-receipt outcomes of one run, as plain data ready for JSON."""
    if predictions.customer_id != dataset.customer_id:
        raise ValueError(f"predictions are for {predictions.customer_id}, and this dataset is {dataset.customer_id}")
    receipts = [admit_receipt(row, fields) for row in dataset.inputs]
    run = evaluate_receipts(dataset.customer_id, apply_predictions(receipts, predictions), ruleset, fields, complete=complete)
    types = {receipt.receipt_id: receipt.submitted_expense_type for receipt in receipts}
    outcomes = [outcome_of(result, dataset.answers[result.receipt_id], types[result.receipt_id]) for result in run.results]
    lines = _line_rows(dataset, predictions)
    threshold = _threshold(ruleset)
    rules = metrics.rule_scores(outcomes)
    lowest = {}
    for line in lines:
        bucket = metrics.confidence_bucket(line["confidence"], threshold)
        known = lowest.get(line["receipt_id"])
        lowest[line["receipt_id"]] = bucket if known is None else _lower_bucket(known, bucket, threshold)
    return {
        "source": predictions.source.model_dump(mode="json"),
        "measured_classifier_performance": predictions.source.measured,
        "customer_id": dataset.customer_id,
        "complete": complete,
        "predictions_sha256": predictions_hash(predictions),
        "warnings": _warnings(predictions, lines),
        "rules": rules,
        "exact_match": metrics.exact_match(outcomes),
        "disposition": metrics.disposition_scores(outcomes),
        "proofs": metrics.proof_scores(outcomes, critical_rules(ruleset)),
        "classification": metrics.classification_scores(lines),
        "calibration": _calibration(predictions, lines),
        "secondary_field_accuracy": {
            "status": "not_evaluated",
            "reason": "the answers hold no recipient_type, venue_type, item_kind or essential_toiletry",
        },
        "breakdowns": {
            "line_confidence": metrics.line_bucket_scores(lines, threshold),
            "receipt_lowest_confidence": metrics.group_scores(outcomes, lambda outcome: [lowest[outcome["receipt_id"]]]),
            "missing_field": metrics.missing_field_scores(outcomes),
            "expense_type": metrics.group_scores(outcomes, lambda outcome: [outcome["expense_type"]]),
            "rule_family": metrics.family_scores(rules["rules"]),
        },
        "receipts": outcomes,
    }


def _lower_bucket(first: str, second: str, threshold) -> str:
    """The less trusted of two buckets. Missing is the least, as it has nothing behind it."""
    order = ["missing", f"below_{threshold}", f"{threshold}_to_0.7", "0.7_to_0.9", "0.9_to_1"]
    return min(first, second, key=order.index)
