"""Compare policy readings and reconcile agreed, disputed, and missing fields."""

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import get_args

import yaml
from pydantic import BaseModel, ConfigDict, Field

from expense_validator.compiler.extract import NUMBER_WORDS, REVIEW_SIGNAL, RULE_ID, Clause, PolicyDocument, extract_document, thresholds_in
from expense_validator.compiler.llm_extract import (
    Complete,
    LlmDocument,
    LlmRule,
    cache_key,
    cached,
    extract_with_llm,
    openrouter,
)
from expense_validator.config import load_config
from expense_validator.policy.vocabulary import Severity, Unit

NUMBER_IN_TEXT = re.compile(r"\d|\b(?:two|three|four|five|six|seven|eight|nine|ten)\b", re.IGNORECASE)
STATUSES = ("agreed", "disagree", "unconfirmed", "only_regex", "only_llm")
UNRESOLVED = frozenset(STATUSES) - {"agreed"}


@dataclass(frozen=True)
class FieldResult:
    """One field of one rule, with JSON-ready canonical values.

    Attributes:
        clause_hash: sha256 of the clause text, or of the whole document for a rule only the LLM found.
    """

    rule_id: str
    field: str
    status: str
    regex: object = None
    llm: object = None
    policy_doc: str | None = None
    clause_hash: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _plain(text: str) -> str:
    return " ".join(text.replace("*", "").split())


