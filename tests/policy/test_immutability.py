"""Built rules cannot be changed, at any depth, and defaults go through validation too."""

import pytest
from pydantic import TypeAdapter, ValidationError

from expense_validator.policy.schema import AppliesTo, NumberParam, Rule, RuleSet

RULE = TypeAdapter(Rule)


def check_rule(**overrides):
    return {
        "kind": "check",
        "rule_id": "AA-01",
        "policy_doc": "doc.md",
        "title": "One",
        "source_quote": "quote",
        "provenance": {"translator": "developer", "clause_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "fingerprint": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"},
        "outcome": "exception",
        "scope": "item",
        "severity": "high",
        "params": {"banned": {"type": "set", "values": ["alcohol"]}},
        "select": {
            "op": "all",
            "conditions": [{"op": "in", "field": "item.category", "values": {"param": "banned"}}],
        },
        "replaces": [{"rule_id": "BB-02", "level": "receipt"}],
        **overrides,
    }


def test_fields_cannot_be_reassigned():
    with pytest.raises(ValidationError):
        RULE.validate_python(check_rule()).severity = "low"


def test_params_cannot_be_changed():
    rule = RULE.validate_python(check_rule())
    with pytest.raises(TypeError):
        rule.params["banned"] = NumberParam(type="number", value="1", unit="usd")
    with pytest.raises(TypeError):
        del rule.params["banned"]
    assert not hasattr(rule.params, "update")
    assert not hasattr(rule.params, "clear")


def test_params_cannot_be_changed_by_going_around_the_view():
    """A dict subclass can be bypassed like this. A read-only view cannot."""
    rule = RULE.validate_python(check_rule())
    with pytest.raises(TypeError):
        dict.__setitem__(rule.params, "banned", "changed")
    with pytest.raises(TypeError):
        dict.clear(rule.params)
    assert rule.params["banned"].values == ("alcohol",)


def test_rules_with_the_same_params_are_equal():
    assert RULE.validate_python(check_rule()) == RULE.validate_python(check_rule())
    assert RULE.validate_python(check_rule()) != RULE.validate_python(check_rule(params={}, select=None, helper="gift_cap"))


def test_lists_inside_a_rule_are_tuples():
    rule = RULE.validate_python(check_rule())
    assert isinstance(rule.params["banned"].values, tuple)
    assert isinstance(rule.select.conditions, tuple)
    assert isinstance(rule.replaces, tuple)
    assert isinstance(rule.severity_cases, tuple)


def test_changing_the_input_afterwards_does_not_change_the_rule():
    data = check_rule()
    rule = RULE.validate_python(data)
    data["params"]["banned"]["values"].append("tobacco")
    data["params"]["other"] = {"type": "set", "values": ["x"]}
    assert rule.params["banned"].values == ("alcohol",)
    assert set(rule.params) == {"banned"}


def test_a_ruleset_is_immutable_and_still_round_trips():
    ruleset = RuleSet.model_validate(
        {"ruleset_version": "v1", "rules": [check_rule(replaces=[])]}
    )
    assert isinstance(ruleset.rules, tuple)
    assert RuleSet.from_json(ruleset.to_json()).to_json() == ruleset.to_json()


def test_applies_to_is_immutable():
    applies = AppliesTo(classes=["alcohol"])
    assert applies.classes == ("alcohol",)
    assert AppliesTo(classes="all", excludes=["gift"]).excludes == ("gift",)


def test_defaults_are_validated():
    """A wrong default would otherwise pass unnoticed, as pydantic skips them unless told not to."""
    from pydantic import BaseModel

    from expense_validator.policy.schema import _Strict

    assert _Strict.model_config["validate_default"] is True
    assert issubclass(_Strict, BaseModel)
    assert RuleSet.model_validate({"ruleset_version": "v1", "rules": []}).schema_version == "1"
