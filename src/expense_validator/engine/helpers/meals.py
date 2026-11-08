"""MEAL-01, REG-02, REG-03 and ALC-02: daily meal caps per person, and alcohol per attendee."""

from dataclasses import dataclass
from decimal import Decimal

from expense_validator.engine.facts import FOOD_OR_DRINK, receipt_facts
from expense_validator.engine.helpers.common import HelperOutcome, lowest_total
from expense_validator.engine.helpers.context import HelperJob
from expense_validator.engine.models import Receipt
from expense_validator.policy.evaluate import evaluate, replaced_on_receipt
from expense_validator.policy.schema import CheckRule

# MEAL-01: food, drink and gratuity lines count as meal spend.
MEAL_SPEND = FOOD_OR_DRINK | {"service_fee"}
# Per-person amounts can repeat forever, so they are compared at a precision past any real amount.
PRECISION = Decimal("1E-10")


@dataclass(frozen=True)
class MealPolicy:
    """Meal questions for any receipt in the job's context, shared with TIME-03."""

    job: HelperJob

    def is_meal(self, receipt: Receipt) -> bool | None:
        return receipt_facts(receipt, self.job.fields)["receipt.is_meal_receipt"]

    def could_be_meal(self, receipt: Receipt) -> bool:
        return self.is_meal(receipt) is not False

    def day_meals(self) -> list[Receipt] | None:
        """Return the employee's receipts that could be meals on this date, or None if one has no date.

        A receipt known not to be a meal cannot change a daily meal total, so its date does not matter.
        """
        return self.job.same_day(self.could_be_meal)

    def per_person(self, receipt: Receipt) -> Decimal | None:
        """Return meal spend per person, or None if a line it depends on is unknown."""
        if receipt.itemized:
            lines = receipt.line_items
            if not all(self.job.category_known(line) for line in lines):
                return None
            amounts = [line.amount_usd for line in lines if line.category in MEAL_SPEND]
            spend = None if None in amounts else sum(amounts, Decimal(0))
        else:
            spend = receipt.total_amount_usd
        return None if spend is None else (spend / (receipt.attendee_count or 1)).quantize(PRECISION)

    def spend_bounds(self, receipt: Receipt) -> tuple[Decimal | None, Decimal | None]:
        """Return the least and most meal spend per person could be, or None if unbounded."""
        least, most = Decimal(0), Decimal(0)
        if not receipt.itemized:
            least = most = receipt.total_amount_usd
            if least is None:
                return Decimal(0), None
        else:
            for line in receipt.line_items:
                known = self.job.category_known(line)
                if known and line.category not in MEAL_SPEND:
                    continue
                amount = line.amount_usd
                if amount is None:
                    # No conversion, so only the sign of what was charged is known.
                    least = None if line.amount < 0 else least
                    most = None if line.amount > 0 else most
                    continue
                least = None if least is None else least + (amount if known else min(amount, Decimal(0)))
                most = None if most is None else most + (amount if known else max(amount, Decimal(0)))
        if self.is_meal(receipt) is None:
            least = None if least is None else min(least, Decimal(0))
            most = None if most is None else max(most, Decimal(0))
        people = receipt.attendee_count or 1
        return tuple(None if value is None else (value / people).quantize(PRECISION) for value in (least, most))

    def meal_cap(self, rule: CheckRule, meals: list[Receipt]) -> Decimal | None:
        """Return the cap that applies to these meals, or None."""
        params = rule.params
        if "cap_usd" in params:
            return params["cap_usd"].value
        if "cap_eur" in params:
            rate = self.job.euro_rate()
            return None if rate is None else params["cap_eur"].value * rate
        homes = self.job.fields["receipt.employee_country"]["home_currency"]
        if any(meal.currency is None or meal.employee_country not in homes for meal in meals):
            return None
        international = any(meal.currency != homes[meal.employee_country] for meal in meals)
        return params["international_usd"].value if international else params["domestic_usd"].value

    def highest_cap(self, rule: CheckRule) -> Decimal | None:
        """Return the most the rule's cap could be on any day, or None if that is not known."""
        params = rule.params
        if "cap_usd" in params:
            return params["cap_usd"].value
        if "cap_eur" in params:
            rate = self.job.euro_rate()
            return None if rate is None else params["cap_eur"].value * rate
        return max(params["domestic_usd"].value, params["international_usd"].value)

    def employee_rule(self) -> CheckRule | None:
        """Return the employee's meal cap rule, regional where one replaces MEAL-01."""
        job = self.job
        facts = receipt_facts(job.receipt, job.fields)
        replaced = replaced_on_receipt(job.ruleset, facts)
        for rule in sorted(job.ruleset.rules, key=lambda item: item.rule_id):
            if not isinstance(rule, CheckRule) or rule.helper != "meal_cap" or rule.status != "active":
                continue
            gate_open = rule.applies_when is None or evaluate(rule.applies_when, facts, rule.params)
            if gate_open and rule.rule_id not in replaced:
                return rule
        return None


