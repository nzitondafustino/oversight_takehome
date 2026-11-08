"""Shared data models: settings, the input receipt, results and provenance, and the policy compiler's models."""

from expense_validator.engine.models import LineItem, Receipt
from expense_validator.models.results import (
    BatchResult,
    DispositionReasonResult,
    ExceptionResult,
    LineResult,
    Metadata,
    Mode,
    OpenQuestionResult,
    Provenance,
    ReceiptResult,
    ReviewSignalResult,
    UncertaintyResult,
)
from expense_validator.models.settings import CONFIG_CACHE, CompilerSettings, RecordedWith, ValidatorSettings

COMPILER_MODELS = ("Approval", "CandidateRuleset", "CompilerMode", "DocumentReading", "ExtractionResult", "PolicyDocumentInput", "PublicationResult", "PublishMode", "ReadingKind", "Resolution", "ReviewItem", "FieldComparison", "ReviewAction", "ReviewDecision", "ReviewIssue", "ReviewPolicy", "ReviewResult", "ReviewedField")

__all__ = ["BatchResult", "CONFIG_CACHE", "DispositionReasonResult", "ExceptionResult", "LineItem", "LineResult", "Metadata", "Mode", "OpenQuestionResult", "Provenance", "Receipt", "ReceiptResult", "ReviewSignalResult", "UncertaintyResult", "ValidatorSettings", "CompilerSettings", "RecordedWith", *COMPILER_MODELS]


def __getattr__(name: str):
    # The compiler's models load on first use, so the validator's models never load the compiler.
    if name not in COMPILER_MODELS:
        raise AttributeError(f"module 'expense_validator.models' has no attribute {name!r}")
    from expense_validator.models import compiler

    return getattr(compiler, name)
