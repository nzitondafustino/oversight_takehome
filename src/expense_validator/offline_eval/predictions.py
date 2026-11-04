"""Classifications to evaluate, with where they came from.

    actual     output of a real classifier, loaded from a file naming its model
    oracle     the answers themselves, standing in for a perfect classifier
    synthetic  made up by a generator, to test how the engine responds
    none       no classification at all

Only the first is ever measured classifier performance. The kind is a claim a file makes: nothing here
can authenticate it, so a file of actual predictions that matches the answers exactly raises a warning.
"""

import json
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from expense_validator.engine.models import CLASSIFIED_FIELDS, Receipt

SourceKind = Literal["actual", "oracle", "synthetic", "none"]
SECONDARY_FIELDS = ("recipient_type", "venue_type", "item_kind", "essential_toiletry")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class Source(_Strict):
    kind: SourceKind
    name: str = Field(pattern=r"\S")
    description: str = Field(pattern=r"\S")
    model: str | None = None
    seed: int | None = None

    @model_validator(mode="after")
    def _check_claim(self):
        if self.kind == "actual" and not self.model:
            raise ValueError("actual predictions must name the model that made them")
        if self.kind != "actual" and self.model:
            raise ValueError(f"{self.kind} classifications have no model: only actual predictions do")
        return self

    @property
    def measured(self) -> bool:
        return self.kind == "actual"


class LinePrediction(_Strict):
    """Values are kept as written, even outside the catalog, as the engine has its own answer to that."""

    line_id: str = Field(pattern=r"\S")
    category: str | None = None
    restricted: StrictBool | None = None
    confidence: Decimal | None = Field(default=None, ge=0, le=1)
    recipient_name: str | None = None
    recipient_type: str | None = None
    venue_type: str | None = None
    item_kind: str | None = None
    essential_toiletry: StrictBool | None = None

    def engine_output(self) -> dict[str, object]:
        given = self.model_dump(exclude={"line_id"}, exclude_none=True)
        return {name: value for name, value in given.items() if name in CLASSIFIED_FIELDS}


class PredictionSet(_Strict):
    customer_id: str = Field(pattern=r"\S")
    source: Source
    predictions: tuple[LinePrediction, ...] = ()

    @model_validator(mode="after")
    def _check_one_per_line(self):
        ids = [item.line_id for item in self.predictions]
        duplicates = sorted({line_id for line_id in ids if ids.count(line_id) > 1})
        if duplicates:
            raise ValueError(f"more than one prediction for: {duplicates}")
        return self

    def by_line(self) -> Mapping[str, LinePrediction]:
        return {item.line_id: item for item in self.predictions}


def load_predictions(path: Path) -> PredictionSet:
    return PredictionSet.model_validate(json.loads(path.read_text()))


def apply_predictions(receipts: Sequence[Receipt], predictions: PredictionSet) -> list[Receipt]:
    """A line with no prediction stays unclassified, and so visibly uncertain. An unknown line is refused."""
    known = {line.line_id for receipt in receipts for line in receipt.line_items}
    unknown = sorted(set(predictions.by_line()) - known)
    if unknown:
        raise ValueError(f"predictions for lines not in this customer's receipts: {unknown}")
    by_line = predictions.by_line()
    return [
        receipt.with_classifications(
            {line.line_id: by_line[line.line_id].engine_output() for line in receipt.line_items if line.line_id in by_line}
        )
        for receipt in receipts
    ]
