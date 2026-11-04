"""Runs the cases in cases/engine/carveouts.yaml, and checks carve-outs on all the real data."""

import pytest
from test_helpers import CONTEXT, FIELDS, RULES, RULESET
from yaml_cases import exact, load_cases, selected

from expense_validator.engine.approvals import resolve_approvals
from expense_validator.engine.carveouts import resolve_carveouts
from expense_validator.engine.checks import run_checks
from expense_validator.engine.helpers import build_context
from expense_validator.engine.replacements import resolve_replacements
from expense_validator.policy.schema import RuleSet


def targets(findings):
    return [{"rule_id": item.rule_id, "line_ids": list(item.line_ids)} for item in findings]


def decisions(items):
    return [
        {"rule_id": item.finding.rule_id, "line_ids": list(item.line_ids), "by": item.rule_id, "reference": item.reference}
        for item in items
    ]


def summary(result):
    return {
        "active": targets(result.active),
        "unaffected": targets(result.unaffected),
        "replaced": targets(item.finding for item in result.replaced),
        "waived": targets(item.finding for item in result.waived),
        "excused": decisions(result.excused),
        "rejected": decisions(result.rejected),
        "undetermined_carveouts": decisions(result.undetermined_carveouts),
        "undetermined": [
            {"rule_id": item.rule_id, "line_id": item.line_id} for item in result.approvals.replacements.undetermined
        ],
        "complete": result.complete,
    }


def resolve(case, ruleset):
    context = build_context("northwind", case["receipts"], FIELDS, complete=case["complete"])
    receipt = next(item for item in context.receipts if item.receipt_id == case["receipt_id"])
    replacements = resolve_replacements(receipt, run_checks(receipt, ruleset, FIELDS, context), ruleset, FIELDS)
    approvals = resolve_approvals(receipt, replacements, ruleset)
    return resolve_carveouts(receipt, approvals, ruleset, FIELDS, context)


def assert_summary(result, expected):
    assert exact(selected(summary(result), expected)) == exact(expected)


def made_up_ruleset(case):
    return RuleSet.model_validate({"ruleset_version": "v1", "rules": case["rules"]})


@pytest.mark.parametrize("case", load_cases("carveouts", "real"))
def test_the_real_rules_decide_this_the_same_way_every_time(case):
    result = resolve(case, RULESET)
    assert_summary(result, case["expected"])
    assert resolve(case, RULESET) == result


@pytest.mark.parametrize("case", load_cases("carveouts", "made_up"))
def test_these_rules_decide_this(case):
    assert_summary(resolve(case, made_up_ruleset(case)), case["expected"])


@pytest.mark.parametrize("case", load_cases("carveouts", "refused"))
def test_a_carve_out_that_cannot_be_decided_is_refused(case):
    with pytest.raises(ValueError, match=case["expected"]):
        resolve(case, made_up_ruleset(case))


# --- What a decision keeps ---


def case_named(name):
    return next(case.values[0] for case in load_cases("carveouts", "real") if case.values[0]["id"] == name)


def facts_of(decision):
    return {item.fact: item.value for item in decision.evidence}


def test_an_excused_finding_keeps_everything_but_its_status_and_says_why():
    result = resolve(case_named("a delay just above the threshold"), RULESET)
    decision = result.excused[0]
    original = result.candidates[0]
    assert (original.status, original.resolved_by) == ("raised", None)
    assert (decision.finding.status, decision.finding.resolved_by) == ("waived", "PERS.BAGGAGE")
    same = {"status", "resolved_by"}
    assert decision.finding.model_dump(exclude=same) == original.model_dump(exclude=same)
    assert decision.finding.provenance == RULES["PERS-01"].provenance
    assert facts_of(decision) == {
        "receipt.baggage_delay_ref": "BAG-1",
        "receipt.baggage_delay_hours": "24.01",
        "param.PERS.BAGGAGE.min_delay_hours": "24",
        "helper.delay_total_usd": "100.00",
        "param.PERS.BAGGAGE.allowance_usd": "150",
    }
    assert "not authenticated" in decision.reason


@pytest.mark.parametrize(
    "name, word",
    [
        ("a delay just below the threshold", "23.99 hours is not more than the 24 required"),
        ("a delay of exactly 24 hours is not more than 24", "24 hours is not more than the 24 required"),
        ("just above the allowance", "above the allowance of 150"),
        ("a massage is personal care, but not an essential toiletry", "not an item the allowance covers"),
        ("neither a reference nor a duration is rejected", "no baggage_delay_ref is on file"),
    ],
)
def test_a_rejected_finding_stays_as_it_was_and_says_why(name, word):
    result = resolve(case_named(name), RULESET)
    decision = result.rejected[0]
    assert decision.finding == result.candidates[0]
    assert decision.finding.status == "raised"
    assert word in decision.reason


