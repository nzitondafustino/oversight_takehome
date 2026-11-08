"""Runs the cases in cases/evidence.yaml, then checks the evidence in the committed receipt output."""

import json

import pytest
from yaml_cases import load_cases

from expense_validator import run_receipts as runner
from expense_validator.compiler.extract import extract_document
from expense_validator.config import ROOT, load_config
from expense_validator.evidence import EvidenceError, PolicyEvidence, passages, plain, policy_evidence
from expense_validator.policy.schema import CheckRule, RuleSet

PATHS = load_config().paths
RULES = {rule.rule_id: rule for rule in RuleSet.from_json(PATHS.ruleset.read_text()).rules}
OUTPUT = [json.loads(line) for line in runner.OUTPUT.read_text().splitlines()]
EXCEPTIONS = [(output["receipt_id"], exception) for output in OUTPUT for exception in output["exceptions"]]


def _clause(policy_doc, rule_id):
    return next(c for c in extract_document((PATHS.policy_docs / policy_doc).read_text(), policy_doc).clauses if c.rule_id == rule_id)


@pytest.mark.parametrize("case", load_cases("evidence", "cases"))
def test_evidence_is_the_quotes_own_sentence_or_the_quote(case):
    evidence = policy_evidence(case["text"], "doc.md", case["rule_id"], case["quote"])
    assert {"text": evidence.text, "expanded": evidence.expanded} == case["expected"]


@pytest.mark.parametrize("case", load_cases("evidence", "refused"))
def test_unverifiable_policy_text_is_an_error(case):
    with pytest.raises(EvidenceError, match=case["expected"]):
        policy_evidence(case["text"], "doc.md", case["rule_id"], case["quote"])


def test_house_01_on_rcp_014_names_what_household_goods_are():
    exception = next(e for receipt_id, e in EXCEPTIONS if receipt_id == "RCP-014" and e["rule_id"] == "HOUSE-01")
    assert exception["evidence"].startswith("Cleaning supplies, laundry products, household paper goods")
    assert plain(exception["source_quote"]) in exception["evidence"] and len(exception["evidence"]) > 3 * len(exception["source_quote"])


def test_alc_03_keeps_the_retail_alcohol_restriction():
    exception = next(e for receipt_id, e in EXCEPTIONS if receipt_id == "RCP-014" and e["rule_id"] == "ALC-03")
    assert exception["evidence"] == "Alcohol purchased from a package store, liquor store, or any other merchant that is not a restaurant, bar or hotel is never reimbursable, regardless of business context."


def test_every_evidence_is_a_passage_of_its_own_clause_and_no_other():
    for receipt_id, exception in EXCEPTIONS:
        clause = _clause(exception["policy_doc"], exception["rule_id"])
        assert exception["source_quote"] == RULES[exception["rule_id"]].source_quote, receipt_id
        assert exception["evidence"] in plain(clause.text)
        if exception["evidence"] != plain(exception["source_quote"]):
            assert exception["evidence"] in passages(clause.text)
            others = [c for c in extract_document((PATHS.policy_docs / exception["policy_doc"]).read_text(), exception["policy_doc"]).clauses if c.rule_id != exception["rule_id"]]
            assert not [c.rule_id for c in others if exception["evidence"] in plain(c.text)], (receipt_id, exception["rule_id"])


def test_rules_whose_quote_sentence_is_not_the_restriction_keep_their_quote():
    kept = {e["rule_id"] for _, e in EXCEPTIONS if e["evidence"] == plain(e["source_quote"])}
    assert kept == {"GIFT-04", "REG-01", "TIME-03"}


def test_a_clause_with_two_severities_keeps_its_quote():
    health = RULES["HEALTH-01"]
    evidence = PolicyEvidence(PATHS.policy_docs)(health.policy_doc, "HEALTH-01", health.source_quote)
    assert not evidence.expanded and evidence.text == plain(health.source_quote) and "more than one severity" in evidence.reason


def test_every_exception_rule_in_the_ruleset_gets_verified_evidence():
    evidence = PolicyEvidence(PATHS.policy_docs)
    for rule in RULES.values():
        if isinstance(rule, CheckRule) and rule.outcome == "exception":
            found = evidence(rule.policy_doc, rule.rule_id, rule.source_quote)
            assert plain(rule.source_quote) in found.text and found.text in plain(_clause(rule.policy_doc, rule.rule_id).text)


def test_a_missing_policy_document_stops_the_run_and_returns_nothing(tmp_path):
    from expense_validator import ExpenseValidator
    from expense_validator.models import ValidatorSettings

    empty = tmp_path / "docs"
    empty.mkdir()
    settings = ValidatorSettings.from_config(ROOT / "config.yaml", mode="replay", cache_dir=ROOT / "evaluation" / "final" / "replay")
    validator = ExpenseValidator(settings.model_copy(update={"policy_docs_path": empty}))
    with pytest.raises(EvidenceError, match="no policy document"):
        validator.validate_batch(runner.load_receipts(), context_complete=True)


@pytest.mark.parametrize("name", ["../rules/ruleset.json", "missing.md"])
def test_a_policy_document_outside_the_folder_is_refused(name):
    with pytest.raises(EvidenceError, match="no policy document"):
        PolicyEvidence(PATHS.policy_docs)(name, "ALC-03", "x")


def test_dispositions_exceptions_and_lines_still_match_the_answers():
    answers = {json.loads(line)["receipt_id"]: json.loads(line)["labels"] for line in (ROOT / "data" / "receipts.jsonl").read_text().splitlines()}
    for output in OUTPUT:
        gold = answers[output["receipt_id"]]
        assert output["disposition"] == gold["disposition"]
        assert {e["rule_id"]: sorted(e["line_ids"]) for e in output["exceptions"]} == {e["rule_id"]: sorted(e["line_ids"]) for e in gold["expected_exceptions"]}
