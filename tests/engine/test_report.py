"""Runs the cases in cases/engine/report.yaml, and measures the report against the real data."""

import random

import pytest
from receipt_rows import ROWS
from test_helpers import CONTEXT, FIELDS, RULESET
from yaml_cases import exact, load_cases, selected

from expense_validator.engine.approvals import resolve_approvals
from expense_validator.engine.carveouts import resolve_carveouts
from expense_validator.engine.checks import run_checks
from expense_validator.engine.helpers import build_context
from expense_validator.engine.models import Finding
from expense_validator.engine.replacements import resolve_replacements
from expense_validator.engine.report import ReportEntry, build_report, entries_of, group_entries, signals_of
from expense_validator.engine.uncertainty import assess_context
from expense_validator.policy.schema import RuleSet


def run(view, context, ruleset):
    replacements = resolve_replacements(view, run_checks(view, ruleset, FIELDS, context), ruleset, FIELDS)
    return resolve_carveouts(view, resolve_approvals(view, replacements, ruleset), ruleset, FIELDS, context)


def report_for(case, ruleset, customer="northwind"):
    context = build_context(customer, case["receipts"], FIELDS, complete=case["complete"])
    assessed = assess_context(context, ruleset, FIELDS)
    view = assessed.assessment_for(case["receipt_id"]).evaluation_receipt
    return build_report(assessed, case["receipt_id"], run(view, assessed.evaluation, ruleset), ruleset, FIELDS)


def groups(report, kind, resolution):
    return [{"rule_id": item.rule_id, "severity": item.severity, "line_ids": list(item.line_ids)} for item in report.of(kind, resolution)]


def summary(report):
    uncertainty = report.of("uncertainty", "active")
    return {
        "exceptions": groups(report, "exception", "active"),
        "unresolved": groups(report, "exception", "unresolved"),
        "replaced": groups(report, "exception", "replaced"),
        "waived": groups(report, "exception", "waived"),
        "excused": groups(report, "exception", "excused"),
        "uncitable": groups(report, "exception", "uncitable"),
        "signals": [{"rule_id": item.rule_id, "line_ids": list(item.line_ids)} for item in report.review_signals],
        "uncertainty": [
            {"line_id": item.line_id, "field": item.field, "reason": item.reason}
            for group in uncertainty
            for item in group.uncertainty
        ],
        "groups_of_uncertainty": len(uncertainty),
        "uncertainty_lines": [line for group in uncertainty for line in group.line_ids],
        "open": [
            {"rule_id": item.rule_id, "whole_receipt": item.whole_receipt, "line_ids": list(item.line_ids), "entries": item.entries}
            for item in report.open_questions
        ],
        "decided": report.decided,
        "sources": [
            {"rule_id": finding.rule_id, "line_ids": list(finding.line_ids)}
            for group in report.groups
            if group.kind != "uncertainty"
            for finding in group.sources
        ],
        "source_notes": [
            {"line_ids": list(finding.line_ids), "note": note}
            for group in report.groups
            if group.kind != "uncertainty"
            for finding, note in zip(group.sources, group.source_notes)
        ],
        "source_severities": [finding.severity for group in report.exceptions for finding in group.sources],
    }


def assert_summary(report, expected):
    expected = dict(expected)
    included = expected.pop("open_includes", [])
    made = summary(report)
    assert all(item in made["open"] for item in included), made["open"]
    assert exact(selected(made, expected)) == exact(expected)


def made_up_ruleset(case):
    return RuleSet.model_validate({"ruleset_version": "v1", "rules": case["rules"]})


@pytest.mark.parametrize("case", load_cases("report", "real"))
def test_the_real_rules_report_this_the_same_way_every_time(case):
    report = report_for(case, RULESET)
    assert_summary(report, case["expected"])
    assert report_for(case, RULESET) == report


@pytest.mark.parametrize("case", load_cases("report", "made_up"))
def test_these_rules_report_this(case):
    assert_summary(report_for(case, made_up_ruleset(case)), case["expected"])


