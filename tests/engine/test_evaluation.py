"""Runs the cases in cases/engine/evaluation.yaml, and measures the public entry point on the real data."""

import inspect
import random

import pytest
from receipt_rows import ROWS, without_answers
from test_helpers import FIELDS, RECIPIENTS, RULESET
from yaml_cases import exact, load_cases, selected

from expense_validator.engine.checks import run_checks
from expense_validator.engine.evaluation import decide_disposition, evaluate_receipts
from expense_validator.engine.helpers import build_context
from expense_validator.engine.inputs import admit_receipt
from expense_validator.engine.models import Receipt
from expense_validator.policy.schema import RuleSet


def evaluate(receipts, complete=True, ruleset=RULESET):
    return evaluate_receipts(receipts, ruleset, FIELDS, complete=complete)


def summary(result):
    return {
        "open_includes_time": any(item.rule_id == "TIME-03" for item in result.open_questions),
        "disposition": result.disposition,
        "decided": result.decided,
        "reasons": [{"code": item.code, "basis": item.basis, "rule_ids": list(item.rule_ids)} for item in result.reasons],
        "exceptions": [group.rule_id for group in result.active_exceptions],
        "signals": [group.rule_id for group in result.review_signals],
        "unresolved": [group.rule_id for group in result.unresolved],
        "uncertainty": 0 if result.uncertainty is None else len(result.uncertainty.sources),
        "open": [item.rule_id for item in result.open_questions],
        "audit": [{"rule_id": group.rule_id, "resolution": group.resolution} for group in result.audit_history],
    }


@pytest.mark.parametrize("case", load_cases("evaluation", "cases"))
def test_the_public_entry_point_gives_this_the_same_way_every_time(case):
    run = evaluate(case["receipts"], case["complete"])
    result = run.result_for(case["receipt_id"])
    assert exact(selected(summary(result), case["expected"])) == exact(case["expected"])
    assert evaluate(case["receipts"], case["complete"]) == run


@pytest.mark.parametrize("case", load_cases("evaluation", "refused"))
def test_an_invalid_run_raises_and_gives_no_outcome(case):
    with pytest.raises(ValueError, match=case["expected"]):
        evaluate_receipts(case["receipts"], RULESET, FIELDS, complete=True)


def case_named(name):
    return next(case.values[0] for case in load_cases("evaluation", "cases") if case.values[0]["id"] == name)


LOW = "a low confidence tobacco line gives no PERS-03, only a review"
TWO = "one receipt's exception does not flag another"


# --- No way around a step ---


def test_the_entry_point_takes_no_worked_out_results():
    assert list(inspect.signature(evaluate_receipts).parameters) == ["receipts", "ruleset", "fields", "complete"]


def test_receipts_are_a_complete_context_unless_said_otherwise():
    receipts = case_named("every check resolved and nothing found is a pass")["receipts"]
    assert evaluate_receipts(receipts, RULESET, FIELDS) == evaluate(receipts, complete=True)
    with pytest.raises(TypeError, match="complete"):
        evaluate_receipts(receipts, RULESET, FIELDS, complete=1)


def test_a_low_confidence_line_gives_a_finding_only_by_going_around_the_entry_point():
    case = case_named(LOW)
    context = build_context(case["receipts"], FIELDS, complete=True)
    around = run_checks(context.receipts[0], RULESET, FIELDS, context)
    assert "PERS-03" in {finding.rule_id for finding in around.findings}
    result = evaluate(case["receipts"]).result_for("R1")
    assert "PERS-03" not in {group.rule_id for group in result.report.groups}


def test_a_built_receipt_goes_through_admission_like_any_other():
    data = {**case_named("every check resolved and nothing found is a pass")["receipts"][0], "employee_country": "IN", "entity": "GLOBAL"}
    with pytest.raises(ValueError, match="contradicts"):
        evaluate([Receipt.model_validate(data)])
    good = admit_receipt(case_named("every check resolved and nothing found is a pass")["receipts"][0], FIELDS)
    assert evaluate([good]).results[0].disposition == "pass"


