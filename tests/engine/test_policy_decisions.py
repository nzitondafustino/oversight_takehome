"""Runs cases/engine/policy_decisions.yaml: the four decided policy questions, and partial contexts."""

import pytest
from test_adversarial_policy import assert_expected, assert_line_order_is_free, assert_receipt_order_is_free, run_of
from yaml_cases import load_cases

CASES = [
    pytest.param(param.values[0], id=f"{section}/{param.id}")
    for section in ("decisions", "partial")
    for param in load_cases("policy_decisions", section)
]


@pytest.mark.parametrize("case", CASES)
def test_the_decision_gives_this_outcome(case):
    assert_expected(run_of(case["receipts"], case["complete"]), case["expected"])


@pytest.mark.parametrize("case", CASES)
def test_receipt_order_changes_nothing(case):
    assert_receipt_order_is_free(case)


@pytest.mark.parametrize("case", CASES)
def test_line_order_changes_nothing(case):
    assert_line_order_is_free(case)


@pytest.mark.parametrize("case", load_cases("policy_decisions", "refused"))
def test_this_cannot_be_evaluated(case):
    with pytest.raises(ValueError, match=case["expected"]):
        run_of(case["receipts"])
