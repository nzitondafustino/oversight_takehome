"""Run the active check rules and report what they could not decide.

Findings are candidates; replacements, references, carve-outs and uncertainty apply later. A result is
complete only when no rule is pending or undetermined.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from expense_validator.engine.facts import item_facts, receipt_facts
from expense_validator.engine.helpers import (
    ReceiptContext,
    Undetermined,
    fields_read_by,
    make_finding,
    require_in_context,
    run_helper,
)
from expense_validator.engine.inputs import allowed_values
from expense_validator.engine.models import Finding, LineItem, Receipt
from expense_validator.policy.evaluate import evaluate_known, evaluate_with_unknowns, matches
from expense_validator.policy.schema import AllOf, CheckRule, RuleSet
from expense_validator.policy.validation import field_errors


@dataclass(frozen=True)
class PendingHelper:
    rule_id: str
    helper: str


@dataclass(frozen=True)
class CheckResult:
    findings: tuple[Finding, ...]
    pending_helpers: tuple[PendingHelper, ...]
    undetermined: tuple[Undetermined, ...]

    @property
    def complete(self) -> bool:
        return not self.pending_helpers and not self.undetermined


def run_checks(
    receipt: Receipt, ruleset: RuleSet, fields: Mapping[str, dict], context: ReceiptContext | None = None
) -> CheckResult:
    """Run every active check rule on an admitted receipt.

    Raises:
        ValueError: If a rule cannot be run as written.
    """
    rules = [rule for rule in ruleset.rules if isinstance(rule, CheckRule) and rule.status == "active"]
    for rule in rules:
        _refuse_invalid_rule(rule, fields)
    if context is not None:
        require_in_context(receipt, context)
    findings, pending, undetermined = [], [], []
    for rule in rules:
        if rule.helper is not None and context is None:
            pending.append(PendingHelper(rule.rule_id, rule.helper))
        elif rule.helper is not None:
            outcome = run_helper(rule, receipt, context, ruleset, fields)
            findings.extend(outcome.findings)
            undetermined.extend(outcome.undetermined)
        elif rule.scope == "receipt":
            _check(rule, receipt, None, receipt_facts(receipt, fields), fields, findings, undetermined)
        else:
            for item in receipt.line_items:
                _check(rule, receipt, item, item_facts(receipt, item, fields), fields, findings, undetermined)
    return CheckResult(tuple(findings), tuple(pending), tuple(undetermined))


def _refuse_invalid_rule(rule: CheckRule, fields: Mapping[str, dict]) -> None:
    """Refuse a rule that would silently never match."""
    problems = field_errors(rule, fields)
    if rule.scope == "receipt":
        problems += [f"a receipt rule cannot read {name}" for name in fields_read_by(rule) if name.startswith("item.")]
    if problems:
        raise ValueError(f"{rule.rule_id}: {'; '.join(problems)}")


def _check(
    rule: CheckRule,
    receipt: Receipt,
    item: LineItem | None,
    facts: Mapping[str, object],
    fields: Mapping[str, dict],
    findings: list[Finding],
    undetermined: list[Undetermined],
) -> None:
    line_id = None if item is None else item.line_id
    if _is_undetermined(rule, facts, fields):
        undetermined.append(Undetermined(rule.rule_id, line_id))
    elif _turns_on_missing_usd(rule, facts, fields):
        undetermined.append(Undetermined(rule.rule_id, line_id, "a USD amount it compares is not available"))
    elif matches(rule, facts):
        findings.append(make_finding(rule, receipt, facts, item))


def _turns_on_missing_usd(rule: CheckRule, facts: Mapping[str, object], fields: Mapping[str, dict]) -> bool:
    usd_fields = {name for name, field in fields.items() if isinstance(field, Mapping) and field.get("unit") == "usd"}
    # Whether the rule catches this, so not its severity cases, which only grade what it caught.
    catches = tuple(condition for condition in (rule.applies_when, rule.select) if condition is not None)
    return evaluate_with_unknowns(AllOf(op="all", conditions=catches), facts, rule.params, usd_fields) is None


def _is_undetermined(rule: CheckRule, facts: Mapping[str, object], fields: Mapping[str, dict]) -> bool:
    """Return whether the rule's outcome depends on a category, or a meal status, that is not known.

    Known facts that rule the rule out settle it, whatever the unknown field holds.
    """
    read = fields_read_by(rule)
    unknown = set()
    if "receipt.is_meal_receipt" in read and facts["receipt.is_meal_receipt"] is None:
        unknown.add("receipt.is_meal_receipt")
    if "item.category" in read and facts["item.category"] not in allowed_values(fields, "item.category"):
        unknown.add("item.category")
    if not unknown:
        return False
    catches = AllOf(op="all", conditions=tuple(condition for condition in (rule.applies_when, rule.select) if condition is not None))
    caught = evaluate_known(catches, facts, rule.params, unknown)
    if caught is not True:
        return caught is None
    # Caught whatever the unknown field holds, but its severity may still turn on it.
    return any(evaluate_known(case.when, facts, rule.params, unknown) is None for case in rule.severity_cases)
