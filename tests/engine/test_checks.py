"""Runs the cases in cases/engine/checks.yaml, and compares run_checks with the answers in the data."""

import pytest
from receipt_rows import ROWS, without_answers
from yaml_cases import exact, load_cases, selected

from expense_validator.compiler.validate import read_fields
from expense_validator.config import load_config
from expense_validator.engine.approvals import resolve_approvals
from expense_validator.engine.checks import run_checks
from expense_validator.engine.inputs import admit_receipt
from expense_validator.engine.replacements import resolve_replacements
from expense_validator.policy.schema import CheckRule, RuleSet

PATHS = load_config().paths
FIELDS = read_fields(PATHS.rule_fields.read_text())
RULESET = RuleSet.from_json(PATHS.ruleset.read_text())
RULES = {rule.rule_id: rule for rule in RULESET.rules}
ACTIVE = [rule for rule in RULESET.rules if isinstance(rule, CheckRule) and rule.status == "active"]
CONDITION_RULES = {rule.rule_id for rule in ACTIVE if not rule.helper}
HELPER_RULES = {rule.rule_id for rule in ACTIVE if rule.helper}


def summary(result):
    return {
        "findings": [
            {"rule_id": item.rule_id, "severity": item.severity, "scope": item.scope, "line_ids": list(item.line_ids)}
            for item in result.findings
        ],
        "pending_helpers": [{"rule_id": item.rule_id, "helper": item.helper} for item in result.pending_helpers],
        "undetermined": [{"rule_id": item.rule_id, "line_id": item.line_id} for item in result.undetermined],
        "complete": result.complete,
    }


def with_gold_categories(row):
    """The correct categories, so that this measures the rules alone. For evaluation only."""
    admitted = admit_receipt(without_answers(row), FIELDS)
    categories = {item["line_id"]: item["labels"]["category"] for item in row["line_items"]}
    return admitted.with_classifications(
        {line_id: {"category": category, "confidence": "1"} for line_id, category in categories.items()}
    )


def assert_summary(result, expected):
    assert exact(selected(summary(result), expected)) == exact(expected)


@pytest.mark.parametrize("case", load_cases("checks", "findings"))
def test_the_real_rules_on_this_receipt(case):
    assert_summary(run_checks(admit_receipt(case["receipt"], FIELDS), RULESET, FIELDS), case["expected"])


@pytest.mark.parametrize("case", load_cases("checks", "details"))
def test_a_finding_carries_these_details(case):
    found = run_checks(admit_receipt(case["receipt"], FIELDS), RULESET, FIELDS).findings
    first = next(item for item in found if item.rule_id == case["rule_id"])
    assert exact(selected(first.model_dump(mode="json"), case["expected"])) == exact(case["expected"])


@pytest.mark.parametrize("case", load_cases("checks", "rules"))
def test_these_rules_on_this_receipt(case):
    ruleset = RuleSet.model_validate({"ruleset_version": "v1", "rules": case["rules"]})
    assert_summary(run_checks(admit_receipt(case["receipt"], FIELDS), ruleset, FIELDS), case["expected"])


@pytest.mark.parametrize("case", load_cases("checks", "refused"))
def test_rules_that_cannot_be_run_are_refused(case):
    receipt = admit_receipt(case["receipt"], FIELDS)
    with pytest.raises(ValueError, match=case["expected"]):
        run_checks(receipt, RuleSet.model_validate({"ruleset_version": "v1", "rules": case["rules"]}), FIELDS)


# --- Against the real data and rules ---


@pytest.mark.parametrize("row", ROWS, ids=lambda row: row["receipt_id"])
def test_a_classified_real_receipt_decides_every_rule_except_the_helper_ones(row):
    receipt = with_gold_categories(row)
    result = run_checks(receipt, RULESET, FIELDS)
    assert result.undetermined == ()
    assert {item.rule_id for item in result.pending_helpers} == HELPER_RULES
    assert result.complete is False
    assert run_checks(receipt, RULESET, FIELDS) == result


@pytest.mark.parametrize("row", ROWS, ids=lambda row: row["receipt_id"])
def test_every_finding_on_the_real_data_comes_from_its_rule_once_per_target(row):
    receipt = with_gold_categories(row)
    found = run_checks(receipt, RULESET, FIELDS).findings
    assert len({(item.rule_id, item.line_ids) for item in found}) == len(found)
    lines = {item.line_id for item in receipt.line_items}
    for finding in found:
        rule = RULES[finding.rule_id]
        assert finding.rule_id in CONDITION_RULES
        assert finding.receipt_id == receipt.receipt_id
        assert set(finding.line_ids) <= lines
        assert finding.status == "raised"
        assert (finding.outcome, finding.scope) == (rule.outcome, rule.scope)
        assert (finding.policy_doc, finding.source_quote, finding.provenance) == (rule.policy_doc, rule.source_quote, rule.provenance)


@pytest.mark.parametrize("row", ROWS, ids=lambda row: row["receipt_id"])
def test_an_unclassified_real_receipt_gives_no_item_finding_and_is_not_complete(row):
    result = run_checks(admit_receipt(without_answers(row), FIELDS), RULESET, FIELDS)
    assert {item.scope for item in result.findings} <= {"receipt"}
    assert {item.line_id for item in result.undetermined} >= {item["line_id"] for item in row["line_items"]}


def test_the_condition_rules_match_the_answers_in_the_data():
    """Measures against the answers once the real replacement and approval steps have run. A rule
    with several findings for one receipt is one answer, as in EXC-04."""
    matched = extras = missed = 0
    for row in ROWS:
        receipt = with_gold_categories(row)
        replacements = resolve_replacements(receipt, run_checks(receipt, RULESET, FIELDS), RULESET, FIELDS)
        got = {}
        for finding in resolve_approvals(receipt, replacements, RULESET).active:
            got.setdefault(finding.rule_id, []).extend(finding.line_ids)
        labels = row["labels"]
        answers = labels["expected_exceptions"] + labels["review_signals"]
        want = {entry["rule_id"]: entry.get("line_ids", []) for entry in answers if entry["rule_id"] in CONDITION_RULES}
        extras += len(set(got) - set(want))
        missed += len(set(want) - set(got))
        for rule_id in set(got) & set(want):
            assert sorted(got[rule_id]) == sorted(want[rule_id]), (row["receipt_id"], rule_id)
            matched += 1
    assert (matched, extras, missed) == (28, 0, 0)
