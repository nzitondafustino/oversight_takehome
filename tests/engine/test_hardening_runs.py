"""A run on the real data with nothing classified, for the hash seed check to compare."""

from receipt_rows import ROWS, without_answers
from test_helpers import FIELDS, RULESET

from expense_validator.engine.evaluation import evaluate_receipts
from expense_validator.engine.inputs import admit_receipt


def unclassified_run():
    receipts = [admit_receipt(without_answers(row), FIELDS) for row in ROWS]
    return evaluate_receipts(receipts, RULESET, FIELDS, complete=False)
