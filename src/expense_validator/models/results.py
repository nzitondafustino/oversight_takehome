"""Define typed validation results and provenance.

ReceiptResult.to_record() exports the receipt record without provenance.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

Mode = Literal["replay", "live", "deterministic"]


class _Result(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LineResult(_Result):
    line_id: str
    description_raw: str  # as printed, even when a translation was classified
    category: str | None
    restricted: bool | None
    confidence: float | None
    english_translation: str | None = None
    attributes: dict[str, str | bool] | None = None  # recipient, venue, item kind, essential toiletry, when read
    classification_error: str | None = None
    classification_details: dict | None = None  # a backend's own evidence, such as Jev's probabilities. Not in to_record()


class ExceptionResult(_Result):
    rule_id: str
    policy_doc: str
    severity: str | None
    line_ids: tuple[str, ...]
    evidence: str  # the sentence of the rule's clause that holds its signed quote, or the quote
    source_quote: str  # the signed quote, unchanged
    explanation: str


class ReviewSignalResult(_Result):
    rule_id: str
    reason: str


class DispositionReasonResult(_Result):
    code: str
    basis: str
    rule_ids: tuple[str, ...]


class OpenQuestionResult(_Result):
    rule_id: str
    line_ids: tuple[str, ...]
    reasons: tuple[str, ...]


class UncertaintyResult(_Result):
    line_id: str
    field: str
    state: str
    reason: str
    confidence: float | None


class Metadata(_Result):
    model: str | None
    translation_model: str | None
    retriever: str  # "none": there is no retrieval stage
    prompt_sha256: str | None
    ruleset_version: str
    replayed: bool
    latency_ms: int | None  # null unless a model was called for this receipt


class Provenance(_Result):
    mode: Mode
    context_complete: bool
    receipts_in_context: int
    ruleset_sha256: str
    model_overrides: tuple[str, ...] = ()  # which models this request chose, rather than the validator's
    classification_backend: str | None = None  # openrouter or jev; None in deterministic mode


class ReceiptResult(_Result):
    receipt_id: str
    disposition: Literal["flag", "review", "pass"]
    line_items: tuple[LineResult, ...]
    exceptions: tuple[ExceptionResult, ...]  # active exceptions only
    review_signals: tuple[ReviewSignalResult, ...]
    decided: bool
    disposition_reasons: tuple[DispositionReasonResult, ...]
    open_questions: tuple[OpenQuestionResult, ...]
    uncertainty: tuple[UncertaintyResult, ...]
    metadata: Metadata
    provenance: Provenance

    def to_record(self) -> dict:
        """Return the receipt as one JSONL object."""
        dump = self.model_dump(mode="json")
        lines = []
        for line in dump["line_items"]:
            record = {name: line[name] for name in ("line_id", "description_raw", "category", "restricted", "confidence")}
            record |= {name: line[name] for name in ("english_translation", "attributes", "classification_error") if line[name] is not None}
            lines.append(record)
        metadata = dump["metadata"]
        return {
            "receipt_id": dump["receipt_id"],
            "disposition": dump["disposition"],
            "line_items": lines,
            "exceptions": dump["exceptions"],
            "review_signals": dump["review_signals"],
            "decided": dump["decided"],
            "disposition_reasons": dump["disposition_reasons"],
            "open_questions": dump["open_questions"],
            "uncertainty": dump["uncertainty"],
            "metadata": {name: metadata[name] for name in ("model", "translation_model", "retriever", "prompt_sha256", "ruleset_version", "replayed", "latency_ms")},
        }


class BatchResult(_Result):
    """A batch's results, in receipt ID order."""

    mode: Mode
    context_complete: bool
    results: tuple[ReceiptResult, ...]

    def for_receipt(self, receipt_id: str) -> ReceiptResult:
        for result in self.results:
            if result.receipt_id == receipt_id:
                return result
        raise KeyError(receipt_id)
