"""Validate that rules read real fields, use helpers correctly, do not conflict and match their clauses."""

import json
from dataclasses import dataclass
from decimal import Decimal
from itertools import combinations
from typing import Literal

import yaml

from expense_validator.compiler.extract import Clause
from expense_validator.policy.schema import (
    AllOf,
    CarveOutRule,
    CheckRule,
    Condition,
    NumberParam,
    ProcessRule,
    Replacement,
    Rule,
    RuleSet,
)
from expense_validator.policy.validation import field_errors, helper_errors, replacement_errors


@dataclass(frozen=True)
class Issue:
    level: Literal["error", "warning"]
    check: str
    rule_id: str
    message: str

    def __str__(self) -> str:
        return f"{self.level} {self.check}: {self.rule_id}: {self.message}"


def read_fields(text: str) -> dict[str, dict]:
    return yaml.safe_load(text)


def validate_ruleset(ruleset: RuleSet, clauses: list[Clause], fields: dict[str, dict]) -> list[Issue]:
    rules = [rule for rule in ruleset.rules if rule.status != "retired"]
    checks = [rule for rule in rules if isinstance(rule, CheckRule)]
    by_id = {rule.rule_id: rule for rule in ruleset.rules}
    return [
        *(issue for rule in rules for issue in check_fields(rule, fields)),
        *(issue for rule in rules for issue in _check_helper(rule)),
        *_check_conflicts([rule for rule in checks if rule.status == "active"]),
        *_check_targets(rules, by_id),
        *_check_references(rules),
        *_check_evidence(rules),
        *_check_faithful(rules, {clause.rule_id: clause for clause in clauses}),
    ]


def _error(check: str, rule: Rule, message: str) -> Issue:
    return Issue("error", check, rule.rule_id, message)


# --- Fields: every condition reads a real field, in a way that field allows ---


def check_fields(rule: Rule, fields: dict[str, dict]) -> list[Issue]:
    return [_error("fields", rule, message) for message in field_errors(rule, fields)]


def _check_helper(rule: Rule) -> list[Issue]:
    return [_error("helpers", rule, message) for message in helper_errors(rule)]


# --- Conflicts: two rules that catch the same thing and neither replaces the other ---


def _resolved(node, params) -> object:
    if isinstance(node, dict):
        if set(node) == {"param"}:
            param = params[node["param"]]
            return list(param.values) if hasattr(param, "values") else [str(param.value), param.unit]
        return {key: _resolved(value, params) for key, value in node.items()}
    if isinstance(node, (list, tuple)):
        return [_resolved(value, params) for value in node]
    return node


def _canonical(condition: Condition | None, rule: CheckRule) -> str | None:
    if condition is None:
        return None
    return json.dumps(_resolved(condition.model_dump(mode="json"), rule.params), sort_keys=True)


def _conjuncts(condition: Condition | None, rule: CheckRule) -> set[str]:
    if condition is None:
        return set()
    if isinstance(condition, AllOf):
        return {part for child in condition.conditions for part in _conjuncts(child, rule)}
    return {_canonical(condition, rule)}


def _exclude_each_other(first: CheckRule, second: CheckRule) -> bool:
    a, b = _conjuncts(first.applies_when, first), _conjuncts(second.applies_when, second)
    negate = lambda part: json.dumps({"condition": json.loads(part), "op": "not"}, sort_keys=True)
    return any(negate(part) in b for part in a) or any(negate(part) in a for part in b)


def _replaces(first: CheckRule, second: CheckRule) -> bool:
    return second.rule_id in {replacement.rule_id for replacement in first.replaces}


def _check_conflicts(rules: list[CheckRule]) -> list[Issue]:
    issues = []
    for first, second in combinations(rules, 2):
        if _replaces(first, second) or _replaces(second, first):
            continue
        same_select = _canonical(first.select, first) == _canonical(second.select, second)
        same_gate = _canonical(first.applies_when, first) == _canonical(second.applies_when, second)
        same_logic = same_select and same_gate and first.helper == second.helper and first.scope == second.scope
        pair = f"{first.rule_id} and {second.rule_id}"
        if same_logic:
            if (first.outcome, first.severity) != (second.outcome, second.severity):
                issues.append(_error("conflicts", first, f"{pair} catch the same thing with different results"))
            else:
                issues.append(_error("conflicts", first, f"{pair} are duplicates"))
        elif same_select and first.select is not None and not _exclude_each_other(first, second):
            message = f"{pair} select the same lines and neither replaces the other"
            issues.append(Issue("warning", "conflicts", first.rule_id, message))
    return issues


