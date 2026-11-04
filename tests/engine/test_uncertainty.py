"""Runs the cases in cases/engine/uncertainty.yaml, and measures uncertainty against the real data."""

from decimal import Decimal

import pytest
from receipt_rows import ROWS, without_answers
from test_helpers import CONTEXT, FIELDS, RULES, RULESET
from yaml_cases import exact, load_cases, selected

from expense_validator.engine.approvals import resolve_approvals
from expense_validator.engine.carveouts import resolve_carveouts
from expense_validator.engine.checks import run_checks
from expense_validator.engine.facts import receipt_facts
from expense_validator.engine.helpers import build_context
from expense_validator.engine.inputs import admit_receipt
from expense_validator.engine.replacements import resolve_replacements
from expense_validator.engine.uncertainty import assess_context, assess_uncertainty, fully_decided
from expense_validator.policy.schema import CheckRule, RuleSet


def targets(findings):
    return [{"rule_id": item.rule_id, "line_ids": list(item.line_ids)} for item in findings]


def assessment_summary(assessment):
    def text(value):
        return None if value is None else str(value)

    category = [item for item in assessment.signals if item.reason == "category_uncertain"]
    secondary = [item for item in assessment.signals if item.reason == "secondary_field_uncertain"]
    return {
        "category": [{"line_id": item.finding.line_ids[0], "state": item.state, "confidence": text(item.confidence)} for item in category],
        "secondary": [{"line_id": item.finding.line_ids[0], "field": item.field, "state": item.state} for item in secondary],
        "eligible": [line.line_id for line in assessment.eligible_lines],
        "exempt": list(assessment.exempt_line_ids),
    }


def run_all(view, assessed, ruleset=RULESET):
    context = assessed.evaluation
    replacements = resolve_replacements(view, run_checks(view, ruleset, FIELDS, context), ruleset, FIELDS)
    approvals = resolve_approvals(view, replacements, ruleset)
    return resolve_carveouts(view, approvals, ruleset, FIELDS, context)


def assessed_case(case):
    context = build_context("northwind", case["receipts"], FIELDS, complete=case["complete"])
    assessed = assess_context(context, RULESET, FIELDS)
    return assessed, assessed.assessment_for(case["receipt_id"])


def evaluated_summary(case):
    assessed, assessment = assessed_case(case)
    result = run_all(assessment.evaluation_receipt, assessed)
    open_items = result.approvals.replacements.undetermined
    return {
        "active": targets(result.active),
        "candidates": targets(result.candidates),
        "excused": targets(item.finding for item in result.excused),
        "open": sorted({item.rule_id for item in open_items}),
        "open_lines": [{"rule_id": item.rule_id, "line_id": item.line_id} for item in open_items],
        "carve_open": targets(item.finding for item in result.undetermined_carveouts),
        "signals": len(assessment.signals),
        "meal": receipt_facts(assessment.evaluation_receipt, FIELDS)["receipt.is_meal_receipt"],
        "complete": result.complete,
        "decided": fully_decided(assessment, result.complete),
    }


@pytest.mark.parametrize("case", load_cases("uncertainty", "assessed"))
def test_a_line_is_assessed_the_same_way_every_time(case):
    _, assessment = assessed_case(case)
    assert exact(selected(assessment_summary(assessment), case["expected"])) == exact(case["expected"])
    assert assessed_case(case)[1] == assessment
    keys = [(item.finding.line_ids, item.field, item.reason) for item in assessment.signals]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("case", load_cases("uncertainty", "evaluated"))
def test_the_rules_read_the_view_the_same_way_every_time(case):
    summary = evaluated_summary(case)
    expected = dict(case["expected"])
    included = expected.pop("open_lines_include", [])
    assert all(item in summary["open_lines"] for item in included)
    assert exact(selected(summary, expected)) == exact(expected)
    assert evaluated_summary(case) == summary


@pytest.mark.parametrize("case", load_cases("uncertainty", "refused"))
def test_an_assessment_with_no_threshold_is_refused(case):
    ruleset = RuleSet.model_validate({"ruleset_version": "v1", "rules": case["rules"]})
    with pytest.raises(ValueError, match=case["expected"]):
        assess_uncertainty(CONTEXT.receipts[0], ruleset, FIELDS)