@pytest.mark.parametrize("case", load_cases("report", "refused"))
def test_a_report_that_cannot_be_made_is_refused(case):
    with pytest.raises(ValueError, match=case["expected"]):
        report_for(case, made_up_ruleset(case))


def real_case(name):
    return next(case.values[0] for case in load_cases("report", "real") if case.values[0]["id"] == name)


MANY = "mixed severities report the highest"
AUDIT = "one rule with active and excused lines makes two groups"
SIGNALS = "several signals across lines are one group, with each reason kept"


# --- What a group keeps ---


def test_a_group_keeps_every_source_whole_with_its_own_severity_and_rule_metadata():
    report = report_for(real_case(MANY), RULESET)
    group = report.exceptions[0]
    assert [finding.severity for finding in group.sources] == ["low", "medium", "low"]
    assert group.severity == "medium"
    assert all(isinstance(finding, Finding) and finding.evidence for finding in group.sources)
    assert (group.customer_id, group.receipt_id, group.rule_id, group.kind) == ("northwind", "R1", "HEALTH-01", "exception")
    assert group.policy_doc == group.sources[0].policy_doc == "non-reimbursable-items.md"
    assert group.provenance == group.sources[0].provenance
    assert group.title == "Over-the-counter medication"
    assert group.group_id == "northwind/R1/HEALTH-01/active"


def test_a_resolved_source_keeps_its_status_and_the_group_says_why():
    report = report_for(real_case(AUDIT), RULESET)
    excused = report.of("exception", "excused")[0]
    assert excused.sources[0].status == "waived" and excused.sources[0].resolved_by == "PERS.BAGGAGE"
    assert "not authenticated" in excused.notes[0]
    active = report.exceptions[0]
    assert active.sources[0].status == "raised"
    assert "not an item the allowance covers" in active.notes[0]


def test_the_uncertainty_group_keeps_each_signal_with_its_details():
    group = report_for(real_case(SIGNALS), RULESET).uncertainty
    assert (group.kind, group.severity, group.rule_id, group.line_ids) == ("uncertainty", None, "EXC-03", ("L1", "L2", "L3"))
    assert [item.state for item in group.uncertainty] == ["low_confidence", "unknown", "missing"]
    assert [str(item.confidence) for item in group.uncertainty] == ["0.1", "0.9", "0.9"]
    assert [finding.line_ids for finding in group.sources] == [("L1",), ("L2",), ("L3",)]
    assert group.title == "Uncertainty" and group.policy_doc == "approval-and-exception-handling.md"


def test_a_report_round_trips_through_json_and_cannot_be_changed():
    report = report_for(real_case(MANY), RULESET)
    assert type(report).model_validate_json(report.model_dump_json()) == report
    with pytest.raises(ValueError):
        report.decided = True
    with pytest.raises(ValueError):
        report.groups[0].severity = "low"
    with pytest.raises(ValueError):
        report.groups[0].sources[0].severity = "high"


def test_a_whole_receipt_exception_cites_no_line_though_its_finding_names_one():
    case = real_case("a whole-receipt exception cites no line")
    report = report_for(case, RULESET)
    view = assess_context(build_context("northwind", case["receipts"], FIELDS, complete=True), RULESET, FIELDS).assessment_for("R1").evaluation_receipt
    named = report.exceptions[0].sources[0].model_copy(update={"line_ids": ("L1",)})
    group = group_entries("northwind", view, [ReportEntry(named, "active")], (), RULESET)[0]
    assert group.line_ids == ()
    assert group.sources[0].line_ids == ("L1",)


def test_a_helper_not_run_is_an_open_question_and_not_a_violation():
    case = real_case("no signal means no uncertainty group")
    assessed = assess_context(build_context("northwind", case["receipts"], FIELDS, complete=True), RULESET, FIELDS)
    view = assessed.assessment_for("R1").evaluation_receipt
    replacements = resolve_replacements(view, run_checks(view, RULESET, FIELDS), RULESET, FIELDS)
    result = resolve_carveouts(view, resolve_approvals(view, replacements, RULESET), RULESET, FIELDS, assessed.evaluation)
    report = build_report(assessed, "R1", result, RULESET, FIELDS)
    question = next(item for item in report.open_questions if item.rule_id == "MEAL-01")
    assert (question.whole_receipt, question.entries, question.reasons) == (True, 1, ("the meal_cap helper has not been run",))
    assert report.exceptions == () and report.decided is False


