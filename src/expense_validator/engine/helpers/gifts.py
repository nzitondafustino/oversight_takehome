"""GIFT-01 and GIFT-04: which lines are gifts, the cap for one recipient, and gifts to officials.

is_client_gift, may_need_recipient and is_official_line are the domain's questions, and other modules
ask them too: TIME-03 totals gifts per recipient, and the uncertainty step asks what needs a recipient.
"""

from collections.abc import Mapping
from decimal import Decimal

from expense_validator.engine.facts import FOOD_OR_DRINK, MEAL_TYPES
from expense_validator.engine.helpers.common import HelperOutcome, Undetermined, lowest_total
from expense_validator.engine.helpers.context import HelperJob
from expense_validator.engine.inputs import allowed_values
from expense_validator.engine.models import Finding, LineItem, Receipt

GIFT_CATEGORIES = frozenset({"gift", "gift_card", "jewelry"})


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


class _GiftCapCheck:
    """GIFT-01 on one receipt: a class because each phase below reads what the ones before it sorted out.

    Lines fall into three groups: settled gifts, gifts whose recipient may be an official (status_open),
    and lines that may or may not count toward a total at all (unsettled, which includes status_open).
    Lines that are status_open are judged first, so the order of findings is part of the behaviour.
    """

    def __init__(self, job: HelperJob) -> None:
        self.job = job
        self.settled: list[LineItem] = []
        self.status_open: list[LineItem] = []
        self.unsettled: list[LineItem] = []
        self.findings: list[Finding] = []
        self.undetermined: list[Undetermined] = []
        self._sort_lines()
        self.totals = self._settled_totals()
        self.cap = job.param("cap_usd")

    def run(self) -> HelperOutcome:
        for line in self.status_open:
            self._judge_status_open(line)
        for line in self.settled:
            self._judge_settled(line)
        return HelperOutcome(tuple(self.findings), tuple(self.undetermined))

    def _sort_lines(self) -> None:
        job = self.job
        for line in job.receipt.line_items:
            gift = is_client_gift(line, job.receipt, job.fields)
            if gift is None:
                self.undetermined.append(job.undetermined("whether this line is a client gift is not known", line))
                self.unsettled.append(line)
            elif gift and line.amount_usd is None:
                self.undetermined.append(job.undetermined("the line has no USD amount", line))
                self.unsettled.append(line)
            elif gift and line.recipient_type is None:
                self.status_open.append(line)
                self.unsettled.append(line)
            elif gift:
                self.settled.append(line)

    def _settled_totals(self) -> dict[tuple[str, bool], Decimal]:
        """Per recipient. An official's lines are totalled apart: GIFT-04 replaces their GIFT-01, so
        they are never part of what an ordinary gift to the same name is measured against.
        """
        totals: dict[tuple[str, bool], Decimal] = {}
        for line in self.settled:
            if line.recipient_name is not None:
                key = (line.recipient_name, is_official_line(line))
                totals[key] = totals.get(key, Decimal(0)) + line.amount_usd
        return totals

    def _require_name(self, line: LineItem) -> None:
        if line.amount_usd >= self.job.param("named_recipient_from_usd"):
            self.findings.append(self.job.line_finding(line, "a gift of this value must name its recipient", amount_usd=line.amount_usd))

    def _judge_status_open(self, line: LineItem) -> None:
        """Worked out as if ordinary, the only way this rule reaches it. If it is an official's, GIFT-04
        replaces the finding on this line, and GIFT-04 is left open on it, so it stays only possible.
        """
        name = line.recipient_name
        if name is None:
            self._require_name(line)
            return
        others = [other for other in self.unsettled if other.recipient_name == name and other is not line]
        lowest = lowest_total(self.totals.get((name, False), Decimal(0)) + line.amount_usd, others)
        if line.amount_usd > 0 and lowest is not None and lowest > self.cap:
            prefix = "at least " if others else ""
            self.findings.append(self.job.line_finding(line, f"{prefix}{lowest.normalize():f} for {name} on this receipt, if not an official's", recipient_total_usd=lowest))
        else:
            self.undetermined.append(self.job.undetermined("whether this gift is an official's, and so outside the cap, is not known", line))

    def _judge_settled(self, line: LineItem) -> None:
        name = line.recipient_name
        if name is None:
            self._require_name(line)
            return
        # GIFT-04 does not catch a refund, so there is no finding of its to replace one of these.
        if is_official_line(line) and line.amount_usd < 0:
            return
        total = self.totals[name, is_official_line(line)]
        if total <= self.cap:
            return
        possible = [other for other in self.unsettled if other.recipient_name == name]
        lowest = lowest_total(total, possible)
        if lowest is None or lowest <= self.cap:
            self.undetermined.append(self.job.undetermined("a line naming this recipient could bring the total within the cap", line))
            return
        prefix = "at least " if possible else ""
        self.findings.append(self.job.line_finding(line, f"{prefix}{lowest.normalize():f} for {name} on this receipt", recipient_total_usd=lowest))


def _gift_cap(job: HelperJob) -> HelperOutcome:
    return _GiftCapCheck(job).run()


def _government_official(job: HelperJob) -> HelperOutcome:
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
