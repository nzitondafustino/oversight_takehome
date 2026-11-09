"""Compile policy documents and developer-authored logic into a ruleset.

Plain builds are development-only. Strict publication requires reconciled
readings and approval bound to the exact candidate.
"""

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml

from expense_validator.compiler.agreement import Resolution, agreement_output, compare_document, counts, read_resolutions, reconcile
from expense_validator.compiler.extract import Clause, extract_document
from expense_validator.compiler.approval import Approval, load_approval, why_approval_fails
from expense_validator.compiler.llm_extract import Complete, RecordingMissing, extract_with_llm, recorded, recording_kind
from expense_validator.compiler.validate import Issue, faithful_discrepancies, held_thresholds, read_fields, validate_ruleset
from expense_validator.config import PathsConfig, load_config
from expense_validator.policy.schema import RuleSet


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _plain(text: str) -> str:
    return " ".join(text.split())


class QuoteNotInClause(ValueError):
    """A rule's developer-written source quote is not in its clause: the text changed since the logic was written."""

    def __init__(self, rule_id: str, quote: str, clause: Clause):
        super().__init__(f"{rule_id}: quote is not in the clause: {quote}")
        self.rule_id, self.quote, self.clause = rule_id, quote, clause


class RulesMissing(ValueError):
    """Clauses the documents state under their own rule ID that no rule implements: each needs a rule written for it."""

    def __init__(self, clauses: list[Clause]):
        super().__init__(f"no rule for: {[clause.rule_id for clause in clauses]}")
        self.clauses = clauses


def missing_rule(clause: Clause) -> dict:
    """A clause with no rule, as a discrepancy: what the clause states, for the rule a reviewer writes for it."""
    return {
        "rule_id": clause.rule_id, "policy_doc": clause.policy_doc, "field": "rule", "compiled": None,
        "stated": {"title": clause.title, "text": clause.text, "severities": list(clause.severities), "review_signal": clause.review_signal,
                   "thresholds": [{"value": str(threshold.value), "unit": threshold.unit} for threshold in clause.thresholds]},
    }


def _complete(entry: dict, clauses: dict[str, Clause]) -> dict:
    # A carve-out has no clause of its own: it is written inside the first rule it modifies.
    source = entry["modifies"][0] if entry["kind"] == "carve_out" else entry["rule_id"]
    clause = clauses.get(source)
    if clause is None:
        raise ValueError(f"{entry['rule_id']}: no clause with the ID {source}")
    if _plain(entry["source_quote"]) not in _plain(clause.text):
        raise QuoteNotInClause(entry["rule_id"], entry["source_quote"], clause)
    return {
        "title": clause.title,
        **entry,
        "policy_doc": clause.policy_doc,
        "provenance": {
            "translator": "developer",
            "clause_hash": _sha256(clause.text),
            "fingerprint": _sha256(json.dumps(entry, sort_keys=True)),
        },
    }


def build_ruleset(logic_text: str, clause_list: list[Clause]) -> RuleSet:
    logic = yaml.safe_load(logic_text)
    ids = [clause.rule_id for clause in clause_list]
    repeated = sorted({rule_id for rule_id in ids if ids.count(rule_id) > 1})
    if repeated:
        # Keyed by rule ID below, so one of the clauses would be dropped without a word.
        raise ValueError(f"rule IDs in more than one document: {repeated}")
    clauses = {clause.rule_id: clause for clause in clause_list}
    retained = logic.get("retained_rules", [])
    for entry in retained:
        source = entry["modifies"][0] if entry["kind"] == "carve_out" else entry["rule_id"]
        clause = clauses.get(source)
        if clause is None or entry["policy_doc"] != clause.policy_doc or entry["provenance"]["clause_hash"] != _sha256(clause.text):
            raise ValueError(f"{entry['rule_id']}: retained rule no longer matches its source clause")
        if _plain(entry["source_quote"]) not in _plain(clause.text):
            raise QuoteNotInClause(entry["rule_id"], entry["source_quote"], clause)
    missing = sorted(set(clauses) - {entry["rule_id"] for entry in [*logic["rules"], *retained]})
    if missing:
        raise RulesMissing([clauses[rule_id] for rule_id in missing])
    rules = [_complete(entry, clauses) for entry in logic["rules"]] + retained
    if "rule_order" in logic:
        order = {identifier: index for index, identifier in enumerate(logic["rule_order"])}
        rules.sort(key=lambda entry: order.get(entry["rule_id"], len(order)))
    return RuleSet.model_validate({"ruleset_version": logic["ruleset_version"], "rules": rules})


MODES = ("production", "simulation")


@dataclass(frozen=True)
class Candidate:
    """A built and validated ruleset awaiting approval. Never written.

    Attributes:
        ruleset_sha256: Hash of the candidate, covering the rule logic and the clauses.
        output: Agreement output for the reviewer.
        reasons: What blocks publication before approval is considered.
    """

    ruleset: RuleSet | None
    ruleset_sha256: str | None
    output: dict
    reasons: list[str]
    report: dict
    issues: tuple[Issue, ...] = ()
    clauses: tuple[Clause, ...] = ()
    discrepancies: tuple[dict, ...] = ()  # rules that do not do what their clause states (validate.faithful_discrepancies)


