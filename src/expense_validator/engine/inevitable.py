"""Whether a violation is certain though the exact rule is not, because a recipient type is unresolved.

A gift with no recipient type may be for a client, an employee or an official, and each is a different
rule. If every reading gives a flag on this receipt, a violation is certain, whichever rule it proves to
be. This project decision is narrow on purpose:

    It reads only recipient types, the only classified field whose values each lead to a known rule.
    The readings are the field catalog's values. None is assumed to be the true one.
    A line whose category is uncertain is not read: what it is, is not known, so nothing can be proved.
    Each reading runs the real rules, so replacements, bounds and severities are the rules' own.
    Only a flag counts. A reading that is compliant, or that cannot be evaluated, ends the claim.
    Lines of the employee's other receipts are read too, as TIME-03 adds up across them.
    Too many lines to read means no claim, not a guess.

This adds no finding. The rules stay unresolved or open as they were, and the proof is reported beside them.
"""

import itertools
from collections.abc import Callable, Mapping

from pydantic import BaseModel, ConfigDict, model_validator

from expense_validator.engine.helpers import ReceiptContext, build_context
from expense_validator.engine.inputs import allowed_values
from expense_validator.engine.models import Receipt
from expense_validator.engine.uncertainty import AssessedContext

RECIPIENT_TYPE = "recipient_type"
# Three values a line, so five lines is 243 runs of the rules.
MAX_LINES_READ = 5


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class ReadLine(_Strict):
    receipt_id: str
    line_id: str
    recipient_type: str


class Reading(_Strict):
    """One way the unresolved lines could be, and the rules that flag this receipt if they are."""

    lines: tuple[ReadLine, ...]
    rule_ids: tuple[str, ...]

    @model_validator(mode="after")
    def _check_it_flags(self):
        if not self.rule_ids:
            raise ValueError("a reading in a proof must flag the receipt")
        return self


class InevitableViolation(_Strict):
    readings: tuple[Reading, ...]
    rule_ids: tuple[str, ...]  # every rule that flags in some reading: which one applies is not known
    explanation: str

    @model_validator(mode="after")
    def _check_it_is_a_proof(self):
        if not self.readings:
            raise ValueError("a proof needs its readings")
        return self


def _unresolved_lines(assessed: AssessedContext, employee_id: str) -> list[tuple[str, str]]:
    found = []
    for assessment in assessed.assessments:
        if assessment.receipt.employee_id != employee_id:
            continue
        for signal in assessment.signals:
            line_id = signal.finding.line_ids[0]
            if signal.field == RECIPIENT_TYPE and line_id not in assessment.uncertain_line_ids:
                found.append((assessment.receipt_id, line_id))
    return sorted(found)


def _as_read(context: ReceiptContext, reading: Mapping[tuple[str, str], str], fields: Mapping[str, dict]) -> ReceiptContext:
    receipts: list[Receipt] = []
    for receipt in context.receipts:
        outputs = {
            line_id: {RECIPIENT_TYPE: value} for (receipt_id, line_id), value in reading.items() if receipt_id == receipt.receipt_id
        }
        receipts.append(receipt.with_classifications(outputs))
    return build_context(context.customer_id, receipts, fields, complete=context.complete)


def prove_violation(
    receipt_id: str,
    context: ReceiptContext,
    assessed: AssessedContext,
    fields: Mapping[str, dict],
    flagging_rules: Callable[[ReceiptContext, str], tuple[str, ...]],
) -> InevitableViolation | None:
    """The proof that every reading flags this receipt, or None if that is not shown.

    flagging_rules gives the rules that flag a receipt in a context, and nothing if it does not flag.
    """
    employee_id = assessed.assessment_for(receipt_id).receipt.employee_id
    lines = _unresolved_lines(assessed, employee_id)
    if not any(owner == receipt_id for owner, _ in lines) or len(lines) > MAX_LINES_READ:
        return None
    values = allowed_values(fields, f"item.{RECIPIENT_TYPE}")
    readings = []
    for chosen in itertools.product(values, repeat=len(lines)):
        rule_ids = flagging_rules(_as_read(context, dict(zip(lines, chosen, strict=True)), fields), receipt_id)
        if not rule_ids:
            return None
        read = tuple(ReadLine(receipt_id=owner, line_id=line_id, recipient_type=value) for (owner, line_id), value in zip(lines, chosen, strict=True))
        readings.append(Reading(lines=read, rule_ids=rule_ids))
    all_rules = tuple(sorted({rule_id for reading in readings for rule_id in reading.rule_ids}))
    names = ", ".join(line_id for _, line_id in lines)
    return InevitableViolation(
        readings=tuple(readings),
        rule_ids=all_rules,
        explanation=(
            f"Every recipient type the catalog allows on {names} gives a flag on this receipt, "
            f"under {', '.join(all_rules)}. A violation is certain. Which rule applies is not, so none is reported as active."
        ),
    )
