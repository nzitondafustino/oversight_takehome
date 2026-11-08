"""TIME-03: receipts split around another rule's threshold. The only helper that crosses domains.

A split is two or more qualifying receipts, each at or below a threshold, that together are above it. A
receipt above the threshold does not qualify, so it neither joins nor breaks a split, and a receipt not
seen can only add to one. Each threshold is tried only where this receipt could qualify, so a receipt that
cannot is settled whatever is missing elsewhere.
"""

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from expense_validator.engine.helpers.common import HelperOutcome
from expense_validator.engine.helpers.context import HelperJob
from expense_validator.engine.helpers.gifts import is_client_gift, is_official_line
from expense_validator.engine.helpers.meals import MealPolicy
from expense_validator.engine.models import LineItem, Receipt, ReplacementTarget

# TIME-03: the rules behind its fixed thresholds, as the clause names them.
ITEMIZATION_RULE = "DOC-01"
GIFT_RULE = "GIFT-01"

UNSEEN = "other receipts at this merchant on this date may be missing"
UNPLACED = "a receipt by this employee that could be part of a split has no merchant name or transaction date"
UNKNOWN = "another threshold in this group depends on something not known"


@dataclass(frozen=True)
class _Share:
    """What one receipt adds toward a threshold: at least low and at most high, None for no bound."""

    receipt: Receipt
    low: Decimal | None
    high: Decimal | None
    sure: bool  # whether the receipt is certainly one the threshold counts, such as a meal

    @staticmethod
    def exact(receipt: Receipt, amount: Decimal | None) -> "_Share":
        # Receipt totals are never negative, so an unknown one is at least 0.
        return _Share(receipt, Decimal(0), None, True) if amount is None else _Share(receipt, amount, amount, True)


def _split_with(own: _Share, others: list[_Share], threshold: Decimal | None, least: Decimal, unseen: bool) -> tuple[bool | None, list[Receipt]]:
    """Return whether this receipt is in a split around the threshold, None if not known, and those surely in it."""
    if threshold is None:
        # With every receipt known, too few can never be a split, whatever the threshold is.
        return (False, []) if not unseen and len(others) + 1 < least else (None, [])
    surely_qualifies = lambda share: share.sure and None not in (share.low, share.high) and share.high <= threshold
    may_qualify = lambda share: share.low is None or share.low <= threshold
    if not may_qualify(own):
        return False, []
    if surely_qualifies(own):
        sure = [share for share in others if surely_qualifies(share)]
        if len(sure) + 1 >= least and own.low + sum((share.low for share in sure), Decimal(0)) > threshold:
            return True, [own.receipt, *(share.receipt for share in sure)]
    if unseen:
        return None, []
    most = lambda share: threshold if share.high is None else min(share.high, threshold)
    maybe = [share for share in others if may_qualify(share)]
    if len(maybe) + 1 >= least and most(own) + sum((most(share) for share in maybe), Decimal(0)) > threshold:
        return None, []
    return False, []


def _combined(readings: list[tuple[bool | None, list[Receipt]]]) -> tuple[bool | None, list[Receipt]]:
    """A split under every reading is established, under none is ruled out, and otherwise not known."""
    found = {result for result, _ in readings}
    if found == {True}:
        return True, sorted({receipt for _, receipts in readings for receipt in receipts}, key=lambda item: item.receipt_id)
    return (False, []) if found == {False} else (None, [])


