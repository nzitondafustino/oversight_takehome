"""The reusable interface must preserve the functional API's results and trust boundaries."""

from copy import deepcopy
from dataclasses import FrozenInstanceError
from pathlib import Path
import os
import subprocess
import sys

import pytest
from pydantic import TypeAdapter, ValidationError
from yaml_cases import exact, load_cases, selected

from expense_validator.compiler.validate import read_fields
from expense_validator.config import ROOT, load_config
from expense_validator.engine import evaluation
from expense_validator.engine.config_validation import validate_rules
from expense_validator.engine.policy_engine import PolicyEngine
from expense_validator.policy.schema import Rule, RuleSet

PATHS = load_config().paths
FIELDS = read_fields(PATHS.rule_fields.read_text())
RULESET = RuleSet.from_json(PATHS.ruleset.read_text())


def summary(result):
    return {
        "receipt_id": result.receipt_id,
        "disposition": result.disposition,
        "decided": result.decided,
        "exceptions": [group.rule_id for group in result.active_exceptions],
    }


@pytest.mark.parametrize("case", load_cases("evaluation", "cases"))
def test_both_interfaces_return_identical_results_and_serialization(case):
    engine = PolicyEngine(ruleset=RULESET, fields=FIELDS)
    expected = evaluation.evaluate_receipts(case["receipts"], RULESET, FIELDS, complete=case["complete"])
    before = deepcopy(case["receipts"])
    result = engine.evaluate(iter(case["receipts"]), complete=case["complete"])
    assert result == expected
    assert result.model_dump_json() == expected.model_dump_json()
    assert engine.evaluate(reversed(case["receipts"]), complete=case["complete"]) == result
    assert exact(case["receipts"]) == exact(before)


@pytest.mark.parametrize("case", load_cases("evaluation", "refused"))
def test_the_class_preserves_admission_errors_and_remains_reusable(case):
    engine = PolicyEngine(ruleset=RULESET, fields=FIELDS)
    with pytest.raises(ValueError, match=case["expected"]) as original:
        evaluation.evaluate_receipts(case["receipts"], RULESET, FIELDS, complete=True)
    with pytest.raises(type(original.value)) as wrapped:
        engine.evaluate(case["receipts"], complete=True)
    assert str(wrapped.value) == str(original.value)
    assert engine.evaluate([], complete=True) == evaluation.evaluate_receipts([], RULESET, FIELDS, complete=True)


@pytest.mark.parametrize("case", load_cases("policy_engine", "isolation"))
def test_each_call_uses_a_fresh_context_without_completeness_leakage(case, monkeypatch):
    engine = PolicyEngine(ruleset=RULESET, fields=FIELDS)
    contexts = []
    build_context = evaluation.build_context

    def record_context(*args, **kwargs):
        context = build_context(*args, **kwargs)
        contexts.append(context)
        return context

    monkeypatch.setattr(evaluation, "build_context", record_context)
    runs = [engine.evaluate(**request) for request in case["evaluations"]]
    assert len(contexts) == len(runs)
    assert len({id(context) for context in contexts}) == len(runs)
    assert [[summary(result) for result in run.results] for run in runs] == case["expected"]["runs"]
    for request, run in zip(case["evaluations"], runs, strict=True):
        assert run.context_complete is request["complete"]
        assert engine.evaluate(**request).model_dump_json() == run.model_dump_json()


@pytest.mark.parametrize("case", load_cases("policy_engine", "configuration"))
def test_the_catalog_is_an_immutable_snapshot_at_every_container_depth(case):
    fields = deepcopy(FIELDS)
    engine = PolicyEngine(ruleset=RULESET, fields=fields)
    before = engine.evaluate(case["receipts"], complete=True)
    original, frozen = fields, engine.fields
    for key in case["path"][:-1]:
        original, frozen = original[key], frozen[key]
    key = case["path"][-1]
    original[key] = case["value"]
    with pytest.raises(TypeError):
        frozen[key] = case["value"]
    assert engine.evaluate(case["receipts"], complete=True) == before
    assert selected(summary(before.results[0]), case["expected"]) == case["expected"]


def test_configuration_attributes_cannot_be_reassigned():
    engine = PolicyEngine(ruleset=RULESET, fields=FIELDS)
    with pytest.raises(FrozenInstanceError):
        engine.fields = {}
    with pytest.raises(FrozenInstanceError):
        engine.ruleset = RULESET
    with pytest.raises(TypeError):
        engine.fields["new"] = {}
    assert engine.ruleset == RULESET
    assert engine.ruleset is not RULESET
    assert engine.ruleset.rules[0] is not RULESET.rules[0]
    with pytest.raises(ValidationError):
        engine.ruleset.rules[0].title = "changed"
    with pytest.raises(TypeError):
        engine.ruleset.rules[0].params["new"] = None


