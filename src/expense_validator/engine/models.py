"""Receipt, line item and finding models.

None means missing, while False and 0 are values. A missing category and a missing confidence stay
separate, for the engine to account for before a clean pass. These models decide nothing.
"""

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, NaiveDatetime, StrictBool, StrictStr, model_validator

from expense_validator.policy.schema import Provenance, RuleId
from expense_validator.policy.vocabulary import Severity

# Has something in it, so that blank text cannot pass for a merchant name or a reference.
Text = Annotated[str, Field(pattern=r"\S")]

# The same words as CheckRule.outcome and CheckRule.scope.
Outcome = Literal["exception", "review_signal"]
Scope = Literal["item", "receipt"]
# raised: counts towards the disposition. waived: excused by a reference or a carve-out.
# replaced: a rule that replaces it at finding level took its place, as TIME-03 does.
FindingStatus = Literal["raised", "waived", "replaced"]
Disposition = Literal["pass", "review", "flag"]
# The only LineItem fields a classifier may set. What the receipt printed is not among them.
CLASSIFIED_FIELDS = frozenset(
    {
        "category",
        "restricted",
        "confidence",
        "recipient_name",
        "recipient_type",
        "venue_type",
        "item_kind",
        "essential_toiletry",
        *(f"{name}_confidence" for name in ("recipient_type", "venue_type", "item_kind", "essential_toiletry")),
    }
)