class _SplitGroup:
    """This receipt's merchant and date, tried against each threshold in citation order."""

    def __init__(self, job: HelperJob, least: Decimal) -> None:
        self.job = job
        self.least = least
        self.meals = MealPolicy(job)
        self.unsettled = False
        self.open_reasons: set[str] = set()
        # What each threshold was split around, the rule it belongs to, and the receipts split.
        self.established: list[tuple[str, ReplacementTarget, list[Receipt]]] = []

    # --- Whether a receipt could qualify for each threshold, from what is known of it ---

    def may_split_itemization(self, receipt: Receipt) -> bool:
        total = receipt.total_amount_usd
        return not receipt.itemized and (total is None or total <= self.job.param("itemization_usd"))

    def may_split_meal_cap(self, receipt: Receipt) -> bool:
        if not self.meals.could_be_meal(receipt):
            return False
        rule = self.meals.employee_rule()
        highest = None if rule is None else self.meals.highest_cap(rule)
        least, _ = self.meals.spend_bounds(receipt)
        return highest is None or least is None or least <= highest

    def may_split_gift_cap(self, receipt: Receipt) -> bool:
        return any(self._may_be_capped_gift(line, receipt) for line in receipt.line_items)

    def _may_be_capped_gift(self, line: LineItem, receipt: Receipt) -> bool:
        # The cap is per recipient, so a gift that names no one cannot be split around it.
        gift = is_client_gift(line, receipt, self.job.fields)
        return gift is None or (gift and not is_official_line(line) and line.recipient_name is not None)

    # --- Each threshold ---

    def _at_merchant(self, relevant: Callable[[Receipt], bool]) -> tuple[list[Receipt], list[Receipt], bool]:
        """Return the other relevant receipts at this merchant on this date, all relevant on this date, and
        whether one could be missing from them."""
        job = self.job
        mine = [other for other in job.same_employee() if relevant(other)]
        dated = [other for other in mine if other.transaction_datetime is not None]
        day = [other for other in dated if other.transaction_datetime.date() == job.receipt.transaction_datetime.date()]
        others = [other for other in day if other.merchant_name == job.receipt.merchant_name and other.receipt_id != job.receipt.receipt_id]
        placed = len(dated) == len(mine) and all(other.merchant_name is not None for other in day)
        if not job.context.complete:
            self.open_reasons.add(UNSEEN)
        elif not placed:
            self.open_reasons.add(UNPLACED)
        return others, day, not (job.context.complete and placed)

    def _settle(self, result: bool | None, receipts: list[Receipt], what: str = "", target: ReplacementTarget | None = None, *, unseen: bool) -> None:
        if result:
            self.established.append((what, target, receipts))
        elif result is None:
            self._leave_open(unseen)

    def _leave_open(self, unseen: bool) -> None:
        self.unsettled = True
        if not unseen:
            self.open_reasons.add(UNKNOWN)

    def try_itemization_threshold(self) -> None:
        """Only non-itemized receipts count toward the itemization threshold."""
        others, _, unseen = self._at_merchant(lambda other: not other.itemized)
        shares = [_Share.exact(other, other.total_amount_usd) for other in others]
        own = _Share.exact(self.job.receipt, self.job.receipt.total_amount_usd)
        result, receipts = _split_with(own, shares, self.job.param("itemization_usd"), self.least, unseen)
        self._settle(result, receipts, "the itemization threshold", ReplacementTarget(rule_id=ITEMIZATION_RULE), unseen=unseen)

    def try_meal_cap(self) -> None:
        """Compare per person against the employee's meal cap for the whole day."""
        rule = self.meals.employee_rule()
        others, day, unseen = self._at_merchant(self.meals.could_be_meal)
        share = lambda meal: _Share(meal, *self.meals.spend_bounds(meal), self.meals.is_meal(meal) is True)
        if rule is None:
            self._settle(*_split_with(share(self.job.receipt), [share(meal) for meal in others], None, self.least, unseen), unseen=unseen)
            return
        # The cap follows every meal that day, at any merchant: on a mixed day the higher cap applies. A
        # receipt only possibly a meal may or may not choose it, and one not seen may raise it to the highest.
        definite = [meal for meal in day if self.meals.is_meal(meal) is True]
        caps = {self.meals.meal_cap(rule, meals) for meals in (definite, day)}
        if unseen:
            caps.add(self.meals.highest_cap(rule))
        readings = [_split_with(share(self.job.receipt), [share(meal) for meal in others], cap, self.least, unseen) for cap in caps]
        result, receipts = _combined(readings)
        around = " or ".join(sorted(f"{cap.normalize():f}" for cap in caps if cap is not None))
        self._settle(result, receipts, f"the meal cap of {around}", ReplacementTarget(rule_id=rule.rule_id), unseen=unseen)

    def try_gift_caps(self) -> None:
        """Compare each recipient's gift total across the receipts with a gift for them."""
        others, _, unseen = self._at_merchant(self.may_split_gift_cap)
        receipt = self.job.receipt
        if self._unsettled_gifts([receipt, *others]):
            self._leave_open(unseen)
        mine = self._gift_totals(receipt)
        for name in sorted(mine):
            shares = [_Share.exact(other, total) for other in others if (total := self._gift_totals(other).get(name)) is not None]
            result, receipts = _split_with(_Share.exact(receipt, mine[name]), shares, self.job.param("gift_cap_usd"), self.least, unseen)
            lines = tuple(line.line_id for line in receipt.line_items if self._counts_for(line, receipt) and line.recipient_name == name)
            self._settle(result, receipts, f"the gift cap for {name}", ReplacementTarget(rule_id=GIFT_RULE, line_ids=lines), unseen=unseen)

    def _counts_for(self, line: LineItem, receipt: Receipt) -> bool:
        """Return whether a line counts toward a named recipient's gift total."""
        gift = is_client_gift(line, receipt, self.job.fields)
        return bool(gift) and not is_official_line(line) and None not in (line.amount_usd, line.recipient_type, line.recipient_name)

    def _gift_totals(self, receipt: Receipt) -> dict[str, Decimal]:
        totals: dict[str, Decimal] = {}
        for line in receipt.line_items:
            if self._counts_for(line, receipt):
                totals[line.recipient_name] = totals.get(line.recipient_name, Decimal(0)) + line.amount_usd
        return totals

    def _unsettled_gifts(self, receipts: list[Receipt]) -> bool:
        """Return whether a line could be a capped gift whose amount or recipient is not known."""
        for receipt in receipts:
            for line in receipt.line_items:
                gift = is_client_gift(line, receipt, self.job.fields)
                if gift and is_official_line(line):
                    continue
                # With no recipient type, this may be an official's, which is outside the gift cap.
                if gift is None or (gift and (line.amount_usd is None or line.recipient_type is None)):
                    return True
        return False

    def outcome(self) -> HelperOutcome:
        job = self.job
        reason = next((item for item in (UNSEEN, UNPLACED, UNKNOWN) if item in self.open_reasons), UNKNOWN)
        open_question = (job.undetermined(reason),) if self.unsettled else ()
        if not self.established:
            return HelperOutcome((), open_question)
        declared = {target.rule_id for target in job.rule.replaces if target.level == "finding"}
        targets = tuple(target for _, target, _ in self.established if target.rule_id in declared)
        around = "; ".join(what for what, _, _ in self.established)
        split = sorted({receipt.receipt_id for _, _, receipts in self.established for receipt in receipts})
        found = job.receipt_finding(
            f"{job.rule.title}: around {around}",
            replaces=targets,
            group=", ".join(split),
            split_around=", ".join(target.rule_id for target in targets) or None,
        )
        return HelperOutcome(found.findings, open_question)


def _split_transactions(job: HelperJob) -> HelperOutcome:
    receipt = job.receipt
    split = _SplitGroup(job, job.param("min_receipts"))
    attempts = [
        attempt
        for may_split, attempt in (
            (split.may_split_itemization, split.try_itemization_threshold),
            (split.may_split_meal_cap, split.try_meal_cap),
            (split.may_split_gift_cap, split.try_gift_caps),
        )
        if may_split(receipt)
    ]
    if not attempts:
        # Above every threshold it could be split around, or under none: no other receipt can change that.
        return HelperOutcome()
    if receipt.merchant_name is None or receipt.transaction_datetime is None:
        return job.not_known("the receipt has no merchant name or transaction date")
    for attempt in attempts:
        attempt()
    return split.outcome()
