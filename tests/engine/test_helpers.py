"""Runs the cases in cases/engine/helpers.yaml, and compares the helpers with the answers in the data."""

from decimal import Decimal

import pytest
from receipt_rows import NO_RECIPIENT_TYPE, ROWS, without_answers
from yaml_cases import exact, load_cases, selected

from expense_validator.compiler.validate import read_fields
from expense_validator.config import load_config
from expense_validator.engine.approvals import resolve_approvals
from expense_validator.engine.carveouts import resolve_carveouts
from expense_validator.engine.checks import run_checks
from expense_validator.engine.helpers import HELPERS, TYPE_COVERS, baggage_allowance, build_context, run_helper
from expense_validator.engine.inputs import admit_receipt
from expense_validator.engine.models import Receipt
from expense_validator.engine.replacements import resolve_replacements
from expense_validator.policy.schema import CheckRule, RuleSet

PATHS = load_config().paths
FIELDS = read_fields(PATHS.rule_fields.read_text())
RULESET = RuleSet.from_json(PATHS.ruleset.read_text())
RULES = {rule.rule_id: rule for rule in RULESET.rules}
ACTIVE = [rule for rule in RULESET.rules if isinstance(rule, CheckRule) and rule.status == "active"]
CONDITION_RULES = {rule.rule_id for rule in ACTIVE if not rule.helper}
HELPER_RULES = {rule.rule_id for rule in ACTIVE if rule.helper}
RECIPIENTS = {case.values[0]["line_id"]: case.values[0]["expected"] for case in load_cases("helpers", "recipients")}


def context_for(case):
    return build_context("northwind", case["receipts"], FIELDS, complete=case["complete"])


def current(case, context):
    return next(receipt for receipt in context.receipts if receipt.receipt_id == case["receipt_id"])


def listed_findings(findings):
    return [
        {
            "rule_id": item.rule_id,
            "severity": item.severity,
            "scope": item.scope,
            "receipt_id": item.receipt_id,
            "line_ids": list(item.line_ids),
        }
        for item in findings
    ]


def listed_undetermined(undetermined):
    return [{"rule_id": item.rule_id, "line_id": item.line_id} for item in undetermined]


def classified(row):
    """The correct categories, and the recipients a classifier would read. For evaluation only."""
    outputs = {}
    for item in row["line_items"]:
        outputs[item["line_id"]] = {"category": item["labels"]["category"], "confidence": "1", **RECIPIENTS.get(item["line_id"], {})}
    return admit_receipt(without_answers(row), FIELDS).with_classifications(outputs)


CONTEXT = build_context("northwind", [classified(row) for row in ROWS], FIELDS, complete=True)


@pytest.mark.parametrize("case", load_cases("helpers", "outcomes"))
def test_a_helper_gives_this_outcome_the_same_way_every_time(case):
    context = context_for(case)
    run = lambda: run_helper(RULES[case["rule_id"]], current(case, context), context, RULESET, FIELDS)
    outcome = run()
    assert exact(listed_findings(outcome.findings)) == exact(case["expected"]["findings"])
    assert exact(listed_undetermined(outcome.undetermined)) == exact(case["expected"]["undetermined"])
    assert run() == outcome
    assert all(item.reason.strip() for item in outcome.undetermined)


@pytest.mark.parametrize("case", load_cases("helpers", "details"))
def test_a_helper_finding_carries_these_details(case):
    context = context_for(case)
    finding = run_helper(RULES[case["rule_id"]], current(case, context), context, RULESET, FIELDS).findings[0]
    assert exact(selected(finding.model_dump(mode="json"), case["expected"])) == exact(case["expected"])


@pytest.mark.parametrize("case", load_cases("helpers", "runs"))
def test_a_run_moves_from_pending_to_decided(case):
    # Only these rules, built around validation, as a rule may refer to others not in the list.
    ruleset = RuleSet.model_construct(rules=tuple(RULES[name] for name in case["rule_ids"]))
    context = context_for(case)
    result = run_checks(current(case, context), ruleset, FIELDS, context if case["with_context"] else None)
    summary = {
        "findings": listed_findings(result.findings),
        "pending_helpers": [{"rule_id": item.rule_id, "helper": item.helper} for item in result.pending_helpers],
        "undetermined": listed_undetermined(result.undetermined),
        "complete": result.complete,
    }
    assert exact(selected(summary, case["expected"])) == exact(case["expected"])


@pytest.mark.parametrize("case", load_cases("helpers", "baggage"))
def test_where_lines_stand_against_the_baggage_allowance(case):
    context = context_for(case)
    result = baggage_allowance(RULES["PERS.BAGGAGE"], current(case, context), context, FIELDS)
    assert list(result.line_ids) == case["expected"]["line_ids"]
    assert result.within is case["expected"]["within"]
    assert (None if result.total_usd is None else str(result.total_usd)) == case["expected"]["total_usd"]


