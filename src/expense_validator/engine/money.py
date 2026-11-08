"""Check whether a receipt's amounts agree with each other.

Each comparison is reconciled, contradictory (admission refuses), unverified or not_applicable. An
unverified USD value is blanked in the view rules read, and no rate is assumed. itemization_complete is a
submitted claim that can only add scrutiny. Tolerances follow rules/fields.yaml's input_contract, and
arithmetic is exact.
"""

from collections.abc import Mapping
from decimal import Decimal, Inexact, localcontext
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from expense_validator.engine.models import Receipt

USD = "USD"
ReconciliationState = Literal["reconciled", "contradictory", "unverified", "not_applicable"]
# Far beyond what the limits in the contract can need, so that nothing here is ever rounded.
EXACT_PRECISION = 120


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class MonetaryContract(_Strict):
    """The input_contract in rules/fields.yaml. Required, as a default would hide assumptions."""

    version: StrictInt = Field(ge=1)
    minor_units: dict[str, StrictInt]
    # With no currency, a contradiction must hold in any currency, so this is the coarsest there is.
    missing_currency_minor_units: StrictInt = Field(ge=0)
    usd_tolerance: Decimal = Field(gt=0)
    max_abs_amount: Decimal = Field(gt=0)
    max_decimal_places: StrictInt = Field(ge=0)

    def half_unit(self, currency: str | None) -> Decimal:
        places = self.missing_currency_minor_units if currency is None else self.minor_units[currency]
        return Decimal(1).scaleb(-places) / 2


class MonetaryReconciliation(_Strict):
    """Where a receipt's amounts stand."""

    contract_version: int
    fx_rate: ReconciliationState
    line_arithmetic: ReconciliationState
    line_conversion: ReconciliationState
    total_conversion: ReconciliationState
    line_sum: ReconciliationState
    line_sum_discrepancy: Decimal | None = None
    unsupported: tuple[str, ...] = ()  # outside the contract, so nothing here could be compared
    problems: tuple[str, ...] = ()

    @property
    def lines_account_for_total(self) -> bool:
        return self.line_sum != "unverified"


def monetary_contract(fields: Mapping[str, dict]) -> MonetaryContract:
    if "input_contract" not in fields:
        raise ValueError("the field catalog has no input_contract, so amounts cannot be compared")
    return MonetaryContract.model_validate(fields["input_contract"])


def _rate(receipt: Receipt) -> Decimal | None:
    """Return the USD rate to compare with. USD is 1; a rate without a currency verifies nothing."""
    if receipt.currency == USD:
        return Decimal(1)
    return None if receipt.currency is None else receipt.fx_rate_to_usd


def _amounts(receipt: Receipt) -> list[tuple[str, Decimal | None]]:
    named = [("fx_rate_to_usd", receipt.fx_rate_to_usd), ("total_amount", receipt.total_amount), ("total_amount_usd", receipt.total_amount_usd)]
    for line in receipt.line_items:
        named += [(f"{line.line_id} unit_price", line.unit_price), (f"{line.line_id} amount", line.amount), (f"{line.line_id} amount_usd", line.amount_usd)]
    return named


def _out_of_limits(receipt: Receipt, contract: MonetaryContract) -> list[str]:
    found = []
    for name, value in _amounts(receipt):
        if value is None:
            continue
        if abs(value) >= contract.max_abs_amount or -value.as_tuple().exponent > contract.max_decimal_places:
            found.append(f"{name} {value} is beyond the contract's limits, of below {contract.max_abs_amount} and {contract.max_decimal_places} decimal places")
    return found


def _unit_price_rounding(unit_price: Decimal, half: Decimal) -> Decimal:
    printed = Decimal(1).scaleb(unit_price.as_tuple().exponent) / 2
    return min(half, printed)


def _converted(name: str, printed: Decimal, usd: Decimal, rate: Decimal, tolerance: Decimal) -> str | None:
    expected = printed * rate
    if abs(usd - expected) <= tolerance:
        return None
    return f"{name} USD value {usd} contradicts {printed} x rate {rate} = {expected}"


def _line_arithmetic(receipt: Receipt, half: Decimal, problems: list[str]) -> ReconciliationState:
    if not receipt.line_items:
        return "not_applicable"
    state: ReconciliationState = "reconciled"
    for line in receipt.line_items:
        expected = line.quantity * line.unit_price
        if abs(line.amount - expected) > half + line.quantity * _unit_price_rounding(line.unit_price, half):
            problems.append(f"{line.line_id} amount {line.amount} contradicts quantity {line.quantity} x unit_price {line.unit_price} = {expected}")
            state = "contradictory"
    return state


