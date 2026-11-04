"""Runs the cases in cases/engine/facts.yaml, and checks the facts against all the real data."""

import pytest
from receipt_rows import ROWS, without_answers
from yaml_cases import exact, load_cases

from expense_validator.compiler.validate import read_fields
from expense_validator.config import load_config
from expense_validator.engine.facts import item_facts, receipt_facts
from expense_validator.engine.inputs import admit_receipt
from expense_validator.engine.models import Receipt
from expense_validator.policy.evaluate import matches, replaced_on_receipt
from expense_validator.policy.schema import CheckRule, RuleSet, walk

PATHS = load_config().paths
FIELDS = read_fields(PATHS.rule_fields.read_text())
RULESET = RuleSet.from_json(PATHS.ruleset.read_text())
RULES = {rule.rule_id: rule for rule in RULESET.rules}


def first_item_facts(data):
    built = Receipt.model_validate(data)
    return item_facts(built, built.line_items[0], FIELDS)


def named_facts(facts, expected):
    # Fact names hold dots of their own, so these are looked up whole.
    return exact({name: facts[name] for name in expected})


@pytest.mark.parametrize("case", load_cases("facts", "receipt"))
def test_receipt_facts(case):
    facts = receipt_facts(Receipt.model_validate(case["receipt"]), FIELDS)
    assert named_facts(facts, case["expected"]) == exact(case["expected"])


@pytest.mark.parametrize("case", load_cases("facts", "item"))
def test_item_facts(case):
    assert named_facts(first_item_facts(case["receipt"]), case["expected"]) == exact(case["expected"])


@pytest.mark.parametrize("case", load_cases("facts", "catalog_refused"))
def test_a_catalog_with_no_categories_is_an_error_not_a_guess(case):
    with pytest.raises(ValueError, match=case["expected"]):
        receipt_facts(Receipt.model_validate(case["receipt"]), case["fields"])


@pytest.mark.parametrize("case", load_cases("facts", "receipt_rules"))
def test_a_receipt_rule_reads_the_facts(case):
    facts = receipt_facts(Receipt.model_validate(case["receipt"]), FIELDS)
    assert matches(RULES[case["rule_id"]], facts) is case["expected"]["fires"]


@pytest.mark.parametrize("case", load_cases("facts", "item_rules"))
def test_an_item_rule_reads_the_facts(case):
    assert matches(RULES[case["rule_id"]], first_item_facts(case["receipt"])) is case["expected"]["fires"]


@pytest.mark.parametrize("case", load_cases("facts", "replacements"))
def test_a_regional_replacement_reads_the_facts(case):
    facts = receipt_facts(Receipt.model_validate(case["receipt"]), FIELDS)
    assert replaced_on_receipt(RULESET, facts) == set(case["expected"]["replaced"])


# --- Against the real data, catalog and rules ---


@pytest.mark.parametrize("row", ROWS, ids=lambda row: row["receipt_id"])
def test_facts_on_the_real_data_are_flat(row):
    built = Receipt.model_validate(without_answers(row))
    for facts in (receipt_facts(built, FIELDS), *(item_facts(built, item, FIELDS) for item in built.line_items)):
        assert "receipt.line_items" not in facts
        assert all(not isinstance(value, (dict, list, tuple)) for value in facts.values())


@pytest.mark.parametrize("row", ROWS, ids=lambda row: row["receipt_id"])
def test_the_unit_price_agrees_with_the_rate_in_the_data(row):
    built = Receipt.model_validate(without_answers(row))
    for item in built.line_items:
        assert item_facts(built, item, FIELDS)["item.unit_price_usd"] == item.unit_price * built.fx_rate_to_usd


def test_every_field_a_rule_reads_is_a_fact():
    available = set(first_item_facts(without_answers(ROWS[0])))
    used = {
        node.field
        for rule in RULESET.rules
        if hasattr(rule, "conditions")
        for condition in rule.conditions()
        for node in walk(condition)
        if hasattr(node, "field")
    }
    assert used - available == set()


def test_every_field_in_the_catalog_is_a_fact():
    # input_contract is not a field: it says how amounts are compared at admission.
    assert set(FIELDS) - {"input_contract"} - set(first_item_facts(without_answers(ROWS[0]))) == set()


@pytest.mark.parametrize("row", ROWS, ids=lambda row: row["receipt_id"])
def test_every_receipt_in_the_data_gives_facts_the_rules_can_run_on(row):
    built = admit_receipt(without_answers(row), FIELDS)
    for rule in RULESET.rules:
        if not isinstance(rule, CheckRule) or rule.helper or rule.status != "active":
            continue
        for item in built.line_items:
            assert matches(rule, item_facts(built, item, FIELDS)) in {True, False}
