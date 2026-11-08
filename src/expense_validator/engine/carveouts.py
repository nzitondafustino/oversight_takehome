"""Decide whether each carve-out excuses, rejects or leaves undetermined the findings it modifies.

The baggage-delay carve-out needs a reference, a stated delay above the rule's hours, and a total within
the allowance across the employee's receipts sharing that reference. A missing duration is undetermined.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from expense_validator.engine.approvals import REFERENCE_FIELDS, ApprovalResult, WaivedFinding
from expense_validator.engine.helpers import (
    ReceiptContext,
    baggage_allowance,
    counts_toward_baggage_allowance,
)
from expense_validator.engine.inputs import allowed_values
from expense_validator.engine.models import Evidence, Finding, LineItem, Receipt
from expense_validator.engine.replacements import ReplacedFinding
from expense_validator.policy.schema import CarveOutRule, RuleSet


@dataclass(frozen=True)
class CarveOutDecision:
    """A carve-out's decision on one finding, with its evidence."""

    finding: Finding
    rule_id: str
    outcome: Literal["confirmed", "rejected", "undetermined"]
    reference: str | None
    line_ids: tuple[str, ...]
    evidence: tuple[Evidence, ...]
    reason: str


@dataclass(frozen=True)
class CarveOutResult:
    approvals: ApprovalResult
    active: tuple[Finding, ...]  # not replaced, waived or excused, including those undetermined here
    excused: tuple[CarveOutDecision, ...]
    rejected: tuple[CarveOutDecision, ...]
    undetermined_carveouts: tuple[CarveOutDecision, ...]

    @property
    def unaffected(self) -> tuple[Finding, ...]:
        decided = [decision.finding for decision in (*self.rejected, *self.undetermined_carveouts)]
        return tuple(finding for finding in self.active if finding not in decided)

    @property
    def candidates(self) -> tuple[Finding, ...]:
        return self.approvals.candidates

    @property
    def replaced(self) -> tuple[ReplacedFinding, ...]:
        return self.approvals.replaced

    @property
    def waived(self) -> tuple[WaivedFinding, ...]:
        return self.approvals.waived

    @property
    def complete(self) -> bool:
        return self.approvals.complete and not self.undetermined_carveouts


def resolve_carveouts(
    receipt: Receipt, approvals: ApprovalResult, ruleset: RuleSet, fields: Mapping[str, dict], context: ReceiptContext
) -> CarveOutResult:
    """Apply carve-outs to the active findings after approvals."""
    if any(finding.receipt_id != receipt.receipt_id for finding in approvals.active):
        raise ValueError(f"{receipt.receipt_id}: has findings for another receipt")
    carveouts = sorted(
        (rule for rule in ruleset.rules if isinstance(rule, CarveOutRule) and rule.status == "active"),
        key=lambda rule: rule.rule_id,
    )
    for rule in carveouts:
        if rule.helper != "baggage_allowance":
            raise ValueError(f"{rule.rule_id}: only the baggage_allowance helper can decide a carve-out")
        if rule.requires_ref != "baggage_delay":
            raise ValueError(f"{rule.rule_id}: baggage_allowance reads a baggage_delay reference, not {rule.requires_ref}")
    active, excused, rejected, undetermined = [], [], [], []
    for finding in approvals.active:
        rule = next((item for item in carveouts if finding.rule_id in item.modifies), None)
        if rule is None:
            active.append(finding)
            continue
        decision = _baggage_decision(rule, finding, receipt, fields, context)
        if decision.outcome == "confirmed":
            excused.append(decision)
            continue
        active.append(finding)
        (rejected if decision.outcome == "rejected" else undetermined).append(decision)
    return CarveOutResult(approvals, tuple(active), tuple(excused), tuple(rejected), tuple(undetermined))


def _baggage_decision(
    rule: CarveOutRule, finding: Finding, receipt: Receipt, fields: Mapping[str, dict], context: ReceiptContext
) -> CarveOutDecision:
    reference = getattr(receipt, REFERENCE_FIELDS[rule.requires_ref])
    hours = receipt.baggage_delay_hours
    least = rule.params["min_delay_hours"].value
    allowance = baggage_allowance(rule, receipt, context, fields)
    limit = rule.params["allowance_usd"].value
    failed, unknown = [], []

    if reference is None:
        failed.append(f"no {REFERENCE_FIELDS[rule.requires_ref]} is on file")
    else:
        stated = _delays_stated(receipt, rule, context)
        if hours is None:
            unknown.append("the delay is not stated, and the reference alone does not prove it")
        elif len(stated) > 1:
            unknown.append("receipts sharing this reference state different delays")
        elif hours <= least:
            failed.append(f"a delay of {hours.normalize():f} hours is not more than the {least.normalize():f} required")

    lines = {line.line_id: line for line in receipt.line_items}
    cited = [lines[line_id] for line_id in finding.line_ids if line_id in lines]
    catalog = allowed_values(fields, "item.category")
    for line in cited:
        counts = counts_toward_baggage_allowance(line)
        if line.category not in catalog:
            unknown.append(f"{line.line_id} has no known category")
        elif counts is None:
            unknown.append(f"{line.line_id} is not known to be an essential toiletry")
        elif counts is False:
            failed.append(f"{line.line_id} is not an item the allowance covers")
    if not cited:
        failed.append("the finding cites no line the allowance could cover")

    if reference is not None:
        if allowance.within is False:
            failed.append(f"{allowance.total_usd.normalize():f} for this delay is above the allowance of {limit.normalize():f}")
        elif allowance.within is None:
            unknown.append(f"the total for this delay cannot be shown to be within {limit.normalize():f}")

    outcome = "rejected" if failed else "undetermined" if unknown else "confirmed"
    reasons = failed or unknown or [f"{reference} is on file, for a stated delay within the allowance. It is not authenticated."]
    evidence = (
        Evidence(fact=f"receipt.{REFERENCE_FIELDS[rule.requires_ref]}", value=reference),
        Evidence(fact="receipt.baggage_delay_hours", value=hours),
        Evidence(fact=f"param.{rule.rule_id}.min_delay_hours", value=least),
        Evidence(fact="helper.delay_total_usd", value=allowance.total_usd),
        Evidence(fact=f"param.{rule.rule_id}.allowance_usd", value=limit),
    )
    marked = finding
    if outcome == "confirmed":
        marked = Finding.model_validate({**finding.model_dump(), "status": "waived", "resolved_by": rule.rule_id})
    return CarveOutDecision(marked, rule.rule_id, outcome, reference, finding.line_ids, evidence, "; ".join(reasons))


def _delays_stated(receipt: Receipt, rule: CarveOutRule, context: ReceiptContext) -> set[Decimal]:
    field = REFERENCE_FIELDS[rule.requires_ref]
    reference = getattr(receipt, field)
    return {
        other.baggage_delay_hours
        for other in context.receipts
        if other.employee_id == receipt.employee_id
        and getattr(other, field) == reference
        and other.baggage_delay_hours is not None
    }
