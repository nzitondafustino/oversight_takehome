"""Offline evaluation. Production code never imports it.

Three kinds, each a module of plain functions that take their inputs as arguments and read no files:

- Policy outputs (policy_outputs.py): run a set of line predictions through the engine and score the
  dispositions, rules and lines against the labelled answers. evaluate_predictions scores one set and
  evaluate_scenarios several. Makes no network call.
- Classification (classification.py): score classified lines against the labelled answers.
  classification_metrics and classification_predictions.
- Robustness (robustness.py): score the classifier on the developer-authored robustness set. score_rows,
  score_receipts and check_labels.

Shared: dataset.py loads a labelled dataset, predictions.py holds the prediction format, metrics.py the
metrics, scenarios.py the built-in oracle and synthetic predictions, report.py formats and writes the
policy-output report, and texts.py holds its written sections.

cli/ holds the command lines, which load files, call these functions and write the results. Nothing outside
cli/ imports it. The commands are unchanged: python -m expense_validator.offline_eval,
expense_validator.classification.evaluate and expense_validator.classification.robustness.

This package imports nothing on purpose, so that importing the policy-output harness never loads a classifier.
"""
