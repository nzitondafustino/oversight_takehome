"""The rows in data/receipts.jsonl, for tests that run over all the real data."""

import json

from expense_validator.config import ROOT

# Neither of RCP-013's lines says who it was for, so GIFT-04 stays open on it, with gold categories alone.
NO_RECIPIENT_TYPE = {"RCP-013"}
ROWS = [json.loads(line) for line in (ROOT / "data" / "receipts.jsonl").read_text().splitlines()]


def without_answers(row: dict) -> dict:
    kept = {name: value for name, value in row.items() if name not in {"labels", "notes"}}
    items = [{name: value for name, value in item.items() if name != "labels"} for item in row["line_items"]]
    return {**kept, "line_items": items}