def _number(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _thresholds(found) -> list[dict]:
    return sorted(({"value": _number(Decimal(t.value)), "unit": t.unit} for t in found), key=lambda t: (t["unit"], Decimal(t["value"])))


def _compare_clause(clause: Clause, rule: LlmRule, policy_doc: str) -> list[FieldResult]:
    regex_thresholds, llm_thresholds = _thresholds(clause.thresholds), _thresholds(rule.thresholds)
    pairs = {
        "title": (clause.title, rule.title.rstrip(".")),
        "section": (clause.section, rule.section),
        "severities": (sorted(set(clause.severities)), sorted(set(rule.severities))),
        "review_signal": (clause.review_signal, rule.review_signal),
        "thresholds": (regex_thresholds, llm_thresholds),
        "references": (sorted(set(clause.references)), sorted(set(rule.references))),
    }
    where = {"policy_doc": policy_doc, "clause_hash": _sha256(clause.text)}
    results = [
        FieldResult(clause.rule_id, field, "agreed" if regex == llm else "disagree", regex, llm, **where)
        for field, (regex, llm) in pairs.items()
    ]
    if not regex_thresholds and not llm_thresholds and NUMBER_IN_TEXT.search(RULE_ID.sub("", clause.text)):
        results = [r if r.field != "thresholds" else FieldResult(r.rule_id, r.field, "unconfirmed", [], [], **where) for r in results]
    quote_found = _plain(rule.quote) in _plain(clause.text)
    results.append(FieldResult(clause.rule_id, "quote", "agreed" if quote_found else "disagree", None, rule.quote, **where))
    return results


def compare_document(document: PolicyDocument, llm: LlmDocument, text: str | None = None) -> list[FieldResult]:
    """Compare one document's regex and LLM readings field by field."""
    llm_rules = {rule.rule_id: rule for rule in llm.rules}
    results = []
    for clause in document.clauses:
        rule = llm_rules.pop(clause.rule_id, None)
        if rule is None:
            results.append(FieldResult(clause.rule_id, "rule", "only_regex", "present", None, document.policy_doc, _sha256(clause.text)))
        else:
            results.extend(_compare_clause(clause, rule, document.policy_doc))
    document_hash = None if text is None else _sha256(text)
    results.extend(FieldResult(rule_id, "rule", "only_llm", None, "present", document.policy_doc, document_hash) for rule_id in llm_rules)
    return results


def agreement_report(docs: Path, complete: Complete) -> list[FieldResult]:
    results = []
    for path in sorted(docs.glob("*.md")):
        text = path.read_text()
        results.extend(compare_document(extract_document(text, path.name), extract_with_llm(text, complete), text))
    return results


def counts(results: list[FieldResult]) -> dict[str, int]:
    return {status: sum(result.status == status for result in results) for status in STATUSES}


def output_sha256(output: dict) -> str:
    """Return the hash an approval binds to: the output without its own hash."""
    content = {name: value for name, value in output.items() if name != "output_sha256"}
    return _sha256(json.dumps(content, sort_keys=True, ensure_ascii=False, default=str))


def agreement_output(
    documents: dict[str, str],
    results: list[FieldResult],
    reconciliation: "Reconciliation",
    reading_errors: dict[str, str] | None,
    reading_kind: str,
) -> dict:
    """Build the reviewer-facing agreement output.

    Each rule is agreed, resolved or needs_review. The output is deterministic, so equal inputs give an equal hash.
    """
    accepted = {(r["policy_doc"], r["rule_id"], r["field"]): r["resolution"] for r in reconciliation.resolved}
    reading_errors = reading_errors or {}
    out_documents, rule_statuses = [], []
    for policy_doc, text in sorted(documents.items()):
        by_rule: dict[str, list[FieldResult]] = {}
        for result in results:
            if result.policy_doc == policy_doc:
                by_rule.setdefault(result.rule_id, []).append(result)
        rules = []
        for rule_id, fields in by_rule.items():
            agreed = [{"field": r.field, "value": r.llm if r.field == "quote" else r.regex} for r in fields if r.status == "agreed"]
            disagreed = []
            for r in fields:
                if r.status != "agreed":
                    resolution = accepted.get((policy_doc, rule_id, r.field))
                    disagreed.append({"field": r.field, "status": r.status, "regex": r.regex, "llm": r.llm, "resolution": None if resolution is None else {name: resolution[name] for name in ("value", "reviewer", "rationale")}})
            status = "agreed" if not disagreed else "resolved" if all(item["resolution"] for item in disagreed) else "needs_review"
            rule_statuses.append(status)
            rules.append({"rule_id": rule_id, "clause_hash": fields[0].clause_hash, "status": status, "agreed": agreed, "disagreed": disagreed})
        entry = {"policy_doc": policy_doc, "document_sha256": _sha256(text), "rules": rules}
        if policy_doc in reading_errors:
            entry["reading_error"] = reading_errors[policy_doc]
        out_documents.append(entry)
    summary = {
        "documents": len(documents),
        "rules": len(rule_statuses),
        "rules_agreed": rule_statuses.count("agreed"),
        "rules_resolved": rule_statuses.count("resolved"),
        "rules_needing_review": rule_statuses.count("needs_review"),
        "fields": counts(results),
        "resolution_errors": reconciliation.errors,
        "reading_errors": len(reading_errors),
    }
    output = {
        "reading_kind": reading_kind,
        "summary": summary,
        "ready_for_approval": reconciliation.publishable and not reading_errors,
        "documents": out_documents,
    }
    return {**output, "output_sha256": output_sha256(output)}


def review_agreement(docs: Path, complete: Complete, resolutions: list["Resolution"], reading_kind: str) -> dict:
    """Read every document both ways, reconcile, and return the agreement output.

    Args:
        docs: Folder of policy documents.
        complete: LLM completion function.
        resolutions: Reviewer resolutions to apply.
        reading_kind: simulation or model, carried into the output and any approval.
    """
    texts = {path.name: path.read_text() for path in sorted(docs.glob("*.md"))}
    results, reading_errors = [], {}
    for name, text in texts.items():
        try:
            reading = extract_with_llm(text, complete)
        except (LookupError, ValueError) as error:
            reading_errors[name] = f"{type(error).__name__}: {str(error).splitlines()[0]}"
            continue
        results.extend(compare_document(extract_document(text, name), reading, text))
    return agreement_output(texts, results, reconcile(results, resolutions, texts), reading_errors, reading_kind)


# --- Reconciliation ---


class Resolution(BaseModel):
    """A reviewer's decision on one unresolved field."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    policy_doc: str = Field(min_length=1)
    rule_id: str = Field(min_length=1)
    field: str = Field(min_length=1)
    value: object
    reviewer: str = Field(pattern=r"\S")
    rationale: str = Field(pattern=r"\S")
    clause_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


def read_resolutions(text: str) -> list[Resolution]:
    return [Resolution.model_validate(entry) for entry in (yaml.safe_load(text) or {}).get("resolutions", [])]


@dataclass(frozen=True)
class Reconciliation:
    resolved: list[dict]  # each unresolved result, with the resolution that settles it
    unresolved: list[FieldResult]  # still needs a reviewer
    errors: list[str]  # resolutions refused: unknown, stale, or not supported by the text

    @property
    def publishable(self) -> bool:
        return not self.unresolved and not self.errors


def reconcile(results: list[FieldResult], resolutions: list[Resolution], documents: dict[str, str]) -> Reconciliation:
    """Apply resolutions to unresolved results, refusing unknown, stale or unsupported ones."""
    open_results = {(r.policy_doc, r.rule_id, r.field): r for r in results if r.status in UNRESOLVED}
    resolved, errors, used = [], [], set()
    for resolution in resolutions:
        key = (resolution.policy_doc, resolution.rule_id, resolution.field)
        name = f"{resolution.policy_doc} {resolution.rule_id} {resolution.field}"
        result = open_results.get(key)
        if result is None:
            errors.append(f"{name}: no unresolved result to resolve")
            continue
        if key in used:
            errors.append(f"{name}: resolved more than once")
            continue
        if resolution.clause_hash != result.clause_hash:
            errors.append(f"{name}: stale: the clause has changed since this was reviewed")
            continue
        problem = _unsupported(resolution, result, documents.get(resolution.policy_doc, ""))
        if problem:
            errors.append(f"{name}: {problem}")
            continue
        used.add(key)
        resolved.append({**result.to_dict(), "resolution": resolution.model_dump()})
    unresolved = [result for key, result in open_results.items() if key not in used]
    return Reconciliation(resolved, unresolved, errors)


def _clause_text(document: str, rule_id: str) -> str:
    clause = next((c for c in extract_document(document, "").clauses if c.rule_id == rule_id), None)
    return "" if clause is None else clause.text


def _numbers_in(text: str) -> set[Decimal]:
    found = {Decimal(match.replace(",", "")) for match in re.findall(r"\d[\d,]*(?:\.\d+)?", RULE_ID.sub("", text))}
    words = {Decimal(number) for word, number in NUMBER_WORDS.items() if re.search(rf"\b{word}\b", text, re.IGNORECASE)}
    return found | words


def _unsupported(resolution: Resolution, result: FieldResult, document: str) -> str | None:
    """Return why a resolution value is invalid or unsupported by the text, or None."""
    return unsupported_value(resolution.field, resolution.rule_id, resolution.value, document)


def unsupported_value(field: str, rule_id: str, value: object, document: str) -> str | None:
    """Return why a value for one field of one rule is invalid or unsupported by the document's text, or None.

    Shared by reviewer resolutions and review decisions, so both are held to the same evidence.
    """
    clause = _clause_text(document, rule_id)
    heading = f"**{rule_id} — "
    if field == "rule":
        if value not in ("present", "absent"):
            return "a rule's value is present or absent"
        if value == "present" and heading not in document:
            return "the document has no heading for this rule, so it cannot be present"
        if value == "absent" and heading in document:
            return "the document has a heading for this rule, so it cannot be absent"
        return None
    if field == "title":
        if not isinstance(value, str) or f"{heading}{value}.**" not in document:
            return "the title must be the rule's heading, word for word"
        return None
    if field == "section":
        if value is not None and not (isinstance(value, str) and re.search(rf"^#+ {re.escape(value)}$", document, re.MULTILINE)):
            return "the section must be null or a heading in the document"
        return None
    if field == "severities":
        if not isinstance(value, list) or not all(item in get_args(Severity) for item in value):
            return f"severities must be a list drawn from {list(get_args(Severity))}"
        missing = [item for item in value if f"**{item}**" not in clause]
        return f"the clause does not state {missing}" if missing else None
    if field == "review_signal":
        if not isinstance(value, bool):
            return "review_signal must be true or false"
        return None if value == (REVIEW_SIGNAL in clause) else "the clause says otherwise"
    if field == "thresholds":
        return _unsupported_thresholds(value, clause)
    if field == "references":
        if not isinstance(value, list) or not all(isinstance(item, str) and RULE_ID.fullmatch(item) and item != rule_id for item in value):
            return "references must be a list of other rule IDs"
        missing = [item for item in value if item not in clause]
        return f"the clause does not mention {missing}" if missing else None
    if field == "quote":
        if not isinstance(value, str) or not value.strip() or _plain(value) not in _plain(clause):
            return "the quote must be in the clause, word for word"
        return None
    return f"{field} is not a field that can be resolved"


def _unsupported_thresholds(value: object, clause: str) -> str | None:
    """Accept a threshold only where the clause states it in its unit's form, as the regex extraction reads it.

    A number alone is not enough: a merchant code or a count of rules is not a limit.
    """
    if not isinstance(value, list):
        return "thresholds must be a list of {value, unit}"
    numbers = _numbers_in(clause)
    limits = {(threshold.value, threshold.unit) for threshold in thresholds_in(clause)}
    for item in value:
        if not isinstance(item, dict) or set(item) != {"value", "unit"} or item["unit"] not in get_args(Unit):
            return f"each threshold is {{value, unit}}, with a unit from {list(get_args(Unit))}"
        try:
            number = Decimal(str(item["value"]))
        except InvalidOperation:
            return f"{item['value']!r} is not a number"
        if number not in numbers:
            return f"the clause does not state the number {item['value']}"
        if (number, item["unit"]) not in limits:
            stated = ", ".join(f"{_number(v)} {u}" for v, u in sorted(limits, key=lambda limit: (limit[1], limit[0]))) or "none"
            return f"the clause states {item['value']}, but not as a limit with unit {item['unit']}. Its limits are: {stated}. Resolve to those, or to [] if none applies"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare the regex and LLM readings of the policy documents.")
    parser.add_argument("--recordings", type=Path, help="replay recorded LLM responses instead of calling OpenRouter")
    parser.add_argument("--resolutions", type=Path, help="a YAML file of reviewer resolutions")
    parser.add_argument("--json", type=Path, help="write the agreement output, for review and approval, as JSON")
    args = parser.parse_args(argv)
    config = load_config()
    if args.recordings:
        from expense_validator.compiler.llm_extract import recorded, recording_kind

        resolutions = read_resolutions(args.resolutions.read_text()) if args.resolutions else []
        texts = [path.read_text() for path in sorted(config.paths.policy_docs.glob("*.md"))]
        output = review_agreement(config.paths.policy_docs, recorded(args.recordings), resolutions, recording_kind(args.recordings, texts))
        if args.json:
            args.json.write_text(json.dumps(output, indent=2, ensure_ascii=False, default=str) + "\n")
        print(json.dumps(output["summary"], default=str), "| ready for approval:", output["ready_for_approval"])
        return 0
    if config.openrouter_api_key is None or not config.llm.model:
        print("Set OPENROUTER_API_KEY in .env and llm.model in config.yaml.", file=sys.stderr)
        return 1
    complete = openrouter(config.llm, config.openrouter_api_key.get_secret_value())
    results = agreement_report(config.paths.policy_docs, cached(complete, cache_key(config.llm.model, config.llm.temperature), config.paths.llm_cache))
    for status, count in counts(results).items():
        print(f"{status}: {count}")
    for result in results:
        if result.status != "agreed":
            print(f"{result.rule_id} {result.field} {result.status}: regex={result.regex} llm={result.llm}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
