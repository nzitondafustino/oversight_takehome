"""The command line. Run from the project root:

    python -m expense_validator.offline_eval                         every built-in scenario
    python -m expense_validator.offline_eval --predictions run.json  also a file of predictions
    python -m expense_validator.offline_eval --only A H0             some of them

A prediction file is JSON: {customer_id, source: {kind, name, description, model}, predictions: [...]}.
Each prediction has a line_id and any of category, restricted, confidence, recipient_name,
recipient_type, venue_type, item_kind and essential_toiletry. Makes no network call.
"""

import argparse
import hashlib
import sys
from pathlib import Path

import yaml

from expense_validator.compiler.validate import read_fields
from expense_validator.config import ROOT, load_config
from expense_validator.engine.inputs import allowed_values
from expense_validator.offline_eval import report
from expense_validator.offline_eval.dataset import load_dataset
from expense_validator.offline_eval.predictions import LinePrediction, PredictionSet, Source, load_predictions
from expense_validator.offline_eval.runner import evaluate_predictions
from expense_validator.offline_eval.scenarios import built_in_scenarios, load_oracle_recipients
from expense_validator.offline_eval.texts import LIMITATIONS, RECOMMENDATIONS
from expense_validator.policy.schema import RuleSet

CUSTOMER = "northwind"
SMOKE_CUSTOMER = "synthetic-smoke"
LOW_CONFIDENCE_LINE = "SYN-006-L1"


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def smoke_predictions(dataset) -> PredictionSet:
    """Right categories and the name each line prints, no recipient types, and one line at low confidence."""
    made = [
        LinePrediction(line_id=line.line_id, category=line.category, recipient_name="Xu", confidence="0.30" if line.line_id == LOW_CONFIDENCE_LINE else "0.95")
        for receipt_id in sorted(dataset.answers)
        for line in dataset.answers[receipt_id].lines
    ]
    source = Source(kind="synthetic", name="S", description="Hand-written receipts: right categories, no recipient types")
    return PredictionSet(customer_id=dataset.customer_id, source=source, predictions=tuple(made))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline evaluation of classifications through the policy engine.")
    parser.add_argument("--data", type=Path, default=ROOT / "data" / "receipts.jsonl")
    parser.add_argument("--customer", default=CUSTOMER)
    parser.add_argument("--predictions", type=Path, action="append", default=[], help="a prediction file, any number of times")
    parser.add_argument("--partial", action="store_true", help="treat the prediction files' runs as complete=False")
    parser.add_argument("--only", nargs="*", help="only these built-in scenarios")
    parser.add_argument("--out", type=Path, default=ROOT / "evaluation" / "output")
    args = parser.parse_args(argv)

    paths = load_config().paths
    ruleset = RuleSet.from_json(paths.ruleset.read_text())
    fields = read_fields(paths.rule_fields.read_text())
    dataset = load_dataset(args.data, args.customer)
    recipients_path = ROOT / "evaluation" / "oracle_recipients.yaml"
    baseline = yaml.safe_load((ROOT / "evaluation" / "baseline_10_6.yaml").read_text())["scenarios"]
    categories = sorted(allowed_values(fields, "item.category"))

    runs = {}
    for scenario in built_in_scenarios(dataset, load_oracle_recipients(recipients_path), categories):
        if args.only is None or scenario.key in args.only:
            runs[scenario.key] = evaluate_predictions(dataset, scenario.predictions, ruleset, fields, complete=scenario.complete)
    for path in args.predictions:
        predictions = load_predictions(path)
        key = predictions.source.name
        if key in runs:
            raise ValueError(f"{path}: the name {key} is already a scenario")
        runs[key] = evaluate_predictions(dataset, predictions, ruleset, fields, complete=not args.partial)

    smoke_path = ROOT / "evaluation" / "synthetic" / "proof_smoke.jsonl"
    smoke_data = load_dataset(smoke_path, SMOKE_CUSTOMER)
    smoke = {"S": evaluate_predictions(smoke_data, smoke_predictions(smoke_data), ruleset, fields)}

    inputs = {
        "dataset_sha256": dataset.sha256,
        "dataset_receipts": len(dataset.inputs),
        "dataset_customer": dataset.customer_id,
        "ruleset_sha256": _hash(paths.ruleset),
        "ruleset_version": ruleset.ruleset_version,
        "fields_sha256": _hash(paths.rule_fields),
        "oracle_recipients_sha256": _hash(recipients_path),
        "smoke_dataset_sha256": smoke_data.sha256,
    }
    comparison = report.baseline_comparison(runs, {key: value for key, value in baseline.items() if key in runs})
    document = report.metrics_document(inputs, runs, smoke, comparison)
    text = report.markdown_report(document, runs, smoke, RECOMMENDATIONS, LIMITATIONS)
    report.write_all(args.out, document, runs, smoke, text)
    print(f"Wrote {args.out}. Scenarios: {', '.join(runs)}. Real classifier measured: {document['real_classifier_measured']}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
