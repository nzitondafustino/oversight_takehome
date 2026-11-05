"""Reads a labelled dataset, read-only, through evaluation-mode ingestion, and adapts its answers for scoring.

Only Dataset.inputs ever reach the engine. Everything under answers is for scoring.
"""

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from expense_validator.engine.models import Receipt
from expense_validator.ingestion import GroundTruth, IngestionMode, iter_jsonl_lines


@dataclass(frozen=True)
class LineAnswer:
    line_id: str
    category: str
    restricted: bool


@dataclass(frozen=True)
class ReceiptAnswer:
    receipt_id: str
    disposition: str
    exceptions: Mapping[str, dict]  # by rule: line_ids and severity
    signals: frozenset[str]
    lines: tuple[LineAnswer, ...]


@dataclass(frozen=True)
class Dataset:
    customer_id: str
    sha256: str
    inputs: tuple[Receipt, ...]
    answers: Mapping[str, ReceiptAnswer]


def _answer(truth: GroundTruth) -> ReceiptAnswer:
    return ReceiptAnswer(
        receipt_id=truth.receipt_id,
        disposition=truth.disposition,
        exceptions={
            item.rule_id: {"line_ids": sorted(item.line_ids), "severity": item.severity}
            for item in truth.expected_exceptions
        },
        signals=frozenset(item.rule_id for item in truth.review_signals),
        lines=tuple(LineAnswer(line_id, labels.category, labels.restricted) for line_id, labels in truth.line_labels.items()),
    )


def load_dataset(path: Path, customer_id: str) -> Dataset:
    """One customer's receipts. To evaluate another customer, load another dataset and run it apart."""
    raw = path.read_bytes()  # parsed from these same bytes, so the hash describes what was loaded
    items = list(iter_jsonl_lines(raw.split(b"\n"), IngestionMode.EVALUATION, source=str(path)))
    return Dataset(
        customer_id=customer_id,
        sha256=hashlib.sha256(raw).hexdigest(),
        inputs=tuple(item.receipt for item in items),
        answers={item.receipt.receipt_id: _answer(item.ground_truth) for item in items},
    )
