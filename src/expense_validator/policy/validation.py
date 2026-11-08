"""Rule compatibility checks shared by the runtime and the compiler."""

from collections.abc import Mapping

from expense_validator.policy.schema import CheckRule, Compare, In, Missing, NumberParam, ParamRef, ProcessRule, Replacement, Rule, walk
from expense_validator.policy.vocabulary import HELPER_CONTRACTS


def field_errors(rule: Rule, fields: Mapping[str, Mapping]) -> list[str]:
    if isinstance(rule, ProcessRule):
        return []
    return [
        message
        for condition in rule.conditions()
        for node in walk(condition)
        if isinstance(node, (Compare, In, Missing))
        for message in _node_errors(rule, node, fields)
    ]


def _node_errors(rule: Rule, node: Compare | In | Missing, fields: Mapping[str, Mapping]) -> list[str]:
    field = fields.get(node.field)
    if field is None:
        return [f"unknown field {node.field}"]
    if isinstance(node, Missing):
        return []
    if isinstance(node, In):
        if field["type"] != "text":
            return [f"in needs a text field, {node.field} is {field['type']}"]
        values = rule.params[node.values.param].values if isinstance(node.values, ParamRef) else node.values
        return _value_errors(node.field, field, values)
    if isinstance(node.value, ParamRef):
        if field["type"] != "number":
            return [f"{node.field} is {field['type']}, cannot compare with a number"]
        param = rule.params[node.value.param]
        if "unit" in field and param.unit != field["unit"]:
            return [f"{node.value.param} is in {param.unit}, {node.field} is in {field['unit']}"]
        return []
    if node.op in {"gt", "gte", "lt", "lte"} or isinstance(node.value, bool) != (field["type"] == "bool"):
        return [f"{node.field} is {field['type']}, cannot compare with {node.value!r}"]
    if field["type"] == "number":
        return [f"{node.field} is a number and must compare against a param"]
    return _value_errors(node.field, field, [node.value])


def _value_errors(name: str, field: Mapping, values) -> list[str]:
    allowed = field.get("values")
    if allowed is None or isinstance(values, bool):
        return []
    return [f"{value!r} is not a known value for {name}" for value in values if not isinstance(value, bool) and value not in allowed]


def helper_errors(rule: Rule) -> list[str]:
    helper = getattr(rule, "helper", None)
    if helper is None:
        return []
    contract = HELPER_CONTRACTS[helper]
    errors = []
    if rule.kind != contract["kind"]:
        errors.append(f"{helper} belongs on a {contract['kind']} rule, not a {rule.kind}")
    if getattr(rule, "scope", None) != contract.get("scope"):
        errors.append(f"{helper} needs scope {contract.get('scope')}")
    params = {name: param.unit for name, param in rule.params.items() if isinstance(param, NumberParam)}
    if len(params) != len(rule.params) or params not in contract["params"]:
        expected = " or ".join(str(option) for option in contract["params"])
        errors.append(f"{helper} needs params {expected}, got {params}")
    return errors


def replacement_errors(rule: CheckRule, replacement: Replacement, target: Rule | None) -> list[str]:
    name = replacement.rule_id
    if not isinstance(target, CheckRule):
        return [f"replaces {name}, which is not a check"]
    errors = []
    if target.outcome != rule.outcome:
        errors.append(f"replaces {name}, which has a different outcome")
    # Removing lines only means something between two rules about lines.
    if replacement.level == "line" and (rule.scope, target.scope) != ("item", "item"):
        errors.append(f"replaces {name} at line level, but both must be item rules")
    if replacement.level == "receipt" and rule.scope != target.scope:
        errors.append(f"replaces {name} at receipt level, but their scopes differ")
    return errors
