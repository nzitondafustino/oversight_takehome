"""The named helpers: policy logic that conditions cannot express.

Each helper reads its clause's thresholds from its rule's params and gives one of three results:
candidate findings, nothing, or undetermined when something it needs is not known. Undetermined
is never a clean result. Replacements, waivers and carve-outs come later, so a helper reports what
it catches regardless: MEAL-01 still fires on a receipt in a TIME-03 split.

A ReceiptContext is one customer's receipts, and must include the receipt being evaluated.
Receipts carry no customer of their own, so keeping customers apart rests on building one context
per customer. complete says whether these are all the receipts that could matter. A partial context
proves nothing about a cap on a total that spans receipts, either way: a receipt not seen could add to
the total or hold an adjustment that lowers it. That covers the meal caps per day, TIME-03, HEALTH-01's
total per trip and the baggage allowance per delay. Such a total stays undetermined, which is not
compliance. What rests on one receipt or line alone is still confirmed: GIFT-01's occasion is one
receipt, ALC-02 is per receipt, and categorical rules and HEALTH-01's "no trip record" read no total.

A breach of a cap on a total must hold at the lowest that total could be. A line that may or may not
count toward it, such as one of unknown category, could be a discount, so a cap above that lowest
total is left undetermined. That is a project decision: the policy does not mention discounts, and
EXC-03 alone would leave the line out, but it also says a false accusation costs more than a missed
review.

The tables below come from the policy text, as the rule schema has no place for them.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal

from expense_validator.engine.facts import FOOD_OR_DRINK, MEAL_TYPES, item_facts, receipt_facts
from expense_validator.engine.inputs import admit_receipt, allowed_values
from expense_validator.engine.models import Evidence, Finding, LineItem, Receipt, ReplacementTarget
from expense_validator.policy.evaluate import evaluate, replaced_on_receipt
from expense_validator.policy.schema import CarveOutRule, CheckRule, RuleSet, walk
from expense_validator.policy.vocabulary import Severity

# MEAL-01: food, drink and gratuity lines count as meal spend.
MEAL_SPEND = FOOD_OR_DRINK | {"service_fee"}
# DOC-03: the categories each declared expense type covers, and those its test ignores.
TYPE_COVERS = {
    "Meals - Individual": {"food_beverage", "alcohol"},
    "Meals - Team": {"food_beverage", "alcohol"},
    "Client Entertainment": {"food_beverage", "alcohol"},
    "Client Gifts": {"gift", "gift_card", "jewelry"},
    "Lodging": {"lodging", "ground_transport", "laundry_service"},
    "Travel - Other": {"laundry_service", "health_otc", "ground_transport", "clothing", "personal_care"},
    "Office Supplies": {"office_supplies", "computer_accessory"},
    "Computer Equipment": {"computer_accessory", "personal_electronics", "software_subscription"},
    "Software": {"software_subscription"},
}
TYPE_IGNORES = {"service_fee", "other"}
GIFT_CATEGORIES = frozenset({"gift", "gift_card", "jewelry"})
# TIME-03: the rules behind its fixed thresholds, as the clause names them.
ITEMIZATION_RULE = "DOC-01"
GIFT_RULE = "GIFT-01"
# Per-person amounts can repeat forever, so they are compared at a precision past any real amount.
PRECISION = Decimal("1E-10")


@dataclass(frozen=True)
class Undetermined:
    rule_id: str
    line_id: str | None  # None for the receipt as a whole
    reason: str = "depends on a category that is missing or outside the catalog"


@dataclass(frozen=True)
class HelperOutcome:
    findings: tuple[Finding, ...] = ()
    undetermined: tuple[Undetermined, ...] = ()


@dataclass(frozen=True)
class BaggageAllowance:
    """Where lines stand against the baggage-delay allowance. This excuses nothing by itself.

    within is None when it is not known. Whether the delay passed the rule's hours is left to the
    carve-out step: the data does not hold it.
    """

    line_ids: tuple[str, ...]
    total_usd: Decimal | None
    within: bool | None


@dataclass(frozen=True)
class ReceiptContext:
    customer_id: str
    receipts: tuple[Receipt, ...]
    complete: bool


def build_context(
    customer_id: str, receipts: Iterable[Receipt | Mapping[str, object]], fields: Mapping[str, dict], *, complete: bool
) -> ReceiptContext:
    """One customer's receipts, each admitted, in receipt ID order. A repeat of a receipt counts once."""
    if not customer_id.strip():
        raise ValueError("a receipt context needs a customer_id")
    admitted: dict[str, Receipt] = {}
    for given in receipts:
        receipt = admit_receipt(given, fields)
        if admitted.setdefault(receipt.receipt_id, receipt) != receipt:
            raise ValueError(f"{receipt.receipt_id}: appears twice with different contents")
    return ReceiptContext(customer_id, tuple(admitted[receipt_id] for receipt_id in sorted(admitted)), complete)


