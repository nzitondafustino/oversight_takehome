"""HEALTH-01: prescription medication, and the OTC cap per trip."""

from decimal import Decimal

from expense_validator.engine.helpers.common import HelperOutcome, Undetermined, lowest_total
from expense_validator.engine.helpers.context import HelperJob
from expense_validator.engine.models import LineItem


def _is_otc(line: LineItem) -> bool:
    if line.item_kind == "prescription_medication":
        return False
    return line.category == "health_otc" or line.item_kind == "otc_medication"


def _could_be_medication(job: HelperJob, line: LineItem) -> bool:
    if line.item_kind == "prescription_medication":
        return False
    return not job.category_known(line) or (line.item_kind is not None and not job.known("item.item_kind", line.item_kind))


def _sort_lines(job: HelperJob) -> tuple[list[LineItem], list[LineItem], list[Undetermined]]:
    prescriptions, medication, undetermined = [], [], []
    for line in job.receipt.line_items:
        if line.item_kind == "prescription_medication":
            if line.amount > 0:
                prescriptions.append(line)
        elif line.item_kind is not None and not job.known("item.item_kind", line.item_kind):
            undetermined.append(job.undetermined("the item kind is outside the catalog", line))
        elif _is_otc(line):
            medication.append(line)
        elif not job.category_known(line):
            undetermined.append(job.undetermined("this line could be medication", line))
    return prescriptions, medication, undetermined


def _trip_standing(job: HelperJob) -> tuple[bool, str, bool]:
    """Return the trip's OTC standing. Each employee has their own allowance, a project decision."""
    lines = [
        line
        for other in job.same_employee()
        if other.trip_id == job.receipt.trip_id
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
    # Only a total seen whole can breach, and it must breach at the least it could be.
    over = job.context.complete and lowest is not None and lowest > cap
    prefix = "at least " if possible else ""
    reason = f"{prefix}{(total if lowest is None else lowest).normalize():f} on this trip against {cap.normalize():f}"
    unclear = len(known) < len(amounts) or not all(job.category_known(line) or _is_otc(line) for line in lines) or bool(possible)
    return over, reason, unclear


def _otc_trip_cap(job: HelperJob) -> HelperOutcome:
    receipt = job.receipt
    prescriptions, medication, undetermined = _sort_lines(job)
    found = [(line, "prescription medication is not reimbursable") for line in prescriptions]
    if medication and receipt.trip_id is None:
        # What is reimbursable only on a trip is a purchase. A total across lines is not in question here.
        found += [(line, "no trip record") for line in medication if line.amount > 0]
    elif medication:
        over, reason, unclear = _trip_standing(job)
        if over:
            found += [(line, reason) for line in medication]
        elif unclear or not job.context.complete:
            undetermined += [job.undetermined("other medication on this trip may be missing", line) for line in medication]
    order = {line.line_id: position for position, line in enumerate(receipt.line_items)}
    found.sort(key=lambda item: order[item[0].line_id])
    return HelperOutcome(tuple(job.line_finding(line, why) for line, why in found), tuple(undetermined))
