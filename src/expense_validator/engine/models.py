"""What the engine reads and what it reports: a receipt, its line items, and the result.

Field names match data/README.md and rules/fields.yaml. Only the facts those mark as
derived are absent here, such as receipt.local_hour: they are worked out from these fields.

A value a rule tests for absence is optional, because a receipt missing it has to reach
that rule rather than be refused here: a missing merchant name is a DOC-02 exception, not
bad input. None is missing, while False and 0 are values, so bools and counts are strict
and neither can stand in for the other.

Categories, entities and other receipt data are plain text, as in the rule schema: the
taxonomy is open-ended and the values belong to rules/fields.yaml. The correct answers in
the data (labels, notes) are not fields, so a row still carrying them is refused.

A false condition does not establish compliance, and missing information is not a
violation either: an item with no category matches no category rule, which says nothing
about whether it is within policy. So a missing category and a missing confidence are kept
as separate None values, for the engine to account for before it returns a clean pass.

These models hold results and check their shape. They decide nothing: which findings are
raised and what disposition follows are the engine's work.
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
    }
)


class _Strict(BaseModel):
    """Refuses unknown keys, checks defaults too, and cannot be changed once built."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


def _needs_a_time(value: object) -> object:
    """A bare date would parse as midnight, and midnight is a real hour to TIME-01."""
    bare_text = isinstance(value, (str, bytes)) and len(value) <= len("2026-04-14")
    bare_date = isinstance(value, date) and not isinstance(value, datetime)
    if bare_text or bare_date:
        raise ValueError("transaction_datetime needs a time as well as a date")
    return value


def _number_as_text(value: object) -> object:
    """Keeps a number exact and its type stable, as JSON would bring a Decimal back as text."""
    return str(value) if isinstance(value, (int, Decimal)) and not isinstance(value, bool) else value


class LineItem(_Strict):
    """One item as printed, plus what classifying its text adds."""

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

    def with_classification(self, output: Mapping[str, object]) -> "LineItem":
        """A new line with this classifier output on it, validated like any other input.

        model_copy(update=...) would skip validation, so it must not be used for this.
        """
        refused = sorted(set(output) - CLASSIFIED_FIELDS)
        if refused:
            raise ValueError(f"{self.line_id}: a classifier may not set {refused}")
        return LineItem.model_validate({**self.model_dump(), **output})


class Receipt(_Strict):
    """One expense as submitted. Times are local to the merchant, with no time zone."""

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
        """A new receipt with classifier output on its lines, by line ID.

        A line with no output keeps what it has, so an unclassified line stays visibly
        unclassified. Output for a line not on this receipt is refused, not dropped.
        """
        unknown = sorted(set(outputs) - {item.line_id for item in self.line_items})
        if unknown:
            raise ValueError(f"{self.receipt_id}: classifier output for unknown lines: {unknown}")
        items = [item.with_classification(outputs.get(item.line_id, {})) for item in self.line_items]
        return Receipt.model_validate({**self.model_dump(), "line_items": items})


class Evidence(_Strict):
    """One fact a finding rests on, such as receipt.attendee_count having no value.

    A value of None says the fact was missing. Numbers are stored as their exact text.
    """

    fact: Text
    value: Annotated[StrictStr | StrictBool | None, BeforeValidator(_number_as_text)]


class ReplacementTarget(_Strict):
    """What a finding established it replaces: a rule, on these lines, or the whole receipt if none."""

    rule_id: RuleId
    line_ids: tuple[Text, ...] = ()


class Finding(_Strict):
    """What one rule found on one receipt.

    An item finding targets exactly one line. A receipt finding may name any lines relevant
    to it, or none. These are targets, not citations: which lines a reported exception cites
    is decided by EXC-04, which also wants one exception per rule on a receipt.
    """

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
    """The answer for one receipt. The disposition is not checked against the findings, as
    that mapping is EXC-01 in the customer's ruleset."""

    receipt_id: Text
    customer_id: Text
    ruleset_version: Text
    disposition: Disposition
    findings: tuple[Finding, ...] = ()

    @model_validator(mode="after")
    def _check_findings_are_for_this_receipt(self):
        others = sorted({finding.receipt_id for finding in self.findings} - {self.receipt_id})
        if others:
            raise ValueError(f"{self.receipt_id}: has findings for other receipts: {others}")
        return self
