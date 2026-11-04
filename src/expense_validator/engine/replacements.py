"""Decides which candidate findings another rule has replaced, at the level the ruleset declares.

    receipt  the replacing rule applies to this receipt, whether or not it caught anything
    line     the replacing rule caught this line, so only that line's finding goes
    finding  the replacing rule's finding established this target, so a declared target is only
             eligible: TIME-03 replaces the threshold actually split around, not all it lists

Candidates are kept as they were. A replaced finding is reported apart, with what replaced it.

A replacement that depends on something not known is possible, not confirmed, so the finding stays
active and the result is not complete: the replacing rule may be pending or undetermined, or its
gate may read a value that is missing. A rule that is confirmed out of play no longer needs
deciding, so its own pending or undetermined entries are set aside as moot.

This decides nothing else. References, carve-outs, uncertainty and disposition come later.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from expense_validator.engine.checks import CheckResult, PendingHelper
from expense_validator.engine.facts import receipt_facts
from expense_validator.engine.helpers import GIFT_RULE, ITEMIZATION_RULE, Undetermined
from expense_validator.engine.models import Finding, Receipt
from expense_validator.policy.evaluate import evaluate
from expense_validator.policy.schema import CheckRule, RuleSet, walk

# When several rules replace one finding, the first level here decides which is reported.
LEVELS = ("receipt", "line", "finding")
# These fire only when one receipt is above the threshold, which is what disproves a split around it,
# so an open TIME-03 cannot possibly replace a finding of theirs. A meal cap is per day, so it can.
BROKEN_BY_ONE_RECEIPT = frozenset({ITEMIZATION_RULE, GIFT_RULE})


@dataclass(frozen=True)
class ReplacedFinding:
    """finding is the candidate marked replaced, with everything else as it was."""

    finding: Finding
    replaced_by: str
    level: str
    reason: str


@dataclass(frozen=True)
class PossibleReplacement:
    """This finding stays active: whether the rule replaces it is not known yet."""

    finding: Finding
    possibly_by: str
    level: str
    reason: str


@dataclass(frozen=True)
class ReplacementResult:
    candidates: tuple[Finding, ...]  # every finding as it came in
    active: tuple[Finding, ...]  # not replaced, including those possibly replaced
    replaced: tuple[ReplacedFinding, ...]
    possible: tuple[PossibleReplacement, ...]
    pending_helpers: tuple[PendingHelper, ...]
    undetermined: tuple[Undetermined, ...]
    moot_pending_helpers: tuple[PendingHelper, ...]
    moot_undetermined: tuple[Undetermined, ...]

    @property
    def unaffected(self) -> tuple[Finding, ...]:
        possible = [item.finding for item in self.possible]
        return tuple(finding for finding in self.active if finding not in possible)

    @property
    def complete(self) -> bool:
        return not (self.pending_helpers or self.undetermined or self.possible)


@dataclass(frozen=True)
class _Claim:
    """A rule replacing, or possibly replacing, a target: on these lines, or all of them if None."""

    target: str
    line_ids: frozenset[str] | None
    by: str
    level: str
    reason: str

    def covers(self, rule_id: str, line_ids: tuple[str, ...]) -> bool:
        return rule_id == self.target and (self.line_ids is None or bool(self.line_ids & set(line_ids)))


def resolve_replacements(
    receipt: Receipt, result: CheckResult, ruleset: RuleSet, fields: Mapping[str, dict]
) -> ReplacementResult:
    """result must be for this receipt, from a run on this ruleset."""
    if any(finding.receipt_id != receipt.receipt_id for finding in result.findings):
        raise ValueError(f"{receipt.receipt_id}: has findings for another receipt")
    rules = [rule for rule in ruleset.rules if isinstance(rule, CheckRule) and rule.status == "active"]
    _refuse_undeclared_targets(result.findings, rules)
    facts = receipt_facts(receipt, fields)
    confirmed = sorted(_confirmed_claims(rules, result, facts), key=lambda claim: (LEVELS.index(claim.level), claim.by))
    possible = _possible_claims(rules, result, facts)

    active, replaced, maybe = [], [], []
    for finding in result.findings:
        claim = next((item for item in confirmed if item.covers(finding.rule_id, finding.line_ids)), None)
        if claim is not None:
            marked = Finding.model_validate({**finding.model_dump(), "status": "replaced", "resolved_by": claim.by})
            replaced.append(ReplacedFinding(marked, claim.by, claim.level, claim.reason))
            continue
        active.append(finding)
        maybe += [
            PossibleReplacement(finding, item.by, item.level, item.reason)
            for item in possible
            if item.covers(finding.rule_id, finding.line_ids)
        ]

    gone = lambda rule_id, line_id: any(item.covers(rule_id, () if line_id is None else (line_id,)) for item in confirmed)
    moot_pending = tuple(item for item in result.pending_helpers if gone(item.rule_id, None))
    moot_open = tuple(item for item in result.undetermined if gone(item.rule_id, item.line_id))
    return ReplacementResult(
        candidates=result.findings,
        active=tuple(active),
        replaced=tuple(replaced),
        possible=tuple(maybe),
        pending_helpers=tuple(item for item in result.pending_helpers if item not in moot_pending),
        undetermined=tuple(item for item in result.undetermined if item not in moot_open),
        moot_pending_helpers=moot_pending,
        moot_undetermined=moot_open,
    )


def _refuse_undeclared_targets(findings: tuple[Finding, ...], rules: list[CheckRule]) -> None:
    declared = {
        rule.rule_id: {target.rule_id for target in rule.replaces if target.level == "finding"} for rule in rules
    }
    for finding in findings:
        for target in finding.replaces:
            if target.rule_id not in declared.get(finding.rule_id, set()):
                raise ValueError(f"{finding.rule_id}: does not declare {target.rule_id} as a target at finding level")


def _gate_is_open(rule: CheckRule, facts: Mapping[str, object]) -> bool:
    return rule.applies_when is None or evaluate(rule.applies_when, facts, rule.params)


def _confirmed_claims(rules: list[CheckRule], result: CheckResult, facts: Mapping[str, object]) -> list[_Claim]:
    claims = []
    for rule in rules:
        for target in rule.replaces:
            if target.level == "receipt" and _gate_is_open(rule, facts):
                reason = f"{rule.rule_id} applies to this receipt, so {target.rule_id} does not"
                claims.append(_Claim(target.rule_id, None, rule.rule_id, "receipt", reason))
    for finding in result.findings:
        rule = next((item for item in rules if item.rule_id == finding.rule_id), None)
        for target in () if rule is None else rule.replaces:
            if target.level == "line":
                reason = f"{rule.rule_id} caught this line, and replaces {target.rule_id} for it"
                claims.append(_Claim(target.rule_id, frozenset(finding.line_ids), rule.rule_id, "line", reason))
        for target in finding.replaces:
            lines = frozenset(target.line_ids) if target.line_ids else None
            reason = f"{finding.rule_id} established this as the threshold split around"
            claims.append(_Claim(target.rule_id, lines, finding.rule_id, "finding", reason))
    return claims


def _possible_claims(rules: list[CheckRule], result: CheckResult, facts: Mapping[str, object]) -> list[_Claim]:
    pending = {item.rule_id for item in result.pending_helpers}
    open_lines: dict[str, set[str] | None] = {}
    for item in result.undetermined:
        if item.line_id is None:
            open_lines[item.rule_id] = None
        elif open_lines.get(item.rule_id, set()) is not None:
            open_lines.setdefault(item.rule_id, set()).add(item.line_id)
    claims = []
    for rule in rules:
        for target in rule.replaces:
            if target.level == "receipt":
                if not _gate_is_open(rule, facts) and _gate_reads_a_missing_value(rule, facts):
                    reason = f"whether {rule.rule_id} applies to this receipt depends on a missing value"
                    claims.append(_Claim(target.rule_id, None, rule.rule_id, "receipt", reason))
            elif rule.rule_id in pending:
                reason = f"{rule.rule_id} has not been run"
                claims.append(_Claim(target.rule_id, None, rule.rule_id, target.level, reason))
            elif target.level == "finding" and target.rule_id in BROKEN_BY_ONE_RECEIPT:
                continue
            elif rule.rule_id in open_lines and _gate_is_open(rule, facts):
                lines = open_lines[rule.rule_id]
                reason = f"{rule.rule_id} could not be decided"
                claims.append(_Claim(target.rule_id, None if lines is None else frozenset(lines), rule.rule_id, target.level, reason))
    return claims


def _gate_reads_a_missing_value(rule: CheckRule, facts: Mapping[str, object]) -> bool:
    if rule.applies_when is None:
        return False
    return any(facts.get(node.field) is None for node in walk(rule.applies_when) if hasattr(node, "field"))
