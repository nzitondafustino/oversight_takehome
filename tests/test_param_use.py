"""Every helper param is described, and a declarative rule's params are found where its conditions compare them."""

from pathlib import Path

import yaml

from expense_validator.policy.param_use import helper_param_use, referenced_params, undescribed_helper_params

LOGIC = {rule["rule_id"]: rule for rule in yaml.safe_load(Path("rules/rule_logic.yaml").read_text())["rules"]}


def test_every_helper_param_the_contracts_name_is_described():
    assert undescribed_helper_params() == []


def test_a_declarative_rule_compares_its_params_in_its_own_conditions():
    assert referenced_params(LOGIC["DOC-01"]["applies_when"]) == {"total_usd"}


def test_a_split_threshold_names_the_rule_it_belongs_to():
    assert helper_param_use(LOGIC["TIME-03"]["helper"], "gift_cap_usd")["threshold_of"] == "GIFT-01"
    assert helper_param_use(LOGIC["TIME-03"]["helper"], "itemization_usd")["threshold_of"] == "DOC-01"
    assert helper_param_use(None, "cap_usd") is None
