"""Validate field decisions against extraction identity and source evidence.

Review performs no model calls, file access, or publication. Unsupported values
and executable-logic changes remain approval blockers.
"""

import dataclasses
import hashlib
import json
from collections import Counter
from decimal import Decimal, InvalidOperation

from expense_validator.compiler.agreement import output_sha256, unsupported_value
from expense_validator.compiler.extract import Clause, Threshold
from expense_validator.compiler.validate import faithful_issues
from expense_validator.exceptions import InvalidInputError
from expense_validator.models.compiler import (
    ExtractionResult,
    FieldComparison,
    ReviewDecision,
    ReviewedField,
    ReviewIssue,
    ReviewPolicy,
    ReviewResult,
)

FIELD_ORDER = ("rule", "title", "section", "severities", "review_signal", "thresholds", "references", "quote")
# These candidate fields must match the regex reading.
AS_READ = ("rule", "title")
# Recheck these fields for source fidelity after review.
FAITHFUL = ("severities", "review_signal", "thresholds")


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _copy(value: object) -> object:
    return json.loads(_canonical(value))


def comparison_id(policy_doc: str, document_sha256: str, rule_id: str, field: str) -> str:
    """Return a comparison's ID: stable for this document text, rule and field, and unique across documents."""
    return "cmp-" + _sha256([policy_doc, document_sha256, rule_id, field])[:32]


def _side(result: dict, side: str) -> object:
    # Mark missing rules as absent, not unknown.
    if result["field"] == "rule":
        return "present" if result[side] == "present" else "absent"
    return result[side]


def _location(text: str, rule_id: str, section: str | None) -> dict:
    heading = f"**{rule_id} — "
    line = next((number for number, line in enumerate(text.splitlines(), start=1) if line.startswith(heading)), None)
    return {"section": section, "line": line}


def comparison_core(comparison: FieldComparison) -> dict:
    """Return the part of a comparison the extraction identity covers: what was read, not who reads it."""
    return {name: _copy(getattr(comparison, name)) for name in ("comparison_id", "policy_doc", "rule_id", "field", "status", "regex_value", "llm_value", "clause_hash")}


def extraction_identity(reading_kind: str, prompt_sha256: str, generation: dict, documents: list[dict], comparisons: list[dict]) -> str:
    """Return the SHA-256 that names one extraction: its readings, documents and generation settings."""
    return _sha256({"reading_kind": reading_kind, "prompt_sha256": prompt_sha256, "generation": generation, "documents": documents, "comparisons": comparisons})


def build_comparisons(results: list[dict], texts: dict[str, str], ids: dict[str, str], clauses: tuple[Clause, ...]) -> list[dict]:
    """Return every field result as comparison data, in a fixed order, without the extraction identity."""
    by_clause = {(clause.policy_doc, clause.rule_id): clause for clause in clauses}
    entries = []
    for result in results:
        policy_doc, rule_id, field = result["policy_doc"], result["rule_id"], result["field"]
        text = texts[policy_doc]
        clause = by_clause.get((policy_doc, rule_id))
        agreed = result["status"] == "agreed"
        entries.append({
            "comparison_id": comparison_id(policy_doc, hashlib.sha256(text.encode()).hexdigest(), rule_id, field),
            "document_id": ids[policy_doc],
            "policy_doc": policy_doc,
            "rule_id": rule_id,
            "field": field,
            "status": result["status"],
            "regex_value": _copy(_side(result, "regex")),
            "llm_value": _copy(_side(result, "llm")),
            "agreed_value": _copy(result["llm"] if field == "quote" else result["regex"]) if agreed else None,
            "evidence": None if clause is None else clause.text,
            "location": None if clause is None else _location(text, rule_id, clause.section),
            "clause_hash": result["clause_hash"],
            "review_required": not agreed,
        })
    return sorted(entries, key=lambda e: (e["policy_doc"], e["rule_id"], FIELD_ORDER.index(e["field"]) if e["field"] in FIELD_ORDER else len(FIELD_ORDER)))


def check_extraction(extraction: ExtractionResult) -> None:
    """Refuse an extraction whose comparisons, identity or candidate have been altered since it was made.

    Raises:
        InvalidInputError: If anything does not match what it claims.
    """
    documents = [{"filename": d.filename, "document_id": d.document_id, "sha256": d.sha256, "reading_error": d.reading_error} for d in extraction.documents]
    generation = {"extraction_model": extraction.extraction_model, "temperature": extraction.temperature}
    expected = extraction_identity(extraction.reading_kind, extraction.prompt_sha256, generation, documents, [comparison_core(c) for c in extraction.comparisons])
    if expected != extraction.extraction_sha256 or any(c.extraction_sha256 != expected for c in extraction.comparisons):
        raise InvalidInputError("the extraction does not match its extraction_sha256: it has been altered, so it cannot be reviewed")
    candidate = extraction.candidate
    if candidate is not None and hashlib.sha256(candidate.ruleset.to_json().encode()).hexdigest() != candidate.ruleset_sha256:
        raise InvalidInputError("the extraction's candidate does not match its ruleset_sha256, so it cannot be reviewed")


