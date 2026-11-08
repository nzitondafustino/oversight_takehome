"""Admit receipts against rules/fields.yaml before facts are built.

Submitted values outside the catalog are refused, as rules would otherwise skip them silently. Classified
values are kept as written, and classification_state reports what they amount to.
"""

from collections.abc import Mapping
from decimal import Decimal
from typing import Literal

from expense_validator.engine.models import LineItem, Receipt
from expense_validator.engine.money import reconcile

SUBMITTED_FIELDS = ("entity", "submitted_expense_type")

ClassifiedField = Literal["category", "recipient_type", "venue_type", "item_kind"]
# valid: in the catalog and, for a category, at or above the threshold. missing: no value.
# unknown: a value outside the catalog. For a category in the catalog only, no_confidence: no
# confidence to judge it by. low_confidence: below the threshold.
ClassificationState = Literal["valid", "missing", "unknown", "no_confidence", "low_confidence"]


def admit_receipt(data: Receipt | Mapping[str, object], fields: Mapping[str, dict]) -> Receipt:
    """Admit a receipt whose submitted values the catalog allows.

    Raises:
        ValueError: If a value is outside the catalog or would silence the rules.
    """
    if isinstance(data, Receipt):
        data = data.model_dump(warnings=False)
    allowed = {name: allowed_values(fields, f"receipt.{name}") for name in SUBMITTED_FIELDS}
    entities = _entity_by_country(fields)
    country = data.get("employee_country")
    if isinstance(country, str):
        if country not in entities:
            receipt_id = data.get("receipt_id")
            raise ValueError(f"{receipt_id}: unsupported employee_country {country!r}, allowed: {sorted(entities)}")
        if data.get("entity") is None:
            data = {**data, "entity": entities[country]}
    receipt = Receipt.model_validate(data)
    for name in SUBMITTED_FIELDS:
        value = getattr(receipt, name)
        if value not in allowed[name]:
            raise ValueError(f"{receipt.receipt_id}: unknown {name} {value!r}, allowed: {allowed[name]}")
    expected = entities.get(receipt.employee_country)
    if receipt.entity != expected:
        raise ValueError(
            f"{receipt.receipt_id}: entity {receipt.entity!r} contradicts "
            f"employee_country {receipt.employee_country!r}, which is in {expected!r}"
        )
    _refuse_what_keeps_rules_quiet(receipt)
    reconciliation = reconcile(receipt, fields)
    if reconciliation.unsupported:
        raise ValueError(f"{receipt.receipt_id}: unsupported amounts: {'; '.join(reconciliation.unsupported)}")
    if reconciliation.problems:
        raise ValueError(f"{receipt.receipt_id}: contradictory amounts: {'; '.join(reconciliation.problems)}")
    return receipt


def _refuse_what_keeps_rules_quiet(receipt: Receipt) -> None:
    for name in ("total_amount", "total_amount_usd"):
        total = getattr(receipt, name)
        if total is not None and total < 0:
            # Unsupported input, not a policy violation: the policy does not define refunds or
            # credits, and a negative total would sit below every threshold. Negative line
            # amounts are allowed, as they may be discounts or adjustments.
            raise ValueError(f"{receipt.receipt_id}: {name} {total} is below zero, which is not supported input")
    if receipt.mcc is None:
        # No rule reports a missing code, and the merchant and alcohol rules all need one.
        raise ValueError(f"{receipt.receipt_id}: has no mcc, so the merchant and alcohol rules cannot apply")
    if receipt.itemized and not receipt.line_items:
        raise ValueError(f"{receipt.receipt_id}: is itemized but lists no line items")
    if receipt.attendee_count == 0:
        # The count includes the employee, so this is not a recorded count of anyone.
        raise ValueError(f"{receipt.receipt_id}: attendee_count 0 cannot be right, as it includes the employee")
    if receipt.currency == "USD" or (receipt.currency is not None and receipt.fx_rate_to_usd is not None):
        # Otherwise DOC-04 reports the missing conversion, as a rate with no currency converts nothing.
        if receipt.total_amount is not None and receipt.total_amount_usd is None:
            # DOC-02 reads the total as printed, so nothing would report this, and the USD thresholds
            # that read this total would stay quiet.
            raise ValueError(f"{receipt.receipt_id}: no total_amount_usd, though it can be converted")
        unconverted = [item.line_id for item in receipt.line_items if item.amount_usd is None]
        if unconverted:
            raise ValueError(f"{receipt.receipt_id}: no amount_usd on {unconverted}, though it can be converted")


def classification_state(
    item: LineItem, name: ClassifiedField, fields: Mapping[str, dict], min_confidence: Decimal
) -> ClassificationState:
    """Return the state of one classified field on a line.

    Confidence applies to the category only. Whether a missing secondary field is uncertain is the caller's call.
    """
    allowed = allowed_values(fields, f"item.{name}")
    value = getattr(item, name)
    if value is None:
        return "missing"
    if value not in allowed:
        return "unknown"
    if name != "category":
        return "valid"
    if item.confidence is None:
        return "no_confidence"
    return "low_confidence" if item.confidence < min_confidence else "valid"


def _entity_by_country(fields: Mapping[str, dict]) -> dict[str, str]:
    countries = fields.get("receipt.entity", {}).get("countries")
    if not countries:
        raise ValueError("receipt.entity lists no countries in the field catalog")
    return {country: entity for entity, listed in countries.items() for country in listed}


def allowed_values(fields: Mapping[str, dict], name: str) -> list[str]:
    values = fields.get(name, {}).get("values")
    if not values:
        raise ValueError(f"{name} lists no allowed values in the field catalog")
    return values
