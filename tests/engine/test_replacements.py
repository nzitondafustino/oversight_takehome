"""Runs the cases in cases/engine/replacements.yaml, and checks replacements on all the real data."""

import pytest
from pydantic import ValidationError
from receipt_rows import NO_RECIPIENT_TYPE, ROWS, without_answers
from test_helpers import CONTEXT, FIELDS, RULES, RULESET
from yaml_cases import exact, load_cases, selected

from expense_validator.engine.checks import CheckResult, PendingHelper, run_checks
from expense_validator.engine.facts import receipt_facts
from expense_validator.engine.helpers import Undetermined, build_context
from expense_validator.engine.inputs import admit_receipt
from expense_validator.engine.models import Finding, ReplacementTarget
from expense_validator.engine.replacements import resolve_replacements
from expense_validator.policy.evaluate import replaced_on_receipt
from expense_validator.policy.schema import RuleSet

# Every made-up rule's gate reads this receipt: its mcc is 5812 and it has no currency.
RECEIPT = admit_receipt(
    {
        "receipt_id": "R1",
        "employee_id": "E-1",
        "employee_country": "US",
        "submitted_expense_type": "Meals - Individual",
        "mcc": "5812",
        "itemized": False,
        "line_items": [],
    },
    FIELDS,
)


def targets(findings):
    return [{"rule_id": item.rule_id, "line_ids": list(item.line_ids)} for item in findings]


def undetermined(items):
    return [{"rule_id": item.rule_id, "line_id": item.line_id} for item in items]


def summary(result):
    return {
        "active": targets(result.active),
        "unaffected": targets(result.unaffected),
        "replaced": [
            {"rule_id": item.finding.rule_id, "line_ids": list(item.finding.line_ids), "by": item.replaced_by, "level": item.level}
            for item in result.replaced
        ],
        "possible": [
            {"rule_id": item.finding.rule_id, "line_ids": list(item.finding.line_ids), "by": item.possibly_by, "level": item.level}
            for item in result.possible
        ],
        "undetermined": undetermined(result.undetermined),
        "moot": undetermined(result.moot_undetermined),
        "pending": [item.rule_id for item in result.pending_helpers],
        "moot_pending": [item.rule_id for item in result.moot_pending_helpers],
        "complete": result.complete,
    }


def assert_summary(result, expected):
    assert exact(selected(summary(result), expected)) == exact(expected)


def made_up_input(case):
    ruleset = RuleSet.model_validate({"ruleset_version": "v1", "rules": case["rules"]})
    rules = {rule.rule_id: rule for rule in ruleset.rules}
    findings = []
    for given in case["findings"]:
        rule = rules[given["rule_id"]]
        findings.append(
            Finding(
                rule_id=rule.rule_id,
                outcome=rule.outcome,
                severity=rule.severity,
                scope=rule.scope,
                receipt_id=given.get("receipt_id", "R1"),
                line_ids=given.get("line_ids", ()),
                replaces=given.get("replaces", ()),
                explanation=rule.title,
                policy_doc=rule.policy_doc,
                source_quote=rule.source_quote,
                provenance=rule.provenance,
            )
        )
    result = CheckResult(
        tuple(findings),
        tuple(PendingHelper(rule_id, "gift_cap") for rule_id in case["pending"]),
        tuple(Undetermined(item["rule_id"], item["line_id"], "not known") for item in case["undetermined"]),
    )
    return result, ruleset


@pytest.mark.parametrize("case", load_cases("replacements", "real"))
def test_the_real_rules_replace_these_the_same_way_every_time(case):
    # Only these rules, built around validation, as a rule may refer to others not in the list.
    ruleset = RuleSet.model_construct(rules=tuple(RULES[name] for name in case["rule_ids"]))
    context = build_context(case["receipts"], FIELDS, complete=case["complete"])
    receipt = next(item for item in context.receipts if item.receipt_id == case["receipt_id"])
    run = lambda: resolve_replacements(
        receipt, run_checks(receipt, ruleset, FIELDS, context if case["with_context"] else None), ruleset, FIELDS
    )
    result = run()
    assert_summary(result, case["expected"])
    assert run() == result


@pytest.mark.parametrize("case", load_cases("replacements", "made_up"))
def test_these_rules_replace_these(case):
    result, ruleset = made_up_input(case)
    assert_summary(resolve_replacements(RECEIPT, result, ruleset, FIELDS), case["expected"])


@pytest.mark.parametrize("case", load_cases("replacements", "refused"))
def test_a_resolution_that_cannot_go_ahead_is_refused(case):
    result, ruleset = made_up_input(case)
    with pytest.raises(ValueError, match=case["expected"]):
        resolve_replacements(RECEIPT, result, ruleset, FIELDS)


# --- The schema's own guards, on what this module relies on ---


def test_a_replacement_cycle_cannot_reach_the_resolver():
    case = load_cases("replacements", "made_up")[0].values[0]
    first, second = case["rules"]
    looped = [{**first, "replaces": [{"rule_id": second["rule_id"], "level": "line"}]}, second]
    with pytest.raises(ValidationError, match="replacement cycle"):
        RuleSet.model_validate({"ruleset_version": "v1", "rules": looped})


