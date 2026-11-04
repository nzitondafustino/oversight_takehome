"""Runs the cases in cases/policy/rules.yaml and cases/policy/rulesets.yaml."""

import json

import pytest
from pydantic import TypeAdapter, ValidationError
from yaml_cases import load_cases

from expense_validator.policy.schema import Rule, RuleSet

RULE = TypeAdapter(Rule)


@pytest.mark.parametrize("case", load_cases("rules", "accepted"))
def test_rule_is_accepted(case):
    fields = RULE.validate_python(case["rule"]).model_dump(mode="json")
    assert {name: fields[name] for name in case["expected"]} == case["expected"]


@pytest.mark.parametrize("case", load_cases("rules", "refused"))
def test_rule_is_refused(case):
    with pytest.raises(ValidationError, match=case["expected"]):
        RULE.validate_python(case["rule"])


@pytest.mark.parametrize("case", load_cases("rulesets", "accepted"))
def test_ruleset_is_accepted_and_round_trips(case):
    text = RuleSet.model_validate(case["ruleset"]).to_json()
    assert RuleSet.from_json(text).to_json() == text

    published = json.loads(text)["rules"]
    assert [rule["rule_id"] for rule in published] == case["expected"]["rule_ids"]
    params = {rule["rule_id"]: rule["params"] for rule in published if rule["rule_id"] in case["expected"]["params"]}
    assert params == case["expected"]["params"]


@pytest.mark.parametrize("case", load_cases("rulesets", "refused"))
def test_ruleset_is_refused(case):
    with pytest.raises(ValidationError, match=case["expected"]):
        RuleSet.model_validate(case["ruleset"])