def test_no_disposition_directive_is_an_error_not_a_pass():
    without = RuleSet.model_construct(rules=tuple(rule for rule in RULESET.rules if rule.rule_id != "EXC-01"), ruleset_version="v1")
    with pytest.raises(ValueError, match="exactly one active disposition directive, found 0"):
        evaluate([], ruleset=without)


def test_a_severity_the_directive_does_not_map_is_an_error_not_a_pass():
    directive = next(rule for rule in RULESET.rules if rule.rule_id == "EXC-01")
    only_low = {**directive.params, "flag_severities": directive.params["flag_severities"].model_copy(update={"values": ("critical",)})}
    narrowed = directive.model_copy(update={"params": only_low})
    report = evaluate(case_named("a high exception flags")["receipts"]).results[0].report
    with pytest.raises(ValueError, match="EXC-01 gives no disposition for severity high"):
        decide_disposition(report, narrowed)


def test_the_flag_severities_come_from_the_rule():
    directive = next(rule for rule in RULESET.rules if rule.rule_id == "EXC-01")
    swapped = {
        "flag_severities": directive.params["flag_severities"].model_copy(update={"values": ("low",)}),
        "review_severities": directive.params["review_severities"].model_copy(update={"values": ("critical", "high", "medium")}),
    }
    report = evaluate(case_named("a high exception flags")["receipts"]).results[0].report
    assert decide_disposition(report, directive.model_copy(update={"params": swapped}))[0] == "review"


def test_a_retired_disposition_directive_is_an_error_not_a_pass():
    retired = tuple(rule.model_copy(update={"status": "retired"}) if rule.rule_id == "EXC-01" else rule for rule in RULESET.rules)
    with pytest.raises(ValueError, match="exactly one active disposition directive, found 0"):
        evaluate([], ruleset=RuleSet.model_construct(rules=retired, ruleset_version="v1"))


def test_an_undecided_receipt_with_nothing_against_it_is_a_provisional_pass_that_says_so():
    report = evaluate(case_named("every check resolved and nothing found is a pass")["receipts"]).results[0].report
    directive = next(rule for rule in RULESET.rules if rule.rule_id == "EXC-01")
    undecided = report.model_copy(update={"decided": False})
    disposition, reasons = decide_disposition(undecided, directive)
    # EXC-01: no exception and no review signal is a pass. Not decided, its reasons keep that evidence was incomplete.
    assert disposition == "pass"
    assert [(item.code, item.basis, item.rule_ids) for item in reasons] == [("nothing_found", "EXC-01", ()), ("incomplete_evidence", "project_decision", ())]


# --- A run ---


def test_results_are_in_receipt_order_whatever_order_receipts_come_in():
    receipts = list(case_named(TWO)["receipts"])
    forward = evaluate(receipts)
    for seed in range(5):
        random.Random(seed).shuffle(receipts)
        assert evaluate(receipts) == forward
    assert [item.receipt_id for item in forward.results] == ["R1", "R2"]


def test_receipts_in_separate_runs_are_kept_apart():
    """A split across two runs is no split, as each is evaluated on its own."""
    case = case_named("every check resolved and nothing found is a pass")
    base = case["receipts"][0]
    line = {**base["line_items"][0], "amount_usd": "40.00"}
    first = {**base, "line_items": [line]}
    second = {**base, "receipt_id": "R2", "line_items": [line]}
    together = evaluate([first, second])
    assert together.result_for("R1").disposition == "flag"
    assert evaluate([first]).results[0].disposition == "pass"
    assert evaluate([second]).results[0].disposition == "pass"


def test_a_run_and_its_results_cannot_be_changed_and_round_trip():
    run = evaluate(case_named("a high exception flags")["receipts"])
    result = run.results[0]
    with pytest.raises(ValueError):
        result.disposition = "pass"
    with pytest.raises(ValueError):
        run.results = ()
    with pytest.raises(ValueError):
        result.reasons[0].code = "nothing_found"
    with pytest.raises(ValueError):
        result.report.decided = True
    assert type(run).model_validate_json(run.model_dump_json()) == run


def test_the_input_is_not_changed():
    receipts = [admit_receipt(case_named(LOW)["receipts"][0], FIELDS)]
    before = receipts[0].model_dump()
    evaluate(receipts)
    assert receipts[0].model_dump() == before
    assert receipts[0].line_items[0].category == "tobacco"


