"""Built-in oracle and synthetic scenarios. Deterministic: draws hash the seed and line ID."""

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml

from expense_validator.evaluation.dataset import Dataset
from expense_validator.evaluation.predictions import LinePrediction, PredictionSet, Source

SEEDS = (0, 1, 2)
ERROR_RATE = Decimal("0.10")
HIGH_CONFIDENCE = Decimal("0.95")
LOW_CONFIDENCE = Decimal("0.30")


@dataclass(frozen=True)
class Scenario:
    key: str
    predictions: PredictionSet
    complete: bool = True


def load_oracle_recipients(path: Path) -> Mapping[str, dict]:
    return yaml.safe_load(path.read_text())["recipients"]


def _draw(seed: int, purpose: str, line_id: str) -> Decimal:
    digest = hashlib.sha256(f"{seed}:{purpose}:{line_id}".encode()).hexdigest()
    return Decimal(int(digest[:8], 16)) / Decimal(16**8)


def _lines(dataset: Dataset):
    for receipt_id in sorted(dataset.answers):
        yield from dataset.answers[receipt_id].lines


def _oracle(dataset: Dataset, recipients: Mapping[str, dict], confidence: str | None, with_recipients: bool) -> tuple[LinePrediction, ...]:
    made = []
    for line in _lines(dataset):
        made.append(
            LinePrediction(
                line_id=line.line_id,
                category=line.category,
                restricted=line.restricted,
                confidence=None if confidence is None else Decimal(confidence),
                **(recipients.get(line.line_id, {}) if with_recipients else {}),
            )
        )
    return tuple(made)


def _wrong_category(seed: int, line, categories: list[str]) -> str:
    others = [category for category in categories if category != line.category]
    return others[int(_draw(seed, "which", line.line_id) * len(others))]


def _with_errors(dataset: Dataset, recipients: Mapping[str, dict], categories: list[str], seed: int, error_confidence: Decimal) -> tuple[LinePrediction, ...]:
    made = []
    for line in _lines(dataset):
        wrong = _draw(seed, "error", line.line_id) < ERROR_RATE
        made.append(
            LinePrediction(
                line_id=line.line_id,
                category=_wrong_category(seed, line, categories) if wrong else line.category,
                confidence=error_confidence if wrong else HIGH_CONFIDENCE,
                **recipients.get(line.line_id, {}),
            )
        )
    return tuple(made)


def _mixed(dataset: Dataset, recipients: Mapping[str, dict], seed: int) -> tuple[LinePrediction, ...]:
    return tuple(
        LinePrediction(
            line_id=line.line_id,
            category=line.category,
            restricted=line.restricted,
            confidence=_draw(seed, "confidence", line.line_id).quantize(Decimal("0.01")),
            **recipients.get(line.line_id, {}),
        )
        for line in _lines(dataset)
    )


def built_in_scenarios(dataset: Dataset, recipients: Mapping[str, dict], categories: list[str]) -> list[Scenario]:
    def scenario(key, kind, description, predictions, complete=True, seed=None):
        source = Source(kind=kind, name=key, description=description, seed=seed)
        return Scenario(key, PredictionSet(source=source, predictions=predictions), complete)

    found = [
        scenario("A", "oracle", "Answer categories, stand-in confidence 1, hand-read recipients", _oracle(dataset, recipients, "1", True)),
        scenario("B", "oracle", "Answer categories, stand-in confidence 1, no recipients", _oracle(dataset, recipients, "1", False)),
        scenario("C", "oracle", "Answer categories and hand-read recipients, no confidence", _oracle(dataset, recipients, None, True)),
        scenario("D", "oracle", "Answer categories and hand-read recipients, all at 0.49", _oracle(dataset, recipients, "0.49", True)),
        scenario("E", "none", "No classifier: every line unclassified", ()),
        scenario("F", "oracle", "As A, with complete=False", _oracle(dataset, recipients, "1", True), complete=False),
    ]
    for seed in SEEDS:
        found.append(scenario(f"G{seed}", "synthetic", "Right category on every line, with confidence drawn uniformly", _mixed(dataset, recipients, seed), seed=seed))
        found.append(scenario(f"H{seed}", "synthetic", "10% of lines wrong at random, wrong lines at confidence 0.95: errors the classifier does not know about", _with_errors(dataset, recipients, categories, seed, HIGH_CONFIDENCE), seed=seed))
        found.append(scenario(f"I{seed}", "synthetic", "The same wrong lines, at confidence 0.30: errors the classifier does know about", _with_errors(dataset, recipients, categories, seed, LOW_CONFIDENCE), seed=seed))
    return found


SMOKE_LOW_CONFIDENCE_LINE = "SYN-006-L1"


def smoke_predictions(dataset: Dataset) -> PredictionSet:
    """Return synthetic predictions for the proof smoke dataset."""
    made = [
        LinePrediction(line_id=line.line_id, category=line.category, recipient_name="Xu", confidence="0.30" if line.line_id == SMOKE_LOW_CONFIDENCE_LINE else "0.95")
        for receipt_id in sorted(dataset.answers)
        for line in dataset.answers[receipt_id].lines
    ]
    source = Source(kind="synthetic", name="S", description="Hand-written receipts: right categories, no recipient types")
    return PredictionSet(source=source, predictions=tuple(made))
