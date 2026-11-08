"""Ingestion results: a receipt for the pipeline and its answers kept beside it."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, StrictBool, StrictStr, field_serializer, model_validator

from expense_validator.engine.models import Disposition, Receipt, Text
from expense_validator.exceptions import IngestionError  # noqa: F401  re-exported from its former home
from expense_validator.policy.schema import RuleId
from expense_validator.policy.vocabulary import Severity


class IngestionMode(str, Enum):
    PRODUCTION = "production"  # answers are not expected, so any that arrive are refused
    EVALUATION = "evaluation"  # answers are required, and returned apart from the receipt


class _Answer(BaseModel):
    """Refuse unknown keys and forbid changes after construction."""

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
    """An expected review signal, with no severity or lines."""

    rule_id: RuleId
    policy_doc: Text
    reason: Text


class GroundTruth(_Answer):
    """The correct answer for one receipt."""

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
        """Refuse an expected exception citing an unknown line, without quoting it."""
        for index, exception in enumerate(self.expected_exceptions):
            for position, line_id in enumerate(exception.line_ids):
                if line_id not in self.line_labels:
                    raise ValueError(f"expected_exceptions.{index}.line_ids.{position} is not a line on this receipt")
        return self


@dataclass(frozen=True)
class IngestedReceipt:
    """A receipt with its answers, which are None in production mode."""

    receipt: Receipt
    ground_truth: GroundTruth | None