@pytest.mark.parametrize("case", load_cases("helpers", "contexts"))
def test_a_context_holds_each_receipt_once_in_order(case):
    context = build_context(case["customer_id"], case["receipts"], FIELDS, complete=case["complete"])
    assert [receipt.receipt_id for receipt in context.receipts] == case["expected"]["receipt_ids"]
    assert context.complete is case["expected"]["complete"]
    assert isinstance(context.receipts, tuple)


@pytest.mark.parametrize("case", load_cases("helpers", "refused"))
def test_a_context_is_refused(case):
    with pytest.raises(ValueError, match=case["expected"]):
        build_context(case["customer_id"], case["receipts"], FIELDS, complete=case["complete"])


# --- The contract of a context, using the real data ---


def test_the_current_receipt_must_be_in_the_context():
    outside = classified(ROWS[0])
    empty = build_context("northwind", [], FIELDS, complete=True)
    with pytest.raises(ValueError, match="is not in the context"):
        run_helper(RULES["MEAL-01"], outside, empty, RULESET, FIELDS)
    with pytest.raises(ValueError, match="is not in the context"):
        run_checks(outside, RULESET, FIELDS, empty)
    with pytest.raises(ValueError, match="is not in the context"):
        baggage_allowance(RULES["PERS.BAGGAGE"], outside, empty, FIELDS)


def test_the_current_receipt_must_be_in_the_context_even_with_no_helper_rule_to_run():
    outside = classified(ROWS[0])
    no_helpers = RuleSet.model_validate({"ruleset_version": "v1", "rules": []})
    with pytest.raises(ValueError, match="is not in the context"):
        run_checks(outside, no_helpers, FIELDS, build_context("northwind", [], FIELDS, complete=True))


def test_the_number_of_receipts_in_a_split_comes_from_the_rule():
    pair = [classified(row) for row in ROWS if row["receipt_id"] in {"RCP-020", "RCP-021"}]
    context = build_context("northwind", pair, FIELDS, complete=True)
    rule = RULES["TIME-03"]
    assert run_helper(rule, context.receipts[0], context, RULESET, FIELDS).findings
    params = {**rule.params, "min_receipts": rule.params["min_receipts"].model_copy(update={"value": Decimal(3)})}
    three_needed = rule.model_copy(update={"params": params})
    assert run_helper(three_needed, context.receipts[0], context, RULESET, FIELDS).findings == ()


def test_the_early_exit_for_too_few_receipts_reads_the_number_from_the_rule():
    first, second = next(case.values[0] for case in load_cases("helpers", "outcomes") if case.values[0]["id"] == "two receipts with unknown lines stay open")["receipts"]
    context = build_context("northwind", [first, second], FIELDS, complete=True)
    rule = RULES["TIME-03"]
    assert run_helper(rule, context.receipts[0], context, RULESET, FIELDS).undetermined
    params = {**rule.params, "min_receipts": rule.params["min_receipts"].model_copy(update={"value": Decimal(3)})}
    three_needed = rule.model_copy(update={"params": params})
    assert run_helper(three_needed, context.receipts[0], context, RULESET, FIELDS).undetermined == ()


def test_alcohol_per_attendee_needs_alc_01_to_tell_where_alcohol_is_permitted():
    context = build_context("northwind", [classified(ROWS[0])], FIELDS, complete=True)
    without = RuleSet.model_construct(rules=(RULES["ALC-02"],))
    with pytest.raises(ValueError, match="needs ALC-01"):
        run_helper(RULES["ALC-02"], context.receipts[0], context, without, FIELDS)


def test_a_receipt_that_differs_from_the_one_in_the_context_is_refused():
    row = ROWS[0]
    unclassified = admit_receipt(without_answers(row), FIELDS)
    context = build_context("northwind", [classified(row)], FIELDS, complete=True)
    with pytest.raises(ValueError, match="differs from the one there"):
        run_checks(unclassified, RULESET, FIELDS, context)


def test_a_repeated_receipt_is_not_counted_twice():
    row = next(item for item in ROWS if item["receipt_id"] == "RCP-020")
    once = build_context("northwind", [classified(row)], FIELDS, complete=True)
    twice = build_context("northwind", [classified(row), classified(row)], FIELDS, complete=True)
    assert twice == once
    assert run_checks(once.receipts[0], RULESET, FIELDS, twice) == run_checks(once.receipts[0], RULESET, FIELDS, once)
    assert "TIME-03" not in {item.rule_id for item in run_checks(once.receipts[0], RULESET, FIELDS, twice).findings}


def test_customers_are_kept_apart_by_their_contexts():
    """The same employee and receipts split between two customers do not add up to a split."""
    first, second = (next(item for item in ROWS if item["receipt_id"] == name) for name in ("RCP-020", "RCP-021"))
    together = build_context("northwind", [classified(first), classified(second)], FIELDS, complete=True)
    apart = build_context("northwind", [classified(first)], FIELDS, complete=True)
    elsewhere = build_context("acme", [classified(second)], FIELDS, complete=True)
    assert "TIME-03" in {item.rule_id for item in run_checks(apart.receipts[0], RULESET, FIELDS, together).findings}
    assert "TIME-03" not in {item.rule_id for item in run_checks(apart.receipts[0], RULESET, FIELDS, apart).findings}
    assert "TIME-03" not in {item.rule_id for item in run_checks(elsewhere.receipts[0], RULESET, FIELDS, elsewhere).findings}
    assert (apart.customer_id, elsewhere.customer_id) == ("northwind", "acme")


