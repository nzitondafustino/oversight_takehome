"""Map helper names to their functions. baggage_allowance is called by the carve-out step instead."""

from collections.abc import Mapping

from expense_validator.engine.facts import receipt_facts
from expense_validator.engine.helpers.common import HelperOutcome
from expense_validator.engine.helpers.context import HelperJob, ReceiptContext, require_in_context
from expense_validator.engine.helpers.documentation import _expense_type_mismatch, _vat_threshold
from expense_validator.engine.helpers.gifts import _gift_cap, _government_official
from expense_validator.engine.helpers.healthcare import _otc_trip_cap
from expense_validator.engine.helpers.meals import _alcohol_per_attendee, _meal_cap
from expense_validator.engine.helpers.splits import _split_transactions
from expense_validator.engine.models import Receipt
from expense_validator.policy.evaluate import evaluate
from expense_validator.policy.schema import CheckRule, RuleSet

HELPERS = {
    "meal_cap": _meal_cap,
    "split_transactions": _split_transactions,
    "gift_cap": _gift_cap,
    "government_official": _government_official,
    "otc_trip_cap": _otc_trip_cap,
    "expense_type_mismatch": _expense_type_mismatch,
    "alcohol_per_attendee": _alcohol_per_attendee,
    "vat_threshold": _vat_threshold,
}


def run_helper(
    rule: CheckRule, receipt: Receipt, context: ReceiptContext, ruleset: RuleSet, fields: Mapping[str, dict]
) -> HelperOutcome:
    helper = HELPERS.get(rule.helper)
    if helper is None:
        raise ValueError(f"{rule.rule_id}: {rule.helper} cannot run on a check rule")
    require_in_context(receipt, context)
    if rule.applies_when is not None and not evaluate(rule.applies_when, receipt_facts(receipt, fields), rule.params):
        return HelperOutcome()
    return helper(HelperJob(rule, receipt, context, ruleset, fields))
