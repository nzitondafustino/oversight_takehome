"""finalize, every classifier's last step: recipient extraction, conflicts and the check of the whole line."""

import json
from decimal import Decimal

import pytest
from pydantic import ValidationError
from yaml_cases import load_cases

from expense_validator.classification.backends import jev
from expense_validator.classification.core.common import finalize
from expense_validator.classification.core.models import ClassifiedLine
from expense_validator.classification.core.recipients import recipient_mentions
from expense_validator.compiler.validate import read_fields
from expense_validator.config import JevThresholds
from expense_validator.engine.policy_engine import PolicyEngine
from expense_validator.engine.uncertainty import assess_uncertainty
from expense_validator.exceptions import ClassificationError
from expense_validator.normalization import normalize_receipt
from expense_validator.policy.schema import RuleSet
from expense_validator.run_receipts import load_receipts
from classification.test_jev import CONFIG, MODEL, ONE, QUESTIONS, VOCABULARY, FakeOpenRouter, _install, _jev_validator, jev_response, offline  # noqa: F401  (offline is a fixture)

BASE = ClassifiedLine(ONE.lines[0].line_id, category="food_beverage", restricted=False, confidence=Decimal("0.9"))
ENGINE = PolicyEngine(RuleSet.from_json(CONFIG.paths.ruleset.read_text()), read_fields(CONFIG.paths.rule_fields.read_text()))


@pytest.mark.parametrize("case", load_cases("classification", "recipient_extractions"))
def test_explicit_names_preserve_spelling_and_source_offsets(case):
    found = recipient_mentions(case["text"], case["translation"])
    assert [m.text for m in found] == case["expected"]["names"]
    for mention in found:
        source = case["text"] if mention.source == "description_normalized" else case["translation"]
        assert source[mention.start:mention.end] == mention.text
        with pytest.raises(ValidationError):
            mention.text = "changed"


@pytest.mark.parametrize("case", load_cases("classification", "finalize"))
def test_finalize_extracts_recipients_and_checks_the_line(case):
    source = ONE.lines[0] if case["description"] is None else ONE.lines[0].model_copy(update={"description_normalized": case["description"]})
    changes = dict(case["line"])
    if changes.get("confidence") is not None:
        changes["confidence"] = Decimal(changes["confidence"])
    result = finalize(BASE._replace(**changes), source, VOCABULARY)
    expected = dict(case["expected"])
    error = expected.pop("error")
    assert (result.error is None) if error is None else (result.error is not None and error in result.error), result.error
    if "conflict" in expected:
        assert ("evidence_conflict" in (result.details or {})) is expected.pop("conflict")
    if "confidence" in expected:
        assert (None if result.confidence is None else str(result.confidence)) == expected.pop("confidence")
    assert {name: getattr(result, name) for name in expected} == expected


def test_a_line_from_another_source_is_refused():
    with pytest.raises(ClassificationError, match="line identity mismatch"):
        finalize(BASE._replace(line_id="other"), ONE.lines[0], VOCABULARY)


def test_a_translation_that_names_someone_else_is_a_conflict_kept_in_the_details_and_sent_to_review():
    receipt = ONE.receipt.model_copy(update={"line_items": (ONE.receipt.line_items[0].model_copy(update={"description_raw": "Flowers for A. Moreau", "language": "fr"}),)})

    class Translator:
        def translate(self, text, language):
            return "Flowers for R. Delgado"

    source = normalize_receipt(receipt, Translator()).lines[0]
    result = finalize(BASE._replace(category="gift", restricted=True, recipient_name="A. Moreau", recipient_type="client"), source, VOCABULARY)
    assert result.recipient_name is None and result.recipient_type == "unidentified"
    conflict = result.details["evidence_conflict"]
    assert {m["text"] for m in conflict["recipient_mentions"]} == {"A. Moreau", "R. Delgado"}
    assert (conflict["recipient_name"], conflict["recipient_type"]) == ("A. Moreau", "client")
    staged = receipt.with_classifications({result.line_id: {"category": result.category, "confidence": result.confidence, "recipient_type": result.recipient_type}})
    assessment = assess_uncertainty(staged, ENGINE.ruleset, ENGINE.fields)
    assert any(s.field == "recipient_type" and s.state == "unknown" for s in assessment.signals)


@pytest.mark.parametrize("case", load_cases("classification", "jev_thresholds"))
def test_jev_thresholds_are_checked(case):
    if case["expected"]["refused"]:
        with pytest.raises(ValidationError):
            JevThresholds(**case["thresholds"])
    else:
        JevThresholds(**case["thresholds"])


def test_jev_thresholds_change_acceptance_without_changing_the_native_answers():
    raw = json.dumps(jev_response(QUESTIONS, {"category": ("food_beverage", 0.9), "recipient_type": ("client", 0.7)}))
    default = jev.classify_line(ONE, ONE.lines[0], lambda questions, state: raw, MODEL, VOCABULARY)
    stricter = jev.classify_line(ONE, ONE.lines[0], lambda questions, state: raw, MODEL, VOCABULARY, JevThresholds(recipient_type=0.8))
    assert default.recipient_type == "client" and stricter.recipient_type is None
    assert default.details["answers"] == stricter.details["answers"]
    assert stricter.details["answers"]["recipient_type"]["probabilities"]["client"] == 0.7
    assert stricter.details["attribute_thresholds"]["recipient_type"] == 0.8


@pytest.mark.parametrize("case", load_cases("classification", "jev_uncertainty"))
def test_a_jev_category_probability_is_judged_by_the_engine(case):
    raw = json.dumps(jev_response(QUESTIONS, {"category": ("food_beverage", case["probability"])}))
    line = jev.classify_line(ONE, ONE.lines[0], lambda questions, state: raw, MODEL, VOCABULARY)
    fields = {name: value for name, value in line._asdict().items() if name in ("category", "restricted", "confidence") and value is not None}
    assessment = assess_uncertainty(ONE.receipt.with_classifications({line.line_id: fields}), ENGINE.ruleset, ENGINE.fields)
    assert any(s.finding.line_ids == (line.line_id,) for s in assessment.signals) is case["expected"]["review"]


def test_a_low_coarse_confidence_is_judged_by_the_engine():
    line = BASE._replace(confidence=Decimal("0.3"))
    receipt = ONE.receipt.with_classifications({line.line_id: {"category": line.category, "restricted": line.restricted, "confidence": line.confidence}})
    assessment = assess_uncertainty(receipt, ENGINE.ruleset, ENGINE.fields)
    assert any(s.field == "category" and s.state == "low_confidence" for s in assessment.signals)
    assert assessment.evaluation_receipt.line_items[0].category is None


def test_configured_thresholds_reach_the_public_facade(offline):
    _install(offline, FakeOpenRouter(lambda state: {"category": ("jewelry", 0.9), "recipient_type": ("client", 0.7)}))
    receipt = next(r for r in load_receipts() if r.receipt_id == "RCP-018")
    result = _jev_validator(jev_thresholds=JevThresholds(recipient_type=0.8)).validate_batch([receipt], context_complete=True).results[0]
    assert result.line_items[0].attributes["recipient_name"] == "A. Moreau"
    assert result.line_items[0].attributes["recipient_type"] == "unidentified"
    assert any(u.field == "recipient_type" and u.state == "unknown" for u in result.uncertainty)
