"""Runs the cases in cases/engine/models.yaml, and checks the models against all the real data."""

import json
from decimal import Decimal

import pytest
from pydantic import ValidationError
from receipt_rows import ROWS, without_answers
from yaml_cases import exact, load_cases, selected

from expense_validator.config import load_config
from expense_validator.engine.models import (
    CLASSIFIED_FIELDS,
    EvaluationResult,
    Evidence,
    Finding,
    LineItem,
    Outcome,
    Receipt,
    Scope,
)
from expense_validator.policy.schema import CheckRule, RuleSet

RULES = {rule.rule_id: rule for rule in RuleSet.from_json(load_config().paths.ruleset.read_text()).rules}
MODELS = {model.__name__: model for model in (Receipt, LineItem, Evidence, Finding, EvaluationResult)}


def assert_values(built, expected):
    assert exact(selected(built.model_dump(mode="json"), expected)) == exact(expected)


@pytest.mark.parametrize("case", load_cases("models", "accepted"))
def test_a_model_is_accepted_round_trips_and_cannot_be_changed(case):
    model = MODELS[case["model"]]
    built = model.model_validate(case["data"])
    assert_values(built, case["expected"])
    assert model.model_validate_json(built.model_dump_json()) == built
    assert model.model_validate(built.model_dump()) == built
    for name in model.model_fields:
        with pytest.raises(ValidationError):
            setattr(built, name, None)


@pytest.mark.parametrize("case", load_cases("models", "refused"))
def test_a_model_is_refused(case):
    with pytest.raises(ValidationError, match=case["expected"]):
        MODELS[case["model"]].model_validate(case["data"])


@pytest.mark.parametrize("case", load_cases("models", "json_refused"))
def test_a_model_is_refused_as_json(case):
    with pytest.raises(ValidationError, match=case["expected"]):
        MODELS[case["model"]].model_validate_json(json.dumps(case["data"]))


@pytest.mark.parametrize("case", load_cases("models", "left_out_refused"))
def test_a_required_field_cannot_be_left_out(case):
    data = {name: value for name, value in case["data"].items() if name != case["field"]}
    with pytest.raises(ValidationError, match=case["expected"]):
        MODELS[case["model"]].model_validate(data)


@pytest.mark.parametrize("case", load_cases("models", "evidence_numbers"))
def test_evidence_takes_a_decimal_as_exact_text(case):
    built = Evidence(fact="receipt.total_amount_usd", value=Decimal(case["decimal"]))
    assert_values(built, case["expected"])
    assert Evidence.model_validate_json(built.model_dump_json()) == built


@pytest.mark.parametrize("case", load_cases("models", "classified"))
def test_classifier_output_is_accepted_and_the_printed_fields_are_kept(case):
    before = LineItem.model_validate(case["line"])
    after = before.with_classification(case["output"])
    assert_values(after, case["expected"])
    assert after.model_dump(exclude=CLASSIFIED_FIELDS) == before.model_dump(exclude=CLASSIFIED_FIELDS)


@pytest.mark.parametrize("case", load_cases("models", "classified_refused"))
def test_invalid_classifier_output_is_refused(case):
    with pytest.raises(ValueError, match=case["expected"]):
        LineItem.model_validate(case["line"]).with_classification(case["output"])


@pytest.mark.parametrize("case", load_cases("models", "receipt_classified"))
def test_a_receipt_keeps_its_fields_when_classified(case):
    before = Receipt.model_validate(case["receipt"])
    after = before.with_classifications(case["outputs"])
    assert_values(after, case["expected"])
    assert after.model_dump(exclude={"line_items"}) == before.model_dump(exclude={"line_items"})


@pytest.mark.parametrize("case", load_cases("models", "receipt_classified_refused"))
def test_a_receipt_refuses_invalid_classifier_output(case):
    with pytest.raises(ValueError, match=case["expected"]):
        Receipt.model_validate(case["receipt"]).with_classifications(case["outputs"])


def test_every_classified_field_is_optional_on_a_line():
    assert all(LineItem.model_fields[name].default is None for name in CLASSIFIED_FIELDS)


def test_shared_words_match_the_rule_schema():
    assert Outcome == CheckRule.model_fields["outcome"].annotation
    assert Scope == CheckRule.model_fields["scope"].annotation


# --- Against the real data and rules ---


@pytest.mark.parametrize("row", ROWS, ids=lambda row: row["receipt_id"])
def test_every_receipt_in_the_data_loads_and_round_trips(row):
    built = Receipt.model_validate(without_answers(row))
    assert len(built.line_items) == len(row["line_items"])
    assert Receipt.model_validate_json(built.model_dump_json()) == built


@pytest.mark.parametrize("row", ROWS, ids=lambda row: row["receipt_id"])
def test_every_answer_in_the_data_fits_a_result(row):
    """The shapes can hold what is correct: an answer citing several lines is one finding per line."""
    labels = row["labels"]
    findings = [
        Finding(
            rule_id=entry["rule_id"],
            outcome=RULES[entry["rule_id"]].outcome,
            severity=entry.get("severity"),
            scope=RULES[entry["rule_id"]].scope,
            receipt_id=row["receipt_id"],
            line_ids=targets,
            explanation=entry["reason"],
            policy_doc=entry["policy_doc"],
            source_quote=RULES[entry["rule_id"]].source_quote,
            provenance=RULES[entry["rule_id"]].provenance,
        )
        for entry in labels["expected_exceptions"] + labels["review_signals"]
        for targets in ([[line_id] for line_id in entry["line_ids"]] if entry.get("line_ids") else [[]])
    ]
    built = EvaluationResult(
        receipt_id=row["receipt_id"],
        ruleset_version="v1",
        disposition=labels["disposition"],
        findings=findings,
    )
    assert EvaluationResult.model_validate_json(built.model_dump_json()) == built


def test_a_real_row_still_carrying_its_answers_is_refused():
    with pytest.raises(ValidationError, match="labels"):
        Receipt.model_validate(ROWS[0])
