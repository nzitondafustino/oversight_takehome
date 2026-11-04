"""Runs cases/engine/completeness.yaml: refunds, missing USD values, and unresolved official status."""

import copy

import pytest
from pydantic import TypeAdapter
from test_adversarial_policy import (
    POLICY_DOCS,
    RULES,
    assert_expected,
    assert_line_order_is_free,
    assert_receipt_order_is_free,
    is_status,
    run_of,
    status_of,
)
from yaml_cases import load_cases

from expense_validator.policy.evaluate import evaluate_with_unknowns
from expense_validator.policy.schema import Condition

AMOUNTS = {"purchase": "329.00", "refund": "-329.00", "zero": "0.00"}
CASES = [
    pytest.param(param.values[0], id=f"{section}/{param.id}")
    for section in ("usd", "officials")
    for param in load_cases("completeness", section)
]


def priced(line, amount, line_id="L1"):
    return {**copy.deepcopy(line), "line_id": line_id, "unit_price": amount, "amount": amount, "amount_usd": amount}


@pytest.mark.parametrize("sign", AMOUNTS)
@pytest.mark.parametrize("case", load_cases("completeness", "refund_rules"))
def test_a_rule_reads_a_lines_sign_as_its_source_says(case, sign):
    receipt = {**case["receipt"], "line_items": [priced(case["line"], AMOUNTS[sign])]}
    result = run_of([receipt]).results[0]
    assert is_status(result, case["rule"], case["expected"][sign]), status_of(result, case["rule"])


@pytest.mark.parametrize("case", load_cases("completeness", "refund_rules"))
def test_a_refund_does_not_cancel_a_purchase_beside_it(case):
    lines = [priced(case["line"], AMOUNTS["purchase"]), priced(case["line"], AMOUNTS["refund"], "L2")]
    for ordered in (lines, lines[::-1]):
        result = run_of([{**case["receipt"], "line_items": ordered}]).results[0]
        group = next(item for item in result.active_exceptions if item.rule_id == case["expected"]["rule_id"])
        assert sorted(group.line_ids) == case["expected"]["beside"]


@pytest.mark.parametrize("case", load_cases("completeness", "refund_rules"))
def test_the_wording_a_refund_decision_rests_on_is_in_the_policy(case):
    rule = RULES[case["rule"]]
    assert case["source"] in " ".join((POLICY_DOCS / rule.policy_doc).read_text().split())


@pytest.mark.parametrize("case", CASES)
def test_this_outcome_holds(case):
    assert_expected(run_of(case["receipts"], case["complete"]), case["expected"])


@pytest.mark.parametrize("case", CASES)
def test_receipt_order_changes_nothing(case):
    assert_receipt_order_is_free(case)


@pytest.mark.parametrize("case", CASES)
def test_line_order_changes_nothing(case):
    assert_line_order_is_free(case)


@pytest.mark.parametrize("case", CASES)
def test_every_group_keeps_its_rules_provenance(case):
    for result in run_of(case["receipts"], case["complete"]).results:
        for group in result.report.groups:
            if group.kind == "exception":
                assert group.provenance == RULES[group.rule_id].provenance
                assert all(finding.provenance == group.provenance for finding in group.sources)


@pytest.mark.parametrize("case", load_cases("completeness", "unknowns"))
def test_a_condition_says_when_it_turns_on_an_unknown(case):
    condition = TypeAdapter(Condition).validate_python(case["condition"])
    assert evaluate_with_unknowns(condition, case["facts"], {}, {"u"}) is case["expected"]["result"]
