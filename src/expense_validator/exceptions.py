"""Package exceptions. A model failing on one line is a result, not an error."""


class ValidatorError(Exception):
    """Base class for errors the validator raises."""


class ConfigurationError(ValidatorError, ValueError):
    """The settings cannot work, or a mode was given what it cannot use."""


class InvalidInputError(ValidatorError, ValueError):
    """A receipt or argument was refused. Messages never include answer values."""


# A line error with exactly this text means replay had no recording: missing evidence, never a classification failure.
NOT_RECORDED = "not in the replay cache: replaying never calls a model"


class ReplayAnswerMissing(ValidatorError, LookupError):
    """Replay found no saved answer for some requests.

    Attributes:
        missing: Request texts with no saved answer.
    """

    def __init__(self, missing: list[str]):
        self.missing = tuple(missing)
        super().__init__(f"{len(missing)} answers are not in the replay cache, and replaying never calls a model")


class EvidenceError(ValidatorError, ValueError):
    """Policy text needed for an exception's evidence could not be verified."""


class ApprovalRefused(ValidatorError, ValueError):
    """An extraction cannot be approved: something blocks it, or the approval record is invalid."""


class ClassificationError(Exception):
    """An expected failure to classify a line."""


class TranslationError(Exception):
    """An expected failure to translate."""


class IngestionError(ValueError):
    """A refused record. The message never includes answer values, so it is safe to log."""

    def __init__(
        self,
        reason: str,
        *,
        receipt_id: str | None = None,
        source: str | None = None,
        line_number: int | None = None,
    ) -> None:
        self.reason = reason
        self.receipt_id = receipt_id
        self.source = source
        self.line_number = line_number
        location = None if source is None else f"{source}:{line_number}"
        super().__init__(": ".join(part for part in (location, receipt_id, reason) if part))


class RecordingMissing(LookupError):
    """No recorded response exists for a document."""


class RecordingConflict(ValidatorError):
    """A recording file holds something other than what was asked for. A recording is never replaced."""