@dataclass(frozen=True)
class Publication:
    published: bool
    reasons: list[str]  # why it was not published, empty when it was
    report: dict


def prepare_candidate(paths: PathsConfig, complete: Complete, resolutions: list[Resolution], reading_kind: str) -> Candidate:
    """Read, reconcile, build and validate the candidate ruleset without writing."""
    texts = {path.name: path.read_text() for path in sorted(paths.policy_docs.glob("*.md"))}
    return prepare_candidate_from_texts(texts, paths.rule_logic.read_text(), paths.rule_fields.read_text(), complete, resolutions, reading_kind)


def prepare_candidate_from_texts(
    texts: dict[str, str],
    logic_text: str,
    fields_text: str,
    complete: Complete,
    resolutions: list[Resolution],
    reading_kind: str,
) -> Candidate:
    """Read, reconcile, build and validate a candidate ruleset from documents in memory.

    Args:
        texts: Policy document text by file name.
        logic_text: The developer-written rule logic, as YAML.
        fields_text: The field catalog, as YAML.
        complete: LLM completion function.
        resolutions: Reviewer resolutions.
        reading_kind: simulation or model.

    Raises:
        ValueError: If a document has a malformed rule heading or repeats a rule ID.
    """
    texts = dict(sorted(texts.items()))
    reasons, results, clauses, reading_errors = [], [], [], {}
    for name, text in texts.items():
        document = extract_document(text, name)
        clauses.extend(document.clauses)
        try:
            reading = extract_with_llm(text, complete)
        except (RecordingMissing, ValueError) as error:
            reading_errors[name] = f"{type(error).__name__}: {str(error).splitlines()[0]}"
            reasons.append(f"{name}: no usable LLM reading: {reading_errors[name]}")
            continue
        results.extend(compare_document(document, reading, text))
    reconciliation = reconcile(results, resolutions, texts)
    reasons += [f"resolution refused: {error}" for error in reconciliation.errors]
    reasons += [f"unresolved: {r.policy_doc} {r.rule_id} {r.field} is {r.status}" for r in reconciliation.unresolved]
    ruleset, issues, discrepancies = None, [], []
    try:
        built = build_ruleset(logic_text, clauses)
    except ValueError as error:
        reasons.append(f"build failed: {str(error).splitlines()[0]}")
        if isinstance(error, QuoteNotInClause):  # the rule logic cites words the current text no longer has
            discrepancies = [{"rule_id": error.rule_id, "policy_doc": error.clause.policy_doc, "field": "source_quote", "stated": error.clause.text, "compiled": error.quote}]
        if isinstance(error, RulesMissing):  # a clause new to the documents: a reviewer writes its rule (an addition)
            discrepancies = [missing_rule(clause) for clause in error.clauses]
    else:
        issues = validate_ruleset(built, clauses, read_fields(fields_text))
        errors = [f"validation: {issue}" for issue in issues if issue.level == "error"]
        errors += _resolution_conflicts(built, reconciliation.resolved)
        reasons += errors
        ruleset = None if errors else built
        discrepancies = faithful_discrepancies(built, {clause.rule_id: clause for clause in clauses})
    ruleset_sha256 = None if ruleset is None else hashlib.sha256(ruleset.to_json().encode()).hexdigest()
    output = agreement_output(texts, results, reconciliation, reading_errors, reading_kind)
    report = {
        "reading_kind": reading_kind,
        "candidate_ruleset_sha256": ruleset_sha256,
        "agreement": output,
        "documents": {name: hashlib.sha256(text.encode()).hexdigest() for name, text in texts.items()},
        "counts": counts(results),
        "results": [result.to_dict() for result in results],
        "resolved": reconciliation.resolved,
        "unresolved": [result.to_dict() for result in reconciliation.unresolved],
        "resolution_errors": reconciliation.errors,
        "validation": [str(issue) for issue in issues],
    }
    return Candidate(ruleset, ruleset_sha256, output, reasons, report, tuple(issues), tuple(clauses), tuple(discrepancies))


def _resolution_conflicts(ruleset: RuleSet, resolved: list[dict]) -> list[str]:
    """Return each resolved threshold that differs from the limits the executable rules hold for its clause."""
    conflicts = []
    for entry in resolved:
        if entry["field"] != "thresholds":
            continue
        chosen = {(Decimal(str(item["value"])), item["unit"]) for item in entry["resolution"]["value"]}
        held = held_thresholds(ruleset, entry["rule_id"])
        if chosen != held:
            conflicts.append(
                f"resolution conflicts with the executable rules: {entry['policy_doc']} {entry['rule_id']} thresholds resolved as [{_written(chosen)}], "
                f"but the rules hold [{_written(held)}]. Update rules/rule_logic.yaml and validate again, or resolve to what the rules hold"
            )
    return conflicts