def test_a_run_says_whether_its_context_was_complete():
    receipts = case_named("every check resolved and nothing found is a pass")["receipts"]
    assert evaluate(receipts, complete=True).context_complete is True
    assert evaluate(receipts, complete=False).context_complete is False


def test_an_empty_run_is_allowed_and_holds_nothing():
    run = evaluate([])
    assert (run.results, run.ruleset_version, run.context_complete) == ((), "v1", True)


# --- The benchmark: gold categories and fixture recipients stand in for a classifier, and the
# confidence of 1 is a stand-in too, as the data holds none ---


def classified(row):
    outputs = {
        item["line_id"]: {"category": item["labels"]["category"], "confidence": "1", **RECIPIENTS.get(item["line_id"], {})}
        for item in row["line_items"]
    }
    return admit_receipt(without_answers(row), FIELDS).with_classifications(outputs)


RUN = evaluate([classified(row) for row in ROWS])


def test_every_real_answer_matches_through_the_public_entry_point():
    matched, extras, missed, exact_lines, exceptions = 0, 0, 0, 0, 0
    for row in ROWS:
        result = RUN.result_for(row["receipt_id"])
        labels = row["labels"]
        got = {group.rule_id: list(group.line_ids) for group in result.active_exceptions}
        want = {entry["rule_id"]: entry["line_ids"] for entry in labels["expected_exceptions"]}
        signals = {group.rule_id for group in result.review_signals}
        want_signals = {entry["rule_id"] for entry in labels["review_signals"]}
        extras += len(set(got) - set(want)) + len(signals - want_signals)
        missed += len(set(want) - set(got)) + len(want_signals - signals)
        matched += len(set(got) & set(want)) + len(signals & want_signals)
        exceptions += len(set(got) & set(want))
        exact_lines += sum(got[rule_id] == want[rule_id] for rule_id in set(got) & set(want))
    assert (matched, extras, missed) == (37, 0, 0)
    assert (exact_lines, exceptions) == (28, 28)


def test_every_real_disposition_matches_the_answers():
    wrong = [(row["receipt_id"], row["labels"]["disposition"], RUN.result_for(row["receipt_id"]).disposition) for row in ROWS if RUN.result_for(row["receipt_id"]).disposition != row["labels"]["disposition"]]
    assert wrong == []


def test_only_one_real_receipt_is_undecided_and_it_is_flagged_anyway():
    undecided = [(item.receipt_id, item.disposition) for item in RUN.results if not item.decided]
    assert undecided == [("RCP-013", "flag")]


PARTIAL = evaluate([classified(row) for row in ROWS], complete=False)


def test_every_real_disposition_matches_the_answers_with_receipts_possibly_missing():
    # RCP-020 and RCP-021 are a split whatever receipts are missing; the clean receipts are passes.
    wrong = [(row["receipt_id"], row["labels"]["disposition"], PARTIAL.result_for(row["receipt_id"]).disposition) for row in ROWS if PARTIAL.result_for(row["receipt_id"]).disposition != row["labels"]["disposition"]]
    assert wrong == []


def test_a_real_pass_with_receipts_possibly_missing_is_provisional_and_says_what_is_open():
    for item in PARTIAL.results:
        if item.disposition != "pass":
            continue
        assert not (item.active_exceptions or item.review_signals or item.unresolved) and item.uncertainty is None
        assert item.decided == (not item.open_questions), item.receipt_id
        if not item.decided:
            assert [reason.code for reason in item.reasons] == ["nothing_found", "incomplete_evidence"], item.receipt_id
            assert item.reasons[1].rule_ids == tuple(question.rule_id for question in item.open_questions)


def test_no_real_review_rests_only_on_incomplete_evidence():
    assert [item.receipt_id for item in RUN.results if [reason.code for reason in item.reasons] == ["incomplete_evidence"]] == []


def test_a_real_low_exception_is_what_keeps_a_receipt_at_review():
    low = {item.receipt_id: item.disposition for item in RUN.results if any(reason.code == "exception_reviews" for reason in item.reasons)}
    assert low == {"RCP-031": "review", "RCP-032": "review"}
