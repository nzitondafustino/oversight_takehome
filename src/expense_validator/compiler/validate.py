"""The Validate step: checks that rules make sense, which the schema alone cannot tell.

The schema says a rule is well formed. This says it reads real fields, uses its helper
properly, does not conflict with another rule, and is faithful to its clause.

An error stops the build. A warning is for a developer to look at. Retired rules are
not checked, because they no longer do anything.
"""

import json
from dataclasses import dataclass
from itertools import combinations
from typing import Literal

import yaml

from expense_validator.compiler.extract import Clause
from expense_validator.policy.schema import (
    AllOf,
    CarveOutRule,
    CheckRule,
    Compare,
    Condition,
    In,
    Missing,
    NumberParam,
    ParamRef,
    ProcessRule,
    Replacement,
    Rule,
    RuleSet,
    walk,
)
from expense_validator.policy.vocabulary import HELPER_CONTRACTS


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
    if isinstance(rule, ProcessRule):
        return []
    issues = []
    for condition in rule.conditions():
        for node in walk(condition):
            if isinstance(node, (Compare, In, Missing)):
                issues.extend(_check_node(rule, node, fields))
    return issues


def _check_node(rule: Rule, node: Compare | In | Missing, fields: dict[str, dict]) -> list[Issue]:
    field = fields.get(node.field)
    if field is None:
        return [_error("fields", rule, f"unknown field {node.field}")]
    if isinstance(node, Missing):
        return []
    if isinstance(node, In):
        if field["type"] != "text":
            return [_error("fields", rule, f"in needs a text field, {node.field} is {field['type']}")]
        values = rule.params[node.values.param].values if isinstance(node.values, ParamRef) else node.values
        return _check_values(rule, node.field, field, values)
    if isinstance(node.value, ParamRef):
        return _check_number(rule, node, field)
    if node.op in {"gt", "gte", "lt", "lte"} or isinstance(node.value, bool) != (field["type"] == "bool"):
        return [_error("fields", rule, f"{node.field} is {field['type']}, cannot compare with {node.value!r}")]
    if field["type"] == "number":
        return [_error("fields", rule, f"{node.field} is a number and must compare against a param")]
    return _check_values(rule, node.field, field, [node.value])


def _check_number(rule: Rule, node: Compare, field: dict) -> list[Issue]:
    if field["type"] != "number":
        return [_error("fields", rule, f"{node.field} is {field['type']}, cannot compare with a number")]
    param = rule.params[node.value.param]
    if "unit" in field and param.unit != field["unit"]:
        return [_error("fields", rule, f"{node.value.param} is in {param.unit}, {node.field} is in {field['unit']}")]
    return []


def _check_values(rule: Rule, name: str, field: dict, values) -> list[Issue]:
    allowed = field.get("values")
    if allowed is None or isinstance(values, bool):
        return []
    return [
        _error("fields", rule, f"{value!r} is not a known value for {name}")
        for value in values
        if not isinstance(value, bool) and value not in allowed
    ]


# --- Helpers: each helper runs on the right kind of rule, with the params it needs ---


def _check_helper(rule: Rule) -> list[Issue]:
    helper = getattr(rule, "helper", None)
    if helper is None:
        return []
    contract = HELPER_CONTRACTS[helper]
    issues = []
    if rule.kind != contract["kind"]:
        issues.append(_error("helpers", rule, f"{helper} belongs on a {contract['kind']} rule, not a {rule.kind}"))
    if getattr(rule, "scope", None) != contract.get("scope"):
        issues.append(_error("helpers", rule, f"{helper} needs scope {contract.get('scope')}"))
    params = {name: param.unit for name, param in rule.params.items() if isinstance(param, NumberParam)}
    if len(params) != len(rule.params) or params not in contract["params"]:
        expected = " or ".join(str(option) for option in contract["params"])
        issues.append(_error("helpers", rule, f"{helper} needs params {expected}, got {params}"))
    return issues


# --- Conflicts: two rules that catch the same thing and neither replaces the other ---


def _resolved(node, params) -> object:
    """A condition as plain data, with each param swapped for its value, so names do not matter."""
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
    """True when one rule's gate is the plain opposite of a part of the other's."""
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
    name = replacement.rule_id
    if not isinstance(target, CheckRule):
        return [_error("conflicts", rule, f"replaces {name}, which is not a check")]
    issues = []
    if target.outcome != rule.outcome:
        issues.append(_error("conflicts", rule, f"replaces {name}, which has a different outcome"))
    # Removing lines only means something between two rules about lines.
    if replacement.level == "line" and (rule.scope, target.scope) != ("item", "item"):
        issues.append(_error("conflicts", rule, f"replaces {name} at line level, but both must be item rules"))
    # Only a finding can be swapped across scopes, as a split replaces the breach it was split around.
    if replacement.level == "receipt" and rule.scope != target.scope:
        issues.append(_error("conflicts", rule, f"replaces {name} at receipt level, but their scopes differ"))
    return issues


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