def _decision_problem(decision: ReviewDecision, extraction: ExtractionResult, comparisons: dict[str, FieldComparison], counts: Counter) -> tuple[str, str] | None:
    """Return the code and message of why a decision cannot apply, or None."""
    if decision.extraction_sha256 != extraction.extraction_sha256:
        return "stale_extraction", "the decision was made on another extraction"
    comparison = comparisons.get(decision.comparison_id)
    if comparison is None:
        return "unknown_comparison", "no comparison in this extraction has this ID"
    if counts[decision.comparison_id] > 1:
        return "duplicate_decision", "more than one decision names this comparison, so none of them applies"
    action, rationale = decision.action, (decision.rationale or "").strip()
    if action == "override":
        if not decision.has_value:
            return "value_required", "an override needs a value, which may be null where null is the value meant"
        if not rationale:
            return "rationale_required", "an override needs a rationale"
        return None
    if decision.has_value:
        return "value_not_allowed", f"{action} takes its value from the extraction, so it takes no value"
    if action == "reject":
        return None if rationale else ("rationale_required", "a rejection needs a rationale")
    if action == "confirm":
        if comparison.status != "agreed":
            return "confirm_not_agreed", f"there is no agreed value to confirm, as the field is {comparison.status}: choose accept_regex, accept_llm or override"
        return None
    # A null section is a value; other null fields mean missing evidence.
    if _source_value(comparison, action) is None and comparison.field != "section":
        return "no_source_value", f"the {action.removeprefix('accept_')} reading has no value for this field"
    return None


def _source_value(comparison: FieldComparison, action: str) -> object:
    return {"confirm": comparison.agreed_value, "accept_regex": comparison.regex_value, "accept_llm": comparison.llm_value}[action]


def _applied(comparison: FieldComparison, decision: ReviewDecision | None, policy: ReviewPolicy) -> tuple[str, object]:
    """Return the outcome and a copy of the final value, so nothing in the extraction can change through it."""
    if decision is None:
        if comparison.status == "agreed" and policy == "exceptions_only":
            return "agreed", _copy(comparison.agreed_value)
        return "undecided", None
    if decision.action == "reject":
        return "rejected", None
    if decision.action == "override":
        return "override", _copy(decision.value)
    outcome = {"confirm": "confirmed", "accept_regex": "regex", "accept_llm": "llm"}[decision.action]
    return outcome, _copy(_source_value(comparison, decision.action))


def _limits(value: object) -> set[tuple[Decimal, str]] | None:
    try:
        return {(Decimal(str(item["value"])), item["unit"]) for item in value}
    except (TypeError, KeyError, InvalidOperation):
        return None


def _as_read_problem(comparison: FieldComparison, value: object) -> str | None:
    if value != comparison.regex_value:
        return f"the executable candidate takes {_canonical(comparison.regex_value)} for {comparison.rule_id} {comparison.field} from the clause, not this value. Update rules/rule_logic.yaml and validate again, or choose that value"
    return None


def _faithful_problems(extraction: ExtractionResult, fields: list[ReviewedField], unsupported: set[str]) -> list[ReviewIssue]:
    """Rerun the candidate's faithful check with the reviewed limits, severities and review signals in its clauses.

    A value the source does not support is already an issue, so it is left out here rather than reported twice.
    """
    final = {
        (f.comparison.policy_doc, f.comparison.rule_id, f.comparison.field): f.final_value
        for f in fields
        if f.outcome not in ("undecided", "rejected") and f.comparison.comparison_id not in unsupported
    }
    clauses = {}
    for clause in extraction.clauses:
        # Unresolved fields retain the validated regex value.
        changes, where = {}, (clause.policy_doc, clause.rule_id)
        limits = _limits(final.get((*where, "thresholds")))
        if limits is not None:
            changes["thresholds"] = [Threshold(value, unit) for value, unit in sorted(limits, key=lambda limit: (limit[1], limit[0]))]
        if isinstance(final.get((*where, "severities")), list):
            changes["severities"] = final[(*where, "severities")]
        if isinstance(final.get((*where, "review_signal")), bool):
            changes["review_signal"] = final[(*where, "review_signal")]
        clauses[clause.rule_id] = dataclasses.replace(clause, **changes)
    return [
        ReviewIssue(level="error", code="executable_conflict", message=f"the reviewed interpretation disagrees with the executable rules: {issue}. Update rules/rule_logic.yaml and validate again, or change the decision")
        for issue in faithful_issues(extraction.candidate.ruleset, clauses)
    ]


