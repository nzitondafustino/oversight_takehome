"""Reviewed changes to the hand-written rule logic, applied as an input to compilation, never to a stored ruleset.

A rule override names one rule in rules/rule_logic.yaml and replaces some of its logic: its display title, outcome,
severity, conditions, waiver reference, or parameter values. An addition (add: true) writes a new check rule for a
clause the documents state under its own rule ID and the logic has no rule for: it also gives the rule's scope, its
params and the words of its clause it quotes, as hand-written logic does. It is applied to the logic text before the ruleset is
built, so the compiler builds, validates and hashes the result exactly as it would hand-written logic: the schema,
the field catalog and the faithful check (a rule's policy limits, severities and review signals must be what its
clause states) all still apply, and an approval binds the resulting candidate. Nothing else can change: not a rule's
ID, kind, policy document, quote or provenance, and a parameter keeps its name and type. Overrides are deterministic:
the same logic and overrides always give the same text.
"""

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

# The logic fields a reviewer may change. Everything else in an entry is fixed.
EDITABLE = ("status", "title", "outcome", "severity", "applies_when", "select", "waived_by_ref", "params")
# What only a new rule gives, as nothing yet holds it. Its ID, kind (check), document and provenance still come from
# its clause, and the build refuses a quote its clause does not hold.
ADDED_ONLY = ("scope", "source_quote")
REQUIRED_TO_ADD = ("outcome", "scope", "source_quote")


class RuleOverride(BaseModel):
    """One rule's reviewed changes. Each key in `set` replaces that field; a param replaces that param's value."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str = Field(pattern=r"^[A-Z][A-Z0-9]*(?:[-.][A-Z0-9]+)+$")
    set: dict[str, Any] = Field(min_length=1)
    reviewer: str = Field(min_length=1, max_length=200, pattern=r"\S")
    rationale: str = Field(min_length=1, max_length=2000, pattern=r"\S")
    add: bool = False  # a new rule for a clause with none, rather than a change to an existing one

    @model_validator(mode="after")
    def _addition_is_whole(self):
        given = set(self.set)
        if not self.add and given & set(ADDED_ONLY):
            raise ValueError(f"these fields cannot be overridden: {sorted(given & set(ADDED_ONLY))}")
        if self.add:
            missing = [name for name in REQUIRED_TO_ADD if not self.set.get(name)]
            if missing:
                raise ValueError(f"a new rule needs {missing}")
            if self.set["scope"] not in ("item", "receipt"):
                raise ValueError("scope must be item or receipt")
            if "status" in given:
                raise ValueError("a new rule is active: it takes no status")
            if not isinstance(self.set["source_quote"], str) or not self.set["source_quote"].strip():
                raise ValueError("source_quote must be words of the rule's clause")
        return self

    @field_validator("set")
    @classmethod
    def _only_editable(cls, changes: dict) -> dict:
        fixed = sorted(set(changes) - set(EDITABLE) - set(ADDED_ONLY))
        if fixed:
            raise ValueError(f"these fields cannot be overridden: {fixed}")
        if "status" in changes and changes["status"] not in ("active", "not_evaluable"):
            raise ValueError("status must be active or not_evaluable")
        if "outcome" in changes and changes["outcome"] not in ("exception", "review_signal"):
            raise ValueError("outcome must be exception or review_signal")
        if "title" in changes and not (isinstance(changes["title"], str) and changes["title"].strip()):
            raise ValueError("title must be text")
        if "params" in changes and not isinstance(changes["params"], dict):
            raise ValueError("params must map each param name to its new value")
        return changes


def admit_overrides(overrides: Iterable[RuleOverride | Mapping]) -> tuple[RuleOverride, ...]:
    """The overrides as models, each rule at most once.

    Raises:
        ValueError: If one is malformed, or a rule is overridden twice.
    """
    if isinstance(overrides, (str, bytes, Mapping)):
        raise ValueError("rule_overrides must be a list of rule overrides")
    admitted = []
    for index, item in enumerate(overrides):
        try:
            admitted.append(item if isinstance(item, RuleOverride) else RuleOverride.model_validate(item))
        except ValidationError as error:
            first = error.errors(include_url=False)[0]
            raise ValueError(f"rule_overrides.{index}: {'.'.join(map(str, first['loc']))}: {first['msg']}") from None
    ids = [item.rule_id for item in admitted]
    repeated = sorted({rule_id for rule_id in ids if ids.count(rule_id) > 1})
    if repeated:
        raise ValueError(f"a rule is overridden more than once: {repeated}")
    return tuple(admitted)


def overrides_sha256(overrides: Iterable[RuleOverride]) -> str:
    """The identity of a set of overrides, in any order."""
    # exclude_defaults: a change made before additions existed keeps the identity it had.
    body = sorted((item.model_dump(mode="json", exclude_defaults=True) for item in overrides), key=lambda item: item["rule_id"])
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def apply_overrides(logic_text: str, overrides: Iterable[RuleOverride]) -> str:
    """The logic text with each override applied to its rule. The original text is unchanged.

    Raises:
        ValueError: If an override names no rule, or would change a param's name or type.
    """
    overrides = tuple(overrides)
    if not overrides:
        return logic_text
    logic = yaml.safe_load(logic_text)
    entries = {entry["rule_id"]: entry for entry in logic["rules"]}
    for override in overrides:
        entry = entries.get(override.rule_id)
        if override.add:
            if entry is not None:
                raise ValueError(f"{override.rule_id}: already has a rule: change it rather than add one")
            entry = entries[override.rule_id] = {"rule_id": override.rule_id, "kind": "check", **override.set}
            logic["rules"].append(entry)
            if "rule_order" in logic:
                logic["rule_order"].append(override.rule_id)
            continue
        if entry is None:
            raise ValueError(f"{override.rule_id}: no rule with this ID to override")
        if entry["kind"] == "process" and override.set.get("status", "active") != "active":
            raise ValueError("process directives cannot be removed")
        for field, value in override.set.items():
            if field == "params":
                entry["params"] = _params(override.rule_id, entry.get("params") or {}, value)
            elif value is None and field in ("applies_when", "select", "waived_by_ref", "severity"):
                entry.pop(field, None)
            else:
                entry[field] = value
    return yaml.safe_dump(logic, sort_keys=False, allow_unicode=True)


def _params(rule_id: str, current: dict, changes: dict) -> dict:
    updated = dict(current)
    for name, value in changes.items():
        if name not in current:
            raise ValueError(f"{rule_id}: no param {name!r} to change: a param is never added")
        if not isinstance(value, dict) or value.get("type") != current[name].get("type"):
            raise ValueError(f"{rule_id}: param {name!r} must stay a {current[name].get('type')} param")
        if value.get("type") == "number" and value.get("unit") != current[name].get("unit"):
            raise ValueError(f"{rule_id}: param {name!r} must keep its unit, {current[name].get('unit')}")
        updated[name] = {**current[name], **value}
    return updated
