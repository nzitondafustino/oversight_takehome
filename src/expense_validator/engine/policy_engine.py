"""A reusable, immutable ruleset and catalog snapshot that evaluates each run in a fresh context."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

from expense_validator.engine import evaluation
from expense_validator.engine.config_validation import validate_catalog, validate_rules
from expense_validator.engine.models import Receipt
from expense_validator.policy.schema import RuleSet


def _freeze(value: object) -> object:
    """Snapshot catalog containers, refusing unsupported value types."""
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise ValueError("field catalog mapping keys must be strings")
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if value is None or type(value) in (str, bool, int, float, Decimal):
        return value
    raise ValueError(f"unsupported field catalog value type: {type(value).__name__}")


@dataclass(frozen=True, slots=True, eq=False)
class PolicyEngine:
    """An immutable ruleset and catalog snapshot, safe to reuse across calls."""

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
        validate_catalog(self.fields)
        validate_rules(self.ruleset, self.fields)

    def evaluate(
        self,
        receipts: Iterable[Receipt | Mapping[str, object]],
        *,
        complete: bool = True,
    ) -> evaluation.EvaluationRun:
        """Evaluate receipts together in a fresh context."""
        return evaluation.evaluate_receipts(receipts, self.ruleset, self.fields, complete=complete)