def test_rebuilding_a_report_keeps_each_finding_with_its_own_note():
    case = real_case("each source keeps its own note, though lines and notes sort differently")
    entries = entries_of(report_for(case, RULESET))
    pairs = [(entry.finding.line_ids[0], entry.note) for entry in entries]
    assert pairs == [("L9", "L9 is not an item the allowance covers"), ("L1", "L1 is not an item the allowance covers")]


def test_the_notes_view_lists_each_distinct_note_once_and_source_notes_keeps_them_all():
    group = report_for(real_case("identical notes are kept for each source, not merged away"), RULESET).exceptions[0]
    assert group.source_notes == ("no baggage_delay_ref is on file", "no baggage_delay_ref is on file")
    assert group.notes == ("no baggage_delay_ref is on file",)


def test_a_group_needs_one_note_entry_per_source():
    group = report_for(real_case(MANY), RULESET).exceptions[0]
    with pytest.raises(ValueError, match="needs one note entry per source"):
        type(group).model_validate({**group.model_dump(), "source_notes": ()})


# --- Order and repeating ---


@pytest.mark.parametrize("name", [MANY, AUDIT, SIGNALS, "different rules on one line stay apart"])
def test_the_order_entries_come_in_changes_nothing(name):
    case = real_case(name)
    report = report_for(case, RULESET)
    view = assess_context(build_context("northwind", case["receipts"], FIELDS, complete=True), RULESET, FIELDS).assessment_for("R1").evaluation_receipt
    entries, signals = list(entries_of(report)), list(signals_of(report))
    for seed in range(5):
        random.Random(seed).shuffle(entries)
        random.Random(seed).shuffle(signals)
        assert group_entries("northwind", view, entries, signals, RULESET) == report.groups


@pytest.mark.parametrize("case", load_cases("report", "real"))
def test_grouping_what_a_report_holds_gives_the_same_groups(case):
    report = report_for(case, RULESET)
    assessed = assess_context(build_context("northwind", case["receipts"], FIELDS, complete=case["complete"]), RULESET, FIELDS)
    view = assessed.assessment_for(case["receipt_id"]).evaluation_receipt
    assert group_entries("northwind", view, entries_of(report), signals_of(report), RULESET) == report.groups


# --- Staying apart ---


def test_customers_with_the_same_receipt_do_not_share_a_group():
    case = real_case("every line a rule caught, in one exception")
    first, second = report_for(case, RULESET, "northwind"), report_for(case, RULESET, "acme")
    assert first.exceptions[0].group_id == "northwind/R1/ALC-01/active"
    assert second.exceptions[0].group_id == "acme/R1/ALC-01/active"
    assert second.customer_id == second.exceptions[0].customer_id == "acme"


def test_a_finding_for_another_receipt_cannot_join_a_group():
    case = real_case("every line a rule caught, in one exception")
    report = report_for(case, RULESET)
    view = assess_context(build_context("northwind", case["receipts"], FIELDS, complete=True), RULESET, FIELDS).assessment_for("R1").evaluation_receipt
    foreign = report.exceptions[0].sources[0].model_copy(update={"receipt_id": "R2"})
    with pytest.raises(ValueError, match="R1: has findings for another receipt"):
        group_entries("northwind", view, [ReportEntry(foreign, "active")], (), RULESET)


def test_a_result_for_another_receipt_or_a_receipt_not_assessed_is_refused():
    case = real_case("a breach on one receipt is reported only on its own")
    assessed = assess_context(build_context("northwind", case["receipts"], FIELDS, complete=True), RULESET, FIELDS)
    view = assessed.assessment_for("R1").evaluation_receipt
    result = run(view, assessed.evaluation, RULESET)
    with pytest.raises(ValueError, match="R2: has findings for another receipt"):
        build_report(assessed, "R2", result, RULESET, FIELDS)
    with pytest.raises(ValueError, match="R9: is not in the assessed context"):
        build_report(assessed, "R9", result, RULESET, FIELDS)


