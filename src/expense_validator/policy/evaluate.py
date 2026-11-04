"""What a condition means: the semantics the schema's docstring describes, as code.

Facts are a flat mapping such as {"item.category": "alcohol", "receipt.mcc": "5812"}.
A field that is absent or None is missing, and a comparison against a missing value is
false. False and 0 are values, not missing.

This is not the engine: it decides one condition, one rule or one receipt's replacements.
Rules with a helper have logic this cannot run, and it does not read receipts.
"""

from collections.abc import Collection, Mapping
from decimal import Decimal

from expense_validator.policy.schema import (
    AllOf,
    AnyOf,
    CheckRule,
    Compare,
    Condition,
    In,
    Missing,
    Not,
    Param,
    ParamRef,
    RuleSet,
)

ORDERED = {"gt": Decimal.__gt__, "gte": Decimal.__ge__, "lt": Decimal.__lt__, "lte": Decimal.__le__}


def evaluate(condition: Condition, facts: Mapping[str, object], params: Mapping[str, Param]) -> bool:
    if isinstance(condition, AllOf):
        return all(evaluate(child, facts, params) for child in condition.conditions)
    if isinstance(condition, AnyOf):
        return any(evaluate(child, facts, params) for child in condition.conditions)
    if isinstance(condition, Not):
        return not evaluate(condition.condition, facts, params)
    actual = facts.get(condition.field)
    if isinstance(condition, Missing):
        return actual is None
    if actual is None:
        return False
    if isinstance(condition, In):
        return actual in _allowed(condition, params)
    return _compare(condition, actual, params)


def evaluate_with_unknowns(
    condition: Condition, facts: Mapping[str, object], params: Mapping[str, Param], unknowable: Collection[str]
) -> bool | None:
    """As evaluate, but None if the answer turns on comparing a field in unknowable that has no value.

    Any false branch of an all, or true branch of an any, settles it whatever the unknown value is.
    """
    if isinstance(condition, (AllOf, AnyOf)):
        results = [evaluate_with_unknowns(child, facts, params, unknowable) for child in condition.conditions]
        settling = isinstance(condition, AnyOf)
        return settling if settling in results else None if None in results else not settling
    if isinstance(condition, Not):
        result = evaluate_with_unknowns(condition.condition, facts, params, unknowable)
        return None if result is None else not result
    if isinstance(condition, Compare) and condition.field in unknowable and facts.get(condition.field) is None:
        return None
    return evaluate(condition, facts, params)


def _allowed(condition: In, params: Mapping[str, Param]) -> tuple[str, ...]:
    return params[condition.values.param].values if isinstance(condition.values, ParamRef) else condition.values


def _compare(condition: Compare, actual: object, params: Mapping[str, Param]) -> bool:
    if isinstance(condition.value, ParamRef):
        threshold = params[condition.value.param].value
        actual = Decimal(str(actual))
        if condition.op in ORDERED:
            return ORDERED[condition.op](actual, threshold)
        expected = threshold
    else:
        expected = condition.value
    # In Python 0 == False and 1 == True, but False and 0 are different values here.
    same = isinstance(actual, bool) == isinstance(expected, bool) and actual == expected
    return same if condition.op == "eq" else not same


def matches(rule: CheckRule, facts: Mapping[str, object]) -> bool:
    """Whether a rule's conditions catch these facts, before any reference or replacement."""
    if rule.helper is not None:
        raise ValueError(f"{rule.rule_id}: uses the helper {rule.helper}, which conditions cannot run")
    gate = rule.applies_when is None or evaluate(rule.applies_when, facts, rule.params)
    return gate and (rule.select is None or evaluate(rule.select, facts, rule.params))


def replaced_on_receipt(ruleset: RuleSet, facts: Mapping[str, object]) -> set[str]:
    """The rules out of play for this receipt, because a rule replacing them applies to it."""
    replaced = set()
    for rule in ruleset.rules:
        if not isinstance(rule, CheckRule) or rule.status != "active":
            continue
        if rule.applies_when is None or evaluate(rule.applies_when, facts, rule.params):
            replaced |= {target.rule_id for target in rule.replaces if target.level == "receipt"}
    return replaced
