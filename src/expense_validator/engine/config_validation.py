"""Runtime configuration checks, independent of policy documents and compilation.

PolicyEngine supplies an immutable snapshot; these functions validate compatibility without
admitting receipts or retaining any customer state.
"""

from collections.abc import Mapping
from typing import get_args

from expense_validator.engine import evaluation
from expense_validator.engine.approvals import _reference_kinds
from expense_validator.engine.helpers import fields_read_by
from expense_validator.engine.inputs import SUBMITTED_FIELDS, allowed_values
from expense_validator.engine.models import LineItem, Receipt
from expense_validator.engine.money import monetary_contract
from expense_validator.engine.report import _whole_receipt_rules
from expense_validator.policy.schema import CarveOutRule, CheckRule, NumberParam, ProcessRule, RuleSet
from expense_validator.policy.validation import field_errors, helper_errors, replacement_errors
from expense_validator.policy.vocabulary import Unit


def _strings(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, tuple) or not value or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{name} must be a nonempty list of strings")
    return value


def validate_catalog(fields: Mapping[str, Mapping]) -> None:
    """Check the catalog shapes consumed by admission and helpers, without reading any receipts."""
    for name, field in fields.items():
        if not isinstance(field, Mapping):
            raise ValueError(f"{name} must be a field catalog mapping")
        if name == "input_contract":
            continue
        if field.get("type") not in ("text", "number", "bool"):
            raise ValueError(f"{name} needs type text, number or bool")
        if "values" in field:
            _strings(field["values"], f"{name}.values")
        if "unit" in field and (field["type"] != "number" or field["unit"] not in get_args(Unit)):
            raise ValueError(f"{name} has an incompatible unit")
    vocabularies = (
        *(f"receipt.{name}" for name in SUBMITTED_FIELDS),
        "item.category", "item.recipient_type", "item.venue_type", "item.item_kind",
    )
    for name in vocabularies:
        _strings(allowed_values(fields, name), f"{name}.values")
    contract = monetary_contract(fields)
    if not contract.minor_units or any(value < 0 for value in contract.minor_units.values()):
        raise ValueError("input_contract.minor_units must contain nonnegative currency precisions")
    entities = fields["receipt.entity"].get("countries")
    homes = fields.get("receipt.employee_country", {}).get("home_currency")
    if not isinstance(entities, Mapping) or not entities or not isinstance(homes, Mapping):
        raise ValueError("the catalog needs entity countries and employee home_currency mappings")
    seen = set()
    for entity, countries in entities.items():
        if entity not in fields["receipt.entity"]["values"]:
            raise ValueError(f"countries mapping names unknown entity {entity}")
        for country in _strings(countries, f"countries.{entity}"):
            if country in seen:
                raise ValueError(f"country {country} occurs more than once in the entity mapping")
            seen.add(country)
            currency = homes.get(country)
            if not isinstance(currency, str) or currency not in contract.minor_units:
                raise ValueError(f"country {country} needs a home_currency supported by input_contract")


def validate_rules(ruleset: RuleSet, fields: Mapping[str, Mapping]) -> None:
    """Check runtime contracts, not policy-source agreement or compiler heuristics."""
    evaluation.disposition_directive(ruleset)
    active = [rule for rule in ruleset.rules if rule.status == "active"]
    directives = {}
    for rule in active:
        if isinstance(rule, ProcessRule) and rule.directive != "guidance":
            if rule.directive in directives:
                raise ValueError(f"needs exactly one active {rule.directive} directive")
            directives[rule.directive] = rule
    for name in ("uncertainty", "evidence"):
        if name not in directives:
            raise ValueError(f"needs exactly one active {name} directive")
    threshold = directives["uncertainty"].params["min_confidence"]
    if not isinstance(threshold, NumberParam) or threshold.unit != "ratio" or not 0 <= threshold.value <= 1:
        raise ValueError("min_confidence must be a number in ratio units between 0 and 1")
    _reference_kinds(ruleset)
    whole_receipt = {
        rule.rule_id for rule in active
        if isinstance(rule, CheckRule) and rule.outcome == "exception" and rule.scope == "receipt"
    }
    if _whole_receipt_rules(ruleset) != whole_receipt:
        raise ValueError("evidence directive must list the receipt-level exception rules")
    by_id = {rule.rule_id: rule for rule in ruleset.rules}
    facts = (
        {f"receipt.{name}" for name in Receipt.model_fields if name != "line_items"}
        | {f"item.{name}" for name in LineItem.model_fields}
        | {"receipt.is_meal_receipt", "receipt.transaction_date", "receipt.local_hour", "item.unit_price_usd", "item.is_purchase"}
    )
    for rule in active:
        errors = field_errors(rule, fields) + helper_errors(rule)
        if not isinstance(rule, ProcessRule):
            errors += [f"unknown runtime fact {name}" for name in fields_read_by(rule) if name not in facts]
        if isinstance(rule, CheckRule):
            for reference in rule.replaces:
                target = by_id.get(reference.rule_id)
                if target is None:
                    errors.append(f"replacement target {reference.rule_id} does not exist")
                else:
                    errors.extend(replacement_errors(rule, reference, target))
        if isinstance(rule, CheckRule) and rule.scope == "receipt":
            errors += [f"a receipt rule cannot read {name}" for name in fields_read_by(rule) if name.startswith("item.")]
        if isinstance(rule, CarveOutRule) and (rule.helper != "baggage_allowance" or rule.requires_ref != "baggage_delay"):
            errors.append("the runtime supports only baggage_allowance carve-outs with a baggage_delay reference")
        if isinstance(rule, CarveOutRule):
            for reference in rule.modifies:
                target = by_id.get(reference)
                if target is None:
                    errors.append(f"carve-out target {reference} does not exist")
                elif not isinstance(target, CheckRule) or target.outcome != "exception":
                    errors.append(f"modifies {reference}, which raises no exception")
        if errors:
            raise ValueError(f"{rule.rule_id}: {'; '.join(errors)}")
