"""Turn an admitted receipt into the flat facts rules read. None means missing; False and 0 are values."""

from collections.abc import Mapping

from expense_validator.engine.inputs import allowed_values
from expense_validator.engine.models import LineItem, Receipt

# The definition of a meal receipt in MEAL-01.
MEAL_TYPES = frozenset({"Meals - Individual", "Meals - Team", "Client Entertainment"})
FOOD_OR_DRINK = frozenset({"food_beverage", "alcohol"})


def receipt_facts(receipt: Receipt, fields: Mapping[str, dict]) -> dict[str, object]:
    when = receipt.transaction_datetime
    return {
        **_prefixed("receipt", receipt.model_dump(exclude={"line_items"})),
        "receipt.is_meal_receipt": _is_meal_receipt(receipt, fields),
        "receipt.transaction_date": None if when is None else when.date().isoformat(),
        "receipt.local_hour": None if when is None else when.hour,
    }


def item_facts(receipt: Receipt, item: LineItem, fields: Mapping[str, dict]) -> dict[str, object]:
    """Return one line's facts together with its receipt's."""
    return {
        **receipt_facts(receipt, fields),
        **_prefixed("item", item.model_dump()),
        "item.unit_price_usd": None if item.amount_usd is None else item.amount_usd / item.quantity,
        "item.is_purchase": item.amount > 0,
    }


def _prefixed(prefix: str, values: Mapping[str, object]) -> dict[str, object]:
    return {f"{prefix}.{name}": value for name, value in values.items()}


def _is_meal_receipt(receipt: Receipt, fields: Mapping[str, dict]) -> bool | None:
    """None when the answer depends on a missing or unknown category."""
    if receipt.submitted_expense_type not in MEAL_TYPES:
        return False
    if not receipt.itemized:
        return True
    categories = {item.category for item in receipt.line_items}
    if categories & FOOD_OR_DRINK:
        return True
    return False if categories <= set(allowed_values(fields, "item.category")) else None
