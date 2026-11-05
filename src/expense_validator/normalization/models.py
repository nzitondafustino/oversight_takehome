"""A receipt's lines prepared for classification, held beside the receipt rather than added to it.

These predict nothing: no category, restricted flag, confidence, finding or disposition.
"""

from pydantic import BaseModel, ConfigDict, StrictStr, model_validator

from expense_validator.engine.models import Receipt, Text
from expense_validator.normalization.text import normalize_text
from expense_validator.normalization.translation import TranslationStatus


class _Normalized(BaseModel):
    """Refuses unknown keys and cannot be changed once built, as the engine's models do."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class NormalizedLine(_Normalized):
    receipt_id: Text
    line_id: Text
    description_raw: StrictStr
    description_normalized: StrictStr
    language: Text
    translation: Text | None = None
    translation_status: TranslationStatus
    translation_error: Text | None = None

    @model_validator(mode="after")
    def _check_consistent(self):
        if self.description_normalized != normalize_text(self.description_raw):
            raise ValueError(f"{self.line_id}: description_normalized is not the normalized description_raw")
        if (self.translation is not None) != (self.translation_status == "translated"):
            raise ValueError(f"{self.line_id}: a translation goes with the status translated, and only that")
        if (self.translation_error is not None) != (self.translation_status == "failed"):
            raise ValueError(f"{self.line_id}: an error goes with the status failed, and only that")
        return self


class NormalizedReceipt(_Normalized):
    """The original receipt, unchanged, and one normalized line for each of its lines, in order."""

    receipt: Receipt
    lines: tuple[NormalizedLine, ...]

    @property
    def receipt_id(self) -> str:
        return self.receipt.receipt_id

    @model_validator(mode="after")
    def _check_lines_are_the_receipts(self):
        found = [(line.receipt_id, line.line_id, line.description_raw, line.language) for line in self.lines]
        expected = [(self.receipt_id, line.line_id, line.description_raw, line.language) for line in self.receipt.line_items]
        if found != expected:
            raise ValueError(f"{self.receipt_id}: normalized lines must be the receipt's lines, in order")
        return self
