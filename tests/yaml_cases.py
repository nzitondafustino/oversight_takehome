"""Discover and validate YAML cases before pytest collects them."""

import json
from decimal import Decimal
from pathlib import Path
import re

import pytest
import yaml

CASES = Path(__file__).parent / "cases"
SECTIONS = {
    "policy_engine": {"isolation", "configuration", "configuration_refused", "catalog_values", "missing_references"},
    "submission_integrity": {"exploits", "adversarial", "refused", "admitted", "unresolved", "unsupported_refused", "unverified_usd", "decided", "itemization_claims"},
    "rules": {"accepted", "refused"},
    "rulesets": {"accepted", "refused"},
    "extraction": {"documents", "clauses", "inline", "refused"},
    "agreement": {"cases", "refused"},
    "build_rules": {"accepted", "refused"},
    "validate": {"cases"},
    "semantics": {"conditions", "rules", "replacements"},
    "models": {
        "accepted",
        "refused",
        "json_refused",
        "left_out_refused",
        "evidence_numbers",
        "classified",
        "classified_refused",
        "receipt_classified",
        "receipt_classified_refused",
    },
    "admission": {
        "admitted",
        "left_out_admitted",
        "refused",
        "left_out_refused",
        "regional",
        "totals",
        "totals_refused",
        "readmitted",
        "readmitted_refused",
        "copied_refused",
        "catalog_refused",
        "states",
        "states_refused",
        "state_catalog_refused",
    },
    "facts": {"receipt", "item", "catalog_refused", "receipt_rules", "item_rules", "replacements"},
    "checks": {"findings", "details", "rules", "refused"},
    "helpers": {"outcomes", "details", "runs", "baggage", "contexts", "refused", "recipients", "dispatch", "names", "modules"},
    "replacements": {"real", "made_up", "refused"},
    "approvals": {"real", "made_up", "refused"},
    "carveouts": {"real", "made_up", "refused"},
    "uncertainty": {"assessed", "evaluated", "refused"},
    "report": {"real", "made_up", "refused"},
    "evaluation": {"cases", "refused"},
    "adversarial": {"scenarios"},
    "adversarial_policy": {
        "bounds",
        "replacement",
        "isolation",
        "conflicts_refused",
        "separate_runs",
        "ladders",
        "spend_bounds",
        "unrelated",
        "ineligible",
        "categorical",
    },
    "policy_decisions": {"decisions", "partial", "refused"},
    "completeness": {"refund_rules", "usd", "officials", "unknowns"},
    "inevitable": {"cases", "at_the_cap"},
    "offline_eval": {"rates", "prf", "rule_scores", "dispositions", "exact", "proofs", "classification", "buckets", "calibration", "families", "sources_refused"},
    "hardening": {"directives", "directives_refused", "tampered_refused", "completeness", "ties"},
    "ingestion": {"ingested", "refused", "secrets_refused", "streamed", "stream_refused"},
    "normalization": {"text", "text_refused", "receipts", "receipts_refused", "lines_refused", "provider", "provider_refused"},
    "classification": {"answers", "answers_refused", "thresholds", "provider", "provider_refused", "line_inputs", "receipt_languages", "scores"},
    "robustness": {"thresholds", "levels", "separation", "confusion", "groups", "attributes", "safety"},
}
INPUTS = {
    ("policy_engine", "isolation"): {"evaluations"},
    ("policy_engine", "configuration"): {"path", "value", "receipts"},
    ("policy_engine", "configuration_refused"): {"field_changes", "rule_changes", "remove_rules"},
    ("policy_engine", "catalog_values"): {"values"},
    ("policy_engine", "missing_references"): {"rule_changes"},
    ("submission_integrity", "exploits"): {"receipts", "reason"},
    ("submission_integrity", "refused"): {"receipt"},
    ("submission_integrity", "admitted"): {"receipt"},
    ("submission_integrity", "unresolved"): {"receipt"},
    ("submission_integrity", "adversarial"): {"result", "receipt"},
    ("submission_integrity", "unsupported_refused"): {"receipt"},
    ("submission_integrity", "unverified_usd"): {"receipts", "complete"},
    ("submission_integrity", "decided"): {"receipt"},
    ("submission_integrity", "itemization_claims"): {"receipt"},
    ("rules", "accepted"): {"rule"},
    ("rules", "refused"): {"rule"},
    ("rulesets", "accepted"): {"ruleset"},
    ("rulesets", "refused"): {"ruleset"},
    ("extraction", "documents"): {"policy_doc"},
    ("extraction", "clauses"): {"policy_doc", "rule_id"},
    ("extraction", "inline"): {"text"},
    ("extraction", "refused"): {"text"},
    ("agreement", "cases"): {"text", "response"},
    ("agreement", "refused"): {"response"},
    ("build_rules", "accepted"): {"text", "logic"},
    ("build_rules", "refused"): {"text", "logic"},
    ("validate", "cases"): {"text", "logic"},
    ("semantics", "conditions"): {"condition", "facts"},
    ("semantics", "rules"): {"rule_id", "facts"},
    ("semantics", "replacements"): {"facts"},
    ("models", "accepted"): {"model", "data"},
    ("models", "refused"): {"model", "data"},
    ("models", "json_refused"): {"model", "data"},
    ("models", "left_out_refused"): {"model", "data", "field"},
    ("models", "evidence_numbers"): {"decimal"},
    ("models", "classified"): {"line", "output"},
    ("models", "classified_refused"): {"line", "output"},
    ("models", "receipt_classified"): {"receipt", "outputs"},
    ("models", "receipt_classified_refused"): {"receipt", "outputs"},
    ("admission", "admitted"): {"receipt"},
    ("admission", "left_out_admitted"): {"receipt", "field"},
    ("admission", "refused"): {"receipt"},
    ("admission", "left_out_refused"): {"receipt", "field"},
    ("admission", "regional"): {"rule_id", "receipt"},
    ("admission", "totals"): {"field", "decimal"},
    ("admission", "totals_refused"): {"field", "decimal"},
    ("admission", "readmitted"): {"receipt"},
    ("admission", "readmitted_refused"): {"receipt"},
    ("admission", "copied_refused"): {"receipt", "update"},
    ("admission", "catalog_refused"): {"receipt", "fields"},
    ("admission", "states"): {"line", "field", "min_confidence"},
    ("admission", "states_refused"): {"line", "field", "min_confidence"},
    ("admission", "state_catalog_refused"): {"line", "field", "fields"},
    ("facts", "receipt"): {"receipt"},
    ("facts", "item"): {"receipt"},
    ("facts", "catalog_refused"): {"receipt", "fields"},
    ("facts", "receipt_rules"): {"rule_id", "receipt"},
    ("facts", "item_rules"): {"rule_id", "receipt"},
    ("facts", "replacements"): {"receipt"},
    ("checks", "findings"): {"receipt"},
    ("checks", "details"): {"rule_id", "receipt"},
    ("checks", "rules"): {"rules", "receipt"},
    ("checks", "refused"): {"rules", "receipt"},
    ("helpers", "outcomes"): {"rule_id", "receipt_id", "complete", "receipts"},
    ("helpers", "details"): {"rule_id", "receipt_id", "complete", "receipts"},
    ("helpers", "runs"): {"rule_ids", "receipt_id", "complete", "receipts", "with_context"},
    ("helpers", "baggage"): {"receipt_id", "complete", "receipts"},
    ("helpers", "contexts"): {"customer_id", "complete", "receipts"},
    ("helpers", "refused"): {"customer_id", "complete", "receipts"},
    ("helpers", "recipients"): {"line_id"},
    ("helpers", "dispatch"): {"helper"},
    ("helpers", "names"): {"name"},
    ("helpers", "modules"): {"module"},
    ("replacements", "real"): {"rule_ids", "receipt_id", "complete", "with_context", "receipts"},
    ("replacements", "made_up"): {"rules", "findings", "undetermined", "pending"},
    ("replacements", "refused"): {"rules", "findings", "undetermined", "pending"},
    ("approvals", "real"): {"receipt"},
    ("approvals", "made_up"): {"rules", "references", "findings", "undetermined", "pending"},
    ("approvals", "refused"): {"rules", "references", "findings", "undetermined", "pending"},
    ("carveouts", "real"): {"receipt_id", "complete", "receipts"},
    ("carveouts", "made_up"): {"rules", "receipt_id", "complete", "receipts"},
    ("carveouts", "refused"): {"rules", "receipt_id", "complete", "receipts"},
    ("uncertainty", "assessed"): {"receipt_id", "complete", "receipts"},
    ("uncertainty", "evaluated"): {"receipt_id", "complete", "receipts"},
    ("uncertainty", "refused"): {"rules"},
    ("report", "real"): {"receipt_id", "complete", "receipts"},
    ("report", "made_up"): {"rules", "receipt_id", "complete", "receipts"},
    ("report", "refused"): {"rules", "receipt_id", "complete", "receipts"},
    ("evaluation", "cases"): {"receipt_id", "complete", "receipts"},
    ("evaluation", "refused"): {"customer_id", "receipts"},
    ("adversarial", "scenarios"): {"receipts", "complete"},
    ("adversarial_policy", "bounds"): {"bound", "receipts", "complete"},
    ("adversarial_policy", "replacement"): {"receipts", "complete"},
    ("adversarial_policy", "isolation"): {"receipts", "complete"},
    ("adversarial_policy", "conflicts_refused"): {"receipts"},
    ("adversarial_policy", "separate_runs"): {"runs"},
    ("adversarial_policy", "ladders"): {"rule", "vary", "amounts", "receipts", "complete"},
    ("adversarial_policy", "spend_bounds"): {"vary", "amounts", "receipts"},
    ("adversarial_policy", "unrelated"): {"receipts", "other"},
    ("adversarial_policy", "ineligible"): {"rule", "receipts", "discount"},
    ("adversarial_policy", "categorical"): {"line_id", "receipts"},
    ("policy_decisions", "decisions"): {"receipts", "complete"},
    ("policy_decisions", "partial"): {"receipts", "complete"},
    ("policy_decisions", "refused"): {"receipts"},
    ("completeness", "refund_rules"): {"rule", "source", "receipt", "line"},
    ("completeness", "usd"): {"receipts", "complete"},
    ("completeness", "officials"): {"receipts", "complete"},
    ("completeness", "unknowns"): {"condition", "facts"},
    ("inevitable", "cases"): {"receipts", "complete"},
    ("inevitable", "at_the_cap"): {"receipts", "complete"},
    ("offline_eval", "rates"): {"numerator", "denominator"},
    ("offline_eval", "prf"): {"tp", "fp", "fn"},
    ("offline_eval", "rule_scores"): {"outcomes"},
    ("offline_eval", "dispositions"): {"outcomes"},
    ("offline_eval", "exact"): {"outcomes"},
    ("offline_eval", "proofs"): {"outcomes"},
    ("offline_eval", "classification"): {"lines"},
    ("offline_eval", "buckets"): {"confidence"},
    ("offline_eval", "calibration"): {"points", "bins"},
    ("offline_eval", "families"): {"rule_id"},
    ("offline_eval", "sources_refused"): {"prediction_set"},
    ("hardening", "directives"): {"directive", "receipts"},
    ("hardening", "directives_refused"): {"directive", "receipts"},
    ("hardening", "tampered_refused"): {"receipts", "tamper"},
    ("hardening", "completeness"): {"patches", "receipts", "complete"},
    ("hardening", "ties"): {"patches", "extra_rules", "receipts", "complete"},
    ("ingestion", "ingested"): {"mode", "record"},
    ("ingestion", "refused"): {"mode", "record"},
    ("ingestion", "secrets_refused"): {"mode", "record"},
    ("ingestion", "streamed"): {"mode", "lines"},
    ("ingestion", "stream_refused"): {"mode", "lines"},
    ("normalization", "text"): {"description"},
    ("normalization", "text_refused"): {"description"},
    ("normalization", "receipts"): {"receipt", "translations"},
    ("normalization", "receipts_refused"): {"receipt"},
    ("normalization", "lines_refused"): {"changes"},
    ("normalization", "provider"): {"status", "body"},
    ("normalization", "provider_refused"): {"status", "body"},
    ("classification", "answers"): {"answer", "description"},
    ("classification", "answers_refused"): {"answer", "description"},
    ("classification", "thresholds"): {"confidence"},
    ("classification", "provider"): {"status", "body"},
    ("classification", "provider_refused"): {"status", "body"},
    ("classification", "line_inputs"): {"receipt", "translations"},
    ("classification", "scores"): {"lines"},
    ("classification", "receipt_languages"): {"languages"},
    ("robustness", "thresholds"): {"rows", "threshold"},
    ("robustness", "levels"): {"rows"},
    ("robustness", "separation"): {"rows"},
    ("robustness", "confusion"): {"rows"},
    ("robustness", "groups"): {"rows"},
    ("robustness", "attributes"): {"rows", "field"},
    ("robustness", "safety"): {"rows"},
}
# Inputs in these suites can be any YAML value: their runners give them to the code under test.
FREE_INPUT_SUITES = {"policy_engine", "submission_integrity", "agreement", "build_rules", "validate", "semantics", "models", "admission", "facts", "checks", "helpers", "replacements", "approvals", "carveouts", "uncertainty", "report", "evaluation", "hardening", "adversarial", "adversarial_policy", "policy_decisions", "completeness", "inevitable", "offline_eval", "ingestion", "normalization", "classification", "robustness"}


