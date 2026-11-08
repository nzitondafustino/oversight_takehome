"""What helpers produce, and the lowest-total calculation they share.

A cap breach must hold at the lowest the total could be, so an unknown line that may be a discount leaves
the cap undetermined. This is a project decision.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal

from expense_validator.engine.models import Evidence, Finding, LineItem, Receipt, ReplacementTarget
from expense_validator.policy.evaluate import evaluate
from expense_validator.policy.schema import CheckRule, walk
from expense_validator.policy.vocabulary import Severity


@dataclass(frozen=True)
class Undetermined:
    rule_id: str
    line_id: str | None  # None for the receipt as a whole
    reason: str = "depends on a category that is missing or outside the catalog"


@dataclass(frozen=True)
class HelperOutcome:
    findings: tuple[Finding, ...] = ()
    undetermined: tuple[Undetermined, ...] = ()


def fields_read_by(rule: CheckRule) -> list[str]:
    """Return every fact the rule reads, in order."""
    return sorted({node.field for condition in rule.conditions() for node in walk(condition) if hasattr(node, "field")})


def make_finding(
    rule: CheckRule,
    receipt: Receipt,
    facts: Mapping[str, object],
    item: LineItem | None = None,
    explanation: str | None = None,
    worked_out: Mapping[str, object] | None = None,
    replaces: tuple[ReplacementTarget, ...] = (),
) -> Finding:
    """Build a finding citing the facts read, then what the helper worked out."""
    default = rule.title if item is None else f"{rule.title}: {item.description_raw}"
    read = [Evidence(fact=name, value=facts[name]) for name in fields_read_by(rule)]
    calculated = [Evidence(fact=f"helper.{name}", value=value) for name, value in (worked_out or {}).items()]
    return Finding(
        rule_id=rule.rule_id,
        outcome=rule.outcome,
        severity=_severity(rule, facts),
        scope=rule.scope,
        receipt_id=receipt.receipt_id,
        line_ids=() if item is None else (item.line_id,),
        replaces=replaces,
        explanation=explanation or default,
        evidence=(*read, *calculated),
        policy_doc=rule.policy_doc,
        source_quote=rule.source_quote,
        provenance=rule.provenance,
    )


def _severity(rule: CheckRule, facts: Mapping[str, object]) -> Severity | None:
    for case in rule.severity_cases:
        if evaluate(case.when, facts, rule.params):
            return case.severity
    return rule.severity


def lowest_total(known: Decimal, unsettled: Iterable[LineItem]) -> Decimal | None:
    """Return the least a total could be, or None if it has no lower bound."""
    lowest = known
    for line in unsettled:
        if line.amount < 0:
            if line.amount_usd is None:
                return None
            lowest += line.amount_usd
    return lowest
