"""Build the EXC-04 report for one receipt.

Findings stay atomic inside groups. Only active groups are exceptions; replaced, waived, excused and
uncitable groups are audit history. decided says whether the receipt was fully evaluated.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, model_validator

from expense_validator.engine.carveouts import CarveOutResult
from expense_validator.engine.helpers import fields_read_by
from expense_validator.engine.models import Finding, Receipt
from expense_validator.engine.money import MonetaryReconciliation, reconcile
from expense_validator.engine.uncertainty import AssessedContext, UncertaintySignal, fully_decided
from expense_validator.policy.schema import CheckRule, ProcessRule, Provenance, RuleSet
from expense_validator.policy.vocabulary import Severity

Kind = Literal["exception", "review_signal", "uncertainty"]
Resolution = Literal["active", "unresolved", "replaced", "waived", "excused", "uncitable"]
# The order groups are listed in, most pressing first.
RESOLUTIONS = get_args(Resolution)
KINDS = get_args(Kind)
SEVERITIES = get_args(Severity)  # highest first


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class UncertaintyDetail(_Strict):
    line_id: str
    field: str
    reason: str
    state: str
    confidence: Decimal | None


class ReportGroup(_Strict):
    group_id: str
    receipt_id: str
    rule_id: str
    kind: Kind
    resolution: Resolution
    severity: Severity | None
    line_ids: tuple[str, ...]
    title: str
    policy_doc: str
    source_quote: str
    provenance: Provenance
    sources: tuple[Finding, ...]
    source_notes: tuple[str | None, ...]  # why each source was resolved as it was, by position
    uncertainty: tuple[UncertaintyDetail, ...] = ()

    @model_validator(mode="after")
    def _check_each_source_has_its_note(self):
        if len(self.source_notes) != len(self.sources):
            raise ValueError(f"{self.group_id}: needs one note entry per source")
        return self

    @property
    def notes(self) -> tuple[str, ...]:
        """Return the distinct source notes."""
        return tuple(sorted({note for note in self.source_notes if note is not None}))


class OpenQuestion(_Strict):
    """What one rule could not decide on a receipt. Not a violation."""

    rule_id: str
    whole_receipt: bool
    line_ids: tuple[str, ...]
    entries: int
    reasons: tuple[str, ...]


class ReceiptReport(_Strict):
    receipt_id: str
    ruleset_version: str
    groups: tuple[ReportGroup, ...]
    open_questions: tuple[OpenQuestion, ...]
    decided: bool
    monetary: MonetaryReconciliation

    def of(self, kind: Kind, resolution: Resolution) -> tuple[ReportGroup, ...]:
        return tuple(group for group in self.groups if group.kind == kind and group.resolution == resolution)

    @property
    def exceptions(self) -> tuple[ReportGroup, ...]:
        return self.of("exception", "active")

    @property
    def review_signals(self) -> tuple[ReportGroup, ...]:
        return self.of("review_signal", "active")

    @property
    def uncertainty(self) -> ReportGroup | None:
        return next(iter(self.of("uncertainty", "active")), None)


@dataclass(frozen=True)
class ReportEntry:
    """One finding on its way into a group, with its resolution."""

    finding: Finding
    resolution: Resolution
    note: str | None = None


def build_report(
    assessed: AssessedContext, receipt_id: str, result: CarveOutResult, ruleset: RuleSet, fields: Mapping[str, dict]
) -> ReceiptReport:
    """Build the report from the steps run on the receipt's evaluation view."""
    assessment = next((item for item in assessed.assessments if item.receipt_id == receipt_id), None)
    if assessment is None:
        raise ValueError(f"{receipt_id}: is not in the assessed context")
    _refuse_a_run_around_the_view(result, assessment.uncertain_line_ids, ruleset)
    groups = group_entries(assessment.evaluation_receipt, _entries(result), assessment.signals, ruleset)
    monetary = reconcile(assessment.receipt, fields)
    return ReceiptReport(
        receipt_id=receipt_id,
        ruleset_version=ruleset.ruleset_version,
        groups=groups,
        open_questions=_open_questions(result),
        decided=fully_decided(assessment, result.complete) and monetary.lines_account_for_total,
        monetary=monetary,
    )


