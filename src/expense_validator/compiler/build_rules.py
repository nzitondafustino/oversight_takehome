"""Builds one Rule for each rule ID: the hand-written logic plus what extraction read.

The title, document and hashes come from the clause, so they cannot drift from the
policy. The build stops if a clause has no rule, a rule has no clause, or a quote is
not in its clause. Running this also validates the rules, then writes rules/ruleset.json.

Run with: python -m expense_validator.compiler.build_rules
"""

import hashlib
import json
import sys

import yaml

from expense_validator.compiler.extract import Clause, extract_document
from expense_validator.compiler.validate import read_fields, validate_ruleset
from expense_validator.config import load_config
from expense_validator.policy.schema import RuleSet


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _plain(text: str) -> str:
    return " ".join(text.split())


def _complete(entry: dict, clauses: dict[str, Clause]) -> dict:
    # A carve-out has no clause of its own: it is written inside the first rule it modifies.
    source = entry["modifies"][0] if entry["kind"] == "carve_out" else entry["rule_id"]
    clause = clauses.get(source)
    if clause is None:
        raise ValueError(f"{entry['rule_id']}: no clause with the ID {source}")
    if _plain(entry["source_quote"]) not in _plain(clause.text):
        raise ValueError(f"{entry['rule_id']}: quote is not in the clause: {entry['source_quote']}")
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
    clauses = {clause.rule_id: clause for clause in clause_list}
    missing = sorted(set(clauses) - {entry["rule_id"] for entry in logic["rules"]})
    if missing:
        raise ValueError(f"no rule for: {missing}")
    rules = [_complete(entry, clauses) for entry in logic["rules"]]
    return RuleSet.model_validate({"ruleset_version": logic["ruleset_version"], "rules": rules})


def main() -> int:
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


if __name__ == "__main__":
    sys.exit(main())