# --- What an assessment keeps ---


def case_named(name):
    return next(case.values[0] for case in load_cases("uncertainty", "assessed") if case.values[0]["id"] == name)


def uncertain_receipt():
    return assessed_case(case_named("only the uncertain line is left out"))[1]


def test_the_admitted_receipt_is_unchanged_and_the_view_differs_only_in_categories():
    assessment = uncertain_receipt()
    case = case_named("only the uncertain line is left out")
    admitted = build_context("northwind", case["receipts"], FIELDS, complete=True).receipts[0]
    assert assessment.receipt == admitted
    view = assessment.evaluation_receipt
    assert view.model_dump(exclude={"line_items"}) == admitted.model_dump(exclude={"line_items"})
    assert [line.line_id for line in view.line_items] == [line.line_id for line in admitted.line_items]
    for before, after in zip(admitted.line_items, view.line_items):
        assert after.model_dump(exclude={"category"}) == before.model_dump(exclude={"category"})
    assert [line.category for line in view.line_items] == ["food_beverage", None, "service_fee"]
    assert [line.category for line in admitted.line_items] == ["food_beverage", "food_beverage", "service_fee"]


def test_a_signal_is_an_exc_03_review_signal_with_its_provenance_and_reason():
    item = uncertain_receipt().signals[0]
    rule = RULES["EXC-03"]
    finding = item.finding
    assert (finding.rule_id, finding.outcome, finding.severity, finding.scope, finding.status) == ("EXC-03", "review_signal", None, "item", "raised")
    assert (finding.receipt_id, finding.line_ids) == ("R1", ("L2",))
    assert (finding.policy_doc, finding.source_quote, finding.provenance) == (rule.policy_doc, rule.source_quote, rule.provenance)
    assert (item.reason, item.field, item.state, item.confidence) == ("category_uncertain", "category", "low_confidence", Decimal("0.1"))
    assert {evidence.fact: evidence.value for evidence in finding.evidence} == {
        "uncertainty.reason": "category_uncertain",
        "uncertainty.state": "low_confidence",
        "item.category": "food_beverage",
        "item.confidence": "0.1",
        "param.EXC-03.min_confidence": "0.5",
    }


def test_a_secondary_signal_carries_its_own_reason():
    item = assessed_case(case_named("a gift with no recipient type"))[1].signals[0]
    assert (item.reason, item.field, item.state) == ("secondary_field_uncertain", "recipient_type", "missing")
    assert item.finding.evidence[0].value == "secondary_field_uncertain"


def test_the_threshold_comes_from_the_rule_and_is_marked_a_project_decision():
    rule = RULES["EXC-03"]
    assert rule.params["min_confidence"].source == "project_decision"
    case = case_named("a category just below the threshold")
    receipt = build_context("northwind", case["receipts"], FIELDS, complete=True).receipts[0]
    params = {"min_confidence": rule.params["min_confidence"].model_copy(update={"value": Decimal("0.4")})}
    lower = RuleSet.model_construct(rules=(rule.model_copy(update={"params": params}),))
    assert assess_uncertainty(receipt, lower, FIELDS).signals == ()
    assert assess_uncertainty(receipt, RULESET, FIELDS).signals != ()


def test_an_assessment_cannot_be_changed():
    assessment = uncertain_receipt()
    with pytest.raises(AttributeError):
        assessment.signals = ()
    with pytest.raises(AttributeError):
        assessment.signals[0].reason = "other"
    with pytest.raises(ValueError):
        assessment.evaluation_receipt.line_items[1].category = "alcohol"


def test_every_receipt_in_a_context_is_read_through_the_same_decision():
    case = next(item.values[0] for item in load_cases("uncertainty", "evaluated") if item.values[0]["id"] == "an uncertain line on another receipt is left out of this one's day total too")
    assessed, _ = assessed_case(case)
    assert [receipt.receipt_id for receipt in assessed.evaluation.receipts] == ["R1", "R2"]
    assert assessed.evaluation.receipts[1].line_items[0].category is None
    assert assessed.original.receipts[1].line_items[0].category == "food_beverage"
    assert (assessed.evaluation.customer_id, assessed.evaluation.complete) == ("northwind", True)


