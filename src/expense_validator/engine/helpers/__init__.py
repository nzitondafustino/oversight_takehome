"""Named helpers for policy logic conditions cannot express.

Each helper returns candidate findings, nothing, or undetermined. Import from this package, not its modules.
"""

from dataclasses import dataclass
from decimal import Decimal

from expense_validator.engine.helpers.common import (
    HelperOutcome,
    Undetermined,
    fields_read_by,
    lowest_total,
    make_finding,
)
from expense_validator.engine.helpers.context import HelperJob, ReceiptContext, build_context, require_in_context
from expense_validator.engine.helpers.documentation import TYPE_COVERS, TYPE_IGNORES
from expense_validator.engine.helpers.gifts import GIFT_CATEGORIES, is_client_gift, is_official_line, may_need_recipient
from expense_validator.engine.helpers.meals import MEAL_SPEND, PRECISION, MealPolicy
from expense_validator.engine.helpers.registry import HELPERS, run_helper
from expense_validator.engine.helpers.splits import GIFT_RULE, ITEMIZATION_RULE
from expense_validator.engine.helpers.travel import BaggageAllowance, baggage_allowance, counts_toward_baggage_allowance
from expense_validator.engine.models import LineItem, Receipt
from expense_validator.policy.schema import CheckRule

__all__ = [
    "GIFT_CATEGORIES",
    "GIFT_RULE",
    "HELPERS",
    "ITEMIZATION_RULE",
    "MEAL_SPEND",
    "PRECISION",
    "TYPE_COVERS",
    "TYPE_IGNORES",
    "BaggageAllowance",
    "HelperJob",
    "HelperOutcome",
    "MealPolicy",
    "ReceiptContext",
    "Undetermined",
    "baggage_allowance",
    "build_context",
    "counts_toward_baggage_allowance",
    "fields_read_by",
    "is_client_gift",
    "is_official_line",
    "lowest_total",
    "make_finding",
    "may_need_recipient",
    "require_in_context",
    "run_helper",
]


@dataclass(frozen=True)
class _Job(HelperJob):
    """Deprecated alias of HelperJob with its former domain methods."""

    def is_meal(self, receipt: Receipt) -> bool | None:
        return MealPolicy(self).is_meal(receipt)

    def per_person(self, receipt: Receipt) -> Decimal | None:
        return MealPolicy(self).per_person(receipt)

    def spend_bounds(self, receipt: Receipt) -> tuple[Decimal | None, Decimal | None]:
        return MealPolicy(self).spend_bounds(receipt)

    def meal_cap(self, rule: CheckRule, meals: list[Receipt]) -> Decimal | None:
        return MealPolicy(self).meal_cap(rule, meals)

    def is_gift(self, line: LineItem) -> bool | None:
        return is_client_gift(line, self.receipt, self.fields)