def group_entries(
    receipt: Receipt,
    entries: Iterable[ReportEntry],
    signals: Iterable[UncertaintySignal],
    ruleset: RuleSet,
) -> tuple[ReportGroup, ...]:
    """Group entries deterministically, whatever their input order."""
    rules = {rule.rule_id: rule for rule in ruleset.rules}
    whole_receipt = _whole_receipt_rules(ruleset)
    position = {line.line_id: index for index, line in enumerate(receipt.line_items)}
    fees = {line.line_id for line in receipt.line_items if line.category == "service_fee"}
    gathered: dict[tuple, list[ReportEntry]] = {}
    for entry in entries:
        finding = entry.finding
        if finding.receipt_id != receipt.receipt_id:
            raise ValueError(f"{receipt.receipt_id}: has findings for another receipt")
        _refuse_what_evidence_forbids(finding, rules, whole_receipt)
        target = () if finding.outcome == "exception" else finding.line_ids
        gathered.setdefault((finding.rule_id, entry.resolution, target), []).append(entry)

    groups = []
    for (rule_id, resolution, _), members in gathered.items():
        groups.append(_group(receipt, rules[rule_id], resolution, members, whole_receipt, position, fees))
    signals = tuple(signals)
    if signals:
        groups.append(_uncertainty_group(receipt, signals, position))
    return tuple(sorted(groups, key=lambda group: (RESOLUTIONS.index(group.resolution), KINDS.index(group.kind), group.group_id)))


def entries_of(report: ReceiptReport) -> tuple[ReportEntry, ...]:
    """Return the entries behind a report's groups, other than uncertainty."""
    entries = []
    for group in report.groups:
        if group.kind == "uncertainty":
            continue
        # An uncitable group was active: it is worked out again from what it cites.
        resolution = "active" if group.resolution == "uncitable" else group.resolution
        entries += [ReportEntry(finding, resolution, note) for finding, note in zip(group.sources, group.source_notes)]
    return tuple(entries)


def signals_of(report: ReceiptReport) -> tuple[UncertaintySignal, ...]:
    group = report.uncertainty
    if group is None:
        return ()
    return tuple(
        UncertaintySignal(finding, detail.reason, detail.field, detail.state, detail.confidence)
        for finding, detail in zip(group.sources, group.uncertainty)
    )


def _entries(result: CarveOutResult) -> list[ReportEntry]:
    replacements = result.approvals.replacements
    unresolved = {}
    for item in sorted(replacements.possible, key=lambda found: (found.possibly_by, found.reason)):
        unresolved.setdefault((item.finding.rule_id, item.finding.line_ids), item.reason)
    for item in result.undetermined_carveouts:
        unresolved[(item.finding.rule_id, item.finding.line_ids)] = item.reason
    rejected = {(item.finding.rule_id, item.finding.line_ids): item.reason for item in result.rejected}
    entries = []
    for finding in result.active:
        key = (finding.rule_id, finding.line_ids)
        if key in unresolved:
            entries.append(ReportEntry(finding, "unresolved", unresolved[key]))
        else:
            entries.append(ReportEntry(finding, "active", rejected.get(key)))
    entries += [ReportEntry(item.finding, "replaced", item.reason) for item in result.replaced]
    entries += [ReportEntry(item.finding, "waived", item.reason) for item in result.waived]
    entries += [ReportEntry(item.finding, "excused", item.reason) for item in result.excused]
    return entries


def _whole_receipt_rules(ruleset: RuleSet) -> frozenset[str]:
    directive = next(
        (
            rule
            for rule in ruleset.rules
            if isinstance(rule, ProcessRule) and rule.directive == "evidence" and rule.status == "active"
        ),
        None,
    )
    if directive is None:
        raise ValueError("no active evidence directive says how exceptions are cited")
    return frozenset(directive.params["whole_receipt_rules"].values)


def _refuse_what_evidence_forbids(finding: Finding, rules: Mapping, whole_receipt: frozenset[str]) -> None:
    if finding.rule_id not in rules:
        raise ValueError(f"{finding.rule_id}: is not in the ruleset")
    if finding.outcome != "exception":
        return
    if finding.scope == "item" and finding.rule_id in whole_receipt:
        raise ValueError(f"{finding.rule_id}: is an item finding, but EXC-04 lists it as whole-receipt")
    if finding.scope == "receipt" and finding.rule_id not in whole_receipt:
        raise ValueError(f"{finding.rule_id}: is a whole-receipt finding, but EXC-04 does not list it")