class _Strict(BaseModel):
    """Refuse unknown keys, validate defaults, and forbid changes after construction."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


def _needs_a_time(value: object) -> object:
    """Refuse a bare date, which would parse as midnight and mislead TIME-01."""
    bare_text = isinstance(value, (str, bytes)) and len(value) <= len("2026-04-14")
    bare_date = isinstance(value, date) and not isinstance(value, datetime)
    if bare_text or bare_date:
        raise ValueError("transaction_datetime needs a time as well as a date")
    return value


def _number_as_text(value: object) -> object:
    return str(value) if isinstance(value, (int, Decimal)) and not isinstance(value, bool) else value


class LineItem(_Strict):
    """One printed line item and its classifier output."""

    line_id: Text
    description_raw: str
    language: Text
    quantity: int = Field(gt=0, strict=True)
    unit_price: Decimal
    amount: Decimal
    # Needs a conversion rate, and a receipt missing its rate is DOC-04, not bad input.
    amount_usd: Decimal | None = None

    # Not on the receipt: None until classified, and each can stay None on its own.
    category: Text | None = None
    restricted: StrictBool | None = None
    confidence: Decimal | None = Field(default=None, ge=0, le=1)  # in the category: read by EXC-03
    recipient_name: Text | None = None
    recipient_type: Text | None = None
    venue_type: Text | None = None
    item_kind: Text | None = None
    # The baggage-delay exception covers essential toiletries, and personal_care also holds massage.
    essential_toiletry: StrictBool | None = None
    # The classifier's confidence in each stated attribute, where it gives one (Jev does; the LLM does not). EXC-03
    # judges it against min_confidence, as it does the category's: a classifier never drops a value by confidence.
    recipient_type_confidence: Decimal | None = Field(default=None, ge=0, le=1)
    venue_type_confidence: Decimal | None = Field(default=None, ge=0, le=1)
    item_kind_confidence: Decimal | None = Field(default=None, ge=0, le=1)
    essential_toiletry_confidence: Decimal | None = Field(default=None, ge=0, le=1)

    def with_classification(self, output: Mapping[str, object]) -> "LineItem":
        """Return a validated copy with classifier output. Never use model_copy for this."""
        refused = sorted(set(output) - CLASSIFIED_FIELDS)
        if refused:
            raise ValueError(f"{self.line_id}: a classifier may not set {refused}")
        return LineItem.model_validate({**self.model_dump(), **output})


class Receipt(_Strict):
    """One submitted expense. Times are local to the merchant, without a time zone."""

    receipt_id: Text
    employee_id: Text
    employee_country: str = Field(pattern=r"^[A-Z]{2}$")
    entity: Text
    submitted_expense_type: Text
    itemized: StrictBool
    # A project extension: a submitted claim that the lines are all of the receipt, as data/README.md does
    # not say they are. It is not evidence of that, and it can only add scrutiny: see money.py.
    itemization_complete: StrictBool | None = None
    line_items: tuple[LineItem, ...]

    merchant_name: Text | None = None
    mcc: str | None = Field(default=None, pattern=r"^[0-9]{4}$")
    mcc_description: Text | None = None
    transaction_datetime: Annotated[NaiveDatetime | None, BeforeValidator(_needs_a_time)] = None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    fx_rate_to_usd: Decimal | None = Field(default=None, gt=0)
    total_amount: Decimal | None = None
    total_amount_usd: Decimal | None = None
    attendee_count: int | None = Field(default=None, ge=0, strict=True)
    trip_id: Text | None = None
    trip_nights: int | None = Field(default=None, ge=0, strict=True)
    # One field per kind of reference, a project decision: EXC-02 and data/README.md describe one
    # field for all of them. A value is a supplied claim. Nothing here authenticates it.
    pre_approval_ref: Text | None = None
    vp_approval_ref: Text | None = None
    manager_approval_ref: Text | None = None
    baggage_delay_ref: Text | None = None
    # A stated fact about the delay, in hours. The reference alone does not prove how long it was.
    baggage_delay_hours: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _check_line_ids_are_unique(self):
        ids = [item.line_id for item in self.line_items]
        duplicates = sorted({line_id for line_id in ids if ids.count(line_id) > 1})
        if duplicates:
            raise ValueError(f"{self.receipt_id}: duplicate line ids: {duplicates}")
        return self

    def with_classifications(self, outputs: Mapping[str, Mapping[str, object]]) -> "Receipt":
        """Return a validated copy with classifier output by line ID. Unknown lines are refused."""
        unknown = sorted(set(outputs) - {item.line_id for item in self.line_items})
        if unknown:
            raise ValueError(f"{self.receipt_id}: classifier output for unknown lines: {unknown}")
        items = [item.with_classification(outputs.get(item.line_id, {})) for item in self.line_items]
        return Receipt.model_validate({**self.model_dump(), "line_items": items})


class Evidence(_Strict):
    """One fact a finding rests on. None means the fact was missing."""

    fact: Text
    value: Annotated[StrictStr | StrictBool | None, BeforeValidator(_number_as_text)]


class ReplacementTarget(_Strict):
    """A rule a finding replaces, on these lines or the whole receipt."""

    rule_id: RuleId
    line_ids: tuple[Text, ...] = ()


class Finding(_Strict):
    """What one rule found on one receipt. EXC-04 decides which lines an exception cites."""

    rule_id: RuleId
    outcome: Outcome
    severity: Severity | None = None
    scope: Scope
    receipt_id: Text
    line_ids: tuple[Text, ...] = ()
    status: FindingStatus = "raised"
    resolved_by: RuleId | None = None  # the rule behind a replacement or a carve-out
    # For a rule that replaces at finding level: which of its declared targets this finding established.
    replaces: tuple[ReplacementTarget, ...] = ()
    explanation: Text
    evidence: tuple[Evidence, ...] = ()
    policy_doc: Text
    source_quote: Text
    provenance: Provenance

    @model_validator(mode="after")
    def _check_shape(self):
        if self.outcome == "exception" and self.severity is None:
            raise ValueError(f"{self.rule_id}: an exception needs a severity")
        if self.outcome == "review_signal" and self.severity is not None:
            raise ValueError(f"{self.rule_id}: a review signal has no severity")
        if self.scope == "item" and len(self.line_ids) != 1:
            raise ValueError(f"{self.rule_id}: an item finding targets exactly one line")
        if self.status == "raised" and self.resolved_by is not None:
            raise ValueError(f"{self.rule_id}: a raised finding has not been resolved")
        if self.status == "replaced" and self.resolved_by is None:
            raise ValueError(f"{self.rule_id}: a replaced finding must name the rule that replaced it")
        return self


class EvaluationResult(_Strict):
    """The result for one receipt."""

    receipt_id: Text
    ruleset_version: Text
    disposition: Disposition
    findings: tuple[Finding, ...] = ()

    @model_validator(mode="after")
    def _check_findings_are_for_this_receipt(self):
        others = sorted({finding.receipt_id for finding in self.findings} - {self.receipt_id})
        if others:
            raise ValueError(f"{self.receipt_id}: has findings for other receipts: {others}")
        return self
