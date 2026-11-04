"""The structure every written rule must fit.

A RuleSet is a versioned list of rules, and each rule is one of three kinds:

    check       flags lines or a receipt that break a clause, and may replace other rules
    carve_out   excuses lines from other rules when a reference is on file
    process     settings for the engine, with no logic of its own

A rule decides what it catches with conditions, or with a named helper when
conditions cannot express the clause. Thresholds and lists live in the rule's
params, and conditions refer to them by name. A rule can also say which item
classes it applies to, as plain names: this schema does not check them.

The fixed words for rules live in vocabulary.py. This file reads top down:
conditions, params, rules, then the RuleSet.

The compiler produces these models, the engine reads them. Anything a clause
needs that this schema cannot express goes to a developer, who extends the
schema or adds a helper. An unknown helper is a validation error. This schema
does not know the receipt and item data: field names and the values they are
compared with are stored as written.

Condition semantics: a comparison against a missing value is false, so `not`
of it is true. That is how "absence of evidence does not satisfy a carve-out"
is expressed. `ne` is a comparison too, so it is also false on a missing value:
write `not` of `eq` when a missing value should satisfy the condition.
"""

from collections.abc import Iterator
from decimal import Decimal
from types import MappingProxyType
from typing import Annotated, Literal, Union

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, WrapSerializer, model_validator

from expense_validator.policy.vocabulary import (
    DIRECTIVE_PARAMS,
    RULE_ID_PARAMS,
    Directive,
    Helper,
    RefKind,
    Severity,
    Unit,
)

SCHEMA_VERSION = "1"

