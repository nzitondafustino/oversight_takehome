"""Select evidence from an exception's verified policy clause.

Use a unique restrictive sentence containing the signed quote; otherwise keep
the verified quote.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from expense_validator.compiler.extract import extract_document
from expense_validator.exceptions import EvidenceError

# What makes a sentence state a restriction, rather than how a finding is reported.
__all__ = ["Evidence", "EvidenceError", "PolicyEvidence", "passages", "plain", "policy_evidence"]

RESTRICTION = re.compile(
    r"\b(?:not reimbursable|never|prohibited|cap|capped|requires?|must|only|up to|(?:critical|high|medium|low) exception)\b",
    re.IGNORECASE,
)
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")




@dataclass(frozen=True)
class Evidence:
    text: str
    expanded: bool  # whether text is more than the signed quote
    reason: str | None = None  # why it was not expanded, when it was not


def plain(text: str) -> str:
    """Remove Markdown emphasis and collapse whitespace without changing words."""
    return " ".join(text.replace("*", "").split())


def passages(clause_text: str) -> list[str]:
    """Split a clause into sentences. List items and table rows are passages of their own."""
    found = []
    for block in re.split(r"\n\s*\n", clause_text):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if any(line.startswith(("- ", "|")) for line in lines):
            found.extend(plain(line.removeprefix("- ")) for line in lines)
        else:
            found.extend(SENTENCE_END.split(plain(" ".join(lines))))
    return [passage for passage in found if passage]


def policy_evidence(policy_text: str, policy_doc: str, rule_id: str, source_quote: str) -> Evidence:
    try:
        document = extract_document(policy_text, policy_doc)
    except ValueError as error:
        raise EvidenceError(f"{policy_doc} cannot be read as a policy: {error}") from error
    # Extraction refuses a document that repeats a rule ID, so a rule has one clause or none.
    clause = next((clause for clause in document.clauses if clause.rule_id == rule_id), None)
    if clause is None:
        raise EvidenceError(f"{policy_doc} has no clause for {rule_id}")
    quote, whole = plain(source_quote), plain(clause.text)
    if not quote or quote not in whole:
        raise EvidenceError(f"{rule_id}: its source_quote is not in its clause in {policy_doc}")
    if len(set(clause.severities)) > 1:
        return Evidence(quote, False, "the clause states more than one severity, so one sentence could misdescribe the finding")
    if whole.count(quote) != 1:
        return Evidence(quote, False, "the quote occurs more than once in the clause")
    holding = [passage for passage in passages(clause.text) if quote in passage]
    if len(holding) != 1:
        return Evidence(quote, False, "no single sentence holds the whole quote")
    sentence = holding[0]
    if sentence == quote:
        return Evidence(quote, False, "the quote is already its whole sentence")
    if not RESTRICTION.search(sentence):
        return Evidence(quote, False, "the sentence holding the quote says how the finding is reported, not what is restricted")
    if sentence not in whole:
        raise EvidenceError(f"{rule_id}: the selected passage is not in its clause")
    return Evidence(sentence, True)


class PolicyEvidence:
    """Evidence for a run's exceptions, reading each policy document once."""

    def __init__(self, docs: Path):
        self.docs = docs
        self.texts: dict[str, str] = {}

    def __call__(self, policy_doc: str, rule_id: str, source_quote: str) -> Evidence:
        if policy_doc not in self.texts:
            path = self.docs / policy_doc
            if path.name != policy_doc or not path.is_file():
                raise EvidenceError(f"no policy document {policy_doc!r} in {self.docs}")
            self.texts[policy_doc] = path.read_text()
        return policy_evidence(self.texts[policy_doc], policy_doc, rule_id, source_quote)
