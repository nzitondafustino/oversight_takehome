"""Runs the cases in cases/ingestion, and checks ingestion against the real data and the real code."""

import ast
import copy
import json
import sys
from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from receipt_rows import ROWS, without_answers
from yaml_cases import exact, load_cases, selected

import expense_validator
from expense_validator.compiler.validate import read_fields
from expense_validator.config import ROOT, load_config
from expense_validator.engine.evaluation import evaluate_receipts
from expense_validator.engine.models import CLASSIFIED_FIELDS, LineItem, Receipt
from expense_validator.ingestion import (
    IngestedReceipt,
    IngestionError,
    IngestionMode,
    ingest_record,
    iter_jsonl,
    iter_jsonl_lines,
)
from expense_validator.policy.schema import RuleSet

DATA = ROOT / "data" / "receipts.jsonl"
PATHS = load_config().paths
FIELDS = read_fields(PATHS.rule_fields.read_text())
RULESET = RuleSet.from_json(PATHS.ruleset.read_text())
EVALUATED = list(iter_jsonl(DATA, IngestionMode.EVALUATION))
SOURCE = Path(expense_validator.__file__).parent


def _write_lines(path: Path, lines: list) -> Path:
    encoded = [
        line if isinstance(line, bytes) else line.encode() if isinstance(line, str) else json.dumps(line).encode()
        for line in lines
    ]
    path.write_bytes(b"\n".join(encoded) + b"\n")
    return path


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in _all_keys(item)}
    if isinstance(value, (list, tuple)):
        return {key for item in value for key in _all_keys(item)}
    return set()


def _answer_texts(row: dict) -> list[str]:
    """Answer text long enough that finding it in a receipt can only mean it leaked."""
    labels = row["labels"]
    return [row["notes"], *(found["reason"] for found in labels["expected_exceptions"] + labels["review_signals"])]


@pytest.mark.parametrize("case", load_cases("ingestion", "ingested"))
def test_a_record_is_ingested_and_left_unchanged(case):
    record = copy.deepcopy(case["record"])
    item = ingest_record(record, case["mode"])
    assert record == case["record"]
    expected = case["expected"]
    assert exact(selected(item.receipt.model_dump(mode="json"), expected["receipt"])) == exact(expected["receipt"])
    if expected["ground_truth"] is None:
        assert item.ground_truth is None
    else:
        found = selected(item.ground_truth.model_dump(mode="json"), expected["ground_truth"])
        assert exact(found) == exact(expected["ground_truth"])


@pytest.mark.parametrize("case", load_cases("ingestion", "refused"))
def test_a_record_is_refused_and_left_unchanged(case):
    record = copy.deepcopy(case["record"])
    with pytest.raises(ValueError, match=case["expected"]):
        ingest_record(record, case["mode"])
    assert record == case["record"]


@pytest.mark.parametrize("case", load_cases("ingestion", "secrets_refused"))
def test_a_refusal_never_quotes_an_answer_value(case):
    with pytest.raises(IngestionError, match=case["expected"]) as error:
        ingest_record(case["record"], case["mode"])
    assert "SECRET" not in str(error.value)
    assert error.value.__cause__ is None


@pytest.mark.parametrize("case", load_cases("ingestion", "streamed"))
def test_lines_are_streamed(case, tmp_path):
    items = list(iter_jsonl(_write_lines(tmp_path / "in.jsonl", case["lines"]), case["mode"]))
    found = [selected(item.receipt.model_dump(mode="json"), names) for item, names in zip(items, case["expected"]["receipts"])]
    assert len(items) == len(case["expected"]["receipts"])
    assert exact(found) == exact(case["expected"]["receipts"])


@pytest.mark.parametrize("case", load_cases("ingestion", "stream_refused"))
def test_a_refused_line_stops_the_stream_and_is_located(case, tmp_path):
    path = _write_lines(tmp_path / "in.jsonl", case["lines"])
    with pytest.raises(IngestionError, match=case["expected"]) as error:
        list(iter_jsonl(path, case["mode"]))
    assert str(path) in str(error.value)
    assert error.value.source == str(path)
    assert isinstance(error.value.line_number, int)


def test_all_the_real_receipts_are_ingested():
    assert len(EVALUATED) == len(ROWS) == 32
    for item, row in zip(EVALUATED, ROWS):
        assert isinstance(item.receipt, Receipt)
        assert item.receipt.receipt_id == row["receipt_id"]


def test_real_receipt_labels_are_extracted_exactly():
    for item, row in zip(EVALUATED, ROWS):
        truth = item.ground_truth.model_dump(mode="json")
        assert truth["receipt_id"] == row["receipt_id"]
        assert truth["disposition"] == row["labels"]["disposition"]
        assert truth["expected_exceptions"] == row["labels"]["expected_exceptions"]
        assert truth["review_signals"] == row["labels"]["review_signals"]
        assert truth["notes"] == row["notes"]


def test_real_line_labels_are_extracted_by_line_id():
    for item, row in zip(EVALUATED, ROWS):
        expected = {line["line_id"]: line["labels"] for line in row["line_items"]}
        assert item.ground_truth.model_dump(mode="json")["line_labels"] == expected
        assert list(item.ground_truth.line_labels) == [line.line_id for line in item.receipt.line_items]


def test_no_answer_reaches_a_real_sanitized_receipt():
    for item, row in zip(EVALUATED, ROWS):
        dumped = item.receipt.model_dump(mode="json")
        assert not _all_keys(dumped) & {"labels", "notes", "expected_exceptions", "review_signals", "disposition"}
        for line in item.receipt.line_items:
            assert all(getattr(line, name) is None for name in CLASSIFIED_FIELDS)
        text = item.receipt.model_dump_json()
        assert not [answer for answer in _answer_texts(row) if answer in text]


