"""What ingestion returns: a receipt for the pipeline, and its answers kept beside it, never in it.

Only an IngestedReceipt's receipt goes downstream. Its ground_truth is for whoever scores the
results offline, and the field names and values follow "The correct answers" in data/README.md.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, StrictBool, StrictStr, field_serializer, model_validator

from expense_validator.engine.models import Disposition, Receipt, Text
from expense_validator.policy.schema import RuleId
from expense_validator.policy.vocabulary import Severity


class IngestionMode(str, Enum):
    PRODUCTION = "production"  # answers are not expected, so any that arrive are refused
    EVALUATION = "evaluation"  # answers are required, and returned apart from the receipt


class IngestionError(ValueError):
    """A record that was refused, and where it came from when it came from a stream.

    The message names fields by their path, including an unknown key's name, and never holds
    the value of an answer, so it is safe to log.
    """

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


class _Answer(BaseModel):
    """Refuses unknown keys and cannot be changed once built, as the engine's models do."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class LineLabels(_Answer):
    """The correct classification of one line."""

    category: Text
    restricted: StrictBool


class ExpectedException(_Answer):
    rule_id: RuleId
    policy_doc: Text
    severity: Severity
    line_ids: tuple[Text, ...]
    reason: Text


class ExpectedSignal(_Answer):
    """A review signal has no severity and cites no lines."""

    rule_id: RuleId
    policy_doc: Text
    reason: Text


class GroundTruth(_Answer):
    """The correct answer for one receipt, with its evaluation-only notes."""

    receipt_id: Text
    disposition: Disposition
    expected_exceptions: tuple[ExpectedException, ...]
    review_signals: tuple[ExpectedSignal, ...]
    line_labels: Annotated[Mapping[str, LineLabels], AfterValidator(MappingProxyType)]
    notes: StrictStr | None = None

    @field_serializer("line_labels")
    def _serialize_line_labels(self, value: Mapping[str, LineLabels]) -> dict[str, dict]:
        return {line_id: labels.model_dump() for line_id, labels in value.items()}

    @model_validator(mode="after")
    def _check_exceptions_cite_known_lines(self):
        """Says where the bad citation is, not what it says: this message may be logged."""
        for index, exception in enumerate(self.expected_exceptions):
            for position, line_id in enumerate(exception.line_ids):
                if line_id not in self.line_labels:
                    raise ValueError(f"expected_exceptions.{index}.line_ids.{position} is not a line on this receipt")
        return self


@dataclass(frozen=True)
class IngestedReceipt:
    """ground_truth is None in production, and always present in evaluation."""

    receipt: Receipt
    ground_truth: GroundTruth | None
