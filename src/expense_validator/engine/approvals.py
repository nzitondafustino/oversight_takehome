"""Decides which active findings an approval reference waives, after replacements.

The EXC-02 directive says which kind of reference each rule accepts, and each kind has its own
field on the receipt. A value in the right field waives that rule. A value in any other field
waives nothing, and the kind is never read from the reference's format.

A reference is a supplied claim, and EXC-02 asks for no more than that: "a recorded one satisfies
that rule". Nothing here authenticates it, or checks that an approval came first.

A baggage-delay reference is not an approval: it belongs to the carve-out step. Replaced findings
stay replaced, and a waiver cannot settle what replacements left open, so that stays open too.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, get_args

from expense_validator.engine.models import Finding, Receipt
from expense_validator.engine.replacements import ReplacedFinding, ReplacementResult
from expense_validator.policy.schema import CheckRule, ProcessRule, RuleSet
from expense_validator.policy.vocabulary import RefKind

CARVE_OUT_KINDS = {"baggage_delay"}
REFERENCE_FIELDS = {kind: f"{kind}_ref" for kind in get_args(RefKind)}


@dataclass(frozen=True)
class WaivedFinding:
    """finding is the candidate marked waived, with everything else as it was."""

    finding: Finding
    kind: str
    field: str
    reference: str
    reason: str


@dataclass(frozen=True)
class ApprovalGap:
    """An active finding its rule would let a reference waive, and why none did."""

    finding: Finding
    required_kind: str
    required_field: str
    state: Literal["no_reference", "wrong_kind"]
    other_fields: tuple[str, ...]  # the reference fields that do hold a value


@dataclass(frozen=True)
class ApprovalResult:
    replacements: ReplacementResult
    active: tuple[Finding, ...]  # not replaced and not waived
    waived: tuple[WaivedFinding, ...]
    gaps: tuple[ApprovalGap, ...]  # those of the active findings a reference could have waived

    @property
    def candidates(self) -> tuple[Finding, ...]:
        return self.replacements.candidates

    @property
    def replaced(self) -> tuple[ReplacedFinding, ...]:
        return self.replacements.replaced

    @property
    def complete(self) -> bool:
        return self.replacements.complete


def resolve_approvals(receipt: Receipt, replacements: ReplacementResult, ruleset: RuleSet) -> ApprovalResult:
    """replacements must be for this receipt, from a run on this ruleset."""
    if any(finding.receipt_id != receipt.receipt_id for finding in replacements.active):
        raise ValueError(f"{receipt.receipt_id}: has findings for another receipt")
    directive, kinds = _reference_kinds(ruleset)
    active, waived, gaps = [], [], []
    for finding in replacements.active:
        kind = kinds.get(finding.rule_id)
        reference = None if kind is None else getattr(receipt, REFERENCE_FIELDS[kind])
        if reference is not None:
            waived.append(_waive(finding, kind, reference, directive))
            continue
        active.append(finding)
        if kind is not None:
            gaps.append(_gap(finding, kind, receipt))
    return ApprovalResult(replacements, tuple(active), tuple(waived), tuple(gaps))


def _reference_kinds(ruleset: RuleSet) -> tuple[str | None, Mapping[str, str]]:
    """The directive's ID, and the kind of reference each check rule accepts, by rule ID.

    The directive decides, and every rule must agree with it, as a reference must never waive
    a rule that does not allow it. A rule that accepts none is simply not listed.
    """
    active = [rule for rule in ruleset.rules if rule.status == "active"]
    directive = next((rule for rule in active if isinstance(rule, ProcessRule) and rule.directive == "reference_kinds"), None)
    listed: dict[str, str] = {}
    if directive is not None:
        for kind in REFERENCE_FIELDS:
            for rule_id in directive.params[f"{kind}_rules"].values:
                if listed.setdefault(rule_id, kind) != kind:
                    raise ValueError(f"{directive.rule_id}: lists {rule_id} under two kinds of reference")
    kinds = {}
    for rule in active:
        if not isinstance(rule, CheckRule):
            continue
        said = listed.get(rule.rule_id)
        if rule.waived_by_ref is None and said is None:
            continue
        if directive is None:
            raise ValueError(f"{rule.rule_id}: accepts a reference, but no directive says which kind")
        if rule.waived_by_ref != said:
            raise ValueError(f"{rule.rule_id}: accepts {rule.waived_by_ref}, but {directive.rule_id} says {said}")
        if said in CARVE_OUT_KINDS:
            raise ValueError(f"{rule.rule_id}: a {said} reference belongs to a carve-out, and waives no check")
        kinds[rule.rule_id] = said
    return (None if directive is None else directive.rule_id), kinds


def _waive(finding: Finding, kind: str, reference: str, directive: str) -> WaivedFinding:
    marked = Finding.model_validate({**finding.model_dump(), "status": "waived", "resolved_by": directive})
    field = REFERENCE_FIELDS[kind]
    reason = f"{field} {reference} is on file, the kind {directive} accepts for {finding.rule_id}. It is not authenticated."
    return WaivedFinding(marked, kind, field, reference, reason)


def _gap(finding: Finding, kind: str, receipt: Receipt) -> ApprovalGap:
    others = tuple(field for field in REFERENCE_FIELDS.values() if getattr(receipt, field) is not None)
    return ApprovalGap(finding, kind, REFERENCE_FIELDS[kind], "wrong_kind" if others else "no_reference", others)
