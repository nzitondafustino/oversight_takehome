"""Expose receipt classification; keep scoring in the evaluation package."""

from expense_validator.classification.core.common import load_taxonomy, load_vocabulary
from expense_validator.classification.backends.llm import build_classifier, classify_receipt
from expense_validator.classification.core.models import Category, ClassifiedLine, Vocabulary
from expense_validator.exceptions import ClassificationError

__all__ = [
    "Category",
    "ClassificationError",
    "ClassifiedLine",
    "Vocabulary",
    "build_classifier",
    "classify_receipt",
    "load_taxonomy",
    "load_vocabulary",
]
