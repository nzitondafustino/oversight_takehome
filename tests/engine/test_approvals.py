"""Runs the cases in cases/engine/approvals.yaml, and checks approvals on all the real data."""

import pytest
from test_helpers import CONTEXT, FIELDS, RULES, RULESET
from test_replacements import RECEIPT, made_up_input
from receipt_rows import NO_RECIPIENT_TYPE
from yaml_cases import exact, load_cases, selected

from expense_validator.engine.approvals import REFERENCE_FIELDS, resolve_approvals
from expense_validator.engine.checks import run_checks
from expense_validator.engine.helpers import build_context
from expense_validator.engine.models import Receipt
from expense_validator.engine.replacements import resolve_replacements
from expense_validator.policy.schema import RuleSet


def targets(findings):
    return [{"rule_id": item.rule_id, "line_ids": list(item.line_ids)} for item in findings]


def summary(result):
    return {
        "active": targets(result.active),
        "replaced": targets(item.finding for item in result.replaced),
        "waived": [
            {
                "rule_id": item.finding.rule_id,
                "line_ids": list(item.finding.line_ids),
                "kind": item.kind,
                "field": item.field,
                "reference": item.reference,
            }
            for item in result.waived
        ],
        "gaps": [
            {
                "rule_id": item.finding.rule_id,
                "line_ids": list(item.finding.line_ids),
                "required": item.required_field,
                "state": item.state,
                "others": list(item.other_fields),
            }
            for item in result.gaps
        ],
        "undetermined": [{"rule_id": item.rule_id, "line_id": item.line_id} for item in result.replacements.undetermined],
        "possible": [
            {"rule_id": item.finding.rule_id, "line_ids": list(item.finding.line_ids), "by": item.possibly_by}
            for item in result.replacements.possible
        ],
        "complete": result.complete,
    }


def assert_summary(result, expected):
    assert exact(selected(summary(result), expected)) == exact(expected)


def real_result(data):
    context = build_context("northwind", [data], FIELDS, complete=True)
    receipt = context.receipts[0]
    replacements = resolve_replacements(receipt, run_checks(receipt, RULESET, FIELDS, context), RULESET, FIELDS)
    return receipt, replacements, resolve_approvals(receipt, replacements, RULESET)


def made_up_result(case):
    receipt = Receipt.model_validate({**RECEIPT.model_dump(), **case["references"]})
    result, ruleset = made_up_input(case)
    return receipt, resolve_replacements(receipt, result, ruleset, FIELDS), ruleset


@pytest.mark.parametrize("case", load_cases("approvals", "real"))
def test_the_real_rules_on_this_receipt_the_same_way_every_time(case):
    _, _, result = real_result(case["receipt"])
    assert_summary(result, case["expected"])
    assert real_result(case["receipt"])[2] == result


@pytest.mark.parametrize("case", load_cases("approvals", "made_up"))
def test_these_rules_on_this_receipt(case):
    receipt, replacements, ruleset = made_up_result(case)
    assert_summary(resolve_approvals(receipt, replacements, ruleset), case["expected"])


@pytest.mark.parametrize("case", load_cases("approvals", "refused"))
def test_a_resolution_that_cannot_go_ahead_is_refused(case):
    receipt, replacements, ruleset = made_up_result(case)
    with pytest.raises(ValueError, match=case["expected"]):
        resolve_approvals(receipt, replacements, ruleset)


def test_findings_for_another_receipt_are_refused():
    receipt, replacements, _ = real_result(waived_case()["receipt"])
    other = receipt.model_copy(update={"receipt_id": "OTHER"})
    with pytest.raises(ValueError, match="OTHER: has findings for another receipt"):
        resolve_approvals(other, replacements, RULESET)


# --- What a waiver keeps ---


def waived_case():
    return next(case.values[0] for case in load_cases("approvals", "real") if case.values[0]["id"] == "TECH-02 with a pre-approval")