def _refuse_a_run_around_the_view(result: CarveOutResult, uncertain: tuple[str, ...], ruleset: RuleSet) -> None:
    """Refuse a result that read a category the uncertainty view blanked."""
    rules = {rule.rule_id: rule for rule in ruleset.rules}
    for finding in result.candidates:
        rule = rules.get(finding.rule_id)
        if not isinstance(rule, CheckRule) or rule.helper is not None or "item.category" not in fields_read_by(rule):
            continue
        if set(finding.line_ids) & set(uncertain):
            raise ValueError(f"{finding.rule_id}: read the category of an uncertain line, so the run skipped the view")


def _ordered_entries(members: Iterable[ReportEntry], position: Mapping[str, int]) -> list[ReportEntry]:
    def order(entry: ReportEntry) -> tuple:
        lines = entry.finding.line_ids
        return (min((position.get(line, len(position)) for line in lines), default=-1), entry.finding.model_dump_json(), entry.note or "")

    return sorted(members, key=order)


def _group(
    receipt: Receipt,
    rule,
    resolution: Resolution,
    members: list[ReportEntry],
    whole_receipt: frozenset[str],
    position: Mapping[str, int],
    fees: set[str],
) -> ReportGroup:
    ordered = _ordered_entries(members, position)
    sources = tuple(entry.finding for entry in ordered)
    kind = sources[0].outcome
    named = sorted({line for finding in sources for line in finding.line_ids}, key=lambda line: position.get(line, len(position)))
    line_ids = tuple(named)
    if kind == "exception":
        line_ids = () if rule.rule_id in whole_receipt else tuple(line for line in named if line not in fees)
        if resolution in {"active", "unresolved"} and rule.rule_id not in whole_receipt and not line_ids:
            resolution = "uncitable"
    identity = [receipt.receipt_id, rule.rule_id, resolution]
    if kind != "exception":
        identity += named
    severities = [finding.severity for finding in sources if finding.severity is not None]
    return ReportGroup(
        group_id="/".join(identity),
        receipt_id=receipt.receipt_id,
        rule_id=rule.rule_id,
        kind=kind,
        resolution=resolution,
        severity=min(severities, key=SEVERITIES.index) if severities else None,
        line_ids=line_ids,
        title=rule.title,
        policy_doc=rule.policy_doc,
        source_quote=rule.source_quote,
        provenance=rule.provenance,
        sources=sources,
        source_notes=tuple(entry.note for entry in ordered),
    )


def _uncertainty_group(
    receipt: Receipt, signals: tuple[UncertaintySignal, ...], position: Mapping[str, int]
) -> ReportGroup:
    ordered = sorted(signals, key=lambda item: (position[item.finding.line_ids[0]], item.reason, item.field))
    first = ordered[0].finding
    return ReportGroup(
        group_id="/".join((receipt.receipt_id, first.rule_id, "active")),
        receipt_id=receipt.receipt_id,
        rule_id=first.rule_id,
        kind="uncertainty",
        resolution="active",
        severity=None,
        line_ids=tuple(sorted({item.finding.line_ids[0] for item in ordered}, key=position.get)),
        title=first.explanation.split(":")[0],
        policy_doc=first.policy_doc,
        source_quote=first.source_quote,
        provenance=first.provenance,
        sources=tuple(item.finding for item in ordered),
        source_notes=(None,) * len(ordered),
        uncertainty=tuple(
            UncertaintyDetail(
                line_id=item.finding.line_ids[0],
                field=item.field,
                reason=item.reason,
                state=item.state,
                confidence=item.confidence,
            )
            for item in ordered
        ),
    )


def _open_questions(result: CarveOutResult) -> tuple[OpenQuestion, ...]:
    gathered: dict[str, list] = {}
    for item in result.approvals.replacements.undetermined:
        gathered.setdefault(item.rule_id, []).append((item.line_id, item.reason))
    for item in result.approvals.replacements.pending_helpers:
        gathered.setdefault(item.rule_id, []).append((None, f"the {item.helper} helper has not been run"))
    # A replacement can rest on a gate reading a missing value, which no other entry would name.
    for item in sorted(result.approvals.replacements.possible, key=lambda found: (found.possibly_by, found.reason)):
        if item.possibly_by not in gathered:
            gathered[item.possibly_by] = [(None, item.reason)]
    return tuple(
        OpenQuestion(
            rule_id=rule_id,
            whole_receipt=any(line is None for line, _ in entries),
            line_ids=tuple(sorted({line for line, _ in entries if line is not None})),
            entries=len(entries),
            reasons=tuple(sorted({reason for _, reason in entries})),
        )
        for rule_id, entries in sorted(gathered.items())
    )