def test_a_directly_built_receipt_is_admitted_on_the_way_into_a_context():
    data = {**without_answers(ROWS[0]), "employee_country": "IN", "entity": "GLOBAL"}
    with pytest.raises(ValueError, match="contradicts"):
        build_context("northwind", [Receipt.model_validate(data)], FIELDS, complete=True)


def test_a_context_cannot_be_changed():
    with pytest.raises(AttributeError):
        CONTEXT.complete = False


# --- The catalog and the policy tables ---


def test_every_helper_on_a_check_rule_has_a_function():
    assert {rule.helper for rule in ACTIVE if rule.helper} == set(HELPERS)


def test_every_expense_type_in_the_catalog_is_in_the_doc_03_table():
    assert set(TYPE_COVERS) == set(FIELDS["receipt.submitted_expense_type"]["values"])
    assert set().union(*TYPE_COVERS.values()) <= set(FIELDS["item.category"]["values"])


def test_every_country_has_a_home_currency():
    countries = {country for listed in FIELDS["receipt.entity"]["countries"].values() for country in listed}
    assert set(FIELDS["receipt.employee_country"]["home_currency"]) == countries


# --- Against the real data and rules ---


@pytest.mark.parametrize("receipt", CONTEXT.receipts, ids=lambda receipt: receipt.receipt_id)
def test_every_classified_real_receipt_is_decided_in_a_complete_context(receipt):
    result = run_checks(receipt, RULESET, FIELDS, CONTEXT)
    assert result.pending_helpers == ()
    assert {item.rule_id for item in result.undetermined} == ({"GIFT-04"} if receipt.receipt_id in NO_RECIPIENT_TYPE else set())
    assert run_checks(receipt, RULESET, FIELDS, CONTEXT) == result


@pytest.mark.parametrize("receipt", CONTEXT.receipts, ids=lambda receipt: receipt.receipt_id)
def test_every_helper_finding_on_the_real_data_comes_from_its_rule(receipt):
    lines = {item.line_id for item in receipt.line_items}
    found = [item for item in run_checks(receipt, RULESET, FIELDS, CONTEXT).findings if item.rule_id in HELPER_RULES]
    assert len({(item.rule_id, item.line_ids) for item in found}) == len(found)
    for finding in found:
        rule = RULES[finding.rule_id]
        assert finding.receipt_id == receipt.receipt_id
        assert set(finding.line_ids) <= lines
        assert finding.status == "raised"
        assert (finding.outcome, finding.scope) == (rule.outcome, rule.scope)
        assert (finding.policy_doc, finding.source_quote, finding.provenance) == (rule.policy_doc, rule.source_quote, rule.provenance)


@pytest.mark.parametrize("row", ROWS, ids=lambda row: row["receipt_id"])
def test_an_unclassified_real_receipt_is_not_complete_in_a_complete_context(row):
    receipts = [admit_receipt(without_answers(item), FIELDS) for item in ROWS]
    context = build_context("northwind", receipts, FIELDS, complete=True)
    receipt = next(item for item in context.receipts if item.receipt_id == row["receipt_id"])
    assert run_checks(receipt, RULESET, FIELDS, context).complete is False


def after_resolution(receipt, result):
    """What the real replacement, approval and carve-out steps leave active."""
    replacements = resolve_replacements(receipt, result, RULESET, FIELDS)
    approvals = resolve_approvals(receipt, replacements, RULESET)
    got = {}
    for finding in resolve_carveouts(receipt, approvals, RULESET, FIELDS, CONTEXT).active:
        got.setdefault(finding.rule_id, []).extend(finding.line_ids)
    return got


@pytest.mark.parametrize("rules, answers", [(CONDITION_RULES, 28), (HELPER_RULES, 9)], ids=["conditions", "helpers"])
def test_the_rules_match_the_answers_in_the_data(rules, answers):
    matched = extras = missed = 0
    by_id = {receipt.receipt_id: receipt for receipt in CONTEXT.receipts}
    for row in ROWS:
        receipt = by_id[row["receipt_id"]]
        result = run_checks(receipt, RULESET, FIELDS, CONTEXT)
        got = {rule_id: lines for rule_id, lines in after_resolution(receipt, result).items() if rule_id in rules}
        labels = row["labels"]
        want = {
            entry["rule_id"]: entry.get("line_ids", [])
            for entry in labels["expected_exceptions"] + labels["review_signals"]
            if entry["rule_id"] in rules
        }
        extras += len(set(got) - set(want))
        missed += len(set(want) - set(got))
        for rule_id in set(got) & set(want):
            assert sorted(got[rule_id]) == sorted(want[rule_id]), (row["receipt_id"], rule_id)
            matched += 1
    assert (matched, extras, missed) == (answers, 0, 0)