def require_in_context(receipt: Receipt, context: ReceiptContext) -> None:
    if receipt not in context.receipts:
        raise ValueError(f"{receipt.receipt_id}: is not in the context, or differs from the one there")


def fields_read_by(rule: CheckRule) -> list[str]:
    """Every fact the rule reads, in order. A finding cites them all, whatever their value."""
    return sorted({node.field for condition in rule.conditions() for node in walk(condition) if hasattr(node, "field")})


def make_finding(
    rule: CheckRule,
    receipt: Receipt,
    facts: Mapping[str, object],
    item: LineItem | None = None,
    explanation: str | None = None,
    worked_out: Mapping[str, object] | None = None,
    replaces: tuple[ReplacementTarget, ...] = (),
) -> Finding:
    """worked_out holds what a helper calculated, cited after the facts the rule read."""
    default = rule.title if item is None else f"{rule.title}: {item.description_raw}"
    read = [Evidence(fact=name, value=facts[name]) for name in fields_read_by(rule)]
    calculated = [Evidence(fact=f"helper.{name}", value=value) for name, value in (worked_out or {}).items()]
    return Finding(
        rule_id=rule.rule_id,
        outcome=rule.outcome,
        severity=_severity(rule, facts),
        scope=rule.scope,
        receipt_id=receipt.receipt_id,
        line_ids=() if item is None else (item.line_id,),
        replaces=replaces,
        explanation=explanation or default,
        evidence=(*read, *calculated),
        policy_doc=rule.policy_doc,
        source_quote=rule.source_quote,
        provenance=rule.provenance,
    )


def _severity(rule: CheckRule, facts: Mapping[str, object]) -> Severity | None:
    for case in rule.severity_cases:
        if evaluate(case.when, facts, rule.params):
            return case.severity
    return rule.severity


def is_client_gift(line: LineItem, receipt: Receipt, fields: Mapping[str, dict]) -> bool | None:
    """Whether a line is a gift GIFT-01 covers. None if that depends on something not known."""
    declared = receipt.submitted_expense_type == "Client Gifts"
    if not declared and line.recipient_name is None:
        return False
    if line.category not in allowed_values(fields, "item.category"):
        return None
    if line.category in {"gift_card", "service_fee"}:
        return False
    if line.category in FOOD_OR_DRINK and receipt.submitted_expense_type in MEAL_TYPES:
        return False
    if line.recipient_type is None:
        return True if declared else None
    if line.recipient_type not in allowed_values(fields, "item.recipient_type"):
        return None
    return line.recipient_type != "employee"


def may_need_recipient(line: LineItem, receipt: Receipt, fields: Mapping[str, dict]) -> bool:
    """Whether a line's recipient type could decide a gift rule, from structured facts alone."""
    if line.category in GIFT_CATEGORIES:
        return True
    if receipt.submitted_expense_type == "Client Gifts" and line.category != "service_fee":
        return True
    return is_client_gift(line, receipt, fields) is None


