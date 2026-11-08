"""Expose the public API through lazy imports."""

from importlib import import_module

_EXPORTS = {
    "ExpenseValidator": "expense_validator.validator",
    "PolicyCompiler": "expense_validator.policy_compiler",
    "ValidatorSettings": "expense_validator.models.settings",
    "CompilerSettings": "expense_validator.models.settings",
    "RecordedWith": "expense_validator.models.settings",
    "Receipt": "expense_validator.engine.models",
    "ValidatorError": "expense_validator.exceptions",
    "ConfigurationError": "expense_validator.exceptions",
    "InvalidInputError": "expense_validator.exceptions",
    "ReplayAnswerMissing": "expense_validator.exceptions",
    "EvidenceError": "expense_validator.exceptions",
    "ApprovalRefused": "expense_validator.exceptions",
    "RecordingConflict": "expense_validator.exceptions",
    **{
        name: "expense_validator.models.compiler"
        for name in ("Approval", "CandidateRuleset", "DocumentReading", "ExtractionResult", "FieldComparison", "PolicyDocumentInput", "PublicationResult", "Resolution", "ReviewDecision", "ReviewIssue", "ReviewItem", "ReviewResult", "ReviewedField")
    },
    **{
        name: "expense_validator.models.results"
        for name in (
            "BatchResult",
            "DispositionReasonResult",
            "ExceptionResult",
            "LineResult",
            "Metadata",
            "Mode",
            "OpenQuestionResult",
            "Provenance",
            "ReceiptResult",
            "ReviewSignalResult",
            "UncertaintyResult",
        )
    },
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str):
    if name not in _EXPORTS:
        raise AttributeError(f"module 'expense_validator' has no attribute {name!r}")
    return getattr(import_module(_EXPORTS[name]), name)


def __dir__() -> list[str]:
    return __all__