class CaseLoader(yaml.SafeLoader):
    """Reject accidental duplicate keys while preserving YAML merge templates."""

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                continue
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise ValueError(f"duplicate YAML key {key!r} at line {key_node.start_mark.line + 1}")
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


# A receipt fixture can ask for this in its totals, so a fixture about policy stays internally consistent
# without repeating each total. It is only ever what the fixture asks for, and malformed-input cases do not use it.
LINES = "$lines"


def _with_totals_from_lines(value):
    if isinstance(value, list):
        return [_with_totals_from_lines(item) for item in value]
    if not isinstance(value, dict):
        return value
    value = {key: _with_totals_from_lines(item) for key, item in value.items()}
    if LINES not in (value.get("total_amount"), value.get("total_amount_usd")):
        return value
    total = sum((Decimal(str(line["amount"])) for line in value.get("line_items", [])), Decimal(0))
    if value.get("total_amount") == LINES:
        value["total_amount"] = str(total)
    if value.get("total_amount_usd") == LINES:
        rate = Decimal(1) if value.get("currency") == "USD" else value.get("fx_rate_to_usd")
        value["total_amount_usd"] = None if rate is None else str((total * Decimal(str(rate))).quantize(Decimal("0.01")))
    return value


def _validate_case(case, suite, section):
    if not isinstance(case, dict):
        raise ValueError("each case must be a mapping")
    inputs = INPUTS[suite, section]
    required = {"id", "expected"} | inputs
    if set(case) != required:
        raise ValueError(f"case keys must be {sorted(required)}, got {sorted(case)}")
    if not isinstance(case["id"], str) or not case["id"].strip():
        raise ValueError("case id must be nonempty text")
    for name in inputs:
        if suite in FREE_INPUT_SUITES:
            continue
        if suite == "extraction":
            if not isinstance(case[name], str):
                raise ValueError(f"{name} must be text")
        elif not isinstance(case[name], dict):
            raise ValueError(f"{name} must be a mapping")
    expected = case["expected"]
    if section.endswith("refused"):
        if not isinstance(expected, str) or not expected.strip():
            raise ValueError("refused expected must be a nonempty error regex")
        re.compile(expected)
    elif suite == "validate":
        if not isinstance(expected, list) or any(not isinstance(value, str) for value in expected):
            raise ValueError("validate expected must be a list of issues")
    elif not isinstance(expected, dict) or not expected:
        raise ValueError("accepted expected must be a nonempty mapping")
    elif suite == "rulesets":
        if set(expected) != {"rule_ids", "params"}:
            raise ValueError("ruleset expected needs rule_ids and params")
        if not isinstance(expected["rule_ids"], list) or not isinstance(expected["params"], dict):
            raise ValueError("rule_ids must be a list and params a mapping")