def is_official_line(line: LineItem) -> bool:
    """A gift GIFT-04 takes out of GIFT-01, as it "replaces GIFT-01 for those lines"."""
    return line.recipient_type == "government_official"


def lowest_total(known: Decimal, unsettled: Iterable[LineItem]) -> Decimal | None:
    """The least a total could be, given lines that may count toward it. None if it has no lower bound.

    Only a negative line can lower it. One with no USD amount has only its sign known.
    """
    lowest = known
    for line in unsettled:
        if line.amount < 0:
            if line.amount_usd is None:
                return None
            lowest += line.amount_usd
    return lowest


@dataclass(frozen=True)
class _Job:
    """One helper rule on one receipt, with what its helper may read."""

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

    def is_meal(self, receipt: Receipt) -> bool | None:
        return receipt_facts(receipt, self.fields)["receipt.is_meal_receipt"]

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

    def same_day(self) -> list[Receipt] | None:
        """This employee's receipts on this receipt's date. None if a date is missing, as it could be this one."""
        mine = self.same_employee()
        if any(other.transaction_datetime is None for other in mine):
            return None
        return [other for other in mine if other.transaction_datetime.date() == self.receipt.transaction_datetime.date()]

    def per_person(self, receipt: Receipt) -> Decimal | None:
        """Meal spend per person, as MEAL-01 defines both. None if a line it depends on is not known."""
        if receipt.itemized:
            lines = receipt.line_items
            if not all(self.category_known(line) for line in lines):
                return None
            amounts = [line.amount_usd for line in lines if line.category in MEAL_SPEND]
            spend = None if None in amounts else sum(amounts, Decimal(0))
        else:
            spend = receipt.total_amount_usd
        return None if spend is None else (spend / (receipt.attendee_count or 1)).quantize(PRECISION)

    def spend_bounds(self, receipt: Receipt) -> tuple[Decimal | None, Decimal | None]:
        """The least and most meal spend per person could be, or None where that has no bound.

        A line of unknown category may or may not be meal spend, so a discount could lower the total
        and a charge could raise it. A line of a known category outside meal spend counts for nothing,
        so it offsets nothing. A receipt not known to be a meal may count for nothing at all.
        """
        least, most = Decimal(0), Decimal(0)
        if not receipt.itemized:
            least = most = receipt.total_amount_usd
            if least is None:
                return Decimal(0), None
        else:
            for line in receipt.line_items:
                known = self.category_known(line)
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

    def euro_rate(self) -> Decimal | None:
        """A euro receipt's own rate, or else the one rate on euro receipts that day, if there is one."""
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

    def meal_cap(self, rule: CheckRule, meals: list[Receipt]) -> Decimal | None:
        """The cap that applies to these meals. None if it cannot be worked out."""
        params = rule.params
        if "cap_usd" in params:
            return params["cap_usd"].value
        if "cap_eur" in params:
            rate = self.euro_rate()
            return None if rate is None else params["cap_eur"].value * rate
        homes = self.fields["receipt.employee_country"]["home_currency"]
        if any(meal.currency is None or meal.employee_country not in homes for meal in meals):
            return None
        international = any(meal.currency != homes[meal.employee_country] for meal in meals)
        return params["international_usd"].value if international else params["domestic_usd"].value

    def is_gift(self, line: LineItem) -> bool | None:
        return is_client_gift(line, self.receipt, self.fields)


