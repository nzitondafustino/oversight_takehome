"""PERS.BAGGAGE: where lines stand against the baggage-delay allowance.

The carve-out step calls this directly. It is not dispatched by the registry, as it runs on a
carve-out rule, and it excuses nothing by itself.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from expense_validator.engine.helpers.common import lowest_total
from expense_validator.engine.helpers.context import ReceiptContext, require_in_context
from expense_validator.engine.inputs import allowed_values
from expense_validator.engine.models import LineItem, Receipt
from expense_validator.policy.schema import CarveOutRule


@dataclass(frozen=True)
class BaggageAllowance:
    """Where lines stand against the baggage-delay allowance. This excuses nothing by itself.

    within is None when it is not known. Whether the delay passed the rule's hours is left to the
    carve-out step: the data does not hold it.
    """

    line_ids: tuple[str, ...]
    total_usd: Decimal | None
    within: bool | None


def counts_toward_baggage_allowance(line: LineItem) -> bool | None:
    """Clothing does, and of personal care only essential toiletries. None if that is not known."""
    if line.category == "clothing":
        return True
    return line.essential_toiletry if line.category == "personal_care" else False


def baggage_allowance(
    rule: CarveOutRule, receipt: Receipt, context: ReceiptContext, fields: Mapping[str, dict]
) -> BaggageAllowance:
    """One allowance per delay, shared across the employee's receipts that record the same reference.

    A carve-out needs evidence, so with no reference on file the lines are not within it.
    """
    require_in_context(receipt, context)
    mine = tuple(line.line_id for line in receipt.line_items if counts_toward_baggage_allowance(line) is not False)
    reference = receipt.baggage_delay_ref
    if reference is None:
        return BaggageAllowance(mine, None, False)
    sharing = [
        other
        for other in context.receipts
        if other.employee_id == receipt.employee_id and other.baggage_delay_ref == reference
    ]
    lines = [line for other in sharing for line in other.line_items]
    amounts = [line.amount_usd for line in lines if counts_toward_baggage_allowance(line)]
    known = [amount for amount in amounts if amount is not None]
    total = sum(known, Decimal(0))
    catalog = allowed_values(fields, "item.category")
    possible = [
        line
        for line in lines
        if line.category not in catalog
        or counts_toward_baggage_allowance(line) is None
        or (counts_toward_baggage_allowance(line) and line.amount_usd is None)
    ]
    lowest = lowest_total(total, possible)
    if context.complete and lowest is not None and lowest > rule.params["allowance_usd"].value:
        return BaggageAllowance(mine, lowest, False)
    unclear = (
        len(known) < len(amounts)
        or any(line.category not in catalog for line in lines)
        or any(counts_toward_baggage_allowance(line) is None for line in lines)
    )
    return BaggageAllowance(mine, total, None if unclear or not context.complete else True)
