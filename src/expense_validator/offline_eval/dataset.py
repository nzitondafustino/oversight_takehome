"""Reads a labelled dataset, read-only, keeping the receipts and their answers apart.

Only Dataset.inputs ever reach the engine. Everything under answers is for scoring.
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

ANSWER_KEYS = {"labels", "notes"}
LINE_ANSWER_KEYS = {"labels"}


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
    inputs: tuple[dict, ...]
    answers: Mapping[str, ReceiptAnswer]


def _answer(row: dict) -> ReceiptAnswer:
    labels = row["labels"]
    return ReceiptAnswer(
        receipt_id=row["receipt_id"],
        disposition=labels["disposition"],
        exceptions={
            item["rule_id"]: {"line_ids": sorted(item.get("line_ids", [])), "severity": item.get("severity")}
            for item in labels["expected_exceptions"]
        },
        signals=frozenset(item["rule_id"] for item in labels["review_signals"]),
        lines=tuple(
            LineAnswer(line["line_id"], line["labels"]["category"], line["labels"]["restricted"]) for line in row["line_items"]
        ),
    )


def without_answers(row: dict) -> dict:
    kept = {name: value for name, value in row.items() if name not in ANSWER_KEYS}
    lines = [{name: value for name, value in line.items() if name not in LINE_ANSWER_KEYS} for line in row["line_items"]]
    return {**kept, "line_items": lines}


def load_dataset(path: Path, customer_id: str) -> Dataset:
    """One customer's receipts. To evaluate another customer, load another dataset and run it apart."""
    raw = path.read_bytes()
    rows = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    ids = [row["receipt_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path}: receipt IDs repeat")
    return Dataset(
        customer_id=customer_id,
        sha256=hashlib.sha256(raw).hexdigest(),
        inputs=tuple(without_answers(row) for row in rows),
        answers={row["receipt_id"]: _answer(row) for row in rows},
    )