def review_extraction(extraction: ExtractionResult, decisions: list[ReviewDecision], policy: ReviewPolicy, texts: dict[str, str]) -> ReviewResult:
    """Apply decisions to a copy of one extraction's comparisons and validate the reviewed interpretation.

    Args:
        extraction: A checked extraction, made without resolutions.
        decisions: Admitted review decisions; any order.
        policy: Which comparisons need a decision.
        texts: The extraction's documents by file name, for the source checks.
    """
    comparisons = {c.comparison_id: c for c in extraction.comparisons}
    counts = Counter(d.comparison_id for d in decisions)
    issues, applied = [], {}
    for decision in sorted(decisions, key=lambda d: (d.comparison_id, _canonical(d.model_dump(mode="json")))):
        problem = _decision_problem(decision, extraction, comparisons, counts)
        if problem:
            issues.append(ReviewIssue(level="error", code=problem[0], message=problem[1], comparison_id=decision.comparison_id))
        else:
            applied[decision.comparison_id] = decision
    fields = []
    for comparison in extraction.comparisons:
        decision = applied.get(comparison.comparison_id)
        outcome, final = _applied(comparison, decision, policy)
        required = policy == "all_fields" or comparison.status != "agreed"
        fields.append(ReviewedField(comparison=comparison, decision=decision, outcome=outcome, final_value=final, decision_required=required))
        issues.extend(_field_issues(extraction, comparison, outcome, final, decision, texts))
    if extraction.candidate is not None:
        unsupported = {issue.comparison_id for issue in issues if issue.code == "unsupported_value"}
        issues.extend(_faithful_problems(extraction, fields, unsupported))
    issues.extend(ReviewIssue(level="error", code="reading_error", message=f"{d.filename}: no usable LLM reading, so its fields cannot be reviewed: {d.reading_error}") for d in extraction.documents if d.reading_error)
    if extraction.candidate is None:
        causes = [blocker for blocker in extraction.blockers if not blocker.startswith("unresolved:")]
        issues.append(ReviewIssue(level="error", code="no_candidate", message=f"there is no valid candidate ruleset: {'; '.join(causes) or 'it did not build'}"))
    remaining = tuple(f.comparison.comparison_id for f in fields if f.decision_required and f.decision is None)
    complete = not remaining
    ready = complete and not any(issue.level == "error" for issue in issues) and extraction.candidate is not None
    body = {
        "reading_kind": extraction.reading_kind,
        "extraction_sha256": extraction.extraction_sha256,
        "policy": policy,
        "candidate_ruleset_sha256": None if extraction.candidate is None else extraction.candidate.ruleset_sha256,
        "fields": tuple(fields),
        "issues": tuple(issues),
        "remaining": remaining,
        "complete": complete,
        "ready_for_approval": ready,
    }
    draft = ReviewResult(**body, review_sha256="0" * 64)
    return draft.model_copy(update={"review_sha256": review_output(draft)["output_sha256"]})


def _field_issues(extraction, comparison: FieldComparison, outcome: str, final: object, decision, texts) -> list[ReviewIssue]:
    where = comparison.comparison_id
    if outcome == "rejected":
        return [ReviewIssue(level="error", code="rejected", message=f"rejected by {decision.reviewer}: {decision.rationale}", comparison_id=where)]
    if outcome == "undecided":
        return []
    problem = unsupported_value(comparison.field, comparison.rule_id, final, texts[comparison.policy_doc])
    if problem:
        return [ReviewIssue(level="error", code="unsupported_value", message=f"{comparison.policy_doc} {comparison.rule_id} {comparison.field}: {problem}", comparison_id=where)]
    if comparison.field in AS_READ:
        problem = _as_read_problem(comparison, final)
        return [ReviewIssue(level="error", code="executable_conflict", message=f"{comparison.policy_doc} {comparison.rule_id} {comparison.field}: {problem}", comparison_id=where)] if problem else []
    if comparison.field not in FAITHFUL and outcome in ("regex", "llm", "override") and final != comparison.agreed_value:
        return [ReviewIssue(level="note", code="source_only", message=f"{comparison.field} is checked against the source text only: no executable rule reads it", comparison_id=where)]
    return []


def review_output(review: ReviewResult) -> dict:
    """Return the review as the output an approval holds, with output_sha256, which is its review_sha256."""
    body = {"kind": "review", **review.model_dump(mode="json", exclude={"review_sha256"})}
    return {**body, "output_sha256": output_sha256(body)}