def test_a_run_that_skipped_the_uncertainty_view_is_refused():
    """Run on the admitted receipt, a low-confidence tobacco line gives PERS-03, and complete says True."""
    case = {"receipts": [{**real_case("a signal and a confirmed exception stay apart")["receipts"][0], "line_items": [{**real_case("every line a rule caught, in one exception")["receipts"][0]["line_items"][0], "category": "tobacco", "confidence": "0.1"}]}]}
    context = build_context("northwind", case["receipts"], FIELDS, complete=True)
    assessed = assess_context(context, RULESET, FIELDS)
    around = run(context.receipts[0], context, RULESET)
    assert [finding.rule_id for finding in around.active][-1] == "PERS-03"
    with pytest.raises(ValueError, match="PERS-03: read the category of an uncertain line"):
        build_report(assessed, "R1", around, RULESET, FIELDS)


def test_decided_cannot_be_true_while_a_signal_stands_though_the_later_steps_are_complete():
    case = real_case("a signal and a confirmed exception stay apart")
    assessed = assess_context(build_context("northwind", case["receipts"], FIELDS, complete=True), RULESET, FIELDS)
    view = assessed.assessment_for("R1").evaluation_receipt
    result = run(view, assessed.evaluation, RULESET)
    assert result.complete is True
    assert build_report(assessed, "R1", result, RULESET, FIELDS).decided is False


# --- The benchmark, with gold categories standing in for a classifier ---


ASSESSED = assess_context(CONTEXT, RULESET, FIELDS)


def report_of(receipt_id):
    view = ASSESSED.assessment_for(receipt_id).evaluation_receipt
    return build_report(ASSESSED, receipt_id, run(view, ASSESSED.evaluation, RULESET), RULESET, FIELDS)


def test_every_real_answer_matches_on_the_report_with_its_exact_lines():
    matched = extras = missed = 0
    for row in ROWS:
        report = report_of(row["receipt_id"])
        labels = row["labels"]
        got = {group.rule_id: list(group.line_ids) for group in report.exceptions}
        assert len(got) == len(report.exceptions), "each rule at most once per receipt"
        want = {entry["rule_id"]: entry["line_ids"] for entry in labels["expected_exceptions"]}
        signals = {group.rule_id for group in report.review_signals}
        want_signals = {entry["rule_id"] for entry in labels["review_signals"]}
        extras += len(set(got) - set(want)) + len(signals - want_signals)
        missed += len(set(want) - set(got)) + len(want_signals - signals)
        for rule_id in set(got) & set(want):
            assert got[rule_id] == want[rule_id], (row["receipt_id"], rule_id)
            matched += 1
        matched += len(signals & want_signals)
    assert (matched, extras, missed) == (37, 0, 0)


def test_the_extra_exc_03_counts_once_on_a_receipt_as_the_data_dictionary_counts_it():
    with_signals = {row["receipt_id"]: report_of(row["receipt_id"]).uncertainty for row in ROWS}
    extra = {name: group for name, group in with_signals.items() if group is not None}
    assert list(extra) == ["RCP-013"]
    assert len(extra["RCP-013"].sources) == 2
    matched, unmatched = 37, len(extra)
    assert (matched, matched + unmatched) == (37, 38)


def test_the_report_agrees_with_the_findings_it_groups():
    for row in ROWS:
        report = report_of(row["receipt_id"])
        view = ASSESSED.assessment_for(row["receipt_id"]).evaluation_receipt
        result = run(view, ASSESSED.evaluation, RULESET)
        counted = sum(len(group.sources) for group in report.groups if group.kind != "uncertainty")
        assert counted == len(result.candidates)
        reported = {group.rule_id for group in (*report.exceptions, *report.review_signals)}
        assert reported == {finding.rule_id for finding in result.active}
        assert report.decided is (row["receipt_id"] != "RCP-013")
