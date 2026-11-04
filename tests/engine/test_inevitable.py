"""Runs cases/engine/inevitable.yaml, and checks each proof by rerunning its readings with the types set."""

import copy

import pytest
from test_adversarial_policy import assert_expected, assert_line_order_is_free, assert_receipt_order_is_free, run_of
from test_helpers import FIELDS, RULESET
from yaml_cases import load_cases

from expense_validator.engine.evaluation import check_run, disposition_directive

CASES = load_cases("inevitable", "cases")


def run_and_inevitable(case):
    run = run_of(case["receipts"], case["complete"])
    return run, {item.receipt_id: item.inevitable for item in run.results}


def proof_of(receipts, complete, receipt_id):
    return run_of(receipts, complete).result_for(receipt_id).inevitable


@pytest.mark.parametrize("case", [*CASES, *load_cases("inevitable", "at_the_cap")])
def test_this_outcome_holds(case):
    wanted = {receipt_id: {key: value for key, value in item.items() if key != "inevitable"} for receipt_id, item in case["expected"].items()}
    run, proofs = run_and_inevitable(case)
    assert_expected(run, wanted)
    for receipt_id, item in case["expected"].items():
        if "inevitable" not in item:
            continue
        want, proof = item["inevitable"], proofs[receipt_id]
        if want is None:
            assert proof is None, receipt_id
        else:
            assert proof is not None, receipt_id
            assert list(proof.rule_ids) == want["rule_ids"]
            assert len(proof.readings) == want["readings"]


@pytest.mark.parametrize("case", CASES)
def test_each_reading_in_a_proof_flags_when_it_is_actually_given(case):
    """The proof says so, and so must the engine with that value on the line, with nothing left to read."""
    for result in run_of(case["receipts"], case["complete"]).results:
        if result.inevitable is None:
            continue
        for reading in result.inevitable.readings:
            receipts = copy.deepcopy(case["receipts"])
            for read in reading.lines:
                receipt = next(item for item in receipts if item["receipt_id"] == read.receipt_id)
                line = next(item for item in receipt["line_items"] if item["line_id"] == read.line_id)
                line["recipient_type"] = read.recipient_type
            given = run_of(receipts, case["complete"]).result_for(result.receipt_id)
            assert given.disposition == "flag"
            flagged = {group.rule_id for group in given.active_exceptions}
            assert set(reading.rule_ids) <= flagged


@pytest.mark.parametrize("case", CASES)
def test_a_proof_adds_no_finding_and_leaves_the_report_as_it_was(case):
    for result in run_of(case["receipts"], case["complete"]).results:
        if result.inevitable is None:
            continue
        assert result.decided is False
        assert not [group for group in result.active_exceptions if group.severity in {"critical", "high", "medium"}]
        assert [reason.code for reason in result.reasons].count("inevitable_violation") == 1
        assert result.inevitable.explanation


@pytest.mark.parametrize("case", CASES)
def test_receipt_order_changes_nothing(case):
    assert_receipt_order_is_free(case)
    forward = run_of(case["receipts"], case["complete"])
    backward = run_of(case["receipts"][::-1], case["complete"])
    assert forward == backward


@pytest.mark.parametrize("case", CASES)
def test_line_order_changes_nothing(case):
    assert_line_order_is_free(case)
    reversed_lines = copy.deepcopy(case["receipts"])
    for receipt in reversed_lines:
        receipt["line_items"].reverse()
    first = {item.receipt_id: item.inevitable for item in run_of(case["receipts"], case["complete"]).results}
    second = {item.receipt_id: item.inevitable for item in run_of(reversed_lines, case["complete"]).results}
    assert first == second


def proved_run():
    case = next(param.values[0] for param in CASES if param.values[0]["id"] == "60 with no recipient type flags in every reading")
    return run_of(case["receipts"], case["complete"])


def checked(run):
    check_run(run, [item.receipt_id for item in run.results], disposition_directive(RULESET), RULESET)


def with_result(run, **changes):
    return run.model_copy(update={"results": (run.results[0].model_copy(update=changes),)})


def test_a_real_proof_passes_the_check():
    checked(proved_run())


def test_a_flag_with_no_exception_and_no_proof_is_refused():
    with pytest.raises(ValueError, match="or a proof one is inevitable"):
        checked(with_result(proved_run(), inevitable=None, reasons=tuple(r for r in proved_run().results[0].reasons if r.code != "inevitable_violation")))


def test_a_reason_with_no_proof_is_refused():
    run = proved_run()
    with pytest.raises(ValueError, match="needs its proof"):
        checked(with_result(run, inevitable=None, disposition="review"))


def test_a_proof_on_a_decided_receipt_is_refused():
    run = proved_run()
    report = run.results[0].report.model_copy(update={"decided": True})
    with pytest.raises(ValueError, match="a decided receipt"):
        checked(with_result(run, decided=True, report=report))


def test_a_proof_with_no_flag_is_refused():
    with pytest.raises(ValueError, match="must flag"):
        checked(with_result(proved_run(), disposition="review"))


def test_a_proof_with_no_reason_is_refused():
    run = proved_run()
    kept = tuple(reason for reason in run.results[0].reasons if reason.code != "inevitable_violation")
    with pytest.raises(ValueError, match="needs its reason"):
        checked(with_result(run, reasons=kept))


def test_a_proof_beside_an_exception_that_flags_is_refused():
    proved = proved_run().results[0]
    flagged = run_of([{**CASES[0].values[0]["receipts"][0], "line_items": [{**CASES[0].values[0]["receipts"][0]["line_items"][0], "recipient_type": "client"}]}], True)
    reason = next(item for item in proved.reasons if item.code == "inevitable_violation")
    undecided = flagged.results[0].report.model_copy(update={"decided": False})
    tampered = with_result(flagged, inevitable=proved.inevitable, reasons=(*flagged.results[0].reasons, reason), decided=False, report=undecided)
    with pytest.raises(ValueError, match="only for a receipt with no active exception"):
        checked(tampered)


def test_the_readings_are_exactly_the_catalog_values():
    readings = proved_run().results[0].inevitable.readings
    assert {reading.lines[0].recipient_type for reading in readings} == set(FIELDS["item.recipient_type"]["values"])
