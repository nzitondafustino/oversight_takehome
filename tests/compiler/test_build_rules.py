"""Runs the cases in cases/compiler/build_rules.yaml, then checks the real rules against the policy and gold answers."""

import json

import pytest
import yaml
from yaml_cases import load_cases

from expense_validator.compiler.build_rules import build_ruleset
from expense_validator.compiler.extract import extract_document
from expense_validator.config import load_config
from expense_validator.policy.schema import CarveOutRule, CheckRule, In, Not, NumberParam, ProcessRule, walk

PATHS = load_config().paths
CLAUSES = {
    clause.rule_id: clause
    for path in sorted(PATHS.policy_docs.glob("*.md"))
    for clause in extract_document(path.read_text(), path.name).clauses
}
RULESET = build_ruleset(PATHS.rule_logic.read_text(), list(CLAUSES.values()))
RULES = {rule.rule_id: rule for rule in RULESET.rules}


def build_inline(case):
    return build_ruleset(yaml.safe_dump(case["logic"]), extract_document(case["text"], "doc.md").clauses)


@pytest.mark.parametrize("case", load_cases("build_rules", "accepted"))
def test_accepted(case):
    fields = build_inline(case).rules[0].model_dump(mode="json")
    assert {name: fields[name] for name in case["expected"]} == case["expected"]


@pytest.mark.parametrize("case", load_cases("build_rules", "refused"))
def test_refused(case):
    with pytest.raises(ValueError, match=case["expected"]):
        build_inline(case)


def test_every_rule_id_has_exactly_one_rule():
    assert len(CLAUSES) == 41
    assert set(RULES) == set(CLAUSES) | {"PERS.BAGGAGE"}


def test_the_published_file_is_up_to_date():
    assert PATHS.ruleset.read_text() == RULESET.to_json()


def test_severity_and_outcome_match_what_extraction_read():
    for rule_id, clause in CLAUSES.items():
        rule = RULES[rule_id]
        if isinstance(rule, CheckRule) and rule.outcome == "exception":
            severities = {rule.severity} | {case.severity for case in rule.severity_cases}
            assert severities == set(clause.severities), rule_id
        if isinstance(rule, CheckRule) and rule.outcome == "review_signal":
            assert clause.review_signal, rule_id


def test_every_number_is_stated_by_a_clause():
    """Every policy number in a rule was typed by hand, so compare it with what extraction read."""
    for rule in RULES.values():
        sources = rule.modifies if isinstance(rule, CarveOutRule) else [rule.rule_id]
        stated = {(t.value, t.unit) for source in sources for t in CLAUSES[source].thresholds}
        for name, param in rule.params.items():
            if isinstance(param, NumberParam) and param.source == "policy":
                assert (param.value, param.unit) in stated, f"{rule.rule_id} {name}"


def test_every_number_in_a_clause_is_held_by_a_rule():
    for rule_id, clause in CLAUSES.items():
        holders = [RULES[rule_id]] + [
            rule for rule in RULES.values() if isinstance(rule, CarveOutRule) and rule_id in rule.modifies
        ]
        held = {
            (param.value, param.unit)
            for rule in holders
            for param in rule.params.values()
            if isinstance(param, NumberParam)
        }
        for threshold in clause.thresholds:
            assert (threshold.value, threshold.unit) in held, f"{rule_id} {threshold}"


def gold_answers():
    """Only read here, to measure the rules. They were not written from these answers."""
    for line in (PATHS.policy_docs.parent / "receipts.jsonl").read_text().splitlines():
        yield json.loads(line)["labels"]


def test_gold_exceptions_match_each_rules_severity_document_and_scope():
    checked = 0
    for labels in gold_answers():
        for exception in labels["expected_exceptions"]:
            rule = RULES[exception["rule_id"]]
            severities = {rule.severity} | {case.severity for case in rule.severity_cases}
            assert rule.outcome == "exception", rule.rule_id
            assert exception["severity"] in severities, rule.rule_id
            assert exception["policy_doc"] == rule.policy_doc, rule.rule_id
            assert bool(exception["line_ids"]) == (rule.scope == "item"), rule.rule_id
            checked += 1
    assert checked > 0


def test_gold_review_signals_come_from_review_signal_rules():
    checked = 0
    for labels in gold_answers():
        for signal in labels["review_signals"]:
            rule = RULES[signal["rule_id"]]
            assert rule.outcome == "review_signal", rule.rule_id
            assert signal["policy_doc"] == rule.policy_doc, rule.rule_id
            checked += 1
    assert checked > 0


def test_whole_receipt_rules_in_evidence_are_the_receipt_scoped_rules():
    whole_receipt = set(RULES["EXC-04"].params["whole_receipt_rules"].values)
    exceptions = {
        rule.rule_id
        for rule in RULES.values()
        if isinstance(rule, CheckRule) and rule.outcome == "exception" and rule.scope == "receipt"
    }
    assert whole_receipt == exceptions


def fields_and_values(condition):
    """Every comparison in a condition, as plain data, with whether it sits under a `not`."""
    found, negated = [], False
    for node in walk(condition):
        if isinstance(node, Not):
            negated = True
        elif hasattr(node, "field"):
            found.append((node.op, node.field, negated))
    return found


def test_gift_03_is_only_for_employee_gifts():
    select = RULES["GIFT-03"].select
    assert ("eq", "item.recipient_type", False) in fields_and_values(select)
    assert ("in", "item.category", True) in fields_and_values(select)
    excluded = next(node for node in walk(select) if isinstance(node, In))
    assert set(excluded.values) == {"food_beverage", "alcohol", "service_fee", "gift_card"}


def test_alc_03_needs_a_merchant_code_before_calling_it_retail():
    conditions = fields_and_values(RULES["ALC-03"].applies_when)
    assert ("missing", "receipt.mcc", True) in conditions
    assert ("in", "receipt.mcc", True) in conditions


def test_process_rules_are_the_ones_that_raise_nothing():
    assert {rule.rule_id for rule in RULES.values() if isinstance(rule, ProcessRule)} == {
        "EXC-01",
        "EXC-02",
        "EXC-03",
        "EXC-04",
        "MCC-03",
        "REG-05",
    }


def test_scoped_logic_retains_verified_rules_without_changing_the_artifact():
    logic = {"ruleset_version": RULESET.ruleset_version, "rules": [],
             "retained_rules": json.loads(RULESET.to_json())["rules"]}
    assert build_ruleset(yaml.safe_dump(logic, sort_keys=False), list(CLAUSES.values())).to_json() == RULESET.to_json()


def test_scoped_logic_refuses_a_retained_rule_whose_source_changed():
    logic = {"ruleset_version": RULESET.ruleset_version, "rules": [],
             "retained_rules": json.loads(RULESET.to_json())["rules"]}
    logic["retained_rules"][0]["provenance"]["clause_hash"] = "0" * 64
    with pytest.raises(ValueError, match="retained rule no longer matches its source clause"):
        build_ruleset(yaml.safe_dump(logic), list(CLAUSES.values()))