def discover_cases(root=CASES):
    """Load all suites recursively; malformed files fail collection with their path."""
    collected = {(suite, section): [] for suite, sections in SECTIONS.items() for section in sections}
    paths = sorted(set(root.rglob("*.yaml")) | set(root.rglob("*.yml")))
    if not paths:
        raise ValueError(f"no YAML cases found in {root}")
    seen = set()
    for path in paths:
        try:
            document = yaml.load(path.read_text(), Loader=CaseLoader)
            if not isinstance(document, dict):
                raise ValueError("document must be a mapping")
            suite = document.get("suite")
            if suite not in SECTIONS:
                raise ValueError(f"suite must be one of {sorted(SECTIONS)}")
            sections = set(document) - {"suite", "templates"}
            if not sections or not sections <= SECTIONS[suite]:
                raise ValueError(f"sections must be drawn from {sorted(SECTIONS[suite])}")
            for section in sorted(sections):
                cases = document[section]
                if not isinstance(cases, list) or not cases:
                    raise ValueError(f"{section} must be a nonempty list")
                for index, case in enumerate(cases):
                    case = cases[index] = _with_totals_from_lines(case)
                    _validate_case(case, suite, section)
                    identity = (suite, section, case["id"])
                    if identity in seen:
                        raise ValueError(f"duplicate case id {identity}")
                    seen.add(identity)
                    label = f"{path.relative_to(root)}::{case['id']}"
                    collected[suite, section].append(pytest.param(case, id=label))
        except (ValueError, TypeError, re.error, yaml.YAMLError) as error:
            raise ValueError(f"{path}: {error}") from error
    return collected


_CATALOG = discover_cases()


def load_cases(suite: str, section: str) -> list:
    """New cases and files require only YAML, never changes to a test runner."""
    cases = _CATALOG[suite, section]
    if not cases:
        raise ValueError(f"no cases for {suite}/{section}")
    return cases


def exact(value) -> str:
    """For comparing through JSON text, where false, 0, null and "0" all differ, unlike in Python."""
    return json.dumps(value, default=str, sort_keys=True)


def selected(data, names) -> dict:
    """The named values from dumped data, where a name can go deeper: line_items.0.category."""
    found = {}
    for name in names:
        value = data
        for part in name.split("."):
            value = value[int(part)] if part.isdigit() else value[part]
        found[name] = value
    return found
