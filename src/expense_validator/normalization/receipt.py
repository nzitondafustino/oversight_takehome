"""Normalizes every line of a receipt from ingestion, for any classifier to read."""

from expense_validator.engine.models import CLASSIFIED_FIELDS, LineItem, Receipt
from expense_validator.normalization.models import NormalizedLine, NormalizedReceipt
from expense_validator.normalization.text import normalize_text
from expense_validator.normalization.translation import NoOpTranslator, Translator, translate_description


def normalize_receipt(receipt: Receipt, translator: Translator = NoOpTranslator()) -> NormalizedReceipt:
    """One normalized line per line on the receipt, which stays as it was. Translation is off by default.

    Takes a Receipt only, so ground truth has no way in: pass IngestedReceipt.receipt. Classifier
    output is refused, as this step comes first and a value there may not be a classifier's.
    """
    if not isinstance(receipt, Receipt):
        raise TypeError(f"normalize_receipt needs a Receipt from ingestion, got {type(receipt).__name__}")
    classified = sorted(
        f"{line.line_id}.{name}" for line in receipt.line_items for name in CLASSIFIED_FIELDS if getattr(line, name) is not None
    )
    if classified:
        raise ValueError(f"{receipt.receipt_id}: normalization comes before classification, but found: {classified}")
    lines = tuple(_normalize_line(receipt.receipt_id, line, translator) for line in receipt.line_items)
    return NormalizedReceipt(receipt=receipt, lines=lines)


def _normalize_line(receipt_id: str, line: LineItem, translator: Translator) -> NormalizedLine:
    normalized = normalize_text(line.description_raw)
    outcome = translate_description(normalized, line.language, translator)
    return NormalizedLine(
        receipt_id=receipt_id,
        line_id=line.line_id,
        description_raw=line.description_raw,
        description_normalized=normalized,
        language=line.language,
        translation=outcome.translation,
        translation_status=outcome.status,
        translation_error=outcome.error,
    )
