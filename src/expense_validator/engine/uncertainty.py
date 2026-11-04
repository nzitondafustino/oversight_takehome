"""EXC-03: which lines cannot be relied on, and what the rules may read while they cannot.

Category uncertainty is the policy's. A line whose category is missing, outside the catalog, below
the EXC-03 threshold, or has no confidence behind it raises a signal, and its category is not used.
The threshold is a project decision, not a source-policy figure. A non-itemized `other` line is
exempt, as the policy says it is not an uncertain category.

Secondary-field uncertainty is a documented project extension, and the line stays in ordinary rules.
A missing secondary field means nothing by itself, as most lines have no venue or recipient, so it
raises a signal only where structured facts say a rule may need it:

    recipient_type       a gift-related category, a line on a Client Gifts receipt, or a named line
                         GIFT-01 could not place
    venue_type           a line in `other` on a Client Entertainment receipt, as ENT-01's venues have
                         no category and the taxonomy sends such items to `other`
    item_kind            a line in `other` on an expense type DOC-03 says covers medication or
                         technology, as the taxonomy sends phones and prescriptions to `other`
    essential_toiletry   a personal care line on a receipt claiming a baggage delay

A value outside the catalog always raises one, as the classifier did answer. Nothing is inferred
from descriptions, merchant codes or references.

The admitted receipt stays the record. The rules read a view of it: the same lines and totals, with
each uncertain line's category blanked, and any USD value nothing supports blanked too, which every
later step already treats as not known. So one decision here governs checks, helpers and carve-outs, on
every receipt in a context.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from expense_validator.engine.helpers import TYPE_COVERS, ReceiptContext, may_need_recipient
from expense_validator.engine.inputs import classification_state
from expense_validator.engine.models import Evidence, Finding, LineItem, Receipt
from expense_validator.engine.money import reconcile, without_unverified_usd
from expense_validator.policy.schema import ProcessRule, RuleSet

ENTERTAINMENT_TYPES = {"Client Entertainment"}
# The expense types DOC-03 says cover medication or technology.
MEDICATION_OR_TECHNOLOGY = {"health_otc", "computer_accessory", "personal_electronics", "software_subscription"}
ITEM_KIND_TYPES = {name for name, covers in TYPE_COVERS.items() if covers & MEDICATION_OR_TECHNOLOGY}

Reason = Literal["category_uncertain", "secondary_field_uncertain"]


@dataclass(frozen=True)
class UncertaintySignal:
    """finding is the EXC-03 review signal for one line, with the rule's provenance."""

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
    """evaluation is original with every receipt swapped for its view, so helpers read one decision."""

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
    return AssessedContext(context, ReceiptContext(context.customer_id, views, context.complete), assessments)


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
    view = without_unverified_usd(
        receipt.with_classifications({line.line_id: {"category": None} for line in uncertain}), reconcile(receipt, fields)
    )
    signals = [
        _signal(rule, receipt, line, "category_uncertain", "category", states[line.line_id], line.category, threshold)
        for line in uncertain
    ]
    for line in view.line_items:
        if line.line_id not in exempt:
            for field, state in _secondary_uncertainty(line, view, fields, threshold):
                signals.append(_signal(rule, receipt, line, "secondary_field_uncertain", field, state, getattr(line, field), threshold))
    return UncertaintyAssessment(receipt, view, tuple(signals), exempt)


def fully_decided(assessment: UncertaintyAssessment, step_complete: bool) -> bool:
    """A later step's complete, along with this: a line a signal is raised on is not settled."""
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
    line: LineItem, receipt: Receipt, fields: Mapping[str, dict], threshold: Decimal
) -> list[tuple[str, str]]:
    found = []
    declared = receipt.submitted_expense_type
    in_other = line.category == "other"
    needed = {
        "recipient_type": may_need_recipient(line, receipt, fields),
        "venue_type": in_other and declared in ENTERTAINMENT_TYPES,
        "item_kind": in_other and declared in ITEM_KIND_TYPES,
    }
    for field, is_needed in needed.items():
        state = classification_state(line, field, fields, threshold)
        if state == "unknown" or (state == "missing" and is_needed):
            found.append((field, state))
    if line.category == "personal_care" and receipt.baggage_delay_ref is not None and line.essential_toiletry is None:
        found.append(("essential_toiletry", "missing"))
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
            Evidence(fact=f"param.{rule.rule_id}.min_confidence", value=threshold),
        ),
        policy_doc=rule.policy_doc,
        source_quote=rule.source_quote,
        provenance=rule.provenance,
    )
    return UncertaintySignal(finding, reason, field, state, line.confidence)
