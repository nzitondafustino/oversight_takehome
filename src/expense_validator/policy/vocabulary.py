"""Fixed vocabulary for rules: severities, units, helpers and directives."""

from typing import Literal

Severity = Literal["critical", "high", "medium", "low"]
RefKind = Literal["pre_approval", "vp_approval", "manager_approval", "baggage_delay"]
Unit = Literal["usd", "eur", "count", "nights", "hour", "ratio"]

Helper = Literal[
    "meal_cap",
    "split_transactions",
    "gift_cap",
    "government_official",
    "otc_trip_cap",
    "baggage_allowance",
    "expense_type_mismatch",
    "alcohol_per_attendee",
    "vat_threshold",
]

Directive = Literal["disposition", "reference_kinds", "uncertainty", "evidence", "fx_rate", "guidance"]
# The exact params each kind of process rule must carry.
DIRECTIVE_PARAMS = {
    "disposition": {"flag_severities", "review_severities"},
    "reference_kinds": {
        "pre_approval_rules",
        "vp_approval_rules",
        "manager_approval_rules",
        "baggage_delay_rules",
    },
    "uncertainty": {"min_confidence"},
    "evidence": {"whole_receipt_rules"},
    "fx_rate": set(),
    "guidance": set(),
}
# The params whose values are rule IDs, so the RuleSet can check those rules exist.
RULE_ID_PARAMS = {
    "pre_approval_rules",
    "vp_approval_rules",
    "manager_approval_rules",
    "baggage_delay_rules",
    "whole_receipt_rules",
}

# What each helper needs: the rule kind and scope it runs on, and the params it reads, by
# name and unit. A helper with several options lists each one.
HELPER_CONTRACTS = {
    "meal_cap": {
        "kind": "check",
        "scope": "receipt",
        "params": [{"domestic_usd": "usd", "international_usd": "usd"}, {"cap_usd": "usd"}, {"cap_eur": "eur"}],
    },
    "split_transactions": {
        "kind": "check",
        "scope": "receipt",
        "params": [{"min_receipts": "count", "itemization_usd": "usd", "gift_cap_usd": "usd"}],
    },
    "gift_cap": {"kind": "check", "scope": "item", "params": [{"cap_usd": "usd", "named_recipient_from_usd": "usd"}]},
    "government_official": {"kind": "check", "scope": "item", "params": [{}]},
    "otc_trip_cap": {"kind": "check", "scope": "item", "params": [{"trip_cap_usd": "usd"}]},
    "baggage_allowance": {"kind": "carve_out", "params": [{"allowance_usd": "usd", "min_delay_hours": "hour"}]},
    "expense_type_mismatch": {"kind": "check", "scope": "receipt", "params": [{}]},
    "alcohol_per_attendee": {"kind": "check", "scope": "item", "params": [{"cap_usd": "usd"}]},
    "vat_threshold": {"kind": "check", "scope": "receipt", "params": [{"above_eur": "eur"}]},
}
