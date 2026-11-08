"""Runs the cases in cases/engine/hardening.yaml, and tests determinism across the public boundary."""

import os
import random
import subprocess
import sys

import pytest
from receipt_rows import ROWS, without_answers
from test_evaluation import RUN, classified, evaluate, summary
from test_helpers import FIELDS, RULES, RULESET
from yaml_cases import exact, load_cases, selected

from expense_validator.config import ROOT
from expense_validator.engine import evaluation
from expense_validator.engine.evaluation import check_run, disposition_directive, evaluate_receipts
from expense_validator.engine.helpers import build_context
from expense_validator.engine.inputs import admit_receipt
from expense_validator.engine.uncertainty import assess_context
from expense_validator.policy.schema import NumberParam, RuleSet, SetParam


def changed_directive(spec):
    directive = RULES["EXC-01"]
    params = dict(directive.params)
    for name, key in (("flag_severities", "flag"), ("review_severities", "review")):
        if key in spec:
            # Around validation, as a ruleset that reached here some other way could hold anything.
            params[name] = SetParam.model_construct(type="set", values=tuple(spec[key]) if isinstance(spec[key], list) else spec[key])
    for name in spec.get("remove", ()):
        del params[name]
    for name in spec.get("as_number", ()):
        params[name] = NumberParam(type="number", value="1", unit="count")
    update = {"params": params, **{key: spec[key] for key in ("rule_id", "status") if key in spec}}
    return directive, directive.model_copy(update=update)


def ruleset_with_directive(spec):
    directive, changed = changed_directive(spec)
    rules = [changed if rule.rule_id == directive.rule_id else rule for rule in RULESET.rules]
    if "duplicate" in spec:
        status = spec.get("duplicate_status", "active")
        rules.append(directive.model_copy(update={"rule_id": spec["duplicate"], "status": status}))
    return RuleSet.model_construct(rules=tuple(rules), ruleset_version="v1")


@pytest.mark.parametrize("case", load_cases("hardening", "directives"))
def test_a_valid_directive_decides(case):
    run = evaluate(case["receipts"], ruleset=ruleset_with_directive(case["directive"]))
    assert run.results[0].disposition == case["expected"]["disposition"]


@pytest.mark.parametrize("case", load_cases("hardening", "directives_refused"))
def test_an_invalid_directive_fails_rather_than_guessing(case):
    with pytest.raises(ValueError, match=case["expected"]):
        evaluate(case["receipts"], ruleset=ruleset_with_directive(case["directive"]))


def tampered(run, spec):
    result = run.results[0]
    report = result.report
    if "source" in spec:
        group = report.groups[0]
        source = group.sources[0].model_copy(update=spec["source"])
        report = report.model_copy(update={"groups": (group.model_copy(update={"sources": (source, *group.sources[1:])}), *report.groups[1:])})
    if "group" in spec:
        report = report.model_copy(update={"groups": (report.groups[0].model_copy(update=spec["group"]), *report.groups[1:])})
    if "report" in spec:
        report = report.model_copy(update=spec["report"])
    update = {**spec.get("result", {}), "report": report}
    if "reason" in spec:
        index = next(i for i, reason in enumerate(result.reasons) if reason.code == spec.get("reason_of", result.reasons[0].code))
        reasons = list(result.reasons)
        reasons[index] = reasons[index].model_copy(update=spec["reason"])
        update["reasons"] = tuple(reasons)
    elif "reasons" in update:
        update["reasons"] = tuple(update["reasons"])
    result = result.model_copy(update=update)
    results = {"drop": (), "duplicate": (result, result)}.get(spec.get("results"), (result,))
    return run.model_copy(update={"results": results})


@pytest.mark.parametrize("case", load_cases("hardening", "tampered_refused"))
def test_a_run_that_does_not_hold_together_fails_loudly(case):
    run = evaluate(case["receipts"])
    directive = disposition_directive(RULESET)
    check_run(run, [item.receipt_id for item in run.results], directive, RULESET)
    with pytest.raises(ValueError, match=case["expected"]):
        check_run(tampered(run, case["tamper"]), ["R1"], directive, RULESET)


def patched_ruleset(patches, extra=()):
    rules = [
        {**rule.model_dump(mode="json"), **patches.get(rule.rule_id, {})}
        for rule in RULESET.rules
    ]
    return RuleSet.model_validate({"ruleset_version": "v1", "rules": [*rules, *extra]})


@pytest.mark.parametrize("case", load_cases("hardening", "completeness"))
def test_every_reason_for_being_undecided_can_be_seen(case):
    result = evaluate(case["receipts"], case["complete"], ruleset=patched_ruleset(case["patches"])).results[0]
    assert exact(selected(summary(result), case["expected"])) == exact(case["expected"])


@pytest.mark.parametrize("case", load_cases("hardening", "ties"))
def test_a_tie_between_rules_does_not_depend_on_their_order(case):
    ruleset = patched_ruleset(case["patches"], case["extra_rules"])
    base = evaluate(case["receipts"], case["complete"], ruleset=ruleset)
    for seed in range(6):
        rules = list(ruleset.rules)
        random.Random(seed).shuffle(rules)
        shuffled = RuleSet.model_validate({"ruleset_version": "v1", "rules": [rule.model_dump(mode="json") for rule in rules]})
        assert evaluate(case["receipts"], case["complete"], ruleset=shuffled) == base
    groups = base.results[0].report.groups
    for key, resolution in (("replaced_by", "replaced"), ("excused_by", "excused")):
        for rule_id, by in case["expected"].get(key, {}).items():
            group = next(item for item in groups if item.rule_id == rule_id and item.resolution == resolution)
            assert group.sources[0].resolved_by == by
    for rule_id, notes in case["expected"].get("notes", {}).items():
        assert list(next(item for item in groups if item.rule_id == rule_id).notes) == notes


