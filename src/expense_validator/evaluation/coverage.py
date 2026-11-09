"""Which rules an evaluation exercised, measured on its receipts rather than inferred from the ruleset.

Execution coverage: an active check rule is evaluated on a receipt where its gate (applies_when) holds on the
receipt's facts, or, for a line rule, on at least one line's facts: the engine's own facts and condition
evaluation, on each receipt as admitted with its classified lines. A rule without a gate is evaluated on every
receipt. A missing fact never opens a gate, so this never counts a rule as run on input it could not read. A
carve-out is evaluated where its selector matches a line. A process rule shapes every receipt's outcome
(guidance never runs). Retired and not-evaluable rules never run.

Positive-case coverage is separate: whether the answers hold an example the rule is expected to raise. A rule can
run on many receipts and never meet a positive case, and that gap in the dataset is what this reports.
"""

from collections.abc import Mapping, Sequence

from expense_validator.engine.facts import item_facts, receipt_facts
from expense_validator.engine.models import Receipt
from expense_validator.policy.evaluate import evaluate
from expense_validator.policy.schema import CheckRule, ProcessRule, RuleSet


def _gate_and_subject(rule, receipt: Receipt, fields: Mapping[str, dict]) -> tuple[bool, bool]:
    """Whether the rule's gate held on this receipt, and whether its subject was there: a line its selector matches,
    for a rule that selects lines; otherwise the gate alone (a helper decides for itself what it looks at)."""
    if isinstance(rule, CheckRule) and rule.scope == "receipt":
        facts = receipt_facts(receipt, fields)
        gate = rule.applies_when is None or evaluate(rule.applies_when, facts, rule.params)
        return gate, gate and (rule.select is None or evaluate(rule.select, facts, rule.params))
    gate = subject = False
    for item in receipt.line_items:
        facts = item_facts(receipt, item, fields)
        opened = getattr(rule, "applies_when", None) is None or evaluate(rule.applies_when, facts, rule.params)
        gate = gate or opened
        subject = subject or (opened and (rule.select is None or evaluate(rule.select, facts, rule.params)))
    return gate, subject


def rule_coverage(receipts: Sequence[Receipt], ruleset: RuleSet, fields: Mapping[str, dict]) -> dict:
    """Each rule's execution on these receipts. Positive cases are joined in by the caller, from the answers.

    gate_receipts: receipts where the rule's gate held. subject_receipts: of those, receipts holding what the rule
    examines (a line its selector matches): where it was evaluated against relevant input.
    """
    rules = {}
    for rule in ruleset.rules:
        if rule.status != "active":
            rules[rule.rule_id] = {"kind": rule.kind, "execution": "never_runs", "gate_receipts": 0, "subject_receipts": 0, "note": f"{rule.status}: not applied"}
        elif isinstance(rule, ProcessRule):
            guidance = rule.directive == "guidance"
            every = 0 if guidance else len(receipts)
            rules[rule.rule_id] = {"kind": rule.kind, "execution": "never_runs" if guidance else "every_receipt", "gate_receipts": every, "subject_receipts": every,
                                   "note": "guidance: raises no finding and decides nothing" if guidance else f"process rule ({rule.directive}): shapes every receipt's outcome"}
        else:
            seen = [_gate_and_subject(rule, receipt, fields) for receipt in receipts]
            gate, subject = sum(1 for opened, _ in seen if opened), sum(1 for _, found in seen if found)
            execution = "evaluated" if subject else "gate_only" if gate else "not_evaluated"
            rules[rule.rule_id] = {"kind": rule.kind, "execution": execution, "gate_receipts": gate, "subject_receipts": subject,
                                   "note": "a helper: it examines every receipt its gate admits" if getattr(rule, "helper", None) else None}
    return {"receipts": len(receipts), "rules": rules}
