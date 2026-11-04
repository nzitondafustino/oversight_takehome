"""The public entry point: the only supported way to get an authoritative result for a receipt.

evaluate_receipts takes receipts and nothing already worked out, so no caller can skip a step:

    admit -> one customer's context -> assess uncertainty -> checks and helpers on the view
          -> replacements -> approvals -> carve-outs -> EXC-04 report -> disposition

The stage functions in the other modules stay importable for unit tests. Only this produces a final
result. An error in any step is raised, never turned into an outcome, and it stops the whole run.

customer_id scopes a run: it names the customer, and keeps one customer's receipts from being added
up with another's across runs. It does not authenticate anything. Receipts carry no owner, so nothing
here can tell whether the receipts passed in belong to that customer, and establishing that is the
caller's job.

The disposition follows EXC-01, reading its severities from the rule:

    any exception of a flag severity                         flag
    otherwise, any exception of a review severity            review
    otherwise, any review signal or EXC-03 signal            review
    otherwise, not fully decided                             review   (a project decision, not EXC-01)
    otherwise                                                pass

A receipt with no such exception also flags if every reading of its unresolved recipient types flags
it: see inevitable.py. That is a project decision too, the only flag with no active exception, and it
adds no finding, so the rules stay unresolved and the receipt stays undecided.

EXC-01 says nothing of incomplete evidence. Sending it to review is this project's decision, so that
missing information never reads as compliance. A flagged receipt can still be undecided: decided
says so, whatever the disposition is. Unresolved, replaced, waived, excused and uncitable groups are
not active exceptions and never flag a receipt.

The severities come only from the ruleset, and its directive must map each one exactly once. Before
a run is returned, check_run confirms it holds together. That only checks its structure: it works out
no disposition of its own, and a failure raises.
"""

from collections.abc import Iterable, Mapping
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict

from expense_validator.engine.approvals import resolve_approvals
from expense_validator.engine.carveouts import resolve_carveouts
from expense_validator.engine.checks import run_checks
from expense_validator.engine.helpers import ReceiptContext, build_context
from expense_validator.engine.inevitable import InevitableViolation, prove_violation
from expense_validator.engine.models import Disposition, Receipt
from expense_validator.engine.replacements import resolve_replacements
from expense_validator.engine.report import OpenQuestion, ReceiptReport, ReportGroup, build_report
from expense_validator.engine.uncertainty import AssessedContext, assess_context
from expense_validator.policy.schema import ProcessRule, RuleSet, SetParam
from expense_validator.policy.vocabulary import Severity

AUDIT_RESOLUTIONS = ("replaced", "waived", "excused", "uncitable")
DISPOSITION_RULE = "EXC-01"
SEVERITY_PARAMS = ("flag_severities", "review_severities")
PROJECT_DECISION = "project_decision"

