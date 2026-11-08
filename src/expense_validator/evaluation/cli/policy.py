"""Run the offline policy-output evaluation scenarios, and write evaluation/output. Makes no network call.

Run: python -m expense_validator.offline_eval [--predictions FILE] [--only A H0]
"""

import argparse
import hashlib
import sys
from pathlib import Path

import yaml

from expense_validator.compiler.validate import read_fields
from expense_validator.config import ROOT, load_config
from expense_validator.engine.inputs import allowed_values
from expense_validator.evaluation import report
from expense_validator.evaluation.dataset import load_dataset
from expense_validator.evaluation.policy_outputs import baseline_comparison, evaluate_predictions, evaluate_scenarios
from expense_validator.evaluation.predictions import load_predictions
from expense_validator.evaluation.scenarios import Scenario, built_in_scenarios, load_oracle_recipients, smoke_predictions
from expense_validator.evaluation.texts import LIMITATIONS, RECOMMENDATIONS
from expense_validator.policy.schema import RuleSet



def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline evaluation of classifications through the policy engine.")
    parser.add_argument("--data", type=Path, default=ROOT / "data" / "receipts.jsonl")
    parser.add_argument("--predictions", type=Path, action="append", default=[], help="a prediction file, any number of times")
    parser.add_argument("--partial", action="store_true", help="treat the prediction files' runs as complete=False")
    parser.add_argument("--only", nargs="*", help="only these built-in scenarios")
    parser.add_argument("--out", type=Path, default=ROOT / "evaluation" / "output")
    args = parser.parse_args(argv)

    paths = load_config().paths
    ruleset = RuleSet.from_json(paths.ruleset.read_text())
    fields = read_fields(paths.rule_fields.read_text())
    dataset = load_dataset(args.data)
    recipients_path = ROOT / "evaluation" / "oracle_recipients.yaml"
    recorded = yaml.safe_load((ROOT / "evaluation" / "baseline_10_6.yaml").read_text())["scenarios"]
    # The 10.6 figures stay as recorded, with only what later engine corrections changed applied over them.
    corrections = yaml.safe_load((ROOT / "evaluation" / "engine_corrections.yaml").read_text())["baseline_10_6"]
    baseline = {key: {**figures, **corrections.get(key, {})} for key, figures in recorded.items()}
    categories = sorted(allowed_values(fields, "item.category"))

    scenarios = [scenario for scenario in built_in_scenarios(dataset, load_oracle_recipients(recipients_path), categories) if args.only is None or scenario.key in args.only]
    for path in args.predictions:
        predictions = load_predictions(path)
        key = predictions.source.name
        if key in {scenario.key for scenario in scenarios}:
            raise ValueError(f"{path}: the name {key} is already a scenario")
        scenarios.append(Scenario(key, predictions, complete=not args.partial))
    runs = evaluate_scenarios(dataset, scenarios, ruleset, fields)

    smoke_path = ROOT / "evaluation" / "synthetic" / "proof_smoke.jsonl"
    smoke_data = load_dataset(smoke_path)
    smoke = {"S": evaluate_predictions(smoke_data, smoke_predictions(smoke_data), ruleset, fields)}

    inputs = {
        "dataset_sha256": dataset.sha256,
        "dataset_receipts": len(dataset.inputs),
        "ruleset_sha256": _hash(paths.ruleset),
        "ruleset_version": ruleset.ruleset_version,
        "fields_sha256": _hash(paths.rule_fields),
        "oracle_recipients_sha256": _hash(recipients_path),
        "smoke_dataset_sha256": smoke_data.sha256,
    }
    comparison = baseline_comparison(runs, {key: value for key, value in baseline.items() if key in runs})
    document = report.metrics_document(inputs, runs, smoke, comparison)
    text = report.markdown_report(document, runs, smoke, RECOMMENDATIONS, LIMITATIONS)
    report.write_all(args.out, document, runs, smoke, text)
    print(f"Wrote {args.out}. Scenarios: {', '.join(runs)}. Real classifier measured: {document['real_classifier_measured']}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