# --- The boundary ---


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None])
def test_complete_must_be_a_real_bool_as_any_other_would_read_as_true(value):
    with pytest.raises(TypeError, match="complete must be True or False"):
        evaluate_receipts([], RULESET, FIELDS, complete=value)


def test_asking_for_a_receipt_not_in_a_run_is_an_error_a_caller_can_catch():
    with pytest.raises(ValueError, match="NOPE: is not in this run"):
        RUN.result_for("NOPE")


def test_the_entry_point_checks_each_run_before_returning_it(monkeypatch):
    """Forced through the real pipeline: a wrong disposition never reaches the caller."""
    receipts = load_cases("hardening", "directives")[0].values[0]["receipts"]
    assert evaluate(receipts).results[0].disposition == "flag"
    wrong = (evaluation.DispositionReason(code="nothing_found", basis="EXC-01"),)
    monkeypatch.setattr(evaluation, "decide_disposition", lambda report, directive: ("pass", wrong))
    with pytest.raises(ValueError, match="a pass cannot have an active exception"):
        evaluate(receipts)


def test_asking_for_a_receipt_not_assessed_is_an_error_a_caller_can_catch():
    assessed = assess_context(build_context([], FIELDS, complete=True), RULESET, FIELDS)
    with pytest.raises(ValueError, match="NOPE: is not in the assessed context"):
        assessed.assessment_for("NOPE")


def test_the_checks_run_on_every_real_result():
    check_run(RUN, [row["receipt_id"] for row in ROWS], disposition_directive(RULESET), RULESET)
    assert len(RUN.results) == len(ROWS)


# --- Determinism, on all the real data ---


RECEIPTS = [classified(row) for row in ROWS]
UNCLASSIFIED = [admit_receipt(without_answers(row), FIELDS) for row in ROWS]


@pytest.mark.parametrize("receipts", [RECEIPTS, UNCLASSIFIED], ids=["classified", "unclassified"])
@pytest.mark.parametrize("complete", [True, False], ids=["complete", "partial"])
def test_receipt_order_and_rule_order_change_nothing_on_the_real_data(receipts, complete):
    base = evaluate(receipts, complete)
    for seed in range(4):
        shuffled = list(receipts)
        random.Random(seed).shuffle(shuffled)
        assert evaluate(shuffled, complete) == base
        rules = [rule.model_dump(mode="json") for rule in RULESET.rules]
        random.Random(seed).shuffle(rules)
        assert evaluate(receipts, complete, ruleset=RuleSet.model_validate({"ruleset_version": "v1", "rules": rules})) == base


def test_a_real_run_round_trips_through_json_exactly():
    text = RUN.model_dump_json()
    back = type(RUN).model_validate_json(text)
    assert back == RUN
    assert back.model_dump_json() == text


def test_line_order_keeps_each_outcome_while_evidence_follows_the_receipt():
    """What is caught does not depend on line order, while the lines cited keep the receipt's order."""
    for row in ROWS:
        reversed_row = {**row, "line_items": list(reversed(row["line_items"]))}
        others = [classified(item) for item in ROWS if item["receipt_id"] != row["receipt_id"]]
        result = evaluate([*others, classified(reversed_row)]).result_for(row["receipt_id"])
        base = RUN.result_for(row["receipt_id"])
        assert (result.disposition, result.decided) == (base.disposition, base.decided)
        assert {group.rule_id: set(group.line_ids) for group in result.active_exceptions} == {group.rule_id: set(group.line_ids) for group in base.active_exceptions}
        for group, base_group in zip(result.active_exceptions, base.active_exceptions):
            assert list(group.line_ids) == list(reversed(base_group.line_ids))


@pytest.mark.parametrize(
    "name",
    [
        "two receipts that could still be a split leave a known day breach unresolved",
        "a receipt known to be over the cap rules out a split, so its breach stays active",
        "a breach with a second receipt at another merchant stays active",
        "a breach with a second receipt on another date stays active",
    ],
)
def test_receipt_order_does_not_change_a_split_group(name):
    receipts = list(next(case.values[0] for case in load_cases("evaluation", "cases") if case.values[0]["id"] == name)["receipts"])
    assert evaluate(list(reversed(receipts))) == evaluate(receipts)


def test_reasons_keep_a_stable_order():
    for result in RUN.results:
        codes = [reason.code for reason in result.reasons]
        order = ["exception_flags", "exception_reviews", "review_signal", "uncertainty", "incomplete_evidence", "nothing_found"]
        assert codes == sorted(codes, key=order.index)


SEED_SCRIPT = """
import hashlib, sys
sys.path.insert(0, "tests")
sys.path.insert(0, "tests/engine")
from test_evaluation import RUN
from test_hardening_runs import unclassified_run
print(hashlib.sha256((RUN.model_dump_json() + unclassified_run().model_dump_json()).encode()).hexdigest())
"""


def test_results_are_the_same_under_different_hash_seeds():
    digests = set()
    for seed in ("0", "1", "2", "4242"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": "src:tests:tests/engine", "PYTHONDONTWRITEBYTECODE": "1"}
        done = subprocess.run([sys.executable, "-c", SEED_SCRIPT], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
        digests.add(done.stdout.strip().splitlines()[-1])
    assert len(digests) == 1
