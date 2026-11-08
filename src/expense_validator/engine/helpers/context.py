"""The receipts helpers read across, and HelperJob.

With complete=False a total spanning receipts is neither confirmed nor
cleared, as an unseen receipt could raise or lower it.
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal

from expense_validator.engine.facts import item_facts, receipt_facts
from expense_validator.engine.helpers.common import HelperOutcome, Undetermined, make_finding
from expense_validator.engine.inputs import admit_receipt, allowed_values
from expense_validator.engine.models import Finding, LineItem, Receipt, ReplacementTarget
from expense_validator.policy.schema import CheckRule, RuleSet


@dataclass(frozen=True)
class ReceiptContext:
    receipts: tuple[Receipt, ...]
    complete: bool


def build_context(receipts: Iterable[Receipt | Mapping[str, object]], fields: Mapping[str, dict], *, complete: bool) -> ReceiptContext:
    """Admit receipts into a context, in receipt ID order."""
    admitted: dict[str, Receipt] = {}
    for given in receipts:
        receipt = admit_receipt(given, fields)
        if admitted.setdefault(receipt.receipt_id, receipt) != receipt:
            raise ValueError(f"{receipt.receipt_id}: appears twice with different contents")
    return ReceiptContext(tuple(admitted[receipt_id] for receipt_id in sorted(admitted)), complete)


def require_in_context(receipt: Receipt, context: ReceiptContext) -> None:
    if receipt not in context.receipts:
        raise ValueError(f"{receipt.receipt_id}: is not in the context, or differs from the one there")


@dataclass(frozen=True)
class HelperJob:
    """One helper rule running on one receipt."""

    rule: CheckRule
    receipt: Receipt
    context: ReceiptContext
    ruleset: RuleSet
    fields: Mapping[str, dict]

    def param(self, name: str) -> Decimal:
        return self.rule.params[name].value

    def known(self, name: str, value: object) -> bool:
        return value in allowed_values(self.fields, name)

    def category_known(self, line: LineItem) -> bool:
        return self.known("item.category", line.category)

    def receipt_finding(
        self, explanation: str, replaces: tuple[ReplacementTarget, ...] = (), **worked_out: object
    ) -> HelperOutcome:
        facts = receipt_facts(self.receipt, self.fields)
        return HelperOutcome((make_finding(self.rule, self.receipt, facts, None, explanation, worked_out, replaces),))

    def line_finding(self, line: LineItem, explanation: str, **worked_out: object) -> Finding:
        facts = item_facts(self.receipt, line, self.fields)
        text = f"{self.rule.title}: {line.description_raw}: {explanation}"
        return make_finding(self.rule, self.receipt, facts, line, text, worked_out)

    def undetermined(self, reason: str, line: LineItem | None = None) -> Undetermined:
        return Undetermined(self.rule.rule_id, None if line is None else line.line_id, reason)

    def not_known(self, reason: str) -> HelperOutcome:
        return HelperOutcome((), (self.undetermined(reason),))

    def same_employee(self) -> list[Receipt]:
        return [other for other in self.context.receipts if other.employee_id == self.receipt.employee_id]

    def same_day(self, relevant: Callable[[Receipt], bool] | None = None) -> list[Receipt] | None:
        """Return this employee's receipts on this receipt's date, or None if a date is missing.

        With relevant, only the receipts it accepts are returned, so a missing date on any other cannot matter.
        """
        mine = [other for other in self.same_employee() if relevant is None or relevant(other)]
        if any(other.transaction_datetime is None for other in mine):
            return None
        return [other for other in mine if other.transaction_datetime.date() == self.receipt.transaction_datetime.date()]

    def euro_rate(self) -> Decimal | None:
        """Return the receipt's own euro rate, or the single euro rate recorded that day."""
        if self.receipt.currency == "EUR" and self.receipt.fx_rate_to_usd is not None:
            return self.receipt.fx_rate_to_usd
        if self.receipt.transaction_datetime is None:
            return None
        day = self.receipt.transaction_datetime.date()
        rates = {
            other.fx_rate_to_usd
            for other in self.context.receipts
            if other.currency == "EUR"
            and other.fx_rate_to_usd is not None
            and other.transaction_datetime is not None
            and other.transaction_datetime.date() == day
        }
        return rates.pop() if len(rates) == 1 else None