def _check_targets(rules: list[Rule], by_id: dict[str, Rule]) -> list[Issue]:
    issues = []
    for rule in rules:
        if isinstance(rule, CheckRule):
            for replacement in rule.replaces:
                issues.extend(_check_replacement(rule, replacement, by_id.get(replacement.rule_id)))
        if isinstance(rule, CarveOutRule):
            for target in rule.modifies:
                other = by_id.get(target)
                if not isinstance(other, CheckRule) or other.outcome != "exception":
                    issues.append(_error("conflicts", rule, f"modifies {target}, which raises no exception"))
    return issues


def _check_replacement(rule: CheckRule, replacement: Replacement, target: Rule | None) -> list[Issue]:
    return [_error("conflicts", rule, message) for message in replacement_errors(rule, replacement, target)]


# --- References and evidence: the process rules agree with the rules they describe ---


def _check_references(rules: list[Rule]) -> list[Issue]:
    directive = next((r for r in rules if isinstance(r, ProcessRule) and r.directive == "reference_kinds"), None)
    if directive is None:
        return []
    actual = {}
    for rule in rules:
        kind = getattr(rule, "waived_by_ref", None) or getattr(rule, "requires_ref", None)
        if kind:
            actual.setdefault(f"{kind}_rules", set()).add(rule.rule_id)
    issues = []
    for name, param in sorted(directive.params.items()):
        if set(param.values) != actual.get(name, set()):
            found = sorted(actual.get(name, set()))
            issues.append(_error("references", directive, f"{name} lists {sorted(param.values)}, rules say {found}"))
    return issues


def _check_evidence(rules: list[Rule]) -> list[Issue]:
    directive = next((r for r in rules if isinstance(r, ProcessRule) and r.directive == "evidence"), None)
    if directive is None:
        return []
    listed = set(directive.params["whole_receipt_rules"].values)
    actual = {
        rule.rule_id
        for rule in rules
        if isinstance(rule, CheckRule) and rule.outcome == "exception" and rule.scope == "receipt"
    }
    if listed == actual:
        return []
    return [_error("evidence", directive, f"lists {sorted(listed)}, whole-receipt exceptions are {sorted(actual)}")]


# --- Faithful: each rule says what its clause says ---


def _source_ids(rule: Rule) -> tuple[str, ...]:
    return rule.modifies if isinstance(rule, CarveOutRule) else (rule.rule_id,)


def _policy_numbers(rule: Rule) -> list[tuple[str, NumberParam]]:
    return [
        (name, param)
        for name, param in rule.params.items()
        if isinstance(param, NumberParam) and param.source == "policy"
    ]


def faithful_issues(ruleset: RuleSet, clauses: dict[str, Clause]) -> list[Issue]:
    """Return how the rules differ from these clauses' limits, severities and review signals: the faithful check."""
    return _check_faithful([rule for rule in ruleset.rules if rule.status != "retired"], clauses)


def held_thresholds(ruleset: RuleSet, rule_id: str) -> set[tuple[Decimal, str]]:
    """Return the policy-sourced limits that the rules implementing this clause hold. Project parameters are not policy."""
    rules = [rule for rule in ruleset.rules if rule.status != "retired" and rule_id in _source_ids(rule)]
    return {(param.value, param.unit) for rule in rules for _, param in _policy_numbers(rule)}


def _check_faithful(rules: list[Rule], clauses: dict[str, Clause]) -> list[Issue]:
    issues = []
    for rule in rules:
        sources = [clauses[source] for source in _source_ids(rule) if source in clauses]
        if not sources:
            issues.append(_error("faithful", rule, "no clause to compare with"))
            continue
        stated = {(t.value, t.unit) for clause in sources for t in clause.thresholds}
        for name, param in _policy_numbers(rule):
            if (param.value, param.unit) not in stated:
                issues.append(_error("faithful", rule, f"{name} is {param.value} {param.unit}, not in its clause"))
        if isinstance(rule, CheckRule):
            issues.extend(_check_outcome(rule, sources[0]))
    for rule_id, clause in clauses.items():
        holders = [rule for rule in rules if rule_id in _source_ids(rule)]
        held = {(param.value, param.unit) for rule in holders for _, param in _policy_numbers(rule)}
        for threshold in clause.thresholds if holders else ():
            if (threshold.value, threshold.unit) not in held:
                message = f"clause states {threshold.value} {threshold.unit}, held by no rule"
                issues.append(Issue("error", "faithful", rule_id, message))
    return issues


def _check_outcome(rule: CheckRule, clause: Clause) -> list[Issue]:
    if rule.outcome == "review_signal":
        return [] if clause.review_signal else [_error("faithful", rule, "review signal, but its clause states none")]
    severities = {rule.severity} | {case.severity for case in rule.severity_cases}
    if severities != set(clause.severities):
        message = f"severity {sorted(severities)}, its clause states {sorted(clause.severities)}"
        return [_error("faithful", rule, message)]
    return []