ReasonCode = Literal[
    "exception_flags",
    "inevitable_violation",
    "exception_reviews",
    "review_signal",
    "uncertainty",
    "incomplete_evidence",
    "lines_do_not_account_for_total",
    "nothing_found",
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class DispositionReason(_Strict):
    """One reason behind a disposition, and where the reason comes from."""

    code: ReasonCode
    basis: str  # the rule that says so, or project_decision
    rule_ids: tuple[str, ...] = ()


class ReceiptEvaluation(_Strict):
    customer_id: str
    ruleset_version: str
    receipt_id: str
    disposition: Disposition
    decided: bool
    reasons: tuple[DispositionReason, ...]
    report: ReceiptReport
    inevitable: InevitableViolation | None = None  # why it flags when no active exception does

    @property
    def active_exceptions(self) -> tuple[ReportGroup, ...]:
        return self.report.exceptions

    @property
    def review_signals(self) -> tuple[ReportGroup, ...]:
        return self.report.review_signals

    @property
    def uncertainty(self) -> ReportGroup | None:
        return self.report.uncertainty

    @property
    def open_questions(self) -> tuple[OpenQuestion, ...]:
        return self.report.open_questions

    @property
    def unresolved(self) -> tuple[ReportGroup, ...]:
        return self.report.of("exception", "unresolved")

    @property
    def audit_history(self) -> tuple[ReportGroup, ...]:
        return tuple(group for group in self.report.groups if group.resolution in AUDIT_RESOLUTIONS)


class EvaluationRun(_Strict):
    customer_id: str
    ruleset_version: str
    context_complete: bool
    results: tuple[ReceiptEvaluation, ...]  # in receipt ID order

    def result_for(self, receipt_id: str) -> ReceiptEvaluation:
        for item in self.results:
            if item.receipt_id == receipt_id:
                return item
        raise ValueError(f"{receipt_id}: is not in this run")


def evaluate_receipts(
    customer_id: str,
    receipts: Iterable[Receipt | Mapping[str, object]],
    ruleset: RuleSet,
    fields: Mapping[str, dict],
    *,
    complete: bool,
) -> EvaluationRun:
    """All one customer's receipts to consider. complete says whether any that matter may be missing.

    Every receipt is admitted here, including a Receipt already built. Pass each customer separately:
    receipts carry no customer, so this cannot tell if another's have been mixed in.
    """
    if not isinstance(complete, bool):
        raise TypeError(f"complete must be True or False, not {complete!r}")
    directive = disposition_directive(ruleset)
    context = build_context(customer_id, receipts, fields, complete=complete)
    assessed = assess_context(context, ruleset, fields)

    def flagging_rules(reading: ReceiptContext, receipt_id: str) -> tuple[str, ...]:
        flags = set(directive.params["flag_severities"].values)
        report = _report_for(assess_context(reading, ruleset, fields), receipt_id, ruleset, fields)
        return tuple(sorted(group.rule_id for group in report.exceptions if group.severity in flags))

    results = []
    for assessment in assessed.assessments:
        report = _report_for(assessed, assessment.receipt_id, ruleset, fields)
        disposition, reasons = decide_disposition(report, directive)
        inevitable = None
        if disposition != "flag":
            inevitable = prove_violation(assessment.receipt_id, context, assessed, fields, flagging_rules)
        if inevitable is not None:
            disposition = "flag"
            reasons = (*reasons, DispositionReason(code="inevitable_violation", basis=PROJECT_DECISION, rule_ids=inevitable.rule_ids))
        results.append(
            ReceiptEvaluation(
                customer_id=customer_id,
                ruleset_version=ruleset.ruleset_version,
                receipt_id=assessment.receipt_id,
                disposition=disposition,
                decided=report.decided,
                reasons=reasons,
                report=report,
                inevitable=inevitable,
            )
        )
    run = EvaluationRun(
        customer_id=customer_id,
        ruleset_version=ruleset.ruleset_version,
        context_complete=complete,
        results=tuple(results),
    )
    check_run(run, [receipt.receipt_id for receipt in context.receipts], directive, ruleset)
    return run


def _report_for(assessed: AssessedContext, receipt_id: str, ruleset: RuleSet, fields: Mapping[str, dict]) -> ReceiptReport:
    view = assessed.assessment_for(receipt_id).evaluation_receipt
    checks = run_checks(view, ruleset, fields, assessed.evaluation)
    replacements = resolve_replacements(view, checks, ruleset, fields)
    approvals = resolve_approvals(view, replacements, ruleset)
    carveouts = resolve_carveouts(view, approvals, ruleset, fields, assessed.evaluation)
    return build_report(assessed, receipt_id, carveouts, ruleset, fields)


def decide_disposition(report: ReceiptReport, directive: ProcessRule) -> tuple[Disposition, tuple[DispositionReason, ...]]:
    """Computed, not judged: the same report always gives the same disposition and reasons."""
    flags = set(directive.params["flag_severities"].values)
    reviews = set(directive.params["review_severities"].values)
    for group in report.exceptions:
        if group.severity not in flags | reviews:
            raise ValueError(f"{group.rule_id}: {directive.rule_id} gives no disposition for severity {group.severity}")
    flagged = tuple(group.rule_id for group in report.exceptions if group.severity in flags)
    low = tuple(group.rule_id for group in report.exceptions if group.severity not in flags)
    reasons = []
    if flagged:
        reasons.append(DispositionReason(code="exception_flags", basis=directive.rule_id, rule_ids=flagged))
    if low:
        reasons.append(DispositionReason(code="exception_reviews", basis=directive.rule_id, rule_ids=low))
    if report.review_signals:
        rule_ids = tuple(sorted({group.rule_id for group in report.review_signals}))
        reasons.append(DispositionReason(code="review_signal", basis=directive.rule_id, rule_ids=rule_ids))
    if report.uncertainty is not None:
        reasons.append(DispositionReason(code="uncertainty", basis=report.uncertainty.rule_id, rule_ids=(report.uncertainty.rule_id,)))
    if not report.monetary.lines_account_for_total:
        reasons.append(DispositionReason(code="lines_do_not_account_for_total", basis=PROJECT_DECISION))
    if not report.decided:
        unresolved = {group.rule_id for group in report.of("exception", "unresolved")}
        still_open = sorted(unresolved | {item.rule_id for item in report.open_questions})
        # Signals, or lines that leave out part of the total, already explain a receipt that is not decided.
        if still_open or (report.uncertainty is None and report.monetary.lines_account_for_total):
            reasons.append(DispositionReason(code="incomplete_evidence", basis=PROJECT_DECISION, rule_ids=tuple(still_open)))
    if flagged:
        return "flag", tuple(reasons)
    if reasons:
        return "review", tuple(reasons)
    return "pass", (DispositionReason(code="nothing_found", basis=directive.rule_id),)


def disposition_directive(ruleset: RuleSet) -> ProcessRule:
    """The one active disposition directive, once it is known to map every severity exactly once."""
    active = [
        rule
        for rule in ruleset.rules
        if isinstance(rule, ProcessRule) and rule.directive == "disposition" and rule.status == "active"
    ]
    if len(active) != 1:
        raise ValueError(f"needs exactly one active disposition directive, found {len(active)}")
    directive = active[0]
    if directive.rule_id != DISPOSITION_RULE:
        raise ValueError(f"{directive.rule_id}: the disposition directive must be {DISPOSITION_RULE}")
    supported = set(get_args(Severity))
    mapped: list[str] = []
    for name in SEVERITY_PARAMS:
        param = directive.params.get(name)
        values = param.values if isinstance(param, SetParam) else None
        if not isinstance(values, tuple) or not values or not all(isinstance(value, str) for value in values):
            raise ValueError(f"{directive.rule_id}: {name} must be a set of severities")
        mapped += values
    unknown = sorted(set(mapped) - supported)
    if unknown:
        raise ValueError(f"{directive.rule_id}: maps severities that do not exist: {unknown}")
    twice = sorted({value for value in mapped if mapped.count(value) > 1})
    if twice:
        raise ValueError(f"{directive.rule_id}: maps a severity more than once: {twice}")
    unmapped = sorted(supported - set(mapped))
    if unmapped:
        raise ValueError(f"{directive.rule_id}: does not map every severity: {unmapped}")
    return directive


def check_run(run: EvaluationRun, receipt_ids: Iterable[str], directive: ProcessRule, ruleset: RuleSet) -> None:
    """Confirms a run holds together, raising if not. This works out no disposition of its own."""
    expected = sorted(receipt_ids)
    found = sorted(result.receipt_id for result in run.results)
    if found != expected:
        raise ValueError(f"needs one result per receipt: expected {expected}, found {found}")
    known_rules = {rule.rule_id for rule in ruleset.rules}
    for result in run.results:
        _check_identity(result, run)
        _check_outcome(result, directive)
        _check_reasons(result, directive, known_rules)


def _check_identity(result: ReceiptEvaluation, run: EvaluationRun) -> None:
    name = result.receipt_id
    if result.customer_id != run.customer_id or result.ruleset_version != run.ruleset_version:
        raise ValueError(f"{name}: its customer or ruleset version is not the run's")
    report = result.report
    if (report.customer_id, report.receipt_id, report.ruleset_version) != (result.customer_id, name, result.ruleset_version):
        raise ValueError(f"{name}: its report is for another customer, receipt or ruleset version")
    if result.decided != report.decided:
        raise ValueError(f"{name}: decided does not agree with its report")
    for group in report.groups:
        if (group.customer_id, group.receipt_id) != (result.customer_id, name):
            raise ValueError(f"{name}: group {group.group_id} is for another customer or receipt")
        if any(finding.receipt_id != name for finding in group.sources):
            raise ValueError(f"{name}: group {group.group_id} holds a finding for another receipt")


def _check_outcome(result: ReceiptEvaluation, directive: ProcessRule) -> None:
    name = result.receipt_id
    flags = set(directive.params["flag_severities"].values)
    flagging = [group.rule_id for group in result.active_exceptions if group.severity in flags]
    if result.disposition == "pass":
        if not result.decided:
            raise ValueError(f"{name}: a pass needs a fully decided report")
        if result.active_exceptions or result.review_signals or result.uncertainty is not None:
            raise ValueError(f"{name}: a pass cannot have an active exception, review signal or uncertainty")
    elif result.disposition == "flag" and not flagging and result.inevitable is None:
        raise ValueError(f"{name}: a flag needs an active exception of a flag severity, or a proof one is inevitable")
    elif result.disposition == "review" and flagging:
        raise ValueError(f"{name}: a review cannot have an active exception of a flag severity: {flagging}")
    monetary = result.report.monetary
    if result.decided and not monetary.lines_account_for_total:
        raise ValueError(f"{name}: a decided receipt cannot have lines that do not account for its total")
    if not monetary.lines_account_for_total and not any(reason.code == "lines_do_not_account_for_total" for reason in result.reasons):
        raise ValueError(f"{name}: lines that do not account for its total need their reason")
    if result.disposition == "pass" and "unverified" in (monetary.fx_rate, monetary.line_conversion, monetary.total_conversion, monetary.line_sum):
        raise ValueError(f"{name}: a pass cannot rest on an amount that could not be verified")
    if result.inevitable is not None:
        # Only uncertainty about which rule applies can need a proof, and it can only ever add a flag.
        if result.decided:
            raise ValueError(f"{name}: a decided receipt cannot rest on a proof that a rule is not known")
        if flagging:
            raise ValueError(f"{name}: a proof is only for a receipt with no active exception that flags")
        if result.disposition != "flag":
            raise ValueError(f"{name}: a proof that a violation is inevitable must flag")
        if not any(reason.code == "inevitable_violation" and reason.rule_ids == result.inevitable.rule_ids for reason in result.reasons):
            raise ValueError(f"{name}: a proof needs its reason")
    elif any(reason.code == "inevitable_violation" for reason in result.reasons):
        raise ValueError(f"{name}: an inevitable violation needs its proof")


def _check_reasons(result: ReceiptEvaluation, directive: ProcessRule, known_rules: set[str]) -> None:
    name = result.receipt_id
    if not result.reasons:
        raise ValueError(f"{name}: needs at least one reason")
    uncertainty = result.uncertainty.rule_id if result.uncertainty is not None else None
    allowed = {
        "exception_flags": directive.rule_id,
        "inevitable_violation": PROJECT_DECISION,
        "exception_reviews": directive.rule_id,
        "review_signal": directive.rule_id,
        "uncertainty": uncertainty,
        "incomplete_evidence": PROJECT_DECISION,
        "lines_do_not_account_for_total": PROJECT_DECISION,
        "nothing_found": directive.rule_id,
    }
    for reason in result.reasons:
        if reason.basis != allowed[reason.code]:
            raise ValueError(f"{name}: {reason.code} cannot rest on {reason.basis}")
        unknown = sorted(set(reason.rule_ids) - known_rules)
        if unknown:
            raise ValueError(f"{name}: {reason.code} names rules not in the ruleset: {unknown}")
