"""Reusable policy configuration; each evaluation uses the existing functional pipeline.

Only configuration belongs to an engine. Customer contexts, receipts and results stay local
to evaluate_receipts, so reusing an engine cannot carry them into a later evaluation.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
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


def _freeze(value: object) -> object:
    """Snapshot catalog containers; reject values outside its scalar/list/mapping format."""
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise ValueError("field catalog mapping keys must be strings")
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if value is None or type(value) in (str, bool, int, float, Decimal):
        return value
    raise ValueError(f"unsupported field catalog value type: {type(value).__name__}")


def _strings(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, tuple) or not value or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{name} must be a nonempty list of strings")
    return value


def _validate_catalog(fields: Mapping[str, Mapping]) -> None:
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


def _validate_rules(ruleset: RuleSet, fields: Mapping[str, Mapping]) -> None:
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
            errors += [message for target in rule.replaces for message in replacement_errors(rule, target, by_id[target.rule_id])]
        if isinstance(rule, CheckRule) and rule.scope == "receipt":
            errors += [f"a receipt rule cannot read {name}" for name in fields_read_by(rule) if name.startswith("item.")]
        if isinstance(rule, CarveOutRule) and (rule.helper != "baggage_allowance" or rule.requires_ref != "baggage_delay"):
            errors.append("the runtime supports only baggage_allowance carve-outs with a baggage_delay reference")
        if isinstance(rule, CarveOutRule):
            errors += [
                f"modifies {target}, which raises no exception" for target in rule.modifies
                if not isinstance(by_id[target], CheckRule) or by_id[target].outcome != "exception"
            ]
        if errors:
            raise ValueError(f"{rule.rule_id}: {'; '.join(errors)}")


@dataclass(frozen=True, slots=True, eq=False)
class PolicyEngine:
    """An immutable ruleset/catalog snapshot, safe to reuse for separate customer runs.

    Construct a new engine to use new policy configuration. As with evaluate_receipts,
    customer_id scopes a run; the caller must establish ownership of its receipts.

    Construction validates runtime compatibility from these inputs alone. Source-policy
    agreement remains the compiler's job. The catalog accepts mappings, sequences and
    immutable scalars; unsupported objects are rejected rather than retained by reference.
    """

    ruleset: RuleSet
    fields: Mapping[str, Mapping[str, object]]

    def __post_init__(self) -> None:
        if not isinstance(self.ruleset, RuleSet):
            raise TypeError("ruleset must be a validated RuleSet")
        if not isinstance(self.fields, Mapping):
            raise TypeError("fields must be a field catalog mapping")
        # Rebuild from data: even a RuleSet can have been copied around validation.
        object.__setattr__(self, "ruleset", RuleSet.model_validate(self.ruleset.model_dump()))
        object.__setattr__(self, "fields", _freeze(self.fields))
        _validate_catalog(self.fields)
        _validate_rules(self.ruleset, self.fields)

    def evaluate(
        self,
        customer_id: str,
        receipts: Iterable[Receipt | Mapping[str, object]],
        *,
        complete: bool,
    ) -> evaluation.EvaluationRun:
        """Evaluate one customer's receipts in a fresh context, without retaining the run."""
        return evaluation.evaluate_receipts(customer_id, receipts, self.ruleset, self.fields, complete=complete)