def test_configuration_types_are_explicit():
    with pytest.raises(TypeError, match="validated RuleSet"):
        PolicyEngine(ruleset=RULESET.model_dump(), fields=FIELDS)
    with pytest.raises(TypeError, match="catalog mapping"):
        PolicyEngine(ruleset=RULESET, fields=None)
    with pytest.raises(ValueError, match="receipt.entity"):
        PolicyEngine(ruleset=RULESET, fields={})


@pytest.mark.parametrize("case", load_cases("policy_engine", "configuration_refused"))
def test_invalid_configuration_is_rejected_before_evaluation(case, monkeypatch):
    fields = deepcopy(FIELDS)
    for name, value in case["field_changes"].items():
        if value is None:
            fields.pop(name, None)
        else:
            fields[name] = value
    rules = [
        {**rule.model_dump(), **case["rule_changes"].get(rule.rule_id, {})}
        for rule in RULESET.rules
        if rule.rule_id not in case["remove_rules"]
    ]
    ruleset = RuleSet.model_validate({**RULESET.model_dump(), "rules": rules})

    def must_not_evaluate(*args, **kwargs):
        raise AssertionError("configuration validation must not evaluate receipts")

    monkeypatch.setattr(evaluation, "evaluate_receipts", must_not_evaluate)
    with pytest.raises(ValueError, match=case["expected"]):
        PolicyEngine(ruleset=ruleset, fields=fields)


@pytest.mark.parametrize("case", load_cases("policy_engine", "missing_references"))
def test_missing_targets_are_value_errors_even_when_ruleset_validation_was_bypassed(case):
    rule_model = TypeAdapter(Rule)
    rules = tuple(
        rule_model.validate_python({**rule.model_dump(), **case["rule_changes"].get(rule.rule_id, {})})
        for rule in RULESET.rules
    )
    # Valid individual rules can still have dangling cross-rule references.
    unchecked = RuleSet.model_construct(ruleset_version=RULESET.ruleset_version, rules=rules)
    with pytest.raises(ValueError) as caught:
        validate_rules(unchecked, FIELDS)
    assert str(caught.value) == case["expected"]["validator_error"]
    with pytest.raises(ValueError, match=case["expected"]["engine_error"]):
        PolicyEngine(ruleset=unchecked, fields=FIELDS)


@pytest.mark.parametrize("case", load_cases("policy_engine", "catalog_values"))
def test_supported_catalog_scalars_are_preserved_without_mutable_aliases(case):
    fields = deepcopy(FIELDS)
    values = deepcopy(case["values"])
    fields["receipt.merchant_name"]["metadata"] = values
    engine = PolicyEngine(ruleset=RULESET, fields=fields)
    frozen = engine.fields["receipt.merchant_name"]["metadata"]
    assert exact(frozen) == exact(case["expected"]["values"])
    with pytest.raises(TypeError):
        frozen[0] = "changed"
    values.clear()
    assert exact(frozen) == exact(case["expected"]["values"])


def test_initialization_reads_no_policy_files(monkeypatch):
    def must_not_read(*args, **kwargs):
        raise AssertionError("initialization must use only the supplied configuration")

    monkeypatch.setattr(Path, "read_text", must_not_read)
    monkeypatch.setattr(Path, "read_bytes", must_not_read)
    PolicyEngine(ruleset=RULESET, fields=FIELDS)


def test_runtime_imports_do_not_load_the_compiler():
    result = subprocess.run(
        [sys.executable, "-c", "import sys; from expense_validator.engine.policy_engine import PolicyEngine; assert not any(name.startswith('expense_validator.compiler') for name in sys.modules)"],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_snapshot_preserves_the_callers_rule_order():
    reordered = RuleSet.model_validate({**RULESET.model_dump(), "rules": list(reversed(RULESET.rules))})
    engine = PolicyEngine(ruleset=reordered, fields=FIELDS)
    assert engine.ruleset == reordered
    assert tuple(rule.rule_id for rule in engine.ruleset.rules) == tuple(rule.rule_id for rule in reordered.rules)


def test_complete_defaults_to_true_and_must_be_a_boolean():
    engine = PolicyEngine(ruleset=RULESET, fields=FIELDS)
    assert engine.evaluate([]).context_complete is True
    with pytest.raises(TypeError, match="complete"):
        engine.evaluate([], complete=1)


def test_evaluate_delegates_to_the_existing_authoritative_pipeline(monkeypatch):
    engine = PolicyEngine(ruleset=RULESET, fields=FIELDS)
    result = evaluation.evaluate_receipts([], RULESET, FIELDS, complete=True)
    calls = []

    def pipeline(receipts, ruleset, fields, *, complete):
        calls.append((receipts, ruleset, fields, complete))
        return result

    monkeypatch.setattr(evaluation, "evaluate_receipts", pipeline)
    receipts = iter(())
    assert engine.evaluate(receipts, complete=True) is result
    assert calls == [(receipts, engine.ruleset, engine.fields, True)]
