"""Runs the cases in cases/engine/adversarial.yaml, end to end through evaluate_receipts alone."""

import itertools

import pytest
from test_helpers import FIELDS, RULESET
from yaml_cases import exact, load_cases, selected

from expense_validator.engine.evaluation import evaluate_receipts


def evaluate(receipts, complete):
    return evaluate_receipts("northwind", receipts, RULESET, FIELDS, complete=complete)


def summary(result):
    groups = result.active_exceptions
    return {
        "disposition": result.disposition,
        "decided": result.decided,
        "active": [group.rule_id for group in groups],
        "severities": {group.rule_id: group.severity for group in groups},
        "unresolved": [group.rule_id for group in result.unresolved],
        "uncertainty": [
            {"line_id": item.line_id, "field": item.field, "state": item.state}
            for item in (result.uncertainty.uncertainty if result.uncertainty else ())
        ],
        "audit": [{"rule_id": group.rule_id, "resolution": group.resolution} for group in result.audit_history],
    }


def assert_expected(run, expected):
    assert sorted(item.receipt_id for item in run.results) == sorted(expected)
    for receipt_id, wanted in expected.items():
        result = run.result_for(receipt_id)
        wanted = dict(wanted)
        still_open = {item.rule_id for item in result.open_questions}
        assert set(wanted.pop("open_includes", ())) <= still_open, (receipt_id, sorted(still_open))
        assert not set(wanted.pop("open_excludes", ())) & still_open, (receipt_id, sorted(still_open))
        assert exact(selected(summary(result), wanted)) == exact(wanted), receipt_id


@pytest.mark.parametrize("case", load_cases("adversarial", "scenarios"))
def test_the_policy_expectation_holds_in_every_receipt_order(case):
    run = evaluate(case["receipts"], case["complete"])
    assert_expected(run, case["expected"])
    for order in itertools.permutations(case["receipts"]):
        assert evaluate(list(order), case["complete"]) == run


@pytest.mark.parametrize("case", load_cases("adversarial", "scenarios"))
def test_every_group_holds_only_its_own_receipt_and_lines(case):
    receipts = {item["receipt_id"]: item for item in case["receipts"]}
    for result in evaluate(case["receipts"], case["complete"]).results:
        own = {line["line_id"] for line in receipts[result.receipt_id]["line_items"]}
        assert result.customer_id == "northwind"
        for group in result.report.groups:
            assert group.receipt_id == result.receipt_id
            assert set(group.line_ids) <= own
            for finding in group.sources:
                assert finding.receipt_id == result.receipt_id
                assert set(finding.line_ids) <= own


def base_receipts():
    return next(case.values[0] for case in load_cases("adversarial", "scenarios") if case.values[0]["id"] == "the three receipts")["receipts"]


@pytest.mark.parametrize("receipt_id", ["R1", "R2", "R3"])
def test_no_receipt_changes_another_on_different_merchants(receipt_id):
    """Each gives the same result alone as beside the others, so nothing leaks between them."""
    receipts = base_receipts()
    together = evaluate(receipts, True).result_for(receipt_id)
    alone = evaluate([item for item in receipts if item["receipt_id"] == receipt_id], True).results[0]
    assert alone == together


def test_the_fixture_is_one_employee_on_one_day_at_different_merchants():
    receipts = base_receipts()
    assert {item["employee_id"] for item in receipts} == {"E-1042"}
    assert {item["transaction_datetime"][:10] for item in receipts} == {"2026-04-14"}
    assert len({item["merchant_name"] for item in receipts}) == 3