def _meal_cap(job: _Job) -> HelperOutcome:
    meal = job.is_meal(job.receipt)
    if meal is False:
        return HelperOutcome()
    if job.receipt.transaction_datetime is None:
        return job.not_known("the receipt has no transaction date")
    day = job.same_day()
    if day is None:
        return job.not_known("a receipt by this employee has no transaction date")
    meals = [other for other in day if job.is_meal(other) is not False]
    cap = job.meal_cap(job.rule, meals)
    if cap is None:
        return job.not_known("the cap that applies cannot be worked out")
    unclear = meal is None or any(job.per_person(other) is None for other in meals)
    lows = [job.spend_bounds(other)[0] for other in meals]
    total = None if None in lows else sum(lows, Decimal(0))
    # Meals only possibly on this date can only raise the cap, so it is the most that could apply,
    # and spend above it at its least is a breach however the rest resolves. Given every receipt.
    if job.context.complete and meal is True and total is not None and total > cap:
        prefix = "at least " if unclear else ""
        return job.receipt_finding(
            f"{job.rule.title}: {prefix}{total.normalize():f} per person on this date against a cap of {cap.normalize():f}",
            day_usd_per_person=total,
            cap_usd=cap,
            receipts_that_day=", ".join(meal.receipt_id for meal in meals),
        )
    if unclear:
        return job.not_known("the meal spend on this date is not known")
    return job.not_known("other receipts on this date may be missing") if not job.context.complete else HelperOutcome()


def _employee_meal_rule(job: _Job) -> CheckRule | None:
    """The meal cap rule for this employee: the regional one, where one replaces MEAL-01."""
    facts = receipt_facts(job.receipt, job.fields)
    replaced = replaced_on_receipt(job.ruleset, facts)
    for rule in sorted(job.ruleset.rules, key=lambda item: item.rule_id):
        if not isinstance(rule, CheckRule) or rule.helper != "meal_cap" or rule.status != "active":
            continue
        gate_open = rule.applies_when is None or evaluate(rule.applies_when, facts, rule.params)
        if gate_open and rule.rule_id not in replaced:
            return rule
    return None


def _split_around(amounts: list[Decimal], threshold: Decimal, least: Decimal) -> bool:
    """TIME-03: enough receipts, each at or below the threshold, and together above it."""
    return len(amounts) >= least and all(amount <= threshold for amount in amounts) and sum(amounts, Decimal(0)) > threshold


def _meal_split_ruled_out(job: _Job, meals: list[Receipt], cap: Decimal | None) -> bool:
    """Whether a split around the meal cap is proved impossible, however the unknown lines resolve.

    cap is worked out from every possible meal in the group, and a possible meal can only raise
    it, so it is the most that could apply. Spend above it at its least disproves "each at or below".
    It is the exact cap only when every one is known to be a meal, and only then can spend at its most
    at or below it disprove "together above".
    """
    if cap is None:
        return False
    bounds = [job.spend_bounds(meal) for meal in meals]
    if any(low is not None and low > cap for low, _ in bounds):
        return True
    highs = [high for _, high in bounds]
    every_meal = all(job.is_meal(meal) is True for meal in meals)
    return every_meal and None not in highs and sum(highs, Decimal(0)) <= cap