def _written(limits: set[tuple[Decimal, str]]) -> str:
    return ", ".join(f"{format(value.normalize(), 'f')} {unit}" for value, unit in sorted(limits, key=lambda limit: (limit[1], limit[0])))


def publication_blockers(candidate: Candidate, reading_kind: str, approval: Approval | None, mode: str, out: Path, production_ruleset: Path) -> list[str]:
    """Return every reason the candidate may not be written to out, empty when it may.

    Raises:
        ValueError: If mode is not production or simulation.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    reasons = list(candidate.reasons)
    if mode == "production" and reading_kind != "model":
        reasons.append(f"production publication needs model readings, and these are {reading_kind}")
    if mode == "simulation" and out.resolve() == production_ruleset.resolve():
        reasons.append("a simulation never writes the production ruleset")
    if approval is None:
        reasons.append("no saved approval")
    else:
        problem = why_approval_fails(approval, candidate.output, candidate.ruleset_sha256)
        if problem:
            reasons.append(f"approval does not hold: {problem}")
        if mode == "production" and approval.reading_kind != "model":
            reasons.append(f"approval is of {approval.reading_kind} readings, which never authorize production")
    return reasons


def publish_with_agreement(
    paths: PathsConfig, complete: Complete, resolutions: list[Resolution], out: Path, *, reading_kind: str, approval: Approval | None, mode: str
) -> Publication:
    """Write the candidate ruleset if nothing blocks it and the approval covers it.

    Args:
        paths: Project paths.
        complete: LLM completion function.
        resolutions: Reviewer resolutions.
        out: Where to write the ruleset.
        reading_kind: simulation or model.
        approval: Saved approval, if any.
        mode: production needs model readings; simulation never writes the production path.

    Returns:
        The publication result, with the reasons it was blocked.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    candidate = prepare_candidate(paths, complete, resolutions, reading_kind)
    reasons = publication_blockers(candidate, reading_kind, approval, mode, out, paths.ruleset)
    report = {**candidate.report, "mode": mode, "approval": None if approval is None else approval.model_dump(exclude={"output"}), "published": not reasons, "reasons": reasons}
    if reasons:
        return Publication(False, reasons, report)
    out.write_text(candidate.ruleset.to_json())
    return Publication(True, [], report)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build rules/ruleset.json from the policy documents and rule logic.")
    parser.add_argument("--strict-agreement", action="store_true", help="publish only if the regex and LLM readings agree or are resolved")
    parser.add_argument("--recordings", type=Path, help="recorded LLM responses to replay, by document hash")
    parser.add_argument("--resolutions", type=Path, help="a YAML file of reviewer resolutions")
    parser.add_argument("--out", type=Path, help="where to write the ruleset: rules/ruleset.json by default")
    parser.add_argument("--report", type=Path, help="where to write the agreement report as JSON")
    parser.add_argument("--approval", type=Path, help="a saved approval of this exact agreement output and candidate ruleset")
    parser.add_argument("--mode", choices=MODES, default="production", help="simulation is for tests and dry runs, and never writes the production ruleset")
    args = parser.parse_args(argv)
    if args.strict_agreement:
        return _main_strict(args)
    paths = load_config().paths
    clauses = [
        clause
        for path in sorted(paths.policy_docs.glob("*.md"))
        for clause in extract_document(path.read_text(), path.name).clauses
    ]
    ruleset = build_ruleset(paths.rule_logic.read_text(), clauses)
    issues = validate_ruleset(ruleset, clauses, read_fields(paths.rule_fields.read_text()))
    for issue in issues:
        print(issue, file=sys.stderr)
    if any(issue.level == "error" for issue in issues):
        return 1
    paths.ruleset.write_text(ruleset.to_json())
    print(f"Wrote {len(ruleset.rules)} rules to {paths.ruleset}")
    return 0


def _main_strict(args: argparse.Namespace) -> int:
    if args.recordings is None:
        print("--strict-agreement needs --recordings: the live LLM provider is not connected yet.", file=sys.stderr)
        return 2
    paths = load_config().paths
    resolutions = read_resolutions(args.resolutions.read_text()) if args.resolutions else []
    texts = [path.read_text() for path in sorted(paths.policy_docs.glob("*.md"))]
    kind = recording_kind(args.recordings, texts)
    approval = load_approval(args.approval) if args.approval else None
    out = args.out or paths.ruleset
    publication = publish_with_agreement(paths, recorded(args.recordings), resolutions, out, reading_kind=kind, approval=approval, mode=args.mode)
    if args.report:
        args.report.write_text(json.dumps(publication.report, indent=2, ensure_ascii=False, default=str) + "\n")
    print(f"{args.mode} run on {kind} readings: {publication.report['counts']}, candidate ruleset {publication.report['candidate_ruleset_sha256']}")
    for reason in publication.reasons:
        print(reason, file=sys.stderr)
    print(f"published to {out}" if publication.published else "not published: nothing was written")
    return 0 if publication.published else 1


if __name__ == "__main__":
    sys.exit(main())
