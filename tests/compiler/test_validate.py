"""Runs the cases in cases/compiler/validate.yaml, then validates the real rules."""

import re

import pytest
import yaml
from yaml_cases import load_cases

from expense_validator.compiler.build_rules import build_ruleset
from expense_validator.compiler.extract import extract_document
from expense_validator.compiler.validate import read_fields, validate_ruleset
from expense_validator.config import load_config
from expense_validator.policy.schema import CheckRule
from expense_validator.policy.vocabulary import HELPER_CONTRACTS

PATHS = load_config().paths
FIELDS = read_fields(PATHS.rule_fields.read_text())
CLAUSES = [
    clause
    for path in sorted(PATHS.policy_docs.glob("*.md"))
    for clause in extract_document(path.read_text(), path.name).clauses
]
RULESET = build_ruleset(PATHS.rule_logic.read_text(), CLAUSES)


@pytest.mark.parametrize("case", load_cases("validate", "cases"))
def test_validate(case):
    clauses = extract_document(case["text"], "doc.md").clauses
    ruleset = build_ruleset(yaml.safe_dump(case["logic"]), clauses)
    assert [str(issue) for issue in validate_ruleset(ruleset, clauses, FIELDS)] == case["expected"]


def test_the_real_rules_have_no_issues():
    assert [str(issue) for issue in validate_ruleset(RULESET, CLAUSES, FIELDS)] == []


def test_every_helper_has_a_contract():
    from typing import get_args

    from expense_validator.policy.vocabulary import Helper

    assert set(HELPER_CONTRACTS) == set(get_args(Helper))


def test_every_helper_is_used_by_a_real_rule():
    used = {getattr(rule, "helper", None) for rule in RULESET.rules}
    assert set(HELPER_CONTRACTS) <= used


def test_category_values_are_the_taxonomy():
    taxonomy = (PATHS.policy_docs.parent / "category-taxonomy.md").read_text()
    assert set(FIELDS["item.category"]["values"]) == set(re.findall(r"^\| `(\w+)` \|", taxonomy, re.MULTILINE))


def test_every_field_in_the_data_dictionary_exists():
    readme = (PATHS.policy_docs.parent / "README.md").read_text()
    # input_contract is not a field a rule reads: it says how amounts are compared at admission.
    fields = {name: field for name, field in FIELDS.items() if name != "input_contract"}
    for name, field in fields.items():
        if name.startswith("receipt.") and name not in {
            "receipt.is_meal_receipt",
            "receipt.transaction_date",
            "receipt.local_hour",
        }:
            assert f"`{name.removeprefix('receipt.')}`" in readme, name
    assert all(field["type"] in {"text", "number", "bool"} for field in fields.values())


def test_all_real_conditions_read_known_fields():
    checks = [rule for rule in RULESET.rules if isinstance(rule, CheckRule)]
    assert checks
    assert not [issue for issue in validate_ruleset(RULESET, CLAUSES, FIELDS) if issue.check == "fields"]