def _split_transactions(job: _Job) -> HelperOutcome:
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
    uncertain = False
    # Each threshold the group is split around, on its own, as the rule that threshold belongs to.
    established: list[tuple[str, ReplacementTarget]] = []

    # The itemization threshold, when every receipt in the group is non-itemized.
    if not any(other.itemized for other in group):
        totals = [other.total_amount_usd for other in group]
        if None in totals:
            # A total already above the threshold rules out a split around it, whatever the others are.
            uncertain = uncertain or not any(total is not None and total > job.param("itemization_usd") for total in totals)
        elif _split_around(totals, job.param("itemization_usd"), least):
            established.append(("the itemization threshold", ReplacementTarget(rule_id=ITEMIZATION_RULE)))

    # The employee's meal cap, compared per person.
    meal_rule = _employee_meal_rule(job)
    meals = [other for other in group if job.is_meal(other) is not False]
    spends = [job.per_person(meal) for meal in meals]
    cap = None if meal_rule is None or not meals else job.meal_cap(meal_rule, meals)
    if meals and _meal_split_ruled_out(job, meals, cap):
        pass
    elif meals and (None in spends or cap is None):
        uncertain = True
    elif meals and _split_around(spends, cap, least):
        established.append((f"the meal cap of {cap.normalize():f}", ReplacementTarget(rule_id=meal_rule.rule_id)))

    # The gift cap for one recipient, across the receipts.
    by_recipient: dict[str, list[Decimal]] = {}
    for other in group:
        other_job = _Job(job.rule, other, job.context, job.ruleset, job.fields)
        totals_here: dict[str, Decimal] = {}
        for line in other.line_items:
            gift = other_job.is_gift(line)
            if gift and is_official_line(line):
                continue
            # With no recipient type, this may be an official's, which is outside the gift cap.
            if gift is None or (gift and (line.amount_usd is None or line.recipient_type is None)):
                uncertain = True
            elif gift and line.recipient_name is not None:
                totals_here[line.recipient_name] = totals_here.get(line.recipient_name, Decimal(0)) + line.amount_usd
        for name, total in totals_here.items():
            by_recipient.setdefault(name, []).append(total)
    for name in sorted(by_recipient):
        if _split_around(by_recipient[name], job.param("gift_cap_usd"), least):
            lines = tuple(line.line_id for line in receipt.line_items if line.recipient_name == name)
            # A receipt in the group with no gift to this recipient has no breach of this cap to replace.
            if lines:
                established.append((f"the gift cap for {name}", ReplacementTarget(rule_id=GIFT_RULE, line_ids=lines)))
            else:
                established.append((f"the gift cap for {name}", None))

    open_question = job.undetermined("another threshold in this group depends on something not known")
    if not established:
        return HelperOutcome((), (open_question,)) if uncertain else HelperOutcome()
    declared = {target.rule_id for target in job.rule.replaces if target.level == "finding"}
    targets = tuple(target for _, target in established if target is not None and target.rule_id in declared)
    around = "; ".join(what for what, _ in established)
    outcome = job.receipt_finding(
        f"{job.rule.title}: around {around}",
        replaces=targets,
        group=_ids(group),
        split_around=", ".join(target.rule_id for target in targets) or None,
    )
    return HelperOutcome(outcome.findings, (open_question,) if uncertain else ())


def _ids(receipts: list[Receipt]) -> str:
    return ", ".join(receipt.receipt_id for receipt in receipts)


def _gift_cap(job: _Job) -> HelperOutcome:
    gifts, status_open, undetermined, unsettled = [], [], [], []
    for line in job.receipt.line_items:
        gift = job.is_gift(line)
        if gift is None:
            undetermined.append(job.undetermined("whether this line is a client gift is not known", line))
            unsettled.append(line)
        elif gift and line.amount_usd is None:
            undetermined.append(job.undetermined("the line has no USD amount", line))
            unsettled.append(line)
        elif gift and line.recipient_type is None:
            status_open.append(line)
            unsettled.append(line)
        elif gift:
            gifts.append(line)
    # An official's lines are totalled apart. GIFT-04 replaces their GIFT-01, so they are never part
    # of what an ordinary gift to the same name is measured against.
    totals: dict[tuple[str, bool], Decimal] = {}
    for line in gifts:
        if line.recipient_name is not None:
            key = (line.recipient_name, is_official_line(line))
            totals[key] = totals.get(key, Decimal(0)) + line.amount_usd
    cap = job.param("cap_usd")
    findings = []
    for line in status_open:
        # Worked out as if ordinary, the only way this rule reaches it. If it is an official's, GIFT-04
        # replaces the finding on this line, and GIFT-04 is left open on it, so it stays only possible.
        name = line.recipient_name
        if name is None:
            if line.amount_usd >= job.param("named_recipient_from_usd"):
                findings.append(job.line_finding(line, "a gift of this value must name its recipient", amount_usd=line.amount_usd))
            continue
        others = [other for other in unsettled if other.recipient_name == name and other is not line]
        lowest = lowest_total(totals.get((name, False), Decimal(0)) + line.amount_usd, others)
        if line.amount_usd > 0 and lowest is not None and lowest > cap:
            prefix = "at least " if others else ""
            findings.append(job.line_finding(line, f"{prefix}{lowest.normalize():f} for {name} on this receipt, if not an official's", recipient_total_usd=lowest))
        else:
            undetermined.append(job.undetermined("whether this gift is an official's, and so outside the cap, is not known", line))
    for line in gifts:
        name = line.recipient_name
        if name is None:
            if line.amount_usd >= job.param("named_recipient_from_usd"):
                findings.append(job.line_finding(line, "a gift of this value must name its recipient", amount_usd=line.amount_usd))
            continue
        # GIFT-04 does not catch a refund, so there is no finding of its to replace one of these.
        if is_official_line(line) and line.amount_usd < 0:
            continue
        total = totals[name, is_official_line(line)]
        if total <= cap:
            continue
        possible = [other for other in unsettled if other.recipient_name == name]
        lowest = lowest_total(total, possible)
        if lowest is None or lowest <= cap:
            undetermined.append(job.undetermined("a line naming this recipient could bring the total within the cap", line))
            continue
        prefix = "at least " if possible else ""
        findings.append(job.line_finding(line, f"{prefix}{lowest.normalize():f} for {name} on this receipt", recipient_total_usd=lowest))
    return HelperOutcome(tuple(findings), tuple(undetermined))


