"""EXC-03: which lines cannot be relied on, and the view of each receipt the rules read.

A missing, unknown or low-confidence category raises a signal and is blanked in the view. A secondary
field raises one only where structured facts say a rule may need it, a project extension. A secondary value
whose own confidence is below min_confidence is blanked in the view as well, so no rule reads it, and is then
judged as a missing value would be: this is the only place an attribute's confidence is judged, never the
classifier. Nothing is inferred from descriptions, merchant codes or references.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from expense_validator.engine.helpers import TYPE_COVERS, ReceiptContext, may_need_recipient
from expense_validator.engine.inputs import classification_state, field_confidence_state
from expense_validator.engine.models import Evidence, Finding, LineItem, Receipt
from expense_validator.engine.money import reconcile, without_unverified_usd
from expense_validator.policy.schema import ProcessRule, RuleSet

ENTERTAINMENT_TYPES = {"Client Entertainment"}
# The expense types DOC-03 says cover medication or technology.
MEDICATION_OR_TECHNOLOGY = {"health_otc", "computer_accessory", "personal_electronics", "software_subscription"}
ITEM_KIND_TYPES = {name for name, covers in TYPE_COVERS.items() if covers & MEDICATION_OR_TECHNOLOGY}

Reason = Literal["category_uncertain", "secondary_field_uncertain"]
SECONDARY_FIELDS = ("recipient_type", "venue_type", "item_kind", "essential_toiletry")


@dataclass(frozen=True)
class UncertaintySignal:
    """The EXC-03 review signal for one line."""

    finding: Finding
    reason: Reason
    field: str
    state: str
    confidence: Decimal | None


@dataclass(frozen=True)
class UncertaintyAssessment:
    receipt: Receipt  # the admitted receipt, unchanged
    evaluation_receipt: Receipt  # what the rules read
    signals: tuple[UncertaintySignal, ...]
    exempt_line_ids: tuple[str, ...]  # non-itemized `other` lines

    @property
    def receipt_id(self) -> str:
        return self.receipt.receipt_id

    @property
    def uncertain_line_ids(self) -> tuple[str, ...]:
        return tuple(item.finding.line_ids[0] for item in self.signals if item.reason == "category_uncertain")

    @property
    def eligible_lines(self) -> tuple[LineItem, ...]:
        return tuple(line for line in self.receipt.line_items if line.line_id not in self.uncertain_line_ids)


@dataclass(frozen=True)
class AssessedContext:
    """The context with every receipt replaced by its evaluation view."""

    original: ReceiptContext
    evaluation: ReceiptContext
    assessments: tuple[UncertaintyAssessment, ...]

    def assessment_for(self, receipt_id: str) -> UncertaintyAssessment:
        for item in self.assessments:
            if item.receipt_id == receipt_id:
                return item
        raise ValueError(f"{receipt_id}: is not in the assessed context")


def assess_context(context: ReceiptContext, ruleset: RuleSet, fields: Mapping[str, dict]) -> AssessedContext:
    assessments = tuple(assess_uncertainty(receipt, ruleset, fields) for receipt in context.receipts)
    views = tuple(item.evaluation_receipt for item in assessments)
    return AssessedContext(context, ReceiptContext(views, context.complete), assessments)


def assess_uncertainty(receipt: Receipt, ruleset: RuleSet, fields: Mapping[str, dict]) -> UncertaintyAssessment:
    rule = _uncertainty_rule(ruleset)
    threshold = rule.params["min_confidence"].value
    exempt = tuple(line.line_id for line in receipt.line_items if not receipt.itemized and line.category == "other")
    states = {
        line.line_id: classification_state(line, "category", fields, threshold)
        for line in receipt.line_items
        if line.line_id not in exempt
    }
    uncertain = [line for line in receipt.line_items if states.get(line.line_id, "valid") != "valid"]
    low = {line.line_id: _low_confidence_fields(line, threshold) for line in receipt.line_items}
    blanked = {line.line_id: {"category": None} for line in uncertain}
    for line_id, names in low.items():
        if names:
            blanked[line_id] = {**blanked.get(line_id, {}), **{name: None for name in names}, **{f"{name}_confidence": None for name in names}}
    view = without_unverified_usd(receipt.with_classifications(blanked), reconcile(receipt, fields))
    signals = [
        _signal(rule, receipt, line, "category_uncertain", "category", states[line.line_id], line.category, threshold, line.confidence)
        for line in uncertain
    ]
    originals = {line.line_id: line for line in receipt.line_items}
    for line in view.line_items:
        if line.line_id not in exempt:
            source = originals[line.line_id]
            for field, state in _secondary_uncertainty(line, view, fields, threshold, low[line.line_id]):
                confidence = getattr(source, f"{field}_confidence") if state == "low_confidence" else source.confidence
                signals.append(_signal(rule, receipt, source, "secondary_field_uncertain", field, state, getattr(source, field), threshold, confidence))
    return UncertaintyAssessment(receipt, view, tuple(signals), exempt)


def _low_confidence_fields(line: LineItem, threshold: Decimal) -> tuple[str, ...]:
    """The stated secondary fields whose own confidence is below the threshold."""
    return tuple(
        name for name in SECONDARY_FIELDS
        if getattr(line, name) is not None and field_confidence_state(getattr(line, f"{name}_confidence"), threshold) == "low_confidence"
    )


def fully_decided(assessment: UncertaintyAssessment, step_complete: bool) -> bool:
    """Return whether a step's result is complete and no line is uncertain."""
    return step_complete and not assessment.signals


