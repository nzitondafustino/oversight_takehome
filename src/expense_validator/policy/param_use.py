"""What each rule parameter is compared with, in words a reviewer can check against the policy text.

A declarative rule says it in its own conditions: a param referenced as {"param": name} in applies_when or select
is compared there, by that condition's operator and field. A helper compares its params in code, so each helper
param is described here, next to the contract in vocabulary.py that names it. The descriptions follow the helpers
in engine/helpers exactly; they never change what the engine does.
"""

from collections.abc import Mapping

from expense_validator.policy.vocabulary import HELPER_CONTRACTS

# helper -> param -> what is measured, how it is compared, what it applies to, and the rule whose threshold it is.
HELPER_PARAM_USE: dict[str, dict[str, dict[str, str | None]]] = {
    "split_transactions": {
        "min_receipts": {"measure": "receipts by the same employee, at the same merchant, on the same date", "operator": "at least", "applies_to": "every split this rule looks for", "threshold_of": None},
        "itemization_usd": {"measure": "each receipt's total in USD", "operator": "each at or below, together above", "applies_to": "receipts that are not itemized", "threshold_of": "DOC-01"},
        "gift_cap_usd": {"measure": "each receipt's client-gift total for one named recipient, in USD", "operator": "each at or below, together above", "applies_to": "client gifts that name their recipient, not gifts to government officials", "threshold_of": "GIFT-01"},
    },
    "gift_cap": {
        "cap_usd": {"measure": "the receipt's client-gift total for one recipient, in USD", "operator": "above", "applies_to": "client gifts that name their recipient; gifts to government officials are totalled apart", "threshold_of": None},
        "named_recipient_from_usd": {"measure": "one client-gift line's amount in USD", "operator": "at or above", "applies_to": "a gift line that names no recipient: from this amount the name is required", "threshold_of": None},
    },
    "meal_cap": {
        "domestic_usd": {"measure": "the day's meal spend per person, in USD", "operator": "above", "applies_to": "days whose meals are all in the employee's home currency", "threshold_of": None},
        "international_usd": {"measure": "the day's meal spend per person, in USD", "operator": "above", "applies_to": "days with a meal in another currency", "threshold_of": None},
        "cap_usd": {"measure": "the day's meal spend per person, in USD", "operator": "above", "applies_to": "the meals of the employees this rule covers", "threshold_of": None},
        "cap_eur": {"measure": "the day's meal spend per person, in EUR", "operator": "above", "applies_to": "the meals of the employees this rule covers", "threshold_of": None},
    },
    "otc_trip_cap": {
        "trip_cap_usd": {"measure": "over-the-counter medication across one trip, in USD", "operator": "above", "applies_to": "one employee's OTC medication on one trip", "threshold_of": None},
    },
    "baggage_allowance": {
        "allowance_usd": {"measure": "items bought for one baggage delay, in USD", "operator": "above (not covered)", "applies_to": "purchases sharing a baggage delay reference", "threshold_of": None},
        "min_delay_hours": {"measure": "the baggage delay, in hours", "operator": "at least", "applies_to": "a delay with its reference on file", "threshold_of": None},
    },
    "alcohol_per_attendee": {
        "cap_usd": {"measure": "the receipt's alcohol total per attendee, in USD", "operator": "above", "applies_to": "alcohol at on-premises venues (ALC-01's merchant codes) where ALC-01 does not already apply", "threshold_of": None},
    },
    "vat_threshold": {
        "above_eur": {"measure": "the receipt total in EUR (converted from USD when needed)", "operator": "above", "applies_to": "receipts that need a VAT number", "threshold_of": None},
    },
}


def helper_param_use(helper: str | None, param: str) -> dict[str, str | None] | None:
    """How a helper compares one of its params, or None when the rule has no helper or the helper no such param."""
    return HELPER_PARAM_USE.get(helper or "", {}).get(param)


def referenced_params(condition: Mapping | None) -> set[str]:
    """The params a condition tree compares against, by name."""
    if not isinstance(condition, Mapping):
        return set()
    found = set()
    value = condition.get("value")
    if isinstance(value, Mapping) and isinstance(value.get("param"), str):
        found.add(value["param"])
    for child in condition.get("conditions") or ():
        found |= referenced_params(child)
    if isinstance(condition.get("condition"), Mapping):
        found |= referenced_params(condition["condition"])
    return found


def undescribed_helper_params() -> list[str]:
    """Every helper param the contracts name that has no description here: none, or a description is missing."""
    missing = []
    for helper, contract in HELPER_CONTRACTS.items():
        for option in contract["params"]:
            missing += [f"{helper}.{name}" for name in option if helper_param_use(helper, name) is None]
    return sorted(set(missing))
