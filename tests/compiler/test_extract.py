"""Runs the cases in cases/compiler/extraction.yaml, then checks what must hold for every document."""

from collections import Counter
from pathlib import Path

import pytest
from yaml_cases import load_cases

from expense_validator.compiler.extract import Clause, PolicyDocument, extract_document

DOCS = Path(__file__).parents[2] / "data" / "policy-docs"
RULES_IN_POLICY = 41  # stated in data/README.md


def read_document(policy_doc: str) -> PolicyDocument:
    return extract_document((DOCS / policy_doc).read_text(), policy_doc)


def joined(text: str) -> str:
    return " ".join(text.split())


def thresholds_of(clause: Clause) -> list[dict]:
    return [{"value": str(threshold.value), "unit": threshold.unit} for threshold in clause.thresholds]


def check_document(document: PolicyDocument, expected: dict) -> None:
    assert document.title == expected["title"]
    assert [clause.rule_id for clause in document.clauses] == expected["rule_ids"]
    assert len(document.context) == len(expected["context_starts"])
    for block, start in zip(document.context, expected["context_starts"]):
        assert block.startswith(start)


@pytest.mark.parametrize("case", load_cases("extraction", "documents"))
def test_document(case):
    check_document(read_document(case["policy_doc"]), case["expected"])


@pytest.mark.parametrize("case", load_cases("extraction", "clauses"))
def test_clause(case):
    clause = next(c for c in read_document(case["policy_doc"]).clauses if c.rule_id == case["rule_id"])
    expected = case["expected"]
    assert clause.policy_doc == case["policy_doc"]
    assert joined(clause.text).startswith(expected["starts"])
    assert joined(clause.text).endswith(expected["ends"])
    assert {
        "title": clause.title,
        "section": clause.section,
        "severities": clause.severities,
        "review_signal": clause.review_signal,
        "thresholds": thresholds_of(clause),
        "references": clause.references,
    } == {name: expected[name] for name in expected if name not in {"starts", "ends"}}


@pytest.mark.parametrize("case", load_cases("extraction", "inline"))
def test_inline(case):
    document = extract_document(case["text"], "doc.md")
    check_document(document, case["expected"])
    by_id = {clause.rule_id: clause for clause in document.clauses}
    for rule_id, text in case["expected"].get("texts", {}).items():
        assert joined(by_id[rule_id].text) == text
    for rule_id, thresholds in case["expected"].get("thresholds", {}).items():
        assert thresholds_of(by_id[rule_id]) == thresholds


@pytest.mark.parametrize("case", load_cases("extraction", "refused"))
def test_refused(case):
    with pytest.raises(ValueError, match=case["expected"]):
        extract_document(case["text"], "doc.md")


def all_documents() -> list[PolicyDocument]:
    return [read_document(path.name) for path in sorted(DOCS.glob("*.md"))]


def test_every_rule_and_document_has_a_case():
    documents = {case.values[0]["policy_doc"] for case in load_cases("extraction", "documents")}
    assert documents == {path.name for path in DOCS.glob("*.md")}
    rule_ids = [clause.rule_id for document in all_documents() for clause in document.clauses]
    assert len(rule_ids) == len(set(rule_ids)) == RULES_IN_POLICY
    assert {case.values[0]["rule_id"] for case in load_cases("extraction", "clauses")} == set(rule_ids)


def test_references_point_at_real_rules():
    documents = all_documents()
    rule_ids = {clause.rule_id for document in documents for clause in document.clauses}
    for document in documents:
        for clause in document.clauses:
            assert set(clause.references) <= rule_ids, clause.rule_id


def test_clause_text_is_kept_exactly_as_written():
    for document in all_documents():
        source = (DOCS / document.policy_doc).read_text()
        for clause in document.clauses:
            assert clause.text in source, clause.rule_id


def test_no_text_is_lost_or_repeated():
    for document in all_documents():
        lines = (DOCS / document.policy_doc).read_text().splitlines()
        source = [word for line in lines if not line.startswith("#") for word in line.split()]
        parts = document.context + [f"**{c.rule_id} — {c.title}.** {c.text}" for c in document.clauses]
        assert Counter(source) == Counter(word for part in parts for word in part.split()), document.policy_doc
