"""Runs the cases in cases/engine/admission.yaml, and checks admission against the real catalog."""

import re
from decimal import Decimal

import pytest
from receipt_rows import ROWS, without_answers
from yaml_cases import exact, load_cases, selected

from expense_validator.compiler.validate import read_fields
from expense_validator.config import load_config
from expense_validator.engine.facts import MEAL_TYPES, item_facts
from expense_validator.engine.inputs import admit_receipt, classification_state
from expense_validator.engine.models import LineItem, Receipt
from expense_validator.policy.evaluate import matches, replaced_on_receipt
from expense_validator.policy.schema import RuleSet

PATHS = load_config().paths
FIELDS = read_fields(PATHS.rule_fields.read_text())
RULESET = RuleSet.from_json(PATHS.ruleset.read_text())
RULES = {rule.rule_id: rule for rule in RULESET.rules}
COUNTRIES = FIELDS["receipt.entity"]["countries"]
BASE = without_answers(ROWS[0])
BASE_LINE = BASE["line_items"][0]


@pytest.mark.parametrize("case", load_cases("admission", "admitted"))
def test_a_receipt_is_admitted(case):
    built = admit_receipt(case["receipt"], FIELDS)
    assert exact(selected(built.model_dump(mode="json"), case["expected"])) == exact(case["expected"])


@pytest.mark.parametrize("case", load_cases("admission", "left_out_admitted"))
def test_a_receipt_is_admitted_with_a_field_left_out(case):
    data = {name: value for name, value in case["receipt"].items() if name != case["field"]}
    built = admit_receipt(data, FIELDS)
    assert exact(selected(built.model_dump(mode="json"), case["expected"])) == exact(case["expected"])


@pytest.mark.parametrize("case", load_cases("admission", "refused"))
def test_a_receipt_is_refused_and_not_corrected(case):
    with pytest.raises(ValueError, match=case["expected"]):
        admit_receipt(case["receipt"], FIELDS)


@pytest.mark.parametrize("case", load_cases("admission", "left_out_refused"))
def test_a_required_submitted_value_cannot_be_left_out(case):
    data = {name: value for name, value in case["receipt"].items() if name != case["field"]}
    with pytest.raises(ValueError, match=case["expected"]):
        admit_receipt(data, FIELDS)


@pytest.mark.parametrize("case", load_cases("admission", "regional"))
def test_an_admitted_receipt_meets_its_regional_rules(case):
    built = admit_receipt(case["receipt"], FIELDS)
    facts = item_facts(built, built.line_items[0], FIELDS)
    assert matches(RULES[case["rule_id"]], facts) is case["expected"]["fires"]
    assert replaced_on_receipt(RULESET, facts) == set(case["expected"]["replaced"])


@pytest.mark.parametrize("case", load_cases("admission", "totals"))
def test_a_decimal_total_is_admitted_exactly(case):
    # BASE is in USD, so the two totals are one amount: a total alone would contradict the other.
    data = {**BASE, "total_amount": Decimal(case["decimal"]), "total_amount_usd": Decimal(case["decimal"])}
    for given in (data, Receipt.model_validate(data)):
        total = getattr(admit_receipt(given, FIELDS), case["field"])
        assert isinstance(total, Decimal)
        assert str(total) == case["expected"]["total"]


@pytest.mark.parametrize("case", load_cases("admission", "totals_refused"))
def test_a_negative_decimal_total_is_refused(case):
    data = {**BASE, case["field"]: Decimal(case["decimal"])}
    for given in (data, Receipt.model_validate(data)):
        with pytest.raises(ValueError, match=case["expected"]):
            admit_receipt(given, FIELDS)


@pytest.mark.parametrize("case", load_cases("admission", "readmitted"))
def test_a_receipt_built_directly_can_be_admitted(case):
    built = admit_receipt(Receipt.model_validate(case["receipt"]), FIELDS)
    assert exact(selected(built.model_dump(mode="json"), case["expected"])) == exact(case["expected"])


@pytest.mark.parametrize("case", load_cases("admission", "readmitted_refused"))
def test_a_receipt_built_directly_is_not_taken_as_admitted(case):
    with pytest.raises(ValueError, match=case["expected"]):
        admit_receipt(Receipt.model_validate(case["receipt"]), FIELDS)


@pytest.mark.parametrize("case", load_cases("admission", "copied_refused"))
def test_a_copy_made_around_validation_is_not_admitted(case):
    admitted = admit_receipt(case["receipt"], FIELDS)
    with pytest.raises(ValueError, match=case["expected"]):
        admit_receipt(admitted.model_copy(update=case["update"]), FIELDS)


@pytest.mark.parametrize("case", load_cases("admission", "catalog_refused"))
def test_a_submitted_catalog_with_no_values_is_an_error_not_a_free_pass(case):
    with pytest.raises(ValueError, match=case["expected"]):
        admit_receipt(case["receipt"], case["fields"])


