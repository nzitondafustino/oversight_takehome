"""The FastAPI example's request handling, without FastAPI: it is not a dependency of the package."""

import importlib.util
import json

from expense_validator import ExpenseValidator
from expense_validator.config import ROOT

spec = importlib.util.spec_from_file_location("fastapi_app", ROOT / "examples" / "fastapi_app.py")
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)
VALIDATOR = ExpenseValidator.from_config(ROOT / "config.yaml", mode="replay", cache_dir=ROOT / "evaluation" / "final" / "replay")
RAW = json.loads((ROOT / "data" / "receipts.jsonl").read_text().splitlines()[13])  # RCP-014


def _clean(record):
    clean = {name: value for name, value in record.items() if name not in {"labels", "notes"}}
    clean["line_items"] = [{name: value for name, value in line.items() if name != "labels"} for line in record["line_items"]]
    return clean


def test_a_valid_request_returns_the_batch():
    status, body = example.validate_request(VALIDATOR, {"context_complete": True, "receipts": [_clean(RAW)]})
    assert status == 200 and body["results"][0]["receipt_id"] == "RCP-014"
    assert body["results"][0]["disposition"] == "flag"
    json.dumps(body)


def test_bad_requests_are_422_and_never_crash_the_server():
    assert example.validate_request(VALIDATOR, [])[0] == 422
    assert example.validate_request(VALIDATOR, {"receipts": []})[0] == 422
    assert example.validate_request(VALIDATOR, {"context_complete": "yes", "receipts": []})[0] == 422
    status, body = example.validate_request(VALIDATOR, {"context_complete": True, "receipts": [RAW]})
    assert status == 422 and "evaluation-only fields" in body["error"]


def test_importing_the_example_needs_no_fastapi_and_builds_no_app():
    assert not hasattr(example, "app")