def _line_conversion(receipt: Receipt, rate: Decimal | None, contract: MonetaryContract, problems: list[str]) -> ReconciliationState:
    if not receipt.line_items:
        return "not_applicable"
    if rate is None:
        return "unverified"
    state: ReconciliationState = "reconciled"
    missing = False
    for line in receipt.line_items:
        if line.amount_usd is None:
            missing = True
            continue
        problem = _converted(line.line_id, line.amount, line.amount_usd, rate, contract.usd_tolerance)
        if problem:
            problems.append(problem)
            state = "contradictory"
    # Reconciled means every line was compared, so one with nothing to compare is not.
    return "unverified" if missing and state == "reconciled" else state


def _total_conversion(receipt: Receipt, rate: Decimal | None, contract: MonetaryContract, problems: list[str]) -> ReconciliationState:
    printed, usd = receipt.total_amount, receipt.total_amount_usd
    if printed is None and usd is None:
        return "not_applicable"
    if printed is None or usd is None or rate is None:
        return "unverified"
    problem = _converted("receipt total", printed, usd, rate, contract.usd_tolerance)
    if problem:
        problems.append(problem)
        return "contradictory"
    return "reconciled"


def _line_sum(receipt: Receipt, half: Decimal, problems: list[str]) -> tuple[ReconciliationState, Decimal | None]:
    claim = receipt.itemization_complete
    if not receipt.itemized:
        if claim is True:
            problems.append("a receipt that is not itemized cannot have complete itemization")
            return "contradictory", None
        return "not_applicable", None
    if claim is False:
        return "unverified", None
    if receipt.total_amount is None:
        return "not_applicable", None
    expected = sum((line.amount for line in receipt.line_items), Decimal(0))
    discrepancy = receipt.total_amount - expected
    if abs(discrepancy) <= half * (len(receipt.line_items) + 1):
        return "reconciled", None
    if claim is True:
        problems.append(f"receipt total {receipt.total_amount} contradicts its complete itemization, whose lines add up to {expected}: a discrepancy of {discrepancy}")
        return "contradictory", discrepancy
    return "unverified", discrepancy


def reconcile(receipt: Receipt, fields: Mapping[str, dict]) -> MonetaryReconciliation:
    """Compare every comparable amount exactly. Problems are the contradictions, in a fixed order."""
    contract = monetary_contract(fields)
    problems: list[str] = []
    unsupported = _out_of_limits(receipt, contract)
    if receipt.currency is not None and receipt.currency not in contract.minor_units:
        unsupported.insert(0, f"currency {receipt.currency} has no precision in input contract {contract.version}, allowed: {sorted(contract.minor_units)}")
    if unsupported:
        return MonetaryReconciliation(
            contract_version=contract.version,
            fx_rate="not_applicable",
            line_arithmetic="not_applicable",
            line_conversion="not_applicable",
            total_conversion="not_applicable",
            line_sum="not_applicable",
            unsupported=tuple(unsupported),
        )
    with localcontext() as exact:
        exact.prec = EXACT_PRECISION
        exact.traps[Inexact] = True
        half = contract.half_unit(receipt.currency)
        rate_state: ReconciliationState = "unverified" if _rate(receipt) is None else "reconciled"
        if receipt.currency == USD and receipt.fx_rate_to_usd is not None and receipt.fx_rate_to_usd != 1:
            problems.append(f"a USD receipt cannot have rate {receipt.fx_rate_to_usd}")
            rate_state = "contradictory"
        rate = _rate(receipt)
        arithmetic = _line_arithmetic(receipt, half, problems)
        lines = _line_conversion(receipt, rate, contract, problems)
        total = _total_conversion(receipt, rate, contract, problems)
        line_sum, discrepancy = _line_sum(receipt, half, problems)
    return MonetaryReconciliation(
        contract_version=contract.version,
        fx_rate=rate_state,
        line_arithmetic=arithmetic,
        line_conversion=lines,
        total_conversion=total,
        line_sum=line_sum,
        line_sum_discrepancy=discrepancy,
        problems=tuple(problems),
    )


def without_unverified_usd(receipt: Receipt, reconciliation: MonetaryReconciliation) -> Receipt:
    """Return the receipt as rules read it, with unsupported USD values blanked."""
    if reconciliation.line_conversion != "unverified" and reconciliation.total_conversion != "unverified":
        return receipt
    data = receipt.model_dump()
    if reconciliation.total_conversion == "unverified":
        data["total_amount_usd"] = None
    if reconciliation.line_conversion == "unverified":
        data["line_items"] = [{**line, "amount_usd": None} for line in data["line_items"]]
    return Receipt.model_validate(data)
