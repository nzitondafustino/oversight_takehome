"""Turns raw receipt records into a validated Receipt and, in evaluation, separate answers.

This only checks shape. The engine still admits every receipt it is given, and classification
comes later. Ingestion is stricter than admission in one way: entity is required here, as Receipt
requires it, while admit_receipt derives a missing one from the country. Deriving it needs the
catalog, which belongs to admission.
"""

import json
from collections.abc import Collection, Iterable, Iterator, Mapping
from decimal import Decimal
from pathlib import Path

from pydantic import ValidationError

from expense_validator.engine.models import CLASSIFIED_FIELDS, Receipt
from expense_validator.ingestion.models import GroundTruth, IngestedReceipt, IngestionError, IngestionMode

ANSWER_FIELDS = frozenset({"labels", "notes"})
LINE_ANSWER_FIELDS = frozenset({"labels"})


def iter_jsonl(path: str | Path, mode: IngestionMode) -> Iterator[IngestedReceipt]:
    """Streams a JSONL file one receipt at a time. See iter_jsonl_lines for what stops it."""
    with Path(path).open("rb") as file:
        yield from iter_jsonl_lines(file, mode, source=str(path))


def iter_jsonl_lines(
    lines: Iterable[str | bytes], mode: IngestionMode, *, source: str = "<lines>"
) -> Iterator[IngestedReceipt]:
    """Streams receipts from JSONL lines, stopping at the first record it has to refuse.

    The IngestionError names the source and line. A blank line is no record. A receipt ID and a
    line ID may each appear only once in a stream, as predictions and answers are keyed by them.
    """
    first_seen: dict[str, int] = {}
    line_first_seen: dict[str, int] = {}
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            item = ingest_record(_parse_line(line), mode)
            receipt_id = item.receipt.receipt_id
            if receipt_id in first_seen:
                raise IngestionError(f"receipt_id repeats, first on line {first_seen[receipt_id]}", receipt_id=receipt_id)
            line_ids = [line.line_id for line in item.receipt.line_items]
            repeated = [f"{line_id} (first on line {line_first_seen[line_id]})" for line_id in line_ids if line_id in line_first_seen]
            if repeated:
                raise IngestionError(f"line_id repeats from an earlier record: {repeated}", receipt_id=receipt_id)
        except IngestionError as error:
            raise IngestionError(error.reason, receipt_id=error.receipt_id, source=source, line_number=line_number) from error
        first_seen[receipt_id] = line_number
        line_first_seen.update(dict.fromkeys(line_ids, line_number))
        yield item


def ingest_record(record: Mapping[str, object], mode: IngestionMode) -> IngestedReceipt:
    """One validated receipt from one raw record, which is left as it was."""
    mode = IngestionMode(mode)
    if not isinstance(record, Mapping):
        raise IngestionError(f"expected a JSON object, got {type(record).__name__}")
    receipt_id = record.get("receipt_id")
    receipt_id = receipt_id if isinstance(receipt_id, str) else None

    # Lines must be plain data, or they would pass by the checks below.
    lines = record.get("line_items", [])
    if not isinstance(lines, (list, tuple)) or not all(isinstance(line, Mapping) for line in lines):
        raise IngestionError("line_items must be a list of JSON objects", receipt_id=receipt_id)

    # Presence is enough: even an empty or null answer says this is not production data.
    if mode is IngestionMode.PRODUCTION:
        answers = sorted(ANSWER_FIELDS & set(record)) + _fields_on_lines(lines, LINE_ANSWER_FIELDS)
        if answers:
            raise IngestionError(f"evaluation-only fields are not allowed in production: {answers}", receipt_id=receipt_id)

    # The model allows these because a classified receipt has them, so an answer here would pass
    # validation and reach the engine looking like a classifier's result.
    classified = _fields_on_lines(lines, CLASSIFIED_FIELDS)
    if classified:
        raise IngestionError(f"a raw receipt may not carry classifier fields: {classified}", receipt_id=receipt_id)

    try:
        receipt = Receipt.model_validate(_sanitized(record))
    except ValidationError as error:
        raise IngestionError(f"invalid receipt: {_summary(error)}", receipt_id=receipt_id) from None

    ground_truth = None if mode is IngestionMode.PRODUCTION else _ground_truth(record, receipt)
    return IngestedReceipt(receipt=receipt, ground_truth=ground_truth)


def _parse_line(line: str | bytes) -> object:
    """Numbers stay exact as Decimal. What JSON leaves ambiguous or disallows is refused."""
    try:
        text = line.decode("utf-8") if isinstance(line, bytes) else line
        return json.loads(text, parse_float=Decimal, parse_constant=_refuse_constant, object_pairs_hook=_refuse_repeats)
    except (ValueError, ArithmeticError, RecursionError) as error:
        raise IngestionError(f"malformed JSON: {error}") from error


def _refuse_constant(name: str) -> None:
    raise ValueError(f"{name} is not a JSON number")


def _refuse_repeats(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """json.loads would keep the last of a repeated key and silently drop the others."""
    names = [name for name, _ in pairs]
    repeated = sorted({name for name in names if names.count(name) > 1})
    if repeated:
        raise ValueError(f"repeated keys: {repeated}")
    return dict(pairs)


def _fields_on_lines(lines: Iterable[Mapping[str, object]], names: Collection[str]) -> list[str]:
    return sorted(f"{line.get('line_id')}.{name}" for line in lines for name in names if name in line)


def _without(data: Mapping[str, object], names: Collection[str]) -> dict[str, object]:
    return {name: value for name, value in data.items() if name not in names}


def _sanitized(record: Mapping[str, object]) -> dict[str, object]:
    """A new record with no answers in it."""
    kept = _without(record, ANSWER_FIELDS)
    if "line_items" in kept:
        kept["line_items"] = [_without(line, LINE_ANSWER_FIELDS) for line in kept["line_items"]]
    return kept


def _ground_truth(record: Mapping[str, object], receipt: Receipt) -> GroundTruth:
    """Built after the receipt, so its checks have already refused any repeated line ID."""
    labels = record.get("labels")
    if not isinstance(labels, Mapping):
        raise IngestionError("evaluation requires receipt-level labels", receipt_id=receipt.receipt_id)
    lines = record["line_items"]
    unlabelled = [line["line_id"] for line in lines if "labels" not in line]
    if unlabelled:
        raise IngestionError(f"evaluation requires labels on every line, missing on: {unlabelled}", receipt_id=receipt.receipt_id)
    # These are set below, so one inside labels would be silently overwritten.
    reserved = sorted({"receipt_id", "line_labels", "notes"} & set(labels))
    if reserved:
        raise IngestionError(f"invalid ground truth: labels may not hold {reserved}", receipt_id=receipt.receipt_id)
    try:
        return GroundTruth.model_validate(
            {
                **labels,
                "receipt_id": receipt.receipt_id,
                "line_labels": {line["line_id"]: line["labels"] for line in lines},
                "notes": record.get("notes"),
            }
        )
    except ValidationError as error:
        raise IngestionError(f"invalid ground truth: {_summary(error)}", receipt_id=receipt.receipt_id) from None


def _summary(error: ValidationError) -> str:
    """Field paths and messages only: pydantic's own text would quote the values it was given.

    So a validator on these models must not put a value in its message either.
    """
    problems = []
    for problem in error.errors():
        path = ".".join(str(part) for part in problem["loc"])
        problems.append(f"{path}: {problem['msg']}" if path else problem["msg"])
    return "; ".join(problems)