def test_a_result_is_not_changed_and_cannot_be_changed():
    case = load_cases("replacements", "made_up")[0].values[0]
    given, ruleset = made_up_input(case)
    resolved = resolve_replacements(RECEIPT, given, ruleset, FIELDS)
    assert resolved.candidates == given.findings
    assert all(finding.status == "raised" for finding in resolved.candidates)
    with pytest.raises(AttributeError):
        resolved.active = ()


# --- Against the real data and rules ---


def resolved_rows():
    for receipt in CONTEXT.receipts:
        result = run_checks(receipt, RULESET, FIELDS, CONTEXT)
        yield receipt, result, resolve_replacements(receipt, result, RULESET, FIELDS)


@pytest.mark.parametrize("receipt", CONTEXT.receipts, ids=lambda receipt: receipt.receipt_id)
def test_a_real_receipt_loses_no_finding(receipt):
    result = run_checks(receipt, RULESET, FIELDS, CONTEXT)
    resolved = resolve_replacements(receipt, result, RULESET, FIELDS)
    assert resolved.candidates == result.findings
    assert len(resolved.active) + len(resolved.replaced) == len(result.findings)
    assert resolved.complete == (receipt.receipt_id not in NO_RECIPIENT_TYPE)
    assert resolved.possible == ()


def test_a_replaced_finding_keeps_everything_but_its_status():
    seen = 0
    for _, result, resolved in resolved_rows():
        for item in resolved.replaced:
            original = next(
                found for found in result.findings if (found.rule_id, found.line_ids) == (item.finding.rule_id, item.finding.line_ids)
            )
            assert original.status == "raised" and original.resolved_by is None
            assert item.finding.status == "replaced"
            assert item.finding.resolved_by == item.replaced_by
            assert item.reason.strip()
            same = {"status", "resolved_by"}
            assert item.finding.model_dump(exclude=same) == original.model_dump(exclude=same)
            seen += 1
    assert seen > 0


def test_receipt_level_replacements_agree_with_the_evaluator():
    for receipt, _, resolved in resolved_rows():
        out = replaced_on_receipt(RULESET, receipt_facts(receipt, FIELDS))
        by_receipt = {item.finding.rule_id for item in resolved.replaced if item.level == "receipt"}
        assert by_receipt <= out
        assert not {finding.rule_id for finding in resolved.active} & out


def test_every_replacement_on_the_real_data_was_declared_at_that_level():
    for _, _, resolved in resolved_rows():
        for item in resolved.replaced:
            declared = {(target.rule_id, target.level) for target in RULES[item.replaced_by].replaces}
            assert (item.finding.rule_id, item.level) in declared


def test_time_03_in_the_data_replaces_only_the_meal_cap_it_was_split_around():
    time_03 = {receipt.receipt_id: resolved for receipt, _, resolved in resolved_rows()}
    for receipt_id in ("RCP-020", "RCP-021"):
        resolved = time_03[receipt_id]
        finding = next(item for item in resolved.active if item.rule_id == "TIME-03")
        assert finding.replaces == (ReplacementTarget(rule_id="MEAL-01"),)
        assert [(item.finding.rule_id, item.level) for item in resolved.replaced] == [("MEAL-01", "finding")]


def test_no_target_established_keeps_the_split_and_everything_else():
    """A rule that lists no target for this threshold can report the split, but replaces nothing."""
    pair = [row for row in ROWS if row["receipt_id"] in {"RCP-020", "RCP-021"}]
    receipts = [item for item in CONTEXT.receipts if item.receipt_id in {row["receipt_id"] for row in pair}]
    context = build_context(receipts, FIELDS, complete=True)
    only_gifts = RULES["TIME-03"].model_copy(update={"replaces": ()})
    rules = tuple(only_gifts if rule.rule_id == "TIME-03" else rule for rule in RULESET.rules)
    ruleset = RuleSet.model_construct(rules=rules)
    result = run_checks(receipts[0], ruleset, FIELDS, context)
    resolved = resolve_replacements(receipts[0], result, ruleset, FIELDS)
    assert {item.rule_id for item in resolved.active} >= {"TIME-03", "MEAL-01"}
    assert next(item for item in resolved.active if item.rule_id == "TIME-03").replaces == ()
    assert resolved.replaced == ()


def test_an_unclassified_real_receipt_does_not_lose_a_finding_to_a_rule_not_decided():
    receipts = [admit_receipt(without_answers(row), FIELDS) for row in ROWS]
    context = build_context(receipts, FIELDS, complete=True)
    for receipt in context.receipts:
        result = run_checks(receipt, RULESET, FIELDS, context)
        resolved = resolve_replacements(receipt, result, RULESET, FIELDS)
        assert all(item.level == "receipt" or item.replaced_by in {found.rule_id for found in result.findings} for item in resolved.replaced)
        assert resolved.complete is False