def test_a_view_needs_no_second_admission_and_keeps_its_lines_when_all_are_uncertain():
    case = next(item.values[0] for item in load_cases("uncertainty", "evaluated") if item.values[0]["id"].startswith("every line uncertain"))
    _, assessment = assessed_case(case)
    assert len(assessment.evaluation_receipt.line_items) == 2
    assert assessment.eligible_lines == ()
    assert assessment.evaluation_receipt.total_amount_usd == assessment.receipt.total_amount_usd
    assert assessment.evaluation_receipt.itemized is True


# --- The benchmark, with gold categories standing in for a classifier ---


ASSESSED = assess_context(CONTEXT, RULESET, FIELDS)
ACTIVE = [rule for rule in RULESET.rules if isinstance(rule, CheckRule) and rule.status == "active"]
CONDITION_RULES = {rule.rule_id for rule in ACTIVE if not rule.helper}
HELPER_RULES = {rule.rule_id for rule in ACTIVE if rule.helper}


def benchmark():
    matched = {"conditions": 0, "helpers": 0}
    extras, missed, extension, signal_on_pass, open_rules = [], [], [], [], []
    for row in ROWS:
        assessment = ASSESSED.assessment_for(row["receipt_id"])
        result = run_all(assessment.evaluation_receipt, ASSESSED)
        got = {}
        for finding in result.active:
            got.setdefault(finding.rule_id, []).extend(finding.line_ids)
        labels = row["labels"]
        want = {entry["rule_id"]: entry.get("line_ids", []) for entry in labels["expected_exceptions"] + labels["review_signals"]}
        for rule_id in sorted(set(got) | set(want)):
            if rule_id not in want:
                extras.append((row["receipt_id"], rule_id))
            elif rule_id not in got:
                missed.append((row["receipt_id"], rule_id))
            else:
                assert sorted(got[rule_id]) == sorted(want[rule_id]), (row["receipt_id"], rule_id)
                matched["conditions" if rule_id in CONDITION_RULES else "helpers"] += 1
        extension += [(row["receipt_id"], item.finding.line_ids[0], item.field, item.reason) for item in assessment.signals]
        if assessment.signals and labels["disposition"] == "pass":
            signal_on_pass.append(row["receipt_id"])
        open_rules += [(row["receipt_id"], item.rule_id) for item in result.approvals.replacements.undetermined]
    return matched, extras, missed, extension, signal_on_pass, open_rules


def test_the_real_data_matches_the_answers_and_adds_only_these_signals():
    matched, extras, missed, extension, signal_on_pass, open_rules = benchmark()
    assert matched == {"conditions": 28, "helpers": 9}
    assert (extras, missed) == ([], [])
    assert extension == [
        ("RCP-013", "RCP-013-L1", "recipient_type", "secondary_field_uncertain"),
        ("RCP-013", "RCP-013-L2", "recipient_type", "secondary_field_uncertain"),
    ]
    assert signal_on_pass == []
    # With no recipient type on either line, either could be an official's.
    assert open_rules == [("RCP-013", "GIFT-04"), ("RCP-013", "GIFT-04")]


def test_with_gold_categories_no_line_is_category_uncertain():
    assert all(item.uncertain_line_ids == () for item in ASSESSED.assessments)


@pytest.mark.parametrize("row", ROWS, ids=lambda row: row["receipt_id"])
def test_a_real_receipt_with_no_classifier_says_it_is_not_decided(row):
    receipts = [admit_receipt(without_answers(item), FIELDS) for item in ROWS]
    assessed = assess_context(build_context("northwind", receipts, FIELDS, complete=True), RULESET, FIELDS)
    assessment = assessed.assessment_for(row["receipt_id"])
    result = run_all(assessment.evaluation_receipt, assessed)
    assert {item.scope for item in result.candidates} <= {"receipt"}
    assert set(assessment.uncertain_line_ids) == {item["line_id"] for item in row["line_items"]}
    assert fully_decided(assessment, result.complete) is False