def test_a_waived_finding_keeps_everything_but_its_status_and_says_why():
    _, replacements, result = real_result(waived_case()["receipt"])
    original = replacements.active[0]
    item = result.waived[0]
    assert original.status == "raised" and original.resolved_by is None
    assert (item.finding.status, item.finding.resolved_by) == ("waived", "EXC-02")
    same = {"status", "resolved_by"}
    assert item.finding.model_dump(exclude=same) == original.model_dump(exclude=same)
    assert item.finding.provenance == RULES["TECH-02"].provenance
    assert item.finding.evidence == original.evidence and item.finding.evidence
    assert "PA-2026-0001" in item.reason and "not authenticated" in item.reason


def test_candidates_are_not_erased_or_changed():
    _, replacements, result = real_result(waived_case()["receipt"])
    assert result.candidates == replacements.candidates
    assert [finding.status for finding in result.candidates] == ["raised"] * len(result.candidates)
    assert result.replaced == replacements.replaced


def test_a_result_cannot_be_changed():
    _, _, result = real_result(waived_case()["receipt"])
    with pytest.raises(AttributeError):
        result.active = ()
    with pytest.raises(AttributeError):
        result.waived[0].reference = "other"
    with pytest.raises(ValueError):
        result.waived[0].finding.status = "raised"


# --- The directive and the model ---


def test_every_kind_of_reference_has_its_own_field_on_a_receipt():
    assert set(REFERENCE_FIELDS.values()) <= set(Receipt.model_fields)


def test_the_real_directive_maps_each_rule_to_the_field_it_needs():
    directive = RULES["EXC-02"]
    for kind, field in REFERENCE_FIELDS.items():
        for rule_id in directive.params[f"{kind}_rules"].values:
            rule = RULES[rule_id]
            assert (getattr(rule, "waived_by_ref", None) or rule.requires_ref) == kind
            assert field == f"{kind}_ref"


def test_a_real_ruleset_with_no_directive_is_refused_only_for_rules_that_accept_a_reference():
    without = RuleSet.model_construct(rules=tuple(rule for rule in RULESET.rules if rule.rule_id != "EXC-02"))
    receipt = CONTEXT.receipts[0]
    replacements = resolve_replacements(receipt, run_checks(receipt, without, FIELDS, CONTEXT), without, FIELDS)
    with pytest.raises(ValueError, match="no directive says which kind"):
        resolve_approvals(receipt, replacements, without)


# --- Against the real data and rules ---


@pytest.mark.parametrize("receipt", CONTEXT.receipts, ids=lambda receipt: receipt.receipt_id)
def test_a_real_receipt_loses_no_finding(receipt):
    replacements = resolve_replacements(receipt, run_checks(receipt, RULESET, FIELDS, CONTEXT), RULESET, FIELDS)
    result = resolve_approvals(receipt, replacements, RULESET)
    assert len(result.active) + len(result.waived) == len(replacements.active)
    assert len(result.active) + len(result.waived) + len(result.replaced) == len(result.candidates)
    assert result.complete == (receipt.receipt_id not in NO_RECIPIENT_TYPE)
    assert resolve_approvals(receipt, replacements, RULESET) == result


def test_the_real_data_has_no_finding_that_a_recorded_reference_waives():
    """Its references are pre-approvals, on receipts where TECH-02 does not fire: they decide nothing."""
    with_reference = [receipt for receipt in CONTEXT.receipts if receipt.pre_approval_ref is not None]
    assert with_reference
    for receipt in CONTEXT.receipts:
        replacements = resolve_replacements(receipt, run_checks(receipt, RULESET, FIELDS, CONTEXT), RULESET, FIELDS)
        assert resolve_approvals(receipt, replacements, RULESET).waived == ()


def test_real_gaps_are_the_two_answers_that_need_a_reference():
    gaps = []
    for receipt in CONTEXT.receipts:
        replacements = resolve_replacements(receipt, run_checks(receipt, RULESET, FIELDS, CONTEXT), RULESET, FIELDS)
        gaps += [(item.finding.receipt_id, item.finding.rule_id, item.state) for item in resolve_approvals(receipt, replacements, RULESET).gaps]
    assert gaps == [("RCP-026", "TECH-02", "no_reference"), ("RCP-032", "TECH-04", "no_reference")]
