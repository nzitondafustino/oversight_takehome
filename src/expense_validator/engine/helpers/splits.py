"""TIME-03: receipts split around a threshold that belongs to another rule.

This is the one helper that crosses domains: the clause names the itemization threshold, the meal cap
and the gift cap. It asks the meal and gift modules their public questions, and they know nothing of it.
"""

from decimal import Decimal

from expense_validator.engine.helpers.common import HelperOutcome
from expense_validator.engine.helpers.context import HelperJob
from expense_validator.engine.helpers.gifts import is_client_gift, is_official_line
from expense_validator.engine.helpers.meals import MealPolicy
from expense_validator.engine.models import Receipt, ReplacementTarget

# TIME-03: the rules behind its fixed thresholds, as the clause names them.
ITEMIZATION_RULE = "DOC-01"
GIFT_RULE = "GIFT-01"


def _split_around(amounts: list[Decimal], threshold: Decimal, least: Decimal) -> bool:
    """TIME-03: enough receipts, each at or below the threshold, and together above it."""
    return len(amounts) >= least and all(amount <= threshold for amount in amounts) and sum(amounts, Decimal(0)) > threshold


def _meal_split_ruled_out(policy: MealPolicy, meals: list[Receipt], cap: Decimal | None) -> bool:
    """Whether a split around the meal cap is proved impossible, however the unknown lines resolve.

    cap is worked out from every possible meal in the group, and a possible meal can only raise
    it, so it is the most that could apply. Spend above it at its least disproves "each at or below".
    It is the exact cap only when every one is known to be a meal, and only then can spend at its most
    at or below it disprove "together above".
    """
    if cap is None:
        return False
    bounds = [policy.spend_bounds(meal) for meal in meals]
    if any(low is not None and low > cap for low, _ in bounds):
        return True
    highs = [high for _, high in bounds]
    every_meal = all(policy.is_meal(meal) is True for meal in meals)
    return every_meal and None not in highs and sum(highs, Decimal(0)) <= cap


class _SplitGroup:
    """One merchant's receipts on one day, tried against each threshold in turn.

    Each threshold is decided on its own and may leave the group uncertain, so one being established
    never hides that another is still open. The order the thresholds are tried in is the order they
    are cited in.
    """

    def __init__(self, job: HelperJob, group: list[Receipt], least: Decimal) -> None:
        self.job = job
        self.group = group
        self.least = least
        self.uncertain = False
        # What each threshold was split around, as the rule it belongs to. A target of None is a
        # threshold this receipt has no breach of to replace.
        self.established: list[tuple[str, ReplacementTarget | None]] = []

    def try_itemization_threshold(self) -> None:
        """Only when every receipt in the group is non-itemized."""
        if any(other.itemized for other in self.group):
            return
        totals = [other.total_amount_usd for other in self.group]
        if None in totals:
            # A total already above the threshold rules out a split around it, whatever the others are.
            self.uncertain = self.uncertain or not any(total is not None and total > self.job.param("itemization_usd") for total in totals)
        elif _split_around(totals, self.job.param("itemization_usd"), self.least):
            self.established.append(("the itemization threshold", ReplacementTarget(rule_id=ITEMIZATION_RULE)))

    def try_meal_cap(self) -> None:
        """The employee's meal cap, compared per person."""
        policy = MealPolicy(self.job)
        rule = policy.employee_rule()
        meals = [other for other in self.group if policy.is_meal(other) is not False]
        if not meals:
            return
        spends = [policy.per_person(meal) for meal in meals]
        cap = None if rule is None else policy.meal_cap(rule, meals)
        if _meal_split_ruled_out(policy, meals, cap):
            return
        if None in spends or cap is None:
            self.uncertain = True
        elif _split_around(spends, cap, self.least):
            self.established.append((f"the meal cap of {cap.normalize():f}", ReplacementTarget(rule_id=rule.rule_id)))

    def try_gift_caps(self) -> None:
        """The gift cap for one recipient, across the receipts."""
        by_recipient = self._gift_totals_by_recipient()
        for name in sorted(by_recipient):
            if _split_around(by_recipient[name], self.job.param("gift_cap_usd"), self.least):
                lines = tuple(line.line_id for line in self.job.receipt.line_items if line.recipient_name == name)
                # A receipt in the group with no gift to this recipient has no breach of this cap to replace.
                target = ReplacementTarget(rule_id=GIFT_RULE, line_ids=lines) if lines else None
                self.established.append((f"the gift cap for {name}", target))

    def _gift_totals_by_recipient(self) -> dict[str, list[Decimal]]:
        """Each recipient's gift total on each receipt in the group that holds one."""
        by_recipient: dict[str, list[Decimal]] = {}
        for other in self.group:
            totals_here: dict[str, Decimal] = {}
            for line in other.line_items:
                gift = is_client_gift(line, other, self.job.fields)
                if gift and is_official_line(line):
                    continue
                # With no recipient type, this may be an official's, which is outside the gift cap.
                if gift is None or (gift and (line.amount_usd is None or line.recipient_type is None)):
                    self.uncertain = True
                elif gift and line.recipient_name is not None:
                    totals_here[line.recipient_name] = totals_here.get(line.recipient_name, Decimal(0)) + line.amount_usd
            for name, total in totals_here.items():
                by_recipient.setdefault(name, []).append(total)
        return by_recipient

    def outcome(self) -> HelperOutcome:
        job = self.job
        open_question = job.undetermined("another threshold in this group depends on something not known")
        if not self.established:
            return HelperOutcome((), (open_question,)) if self.uncertain else HelperOutcome()
        declared = {target.rule_id for target in job.rule.replaces if target.level == "finding"}
        targets = tuple(target for _, target in self.established if target is not None and target.rule_id in declared)
        around = "; ".join(what for what, _ in self.established)
        found = job.receipt_finding(
            f"{job.rule.title}: around {around}",
            replaces=targets,
            group=", ".join(receipt.receipt_id for receipt in self.group),
            split_around=", ".join(target.rule_id for target in targets) or None,
        )
        return HelperOutcome(found.findings, (open_question,) if self.uncertain else ())


def _split_transactions(job: HelperJob) -> HelperOutcome:
    receipt = job.receipt
    if not job.context.complete:
        # A receipt not seen could sit above the threshold, and so disprove a split that looks clear.
        return job.not_known("other receipts at this merchant on this date may be missing")
    if receipt.merchant_name is None or receipt.transaction_datetime is None:
        return job.not_known("the receipt has no merchant name or transaction date")
    day = job.same_day()
    if day is None or any(other.merchant_name is None for other in day):
        return job.not_known("a receipt by this employee has no merchant name or transaction date")
    group = [other for other in day if other.merchant_name == receipt.merchant_name]
    least = job.param("min_receipts")
    # Too few receipts can never be a split, whatever is unknown about them. The context is complete
    # here, so none can be missing.
    if len(group) < least:
        return HelperOutcome()
    split = _SplitGroup(job, group, least)
    split.try_itemization_threshold()
    split.try_meal_cap()
    split.try_gift_caps()
    return split.outcome()