def _meal_cap(job: HelperJob) -> HelperOutcome:
    policy = MealPolicy(job)
    meal = policy.is_meal(job.receipt)
    if meal is False:
        return HelperOutcome()
    if job.receipt.transaction_datetime is None:
        return job.not_known("the receipt has no transaction date")
    meals = policy.day_meals()
    if meals is None:
        return job.not_known("a receipt by this employee that could be a meal has no transaction date")
    cap = policy.meal_cap(job.rule, meals)
    if cap is None:
        return job.not_known("the cap that applies cannot be worked out")
    unclear = meal is None or any(policy.per_person(other) is None for other in meals)
    lows = [policy.spend_bounds(other)[0] for other in meals]
    total = None if None in lows else sum(lows, Decimal(0))
    # Meals only possibly on this date can only raise the cap, so it is the most that could apply,
    # and spend above it at its least is a breach however the rest resolves. Given every receipt.
    if job.context.complete and meal is True and total is not None and total > cap:
        prefix = "at least " if unclear else ""
        return job.receipt_finding(
            f"{job.rule.title}: {prefix}{total.normalize():f} per person on this date against a cap of {cap.normalize():f}",
            day_usd_per_person=total,
            cap_usd=cap,
            receipts_that_day=", ".join(other.receipt_id for other in meals),
        )
    if unclear:
        return job.not_known("the meal spend on this date is not known")
    if not job.context.complete:
        return job.not_known("other receipts on this date may be missing")
    return HelperOutcome()


def _alcohol_per_attendee(job: HelperJob) -> HelperOutcome:
    """ALC-02 applies only where ALC-01 permits the alcohol."""
    receipt = job.receipt
    alc_01 = next((rule for rule in job.ruleset.rules if rule.rule_id == "ALC-01"), None)
    if not isinstance(alc_01, CheckRule):
        raise ValueError(f"{job.rule.rule_id}: needs ALC-01 in the ruleset, to tell where alcohol is permitted")
    facts = receipt_facts(receipt, job.fields)
    if receipt.mcc not in alc_01.params["on_premises"].values or evaluate(alc_01.applies_when, facts, alc_01.params):
        return HelperOutcome()
    undetermined = [job.undetermined("this line could be alcohol", line) for line in receipt.line_items if not job.category_known(line)]
    alcohol = [line for line in receipt.line_items if line.category == "alcohol"]
    undetermined += [job.undetermined("the line has no USD amount", line) for line in alcohol if line.amount_usd is None]
    total = sum((line.amount_usd for line in alcohol if line.amount_usd is not None), Decimal(0))
    people = receipt.attendee_count
    possible = [line for line in receipt.line_items if not job.category_known(line) or (line.category == "alcohol" and line.amount_usd is None)]
    lowest = lowest_total(total, possible)
    if lowest is None or lowest <= job.param("cap_usd") * people:
        return HelperOutcome((), tuple(undetermined))
    per_person = (lowest / people).quantize(PRECISION)
    prefix = "at least " if possible else ""
    explanation = f"{prefix}{per_person.normalize():f} per attendee against {job.param('cap_usd').normalize():f}"
    findings = (job.line_finding(line, explanation, alcohol_usd=lowest, attendees=people) for line in alcohol if line.amount_usd is not None)
    return HelperOutcome(tuple(findings), tuple(undetermined))
