"""PERS.BAGGAGE allowance, called directly by the carve-out step."""

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
    """Where lines stand against the baggage-delay allowance. Excuses nothing by itself.

    Attributes:
        within: Whether the lines are within the allowance, or None if not known.
    """

    line_ids: tuple[str, ...]
    total_usd: Decimal | None
    within: bool | None


def counts_toward_baggage_allowance(line: LineItem) -> bool | None:
    """Return whether a line counts: clothing, and essential toiletries only."""
    if line.category == "clothing":
        return True
    return line.essential_toiletry if line.category == "personal_care" else False


def baggage_allowance(
    rule: CarveOutRule, receipt: Receipt, context: ReceiptContext, fields: Mapping[str, dict]
) -> BaggageAllowance:
    """Return one allowance per delay reference, shared across the employee's receipts."""
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
