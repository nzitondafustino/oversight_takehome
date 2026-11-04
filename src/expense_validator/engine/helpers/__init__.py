"""The named helpers: policy logic that conditions cannot express.

Each helper reads its clause's thresholds from its rule's params and gives one of three results:
candidate findings, nothing, or undetermined when something it needs is not known. Replacements,
waivers and carve-outs come later, so a helper reports what it catches regardless: MEAL-01 still fires
on a receipt in a TIME-03 split.

Import from this package, not from its modules, outside it. The tables in the domain modules come from
the policy text, as the rule schema has no place for them.

    common.py         what helpers produce, and the lowest a total could be
    context.py        ReceiptContext, and HelperJob: one rule on one receipt
    meals.py          MEAL-01, REG-02, REG-03, ALC-02
    gifts.py          GIFT-01, GIFT-04
    healthcare.py     HEALTH-01
    travel.py         PERS.BAGGAGE, for the carve-out step
    documentation.py  DOC-03, REG-04
    splits.py         TIME-03, which reads meals and gifts
    registry.py       the rule's helper name to its function
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
    """HelperJob under its earlier name, with the domain questions it used to answer itself.

    Kept for the callers that build one to read a bound directly. New code uses HelperJob and MealPolicy.
    """

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