def _government_official(job: _Job) -> HelperOutcome:
    findings, undetermined = [], []
    for line in job.receipt.line_items:
        kind = line.recipient_type
        # "Provided": a refund is not provided to anyone. "Anything of value" keeps a zero-priced gift in.
        if line.amount < 0:
            continue
        if kind is None:
            if may_need_recipient(line, job.receipt, job.fields):
                undetermined.append(job.undetermined("this line could be for an official: its recipient type is missing", line))
            continue
        if not job.known("item.recipient_type", kind):
            undetermined.append(job.undetermined("the recipient type is outside the catalog", line))
        elif kind != "government_official":
            continue
        elif not job.category_known(line):
            undetermined.append(job.undetermined("this line could be a gratuity, which is not cited", line))
        elif line.category != "service_fee":
            findings.append(job.line_finding(line, "provided to a government official"))
    return HelperOutcome(tuple(findings), tuple(undetermined))


def _is_otc(line: LineItem) -> bool:
    if line.item_kind == "prescription_medication":
        return False
    return line.category == "health_otc" or line.item_kind == "otc_medication"


def _could_be_medication(job: _Job, line: LineItem) -> bool:
    """For a line not known to be medication. Prescription medication is outside the cap either way."""
    if line.item_kind == "prescription_medication":
        return False
    return not job.category_known(line) or (line.item_kind is not None and not job.known("item.item_kind", line.item_kind))


