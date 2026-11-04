"""Reusable policy configuration; each evaluation uses the existing functional pipeline.

Only configuration belongs to an engine. Customer contexts, receipts and results stay local
to evaluate_receipts, so reusing an engine cannot carry them into a later evaluation.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from expense_validator.engine import evaluation
from expense_validator.engine.models import Receipt
from expense_validator.policy.schema import RuleSet


def _freeze(value: object) -> object:
    """Snapshot the catalog's mappings and sequences without retaining mutable input containers."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True, slots=True, eq=False)
class PolicyEngine:
    """An immutable ruleset/catalog snapshot, safe to reuse for separate customer runs.

    Construct a new engine to use new policy configuration. As with evaluate_receipts,
    customer_id scopes a run; the caller must establish ownership of its receipts.
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

    def evaluate(
        self,
        customer_id: str,
        receipts: Iterable[Receipt | Mapping[str, object]],
        *,
        complete: bool,
    ) -> evaluation.EvaluationRun:
        """Evaluate one customer's receipts in a fresh context, without retaining the run."""
        return evaluation.evaluate_receipts(customer_id, receipts, self.ruleset, self.fields, complete=complete)
