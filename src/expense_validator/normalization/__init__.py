"""Prepares receipt line descriptions for classification. Decides nothing about what an item is."""

from expense_validator.normalization.models import NormalizedLine, NormalizedReceipt
from expense_validator.normalization.receipt import normalize_receipt
from expense_validator.normalization.text import normalize_text
from expense_validator.normalization.translation import (
    LlmTranslator,
    NoOpTranslator,
    TranslationError,
    TranslationStatus,
    Translator,
    build_translator,
)

__all__ = [
    "LlmTranslator",
    "NoOpTranslator",
    "NormalizedLine",
    "NormalizedReceipt",
    "TranslationError",
    "TranslationStatus",
    "Translator",
    "build_translator",
    "normalize_receipt",
    "normalize_text",
]