def test_a_receipt_has_nowhere_to_hold_an_answer():
    assert not {"labels", "notes", "ground_truth"} & (set(Receipt.model_fields) | set(LineItem.model_fields))


def test_a_sanitized_receipt_matches_the_real_row_without_its_answers():
    for item, row in zip(EVALUATED, ROWS):
        assert item.receipt == Receipt.model_validate(without_answers(row))


def test_production_takes_each_real_receipt_once_its_answers_are_gone():
    for item, row in zip(EVALUATED, ROWS):
        ingested = ingest_record(without_answers(row), IngestionMode.PRODUCTION)
        assert ingested.ground_truth is None
        assert ingested.receipt == item.receipt


def test_production_refuses_every_real_labelled_record():
    for row in ROWS:
        with pytest.raises(IngestionError, match="not allowed in production"):
            ingest_record(row, IngestionMode.PRODUCTION)


def test_real_records_are_not_changed():
    for row in ROWS:
        before = copy.deepcopy(row)
        ingest_record(row, IngestionMode.EVALUATION)
        assert row == before


def test_the_engine_gets_the_same_results_from_sanitized_receipts():
    from_ingestion = evaluate_receipts("c", [item.receipt for item in EVALUATED], RULESET, FIELDS, complete=True)
    from_rows = evaluate_receipts("c", [without_answers(row) for row in ROWS], RULESET, FIELDS, complete=True)
    assert [run.receipt_id for run in from_ingestion.results] == sorted(row["receipt_id"] for row in ROWS)
    assert from_ingestion == from_rows


def test_refusals_never_quote_an_answer():
    answers = {
        "labels": {"expected_exceptions": [], "review_signals": [], "disposition": "SECRET-DISPOSITION"},
        "notes": "SECRET-NOTE",
    }
    row = without_answers(ROWS[0])
    records = [
        ({**row, **answers}, IngestionMode.PRODUCTION),
        ({**ROWS[0], **answers}, IngestionMode.EVALUATION),
        ({**row, "employee_country": "lower", **answers}, IngestionMode.EVALUATION),
    ]
    for record, mode in records:
        with pytest.raises(IngestionError) as error:
            ingest_record(record, mode)
        assert "SECRET" not in str(error.value)
        assert error.value.__cause__ is None


def test_results_cannot_be_changed():
    item = EVALUATED[0]
    with pytest.raises(FrozenInstanceError):
        item.ground_truth = None
    with pytest.raises(ValidationError):
        item.ground_truth.disposition = "flag"
    with pytest.raises(TypeError):
        item.ground_truth.line_labels["new"] = None
    with pytest.raises(ValidationError):
        item.receipt.receipt_id = "other"


def test_ground_truth_is_not_tied_to_the_record_it_came_from():
    row = copy.deepcopy(ROWS[0])
    item = ingest_record(row, IngestionMode.EVALUATION)
    before = item.ground_truth.model_dump()
    row["labels"]["disposition"] = "changed"
    row["line_items"][0]["labels"]["category"] = "changed"
    assert item.ground_truth.model_dump() == before


def test_lines_are_read_only_as_receipts_are_asked_for():
    consumed = []

    def lines():
        for row in ROWS:
            consumed.append(row["receipt_id"])
            yield json.dumps(row)

    stream = iter_jsonl_lines(lines(), IngestionMode.EVALUATION)
    assert consumed == []
    next(stream)
    assert consumed == [ROWS[0]["receipt_id"]]


def test_receipts_before_a_bad_line_are_delivered_first():
    stream = iter_jsonl_lines([json.dumps(ROWS[0]), "{broken"], IngestionMode.EVALUATION, source="feed")
    assert next(stream).receipt.receipt_id == ROWS[0]["receipt_id"]
    with pytest.raises(IngestionError, match="^feed:2: malformed JSON") as error:
        next(stream)
    assert (error.value.source, error.value.line_number) == ("feed", 2)


def test_a_line_separator_inside_a_description_does_not_split_a_record(tmp_path):
    row = without_answers(ROWS[0])
    line = {**row["line_items"][0], "description_raw": "first\u2028second\u0085third"}
    path = tmp_path / "in.jsonl"
    path.write_text(json.dumps({**row, "line_items": [line]}, ensure_ascii=False) + "\n", encoding="utf-8")
    items = list(iter_jsonl(path, IngestionMode.PRODUCTION))
    assert [item.receipt.line_items[0].description_raw for item in items] == [line["description_raw"]]


def test_an_unknown_mode_is_not_reported_as_malformed_json():
    with pytest.raises(ValueError, match="not a valid IngestionMode") as error:
        list(iter_jsonl_lines([json.dumps(ROWS[0])], "staging"))
    assert not isinstance(error.value, IngestionError)


def test_a_mapping_and_a_stream_agree_on_a_decimal():
    line = json.dumps(without_answers(ROWS[0]))
    from_stream = next(iter_jsonl_lines([line], IngestionMode.PRODUCTION)).receipt
    assert type(from_stream.total_amount) is Decimal
    assert from_stream == ingest_record(json.loads(line), IngestionMode.PRODUCTION).receipt


def _imports(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module)
    return found


def test_nothing_downstream_can_import_ingestion():
    for package in ("engine", "policy", "compiler"):
        for path in (SOURCE / package).rglob("*.py"):
            assert not [name for name in _imports(path) if name.startswith("expense_validator.ingestion")], path


def test_ingestion_needs_no_model_provider():
    allowed = {"pydantic", "expense_validator"} | set(sys.stdlib_module_names)
    for path in (SOURCE / "ingestion").rglob("*.py"):
        assert {name.split(".")[0] for name in _imports(path)} <= allowed, path
