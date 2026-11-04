"""Compares the regex and LLM readings of a policy document, field by field.

Each field ends up as one of:

    agreed       both readings match
    disagree     they differ, so a developer decides
    unconfirmed  both found no thresholds, but the clause has numbers in it

A rule only one reading found is reported as only_regex or only_llm. Agreement covers
these shared fields only. A rule's conditions and carve-outs still need a developer.

Run with: python -m expense_validator.compiler.agreement
This needs OPENROUTER_API_KEY in .env and llm.model in config.yaml, and sends the
policy text to OpenRouter.
"""

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from expense_validator.compiler.extract import RULE_ID, Clause, PolicyDocument, extract_document
from expense_validator.config import load_config
from expense_validator.compiler.llm_extract import (
    Complete,
    LlmDocument,
    LlmRule,
    cached,
    extract_with_llm,
    openrouter,
)

NUMBER_IN_TEXT = re.compile(r"\d|\b(?:two|three|four|five|six|seven|eight|nine|ten)\b", re.IGNORECASE)


@dataclass(frozen=True)
class FieldResult:
    rule_id: str
    field: str
    status: str
    regex: object = None
    llm: object = None


def _plain(text: str) -> str:
    return " ".join(text.replace("*", "").split())


def _compare_clause(clause: Clause, rule: LlmRule) -> list[FieldResult]:
    regex_thresholds = {(t.value, t.unit) for t in clause.thresholds}
    llm_thresholds = {(t.value, t.unit) for t in rule.thresholds}
    pairs = {
        "title": (clause.title, rule.title.rstrip(".")),
        "section": (clause.section, rule.section),
        "severities": (set(clause.severities), set(rule.severities)),
        "review_signal": (clause.review_signal, rule.review_signal),
        "thresholds": (regex_thresholds, llm_thresholds),
        "references": (set(clause.references), set(rule.references)),
    }
    results = [
        FieldResult(clause.rule_id, field, "agreed" if regex == llm else "disagree", regex, llm)
        for field, (regex, llm) in pairs.items()
    ]
    if not regex_thresholds and not llm_thresholds and NUMBER_IN_TEXT.search(RULE_ID.sub("", clause.text)):
        results = [r if r.field != "thresholds" else FieldResult(r.rule_id, r.field, "unconfirmed") for r in results]
    quote_found = _plain(rule.quote) in _plain(clause.text)
    results.append(FieldResult(clause.rule_id, "quote", "agreed" if quote_found else "disagree", None, rule.quote))
    return results


def compare_document(document: PolicyDocument, llm: LlmDocument) -> list[FieldResult]:
    llm_rules = {rule.rule_id: rule for rule in llm.rules}
    results = []
    for clause in document.clauses:
        rule = llm_rules.pop(clause.rule_id, None)
        if rule is None:
            results.append(FieldResult(clause.rule_id, "rule", "only_regex"))
        else:
            results.extend(_compare_clause(clause, rule))
    results.extend(FieldResult(rule_id, "rule", "only_llm") for rule_id in llm_rules)
    return results


def agreement_report(docs: Path, complete: Complete) -> list[FieldResult]:
    results = []
    for path in sorted(docs.glob("*.md")):
        text = path.read_text()
        results.extend(compare_document(extract_document(text, path.name), extract_with_llm(text, complete)))
    return results


def main() -> int:
    config = load_config()
    if config.openrouter_api_key is None or not config.llm.model:
        print("Set OPENROUTER_API_KEY in .env and llm.model in config.yaml.", file=sys.stderr)
        return 1
    complete = openrouter(config.llm, config.openrouter_api_key.get_secret_value())
    results = agreement_report(config.paths.policy_docs, cached(complete, config.llm.model, config.paths.llm_cache))
    for status in ("agreed", "disagree", "unconfirmed", "only_regex", "only_llm"):
        print(f"{status}: {sum(result.status == status for result in results)}")
    for result in results:
        if result.status != "agreed":
            print(f"{result.rule_id} {result.field} {result.status}: regex={result.regex} llm={result.llm}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