RULE_ID_PATTERN = r"^[A-Z]+(-\d{2})?(\.[A-Z_]+)?$"
RuleId = Annotated[str, Field(pattern=RULE_ID_PATTERN)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class _Strict(BaseModel):
    """Refuses unknown keys, checks defaults too, and cannot be changed once built.

    Frozen only stops a field being reassigned, so collections are tuples and params a
    read-only mapping: nothing inside a built rule can change either.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


# --- Conditions: true or false for a receipt or a line item ---


class ParamRef(_Strict):
    """Points at a value in the rule's params, by name."""

    param: str


class Compare(_Strict):
    """A field against one value, such as item.category eq "alcohol"."""

    op: Literal["gt", "gte", "lt", "lte", "eq", "ne"]
    field: str
    # An ordered comparison only ever uses a ParamRef, so each threshold has one
    # home that the source check can compare against the policy text.
    value: ParamRef | str | bool

    @model_validator(mode="after")
    def _check_operands(self):
        if self.op in {"gt", "gte", "lt", "lte"} and not isinstance(self.value, ParamRef):
            raise ValueError(f"{self.op} on {self.field} must compare against a param")
        return self


class In(_Strict):
    """A field against a list of allowed values."""

    op: Literal["in"]
    field: str
    values: ParamRef | tuple[str, ...]


class Missing(_Strict):
    """True when the field has no value."""

    op: Literal["missing"]
    field: str


class AllOf(_Strict):
    """True when every inner condition is true."""

    op: Literal["all"]
    conditions: tuple["Condition", ...] = Field(min_length=1)


class AnyOf(_Strict):
    """True when at least one inner condition is true."""

    op: Literal["any"]
    conditions: tuple["Condition", ...] = Field(min_length=1)


class Not(_Strict):
    """True when the inner condition is false."""

    op: Literal["not"]
    condition: "Condition"


Condition = Annotated[Union[Compare, In, Missing, AllOf, AnyOf, Not], Field(discriminator="op")]

AllOf.model_rebuild()
AnyOf.model_rebuild()
Not.model_rebuild()


def walk(condition: Condition) -> Iterator[Condition]:
    """Yield a condition and every condition nested inside it."""
    yield condition
    if isinstance(condition, (AllOf, AnyOf)):
        for child in condition.conditions:
            yield from walk(child)
    elif isinstance(condition, Not):
        yield from walk(condition.condition)


# --- Params: the thresholds and lists a rule refers to by name ---


class NumberParam(_Strict):
    """A threshold, such as a $200 cap."""

    type: Literal["number"]
    value: Decimal
    unit: Unit
    # project_decision marks a value the docs do not state, such as the EXC-03
    # threshold. The source check skips these; everything else must match the clause.
    source: Literal["policy", "project_decision"] = "policy"


class SetParam(_Strict):
    """A list of values, such as medium-risk merchant codes."""

    type: Literal["set"]
    values: tuple[str, ...] = Field(min_length=1)


Param = Annotated[Union[NumberParam, SetParam], Field(discriminator="type")]
# A read-only view of a private dict: nothing else holds that dict, so nothing can change it.
# A view does not convert to JSON by itself, so it is handed over as a dict for that.
Params = Annotated[
    dict[str, Param],
    AfterValidator(MappingProxyType),
    WrapSerializer(lambda params, handler: handler(dict(params))),
]


# --- Rules ---


class AppliesTo(_Strict):
    """Which item classes a rule applies to: every class, or specific ones."""

    classes: Literal["all"] | tuple[str, ...] = Field(min_length=1)
    excludes: tuple[str, ...] = ()  # with all, the classes the clause rules out

    @model_validator(mode="after")
    def _excludes_only_with_all(self):
        if self.excludes and self.classes != "all":
            raise ValueError("excludes only makes sense with classes: all")
        return self


class Provenance(_Strict):
    """How a rule was produced, so a change in its source can be detected."""

    translator: Literal["template", "llm", "developer"]
    clause_hash: Sha256
    fingerprint: Sha256
    model: str | None = None
    prompt_hash: Sha256 | None = None

    @model_validator(mode="after")
    def _llm_is_reproducible(self):
        if self.translator == "llm" and not (self.model and self.prompt_hash):
            raise ValueError("an llm translation must record its model and prompt_hash")
        return self


class _RuleBase(_Strict):
    """What every rule kind has: its identity, its source and its params."""

    rule_id: RuleId
    policy_doc: str = Field(pattern=r"^[a-z-]+\.md$")
    title: str = Field(min_length=1)
    source_quote: str = Field(min_length=1)
    # retired: no longer policy. not_evaluable: recorded, but cannot be evaluated,
    # so it may carry no logic.
    status: Literal["active", "retired", "not_evaluable"] = "active"
    params: Params = Field(default_factory=dict)
    applies_to: AppliesTo | None = None  # the item classes it covers, not decided yet
    provenance: Provenance

    @model_validator(mode="after")
    def _check_rule_id_params(self):
        for name in RULE_ID_PARAMS & set(self.params):
            self._require_param(name, SetParam)
        return self

    def _check_condition(self, condition: Condition) -> None:
        for node in walk(condition):
            if isinstance(node, Compare) and isinstance(node.value, ParamRef):
                self._require_param(node.value.param, NumberParam)
            elif isinstance(node, In) and isinstance(node.values, ParamRef):
                self._require_param(node.values.param, SetParam)

    def _require_param(self, name: str, expected: type) -> None:
        param = self.params.get(name)
        if param is None:
            raise ValueError(f"{self.rule_id}: condition uses undefined param {name!r}")
        if not isinstance(param, expected):
            raise ValueError(f"{self.rule_id}: param {name!r} must be a {expected.__name__}")


class SeverityCase(_Strict):
    """A severity that applies when its condition is true."""

    when: Condition
    severity: Severity


class Replacement(_Strict):
    """One rule a check replaces, and when in evaluation it does so."""

    rule_id: RuleId
    # receipt: chosen before evaluation. line: lines removed before evaluation.
    # finding: resolved after evaluation.
    level: Literal["receipt", "line", "finding"]


class CheckRule(_RuleBase):
    """Flags lines or a receipt that break a clause."""

    kind: Literal["check"]
    outcome: Literal["exception", "review_signal"]
    scope: Literal["item", "receipt"]
    severity: Severity | None = None
    severity_cases: tuple[SeverityCase, ...] = ()
    applies_when: Condition | None = None  # receipt-level gate: does this rule apply at all?
    select: Condition | None = None  # which lines the rule catches
    helper: Helper | None = None  # named logic, for what conditions cannot express
    waived_by_ref: RefKind | None = None  # a reference on file that excuses the finding
    replaces: tuple[Replacement, ...] = ()  # rules this one takes the place of

    def conditions(self) -> list[Condition]:
        found = [c for c in (self.applies_when, self.select) if c is not None]
        return found + [case.when for case in self.severity_cases]

    @model_validator(mode="after")
    def _check_shape(self):
        self._check_severity_fits_outcome()
        for condition in self.conditions():
            self._check_condition(condition)
        self._check_applies_when_is_receipt_level()
        if self.status == "active":
            self._check_logic_is_complete()
        return self

    def _check_severity_fits_outcome(self) -> None:
        if self.outcome == "exception" and self.severity is None:
            raise ValueError(f"{self.rule_id}: an exception needs a severity")
        if self.outcome == "review_signal" and (self.severity or self.severity_cases):
            raise ValueError(f"{self.rule_id}: a review signal has no severity")

    def _check_applies_when_is_receipt_level(self) -> None:
        if self.applies_when is None:
            return
        for node in walk(self.applies_when):
            if getattr(node, "field", "").startswith("item."):
                raise ValueError(f"{self.rule_id}: applies_when is receipt-level, found {node.field}")

    def _check_logic_is_complete(self) -> None:
        if self.helper is not None:
            if self.select is not None:
                raise ValueError(f"{self.rule_id}: a helper selects its own lines, remove select")
            return
        if self.scope == "item" and self.select is None:
            raise ValueError(f"{self.rule_id}: an item rule needs select or a helper")
        if self.scope == "receipt":
            if self.select is not None:
                raise ValueError(f"{self.rule_id}: a whole-receipt rule cites no items, remove select")
            if self.applies_when is None:
                raise ValueError(f"{self.rule_id}: a whole-receipt rule needs applies_when or a helper")
        unused = set(self.params) - self._referenced_params()
        if unused:
            raise ValueError(f"{self.rule_id}: params never used: {sorted(unused)}")

    def _referenced_params(self) -> set[str]:
        used = set()
        for condition in self.conditions():
            for node in walk(condition):
                if isinstance(node, Compare) and isinstance(node.value, ParamRef):
                    used.add(node.value.param)
                elif isinstance(node, In) and isinstance(node.values, ParamRef):
                    used.add(node.values.param)
        return used


class CarveOutRule(_RuleBase):
    """Excuses lines from the rules it modifies when the required reference is on file."""

    kind: Literal["carve_out"]
    modifies: tuple[RuleId, ...] = Field(min_length=1)
    requires_ref: RefKind
    select: Condition | None = None
    helper: Helper | None = None

    def conditions(self) -> list[Condition]:
        return [self.select] if self.select is not None else []

    @model_validator(mode="after")
    def _check_shape(self):
        if (self.select is None) == (self.helper is None):
            raise ValueError(f"{self.rule_id}: a carve-out needs exactly one of select or helper")
        for condition in self.conditions():
            self._check_condition(condition)
        return self


class ProcessRule(_RuleBase):
    """Settings for the engine, such as how severities map to a disposition."""

    kind: Literal["process"]
    directive: Directive

    @model_validator(mode="after")
    def _check_directive(self):
        expected = DIRECTIVE_PARAMS[self.directive]
        if set(self.params) != expected:
            raise ValueError(f"{self.rule_id}: {self.directive} needs params {sorted(expected)}")
        return self


Rule = Annotated[Union[CheckRule, CarveOutRule, ProcessRule], Field(discriminator="kind")]


# --- RuleSet: the published file, and the checks that need every rule at once ---


def _referenced_rule_ids(rule: _RuleBase) -> set[str]:
    """The IDs of the other rules this rule depends on."""
    referenced = set()
    if isinstance(rule, CheckRule):
        referenced |= {replacement.rule_id for replacement in rule.replaces}
    if isinstance(rule, CarveOutRule):
        referenced |= set(rule.modifies)
    for name in RULE_ID_PARAMS & set(rule.params):
        referenced |= set(rule.params[name].values)
    return referenced


class RuleSet(_Strict):
    """A versioned list of rules that are valid together."""

    schema_version: Literal["1"] = SCHEMA_VERSION
    ruleset_version: str = Field(min_length=1)
    rules: tuple[Rule, ...]

    @model_validator(mode="after")
    def _check_set(self):
        ids = [rule.rule_id for rule in self.rules]
        duplicates = sorted({rule_id for rule_id in ids if ids.count(rule_id) > 1})
        if duplicates:
            raise ValueError(f"duplicate rule ids: {duplicates}")

        by_id = {rule.rule_id: rule for rule in self.rules}
        for rule in self.rules:
            if rule.status != "active":
                continue
            for target in _referenced_rule_ids(rule):
                if target not in by_id:
                    raise ValueError(f"{rule.rule_id} references unknown rule {target}")
                if by_id[target].status == "retired":
                    raise ValueError(f"{rule.rule_id} references retired rule {target}")

        self._check_no_replacement_cycle()
        return self

    def _check_no_replacement_cycle(self) -> None:
        edges = {
            rule.rule_id: [replacement.rule_id for replacement in rule.replaces]
            for rule in self.rules
            if isinstance(rule, CheckRule)
        }
        done: set[str] = set()

        def visit(rule_id: str, path: tuple[str, ...]) -> None:
            if rule_id in path:
                raise ValueError(f"replacement cycle: {' -> '.join(path + (rule_id,))}")
            if rule_id in done:
                return
            for target in edges.get(rule_id, []):
                visit(target, path + (rule_id,))
            done.add(rule_id)

        for rule_id in edges:
            visit(rule_id, ())

    def to_json(self) -> str:
        """Canonical form: sorted by rule ID, so upserts give clean git diffs."""
        ordered = self.model_copy(update={"rules": tuple(sorted(self.rules, key=lambda rule: rule.rule_id))})
        return ordered.model_dump_json(indent=2, exclude_none=True) + "\n"

    @classmethod
    def from_json(cls, text: str) -> "RuleSet":
        return cls.model_validate_json(text)