@pytest.mark.parametrize("case", load_cases("admission", "states"))
def test_a_classification_state_survives_json(case):
    item = LineItem.model_validate(case["line"])
    threshold = Decimal(case["min_confidence"])
    assert classification_state(item, case["field"], FIELDS, threshold) == case["expected"]["state"]
    back = LineItem.model_validate_json(item.model_dump_json())
    assert classification_state(back, case["field"], FIELDS, threshold) == case["expected"]["state"]


@pytest.mark.parametrize("case", load_cases("admission", "states_refused"))
def test_a_field_with_no_vocabulary_has_no_classification_state(case):
    item = LineItem.model_validate(case["line"])
    with pytest.raises(ValueError, match=case["expected"]):
        classification_state(item, case["field"], FIELDS, Decimal(case["min_confidence"]))


@pytest.mark.parametrize("case", load_cases("admission", "state_catalog_refused"))
def test_a_classified_catalog_with_no_values_is_an_error_not_a_free_pass(case):
    item = LineItem.model_validate(case["line"])
    with pytest.raises(ValueError, match=case["expected"]):
        classification_state(item, case["field"], case["fields"], Decimal("0.5"))


# --- Against the real data, catalog and rules ---


@pytest.mark.parametrize("row", ROWS, ids=lambda row: row["receipt_id"])
def test_every_receipt_in_the_data_is_admitted(row):
    built = admit_receipt(without_answers(row), FIELDS)
    assert (built.entity, built.submitted_expense_type) == (row["entity"], row["submitted_expense_type"])


@pytest.mark.parametrize("row", ROWS, ids=lambda row: row["receipt_id"])
def test_admitting_a_real_receipt_again_changes_nothing_even_once_classified(row):
    once = admit_receipt(without_answers(row), FIELDS)
    assert admit_receipt(once, FIELDS) == once
    classified = once.with_classifications({item.line_id: {"category": "other", "confidence": "0.9"} for item in once.line_items})
    assert admit_receipt(classified, FIELDS) == classified


@pytest.mark.parametrize("row", ROWS, ids=lambda row: row["receipt_id"])
def test_the_entity_derived_for_every_receipt_is_the_one_in_the_data(row):
    data = {name: value for name, value in without_answers(row).items() if name != "entity"}
    assert admit_receipt(data, FIELDS).entity == row["entity"]


def test_every_expense_type_the_catalog_allows_is_admitted():
    for value in FIELDS["receipt.submitted_expense_type"]["values"]:
        assert admit_receipt({**BASE, "submitted_expense_type": value}, FIELDS).submitted_expense_type == value


def test_every_country_the_catalog_lists_is_admitted_with_its_entity():
    for entity, countries in COUNTRIES.items():
        for country in countries:
            assert admit_receipt({**BASE, "employee_country": country, "entity": entity}, FIELDS).entity == entity
            assert admit_receipt({**BASE, "employee_country": country, "entity": None}, FIELDS).entity == entity


def test_every_country_is_refused_under_every_other_entity():
    for entity, countries in COUNTRIES.items():
        for country in countries:
            for other in set(COUNTRIES) - {entity}:
                with pytest.raises(ValueError, match="contradicts"):
                    admit_receipt({**BASE, "employee_country": country, "entity": other}, FIELDS)


def test_the_countries_cover_every_entity_and_each_country_has_one():
    assert set(COUNTRIES) == set(FIELDS["receipt.entity"]["values"])
    listed = [country for countries in COUNTRIES.values() for country in countries]
    assert len(listed) == len(set(listed))


def test_the_regional_countries_match_the_regional_addendum():
    text = (PATHS.policy_docs / "regional-addendum.md").read_text()
    stated = {entity: codes.split(", ") for entity, codes in re.findall(r"^## (\w+) entity — employees in (.+)$", text, re.MULTILINE)}
    assert stated == {entity: COUNTRIES[entity] for entity in stated}
    assert set(COUNTRIES) - set(stated) == {"GLOBAL"}


@pytest.mark.parametrize("name", ["category", "recipient_type", "venue_type", "item_kind"])
def test_every_value_the_catalog_allows_is_valid(name):
    threshold = RULES["EXC-03"].params["min_confidence"].value
    for value in FIELDS[f"item.{name}"]["values"]:
        item = LineItem.model_validate({**BASE_LINE, name: value, "confidence": "1"})
        assert classification_state(item, name, FIELDS, threshold) == "valid"


def test_every_gold_category_in_the_data_is_in_the_catalog():
    gold = {item["labels"]["category"] for row in ROWS for item in row["line_items"]}
    assert gold <= set(FIELDS["item.category"]["values"])


def test_the_threshold_in_exc_03_can_be_compared_with_a_confidence():
    threshold = RULES["EXC-03"].params["min_confidence"].value
    assert isinstance(threshold, Decimal)
    item = LineItem.model_validate({**BASE_LINE, "category": "alcohol", "confidence": str(threshold)})
    assert classification_state(item, "category", FIELDS, threshold) == "valid"


def test_the_meal_types_in_the_facts_are_in_the_catalog():
    assert MEAL_TYPES <= set(FIELDS["receipt.submitted_expense_type"]["values"])
