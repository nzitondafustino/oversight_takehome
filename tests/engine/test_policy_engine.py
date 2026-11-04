"""The reusable interface must preserve the functional API's results and trust boundaries."""

from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest
from pydantic import ValidationError
from yaml_cases import exact, load_cases, selected

from expense_validator.compiler.validate import read_fields
from expense_validator.config import load_config
from expense_validator.engine import evaluation
from expense_validator.engine.policy_engine import PolicyEngine
from expense_validator.policy.schema import RuleSet

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
    expected = evaluation.evaluate_receipts("northwind", case["receipts"], RULESET, FIELDS, complete=case["complete"])
    before = deepcopy(case["receipts"])
    result = engine.evaluate("northwind", iter(case["receipts"]), complete=case["complete"])
    assert result == expected
    assert result.model_dump_json() == expected.model_dump_json()
    assert engine.evaluate("northwind", reversed(case["receipts"]), complete=case["complete"]) == result
    assert exact(case["receipts"]) == exact(before)


@pytest.mark.parametrize("case", load_cases("evaluation", "refused"))
def test_the_class_preserves_admission_errors_and_remains_reusable(case):
    engine = PolicyEngine(ruleset=RULESET, fields=FIELDS)
    with pytest.raises(ValueError, match=case["expected"]) as original:
        evaluation.evaluate_receipts(case["customer_id"], case["receipts"], RULESET, FIELDS, complete=True)
    with pytest.raises(type(original.value)) as wrapped:
        engine.evaluate(case["customer_id"], case["receipts"], complete=True)
    assert str(wrapped.value) == str(original.value)
    assert engine.evaluate("after-error", [], complete=True) == evaluation.evaluate_receipts("after-error", [], RULESET, FIELDS, complete=True)


@pytest.mark.parametrize("case", load_cases("policy_engine", "isolation"))
def test_each_call_uses_a_fresh_context_without_customer_or_completeness_leakage(case, monkeypatch):
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
        assert run.customer_id == request["customer_id"]
        assert run.context_complete is request["complete"]
        assert all(result.customer_id == request["customer_id"] for result in run.results)
        assert engine.evaluate(**request).model_dump_json() == run.model_dump_json()


@pytest.mark.parametrize("case", load_cases("policy_engine", "configuration"))
def test_the_catalog_is_an_immutable_snapshot_at_every_container_depth(case):
    fields = deepcopy(FIELDS)
    engine = PolicyEngine(ruleset=RULESET, fields=fields)
    before = engine.evaluate("c", case["receipts"], complete=True)
    original, frozen = fields, engine.fields
    for key in case["path"][:-1]:
        original, frozen = original[key], frozen[key]
    key = case["path"][-1]
    original[key] = case["value"]
    with pytest.raises(TypeError):
        frozen[key] = case["value"]
    assert engine.evaluate("c", case["receipts"], complete=True) == before
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


def test_snapshot_preserves_the_callers_rule_order():
    reordered = RuleSet.model_validate({**RULESET.model_dump(), "rules": list(reversed(RULESET.rules))})
    engine = PolicyEngine(ruleset=reordered, fields=FIELDS)
    assert engine.ruleset == reordered
    assert tuple(rule.rule_id for rule in engine.ruleset.rules) == tuple(rule.rule_id for rule in reordered.rules)


def test_complete_must_still_be_supplied_as_a_boolean():
    engine = PolicyEngine(ruleset=RULESET, fields=FIELDS)
    with pytest.raises(TypeError, match="complete"):
        engine.evaluate("c", [])
    with pytest.raises(TypeError, match="complete"):
        engine.evaluate("c", [], complete=1)


def test_evaluate_delegates_to_the_existing_authoritative_pipeline(monkeypatch):
    engine = PolicyEngine(ruleset=RULESET, fields=FIELDS)
    result = evaluation.evaluate_receipts("c", [], RULESET, FIELDS, complete=True)
    calls = []

    def pipeline(customer_id, receipts, ruleset, fields, *, complete):
        calls.append((customer_id, receipts, ruleset, fields, complete))
        return result

    monkeypatch.setattr(evaluation, "evaluate_receipts", pipeline)
    receipts = iter(())
    assert engine.evaluate("c", receipts, complete=True) is result
    assert calls == [("c", receipts, engine.ruleset, engine.fields, True)]