@pytest.mark.parametrize(
    "name, word",
    [
        ("a reference with no duration is undetermined", "the delay is not stated"),
        ("personal care not known to be essential is undetermined", "not known to be an essential toiletry"),
        ("receipts sharing a reference that state different delays are undetermined", "state different delays"),
        ("a partial context cannot show the shared total is within the allowance", "cannot be shown to be within 150"),
    ],
)
def test_an_undetermined_finding_stays_as_it_was_and_says_why(name, word):
    result = resolve(case_named(name), RULESET)
    decision = result.undetermined_carveouts[0]
    assert decision.finding == result.candidates[0]
    assert decision.finding.status == "raised"
    assert word in decision.reason


def test_a_missing_duration_is_cited_as_missing_not_zero():
    decision = resolve(case_named("a reference with no duration is undetermined"), RULESET).undetermined_carveouts[0]
    assert facts_of(decision)["receipt.baggage_delay_hours"] is None


def test_a_result_cannot_be_changed_and_keeps_every_candidate():
    result = resolve(case_named("a delay just above the threshold"), RULESET)
    assert [finding.status for finding in result.candidates] == ["raised"]
    with pytest.raises(AttributeError):
        result.active = ()
    with pytest.raises(AttributeError):
        result.excused[0].reference = "other"
    with pytest.raises(ValueError):
        result.excused[0].finding.status = "raised"


def test_resolving_changes_nothing_it_is_given():
    case = case_named("a delay just above the threshold")
    context = build_context("northwind", case["receipts"], FIELDS, complete=True)
    receipt = context.receipts[0]
    replacements = resolve_replacements(receipt, run_checks(receipt, RULESET, FIELDS, context), RULESET, FIELDS)
    approvals = resolve_approvals(receipt, replacements, RULESET)
    before = (receipt.model_dump(), [item.model_dump() for item in approvals.active], [item.model_dump() for item in approvals.candidates])
    resolve_carveouts(receipt, approvals, RULESET, FIELDS, context)
    assert before == (receipt.model_dump(), [item.model_dump() for item in approvals.active], [item.model_dump() for item in approvals.candidates])
    assert all(item.status == "raised" for item in approvals.active)


def test_findings_for_another_receipt_are_refused():
    case = case_named("a delay just above the threshold")
    context = build_context("northwind", case["receipts"], FIELDS, complete=True)
    receipt = context.receipts[0]
    replacements = resolve_replacements(receipt, run_checks(receipt, RULESET, FIELDS, context), RULESET, FIELDS)
    approvals = resolve_approvals(receipt, replacements, RULESET)
    other = receipt.model_copy(update={"receipt_id": "OTHER"})
    with pytest.raises(ValueError, match="OTHER: has findings for another receipt"):
        resolve_carveouts(other, approvals, RULESET, FIELDS, context)


# --- Against the real data and rules ---


def resolved_rows():
    for receipt in CONTEXT.receipts:
        replacements = resolve_replacements(receipt, run_checks(receipt, RULESET, FIELDS, CONTEXT), RULESET, FIELDS)
        approvals = resolve_approvals(receipt, replacements, RULESET)
        yield receipt, approvals, resolve_carveouts(receipt, approvals, RULESET, FIELDS, CONTEXT)


def test_the_real_data_holds_no_delay_duration_or_reference():
    assert all(receipt.baggage_delay_ref is None and receipt.baggage_delay_hours is None for receipt in CONTEXT.receipts)


def test_no_real_finding_is_excused_or_left_undetermined_as_no_reference_is_on_file():
    rejected = []
    for _, approvals, result in resolved_rows():
        assert result.excused == () and result.undetermined_carveouts == ()
        assert result.active == approvals.active
        assert result.undetermined_carveouts == ()
        rejected += [(item.finding.receipt_id, item.finding.rule_id, item.line_ids) for item in result.rejected]
    assert rejected == [
        ("RCP-017", "PERS-01", ("RCP-017-L1",)),
        ("RCP-027", "PERS-02", ("RCP-027-L1",)),
        ("RCP-027", "PERS-02", ("RCP-027-L2",)),
    ]