def _uncertainty_rule(ruleset: RuleSet) -> ProcessRule:
    rule = next(
        (
            item
            for item in ruleset.rules
            if isinstance(item, ProcessRule) and item.directive == "uncertainty" and item.status == "active"
        ),
        None,
    )
    if rule is None:
        raise ValueError("no active uncertainty directive gives the confidence threshold")
    return rule


def _secondary_uncertainty(
    line: LineItem, receipt: Receipt, fields: Mapping[str, dict], threshold: Decimal, low: tuple[str, ...]
) -> list[tuple[str, str]]:
    """The uncertain secondary fields of a line in the view, where low-confidence values are already blanked: each
    is judged as missing, and reported as low_confidence."""
    found = []
    declared = receipt.submitted_expense_type
    in_other = line.category == "other"
    needed = {
        "recipient_type": may_need_recipient(line, receipt, fields),
        "venue_type": in_other and declared in ENTERTAINMENT_TYPES,
        "item_kind": in_other and declared in ITEM_KIND_TYPES,
    }
    for field, is_needed in needed.items():
        state = "low_confidence" if field in low else classification_state(line, field, fields, threshold)
        if state == "unknown" or (state in ("missing", "low_confidence") and is_needed):
            found.append((field, state))
    if line.category == "personal_care" and receipt.baggage_delay_ref is not None and line.essential_toiletry is None:
        found.append(("essential_toiletry", "low_confidence" if "essential_toiletry" in low else "missing"))
    return found


def _signal(
    rule: ProcessRule,
    receipt: Receipt,
    line: LineItem,
    reason: Reason,
    field: str,
    state: str,
    value: object,
    threshold: Decimal,
    confidence: Decimal | None,
) -> UncertaintySignal:
    finding = Finding(
        rule_id=rule.rule_id,
        outcome="review_signal",
        scope="item",
        receipt_id=receipt.receipt_id,
        line_ids=(line.line_id,),
        explanation=f"{rule.title}: {line.description_raw}: {field} is {state}",
        evidence=(
            Evidence(fact="uncertainty.reason", value=reason),
            Evidence(fact="uncertainty.state", value=state),
            Evidence(fact=f"item.{field}", value=value),
            Evidence(fact="item.confidence", value=line.confidence),
            *((Evidence(fact=f"item.{field}_confidence", value=confidence),) if state == "low_confidence" and field != "category" else ()),
            Evidence(fact=f"param.{rule.rule_id}.min_confidence", value=threshold),
        ),
        policy_doc=rule.policy_doc,
        source_quote=rule.source_quote,
        provenance=rule.provenance,
    )
    return UncertaintySignal(finding, reason, field, state, confidence)