def _otc_trip_cap(job: _Job) -> HelperOutcome:
    receipt = job.receipt
    findings, undetermined, medication = [], [], []
    for line in receipt.line_items:
        if line.item_kind == "prescription_medication":
            if line.amount > 0:
                findings.append((line, "prescription medication is not reimbursable"))
        elif line.item_kind is not None and not job.known("item.item_kind", line.item_kind):
            undetermined.append(job.undetermined("the item kind is outside the catalog", line))
        elif _is_otc(line):
            medication.append(line)
        elif not job.category_known(line):
            undetermined.append(job.undetermined("this line could be medication", line))
    over = False
    cited = medication
    if medication and receipt.trip_id is None:
        # What is reimbursable only on a trip is a purchase. A total across lines is not in question here.
        cited = [line for line in medication if line.amount > 0]
        over = True
        reason = "no trip record"
    elif medication:
        # Each employee has their own allowance on a trip, a project decision. Trip IDs need not be unique to one.
        lines = [
            line
            for other in job.same_employee()
            if other.trip_id == receipt.trip_id
            for line in other.line_items
        ]
        amounts = [line.amount_usd for line in lines if _is_otc(line)]
        known = [amount for amount in amounts if amount is not None]
        total = sum(known, Decimal(0))
        cap = job.param("trip_cap_usd")
        possible = [
            line
            for line in lines
            if (line.amount_usd is None if _is_otc(line) else _could_be_medication(job, line))
        ]
        lowest = lowest_total(total, possible)
        over = job.context.complete and lowest is not None and lowest > cap
        prefix = "at least " if possible else ""
        reason = f"{prefix}{(total if lowest is None else lowest).normalize():f} on this trip against {cap.normalize():f}"
        unclear = len(known) < len(amounts) or not all(job.category_known(line) or _is_otc(line) for line in lines) or bool(possible)
        if not over and (unclear or not job.context.complete):
            undetermined.extend(job.undetermined("other medication on this trip may be missing", line) for line in medication)
    if over:
        findings.extend((line, reason) for line in cited)
    order = {line.line_id: position for position, line in enumerate(receipt.line_items)}
    findings.sort(key=lambda found: order[found[0].line_id])
    return HelperOutcome(tuple(job.line_finding(line, why) for line, why in findings), tuple(undetermined))


def _alcohol_per_attendee(job: _Job) -> HelperOutcome:
    """ALC-02 applies only where ALC-01 permits the alcohol, so it reads ALC-01's conditions."""
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


def _expense_type_mismatch(job: _Job) -> HelperOutcome:
    receipt = job.receipt
    covers = TYPE_COVERS.get(receipt.submitted_expense_type)
    if covers is None:
        return job.not_known("the policy table has no entry for this expense type")
    categories = {line.category for line in receipt.line_items if job.category_known(line)}
    if categories & covers:
        return HelperOutcome()
    if len(categories) < len({line.category for line in receipt.line_items}):
        return job.not_known("a line that could match the declared type has no known category")
    if not categories - TYPE_IGNORES:
        return HelperOutcome()
    return job.receipt_finding(f"{job.rule.title}: declared as {receipt.submitted_expense_type}", categories=", ".join(sorted(categories)))


def _vat_threshold(job: _Job) -> HelperOutcome:
    """The data holds no VAT number, and REG-04 says to treat that as missing, so only the amount decides."""
    receipt = job.receipt
    above = job.param("above_eur")
    if receipt.currency == "EUR":
        if receipt.total_amount is None:
            return job.not_known("the receipt has no total")
        is_above = receipt.total_amount > above
    else:
        rate = job.euro_rate()
        if receipt.total_amount_usd is None or rate is None:
            return job.not_known("the total in euros cannot be worked out")
        is_above = receipt.total_amount_usd > above * rate
    if not is_above:
        return HelperOutcome()
    return job.receipt_finding(f"{job.rule.title}: above EUR {above.normalize():f} with no VAT number recorded", vat_number=None)


HELPERS = {
    "meal_cap": _meal_cap,
    "split_transactions": _split_transactions,
    "gift_cap": _gift_cap,
    "government_official": _government_official,
    "otc_trip_cap": _otc_trip_cap,
    "expense_type_mismatch": _expense_type_mismatch,
    "alcohol_per_attendee": _alcohol_per_attendee,
    "vat_threshold": _vat_threshold,
}


def run_helper(
    rule: CheckRule, receipt: Receipt, context: ReceiptContext, ruleset: RuleSet, fields: Mapping[str, dict]
) -> HelperOutcome:
    helper = HELPERS.get(rule.helper)
    if helper is None:
        raise ValueError(f"{rule.rule_id}: {rule.helper} cannot run on a check rule")
    require_in_context(receipt, context)
    if rule.applies_when is not None and not evaluate(rule.applies_when, receipt_facts(receipt, fields), rule.params):
        return HelperOutcome()
    return helper(_Job(rule, receipt, context, ruleset, fields))


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
