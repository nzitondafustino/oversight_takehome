"""Runs the active check rules, and reports what they could not decide.

An item rule gives one finding per line it catches, and a receipt rule one finding for the
receipt, in the order of the ruleset. Findings are candidates, all raised. What takes one out
of play comes later: replacements, references, carve-outs and uncertainty. So an APAC receipt
gets ALC-01 here as well as REG-01.

A result is complete only when every active check rule was decided. Two things stop that:

    pending_helpers  a rule needs a helper, and no ReceiptContext was given to run it with
    undetermined     a rule depends on something not known, such as a category that is missing
                     or outside the catalog

An undetermined rule gives no finding, as that could be false: a `not` on a missing category
is true. It is not reported as passed either, as a quiet rule does not establish compliance. A USD
threshold compared with a USD value that is not available is the same: the comparison would read as
false. It does not stay open where another condition already settles the rule.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from expense_validator.compiler.validate import check_fields
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
from expense_validator.policy.evaluate import evaluate, evaluate_with_unknowns, matches
from expense_validator.policy.schema import AllOf, CheckRule, RuleSet


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
    """The receipt must already be admitted, and in the context if one is given.

    A rule that cannot be run as written is an error. Without a context, helper rules stay pending.
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
    """Such a rule would stay quiet on every receipt, as a comparison of unlike values is false."""
    problems = [issue.message for issue in check_fields(rule, fields)]
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
    read = fields_read_by(rule)
    if "receipt.is_meal_receipt" in read and facts["receipt.is_meal_receipt"] is None:
        return True
    if "item.category" not in read or facts["item.category"] in allowed_values(fields, "item.category"):
        return False
    # A rule that does not apply to this receipt does not apply whatever the category is.
    return rule.applies_when is None or evaluate(rule.applies_when, facts, rule.params)
