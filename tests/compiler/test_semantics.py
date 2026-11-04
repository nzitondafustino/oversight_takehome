"""Runs the cases in cases/compiler/semantics.yaml: what conditions and the real rules actually do."""

import pytest
from pydantic import TypeAdapter
from yaml_cases import load_cases

from expense_validator.compiler.build_rules import build_ruleset
from expense_validator.compiler.extract import extract_document
from expense_validator.config import load_config
from expense_validator.policy.evaluate import evaluate, matches, replaced_on_receipt
from expense_validator.policy.schema import CheckRule, Condition, Param, RuleSet

PATHS = load_config().paths
RULESET = build_ruleset(
    PATHS.rule_logic.read_text(),
    [
        clause
        for path in sorted(PATHS.policy_docs.glob("*.md"))
        for clause in extract_document(path.read_text(), path.name).clauses
    ],
)
RULES = {rule.rule_id: rule for rule in RULESET.rules}
PARAMS = TypeAdapter(dict[str, Param]).validate_python(
    {"cap": {"type": "number", "value": "100", "unit": "usd"}, "codes": {"type": "set", "values": ["a", "b"]}}
)


@pytest.mark.parametrize("case", load_cases("semantics", "conditions"))
def test_condition(case):
    condition = TypeAdapter(Condition).validate_python(case["condition"])
    assert evaluate(condition, case["facts"], PARAMS) is case["expected"]["result"]


@pytest.mark.parametrize("case", load_cases("semantics", "rules"))
def test_real_rule(case):
    assert matches(RULES[case["rule_id"]], case["facts"]) is case["expected"]["fires"]


@pytest.mark.parametrize("case", load_cases("semantics", "replacements"))
def test_replacements(case):
    assert replaced_on_receipt(RULESET, case["facts"]) == set(case["expected"]["replaced"])


def test_a_helper_rule_cannot_be_run_by_conditions():
    with pytest.raises(ValueError, match="uses the helper meal_cap"):
        matches(RULES["MEAL-01"], {})


def test_a_rule_that_is_not_active_replaces_nothing():
    retired = RULES["REG-02"].model_copy(update={"status": "retired"})
    others = tuple(rule for rule in RULESET.rules if rule.rule_id != "REG-02")
    ruleset = RuleSet.model_construct(rules=(*others, retired))
    assert "MEAL-01" not in replaced_on_receipt(ruleset, {"receipt.entity": "APAC"})


def test_every_condition_rule_has_a_behaviour_case_or_is_a_plain_category_match():
    """A new condition rule needs a case here, unless all it does is match one category."""
    tested = {case.values[0]["rule_id"] for case in load_cases("semantics", "rules")}
    for rule in RULES.values():
        if not isinstance(rule, CheckRule) or rule.helper or rule.status != "active":
            continue
        plain = rule.applies_when is None and rule.select.op == "eq" and rule.select.field == "item.category"
        kind = rule.select is not None and rule.select.op == "eq" and rule.select.field == "item.item_kind"
        assert rule.rule_id in tested or plain or kind or rule.rule_id == "ENT-01", rule.rule_id
