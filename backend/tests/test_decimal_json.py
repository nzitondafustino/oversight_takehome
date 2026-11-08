"""Request bodies keep exact decimals, as the core's JSONL ingestion does."""

import json
from decimal import Decimal

import pytest
from conftest import case_ids, create_ruleset, load_cases, receipt
from expense_validator import ExpenseValidator

from expense_api.api.json_body import parse_json

CASES = load_cases("json_bodies")


@pytest.mark.parametrize("case", CASES, ids=case_ids(CASES))
def test_a_body_decodes_as_the_core_reads_json(case):
    if case.get("refused"):
        with pytest.raises(json.JSONDecodeError):
            parse_json(case["body"].encode())
        return
    value = parse_json(case["body"].encode())["value"]
    if "decimal" in case:
        assert type(value) is Decimal and str(value) == case["decimal"]
    else:
        assert type(value) is int and value == case["integer"]


def test_a_receipt_reaches_the_validator_with_exact_amounts(make_client):
    seen = []

    class Recording(ExpenseValidator):
        def validate_batch(self, receipts, **choices):
            seen.extend(receipts)
            return super().validate_batch(receipts, **choices)

    client = make_client(build_validator=Recording)
    body = json.dumps({"ruleset_id": create_ruleset(client)["ruleset_id"], "receipts": [receipt("RCP-001")], "context_complete": True, "preview": True})
    body = body.replace('"total_amount": 48.2,', '"total_amount": 48.20,')  # as a JSON number with its printed precision

    assert client.post("/v1/validate", content=body, headers={"content-type": "application/json"}).status_code == 201
    total = seen[0]["total_amount"]
    assert type(total) is Decimal and str(total) == "48.20"
    assert type(seen[0]["line_items"][0]["quantity"]) is int
