"""Runs cases/engine/submission_integrity.yaml: amounts that agree, contradict, or cannot be compared."""

import re
from decimal import Decimal
from pathlib import Path

import pytest
from receipt_rows import ROWS, without_answers
from test_helpers import FIELDS, RULESET
from test_adversarial_policy import assert_expected
from yaml_cases import exact, load_cases, selected

from expense_validator.engine import money
from expense_validator.engine.evaluation import check_run, disposition_directive, evaluate_receipts
from expense_validator.engine.inputs import admit_receipt
from expense_validator.engine.models import CLASSIFIED_FIELDS, Receipt


def as_json(receipt):
    return receipt.model_dump(mode="json")


def values(found, wanted):
    """The named values of an admitted receipt, where a name goes deeper: line_items.0.amount."""
    out = {}
    for name in wanted:
        value = found
        for part in name.split("."):
            value = value[int(part)] if part.isdigit() else value[part]
        out[name] = value
    return out


def run_one(receipt):
    return evaluate_receipts("c", [receipt], RULESET, FIELDS, complete=True).results[0]


def assert_evaluation(result, wanted):
    wanted = dict(wanted)
    still_open = {item.rule_id for item in result.open_questions}
    assert set(wanted.pop("open_includes", ())) <= still_open, sorted(still_open)
    assert not set(wanted.pop("open_excludes", ())) & still_open, sorted(still_open)
    states = wanted.pop("monetary", {})
    monetary = result.report.monetary.model_dump(mode="json")
    assert exact({name: monetary[name] for name in states}) == exact(states)
    found = {"disposition": result.disposition, "decided": result.decided, "active": [group.rule_id for group in result.active_exceptions]}
    assert exact(selected(found, wanted)) == exact(wanted)


@pytest.mark.parametrize("case", load_cases("submission_integrity", "exploits"))
def test_an_exploit_is_refused_with_this_exact_error_and_gets_no_result(case):
    with pytest.raises(ValueError) as caught:
        evaluate_receipts("attack", case["receipts"], RULESET, FIELDS, complete=True)
    assert str(caught.value) == case["expected"]["error"]


@pytest.mark.parametrize("case", load_cases("submission_integrity", "adversarial"))
def test_each_adversarial_case_is_accepted_and_reconciled_unresolved_or_rejected(case):
    if case["result"] == "rejected":
        with pytest.raises(ValueError, match=re.escape(case["expected"]["error"])):
            run_one(case["receipt"])
        return
    result = run_one(case["receipt"])
    assert_evaluation(result, case["expected"])
    states = result.report.monetary.model_dump(mode="json")
    names = ("fx_rate", "line_arithmetic", "line_conversion", "total_conversion", "line_sum")
    assert "contradictory" not in {states[name] for name in names}
    if case["result"] == "reconciled":
        assert "unverified" not in {states[name] for name in names}
    else:
        assert not (result.disposition == "pass")


@pytest.mark.parametrize("case", load_cases("submission_integrity", "refused"))
def test_contradictory_amounts_are_refused(case):
    with pytest.raises(ValueError, match=case["expected"]):
        admit_receipt(case["receipt"], FIELDS)


@pytest.mark.parametrize("case", load_cases("submission_integrity", "unsupported_refused"))
def test_amounts_outside_the_contract_are_refused_as_such(case):
    with pytest.raises(ValueError, match=case["expected"]) as caught:
        admit_receipt(case["receipt"], FIELDS)
    assert "unsupported amounts" in str(caught.value)
    assert "contradictory" not in str(caught.value)


@pytest.mark.parametrize("case", load_cases("submission_integrity", "admitted"))
def test_consistent_amounts_are_admitted_unchanged(case):
    found = as_json(admit_receipt(case["receipt"], FIELDS))
    assert exact(values(found, case["expected"])) == exact(case["expected"])


@pytest.mark.parametrize("case", load_cases("submission_integrity", "unresolved"))
def test_missing_evidence_is_not_a_contradiction(case):
    assert_evaluation(run_one(case["receipt"]), case["expected"])


# --- 15: nothing gets round admission ---


def receipt_data():
    return next(param.values[0] for param in load_cases("submission_integrity", "admitted") if param.values[0]["id"] == "a zero line")["receipt"]


def test_a_receipt_built_directly_is_admitted_like_any_other():
    tampered = {**receipt_data(), "entity": "GLOBAL", "total_amount_usd": "1.00"}
    built = Receipt.model_validate(tampered)
    assert built.total_amount_usd == Decimal("1.00")
    with pytest.raises(ValueError, match="receipt total USD value 1.00 contradicts"):
        run_one(built)


def test_a_copy_made_around_validation_is_admitted_like_any_other():
    copy = admit_receipt(receipt_data(), FIELDS).model_copy(update={"total_amount_usd": Decimal("1.00")})
    with pytest.raises(ValueError, match="receipt total USD value 1.00 contradicts"):
        run_one(copy)


def test_a_line_copied_around_validation_is_admitted_like_any_other():
    admitted = admit_receipt(receipt_data(), FIELDS)
    line = admitted.line_items[0].model_copy(update={"amount_usd": Decimal("1.00")})
    copy = admitted.model_copy(update={"line_items": (line, *admitted.line_items[1:])})
    with pytest.raises(ValueError, match="USD value 1.00 contradicts"):
        run_one(copy)


@pytest.mark.parametrize("field", ["amount", "amount_usd", "unit_price", "quantity"])
def test_a_classifier_cannot_set_an_amount(field):
    assert field not in CLASSIFIED_FIELDS
    with pytest.raises(ValueError, match="a classifier may not set"):
        admit_receipt(receipt_data(), FIELDS).with_classifications({"L1": {field: 1}})


def test_a_classifier_cannot_set_itemization_complete():
    assert "itemization_complete" not in CLASSIFIED_FIELDS


# --- The record, and the view rules read ---


def no_rate_case():
    return next(param.values[0] for param in load_cases("submission_integrity", "adversarial") if param.values[0]["id"].startswith("1 "))["receipt"]


def test_the_record_keeps_a_submitted_usd_value_though_rules_do_not_read_it():
    from expense_validator.engine.helpers import build_context
    from expense_validator.engine.uncertainty import assess_context

    context = build_context("c", [no_rate_case()], FIELDS, complete=True)
    assessment = assess_context(context, RULESET, FIELDS).assessments[0]
    assert assessment.receipt.total_amount_usd == Decimal("1.00")
    assert assessment.receipt.line_items[0].amount_usd == Decimal("1.00")
    assert assessment.evaluation_receipt.total_amount_usd is None
    assert assessment.evaluation_receipt.line_items[0].amount_usd is None
    # Original-currency amounts are read as ever.
    assert assessment.evaluation_receipt.total_amount == Decimal("100.00")
    assert assessment.evaluation_receipt.line_items[0].amount == Decimal("100.00")


def test_lines_that_do_not_account_for_the_total_give_their_own_reason_alone():
    case = next(param.values[0] for param in load_cases("submission_integrity", "adversarial") if param.values[0]["id"].startswith("10 itemization declared incomplete"))
    result = run_one(case["receipt"])
    assert [(reason.code, reason.basis, reason.rule_ids) for reason in result.reasons] == [("lines_do_not_account_for_total", "project_decision", ())]


def test_a_supplied_usd_value_beside_no_rate_makes_no_difference_to_the_result():
    with_usd = run_one(no_rate_case())
    data = no_rate_case()
    without_usd = run_one({**data, "total_amount_usd": None, "line_items": [{**data["line_items"][0], "amount_usd": None}]})
    assert with_usd.disposition == without_usd.disposition
    assert with_usd.decided == without_usd.decided
    assert [group.rule_id for group in with_usd.active_exceptions] == [group.rule_id for group in without_usd.active_exceptions]
    assert with_usd.open_questions == without_usd.open_questions


def test_reconciled_values_are_not_blanked():
    receipt = admit_receipt(receipt_data(), FIELDS)
    assert money.without_unverified_usd(receipt, money.reconcile(receipt, FIELDS)) is receipt


# --- The contract ---


def test_the_contract_must_be_there_with_no_default():
    with pytest.raises(ValueError, match="no input_contract"):
        admit_receipt(receipt_data(), {name: field for name, field in FIELDS.items() if name != "input_contract"})


@pytest.mark.parametrize("change", [{"version": 0}, {"minor_units": None}, {"usd_tolerance": "0"}, {"max_decimal_places": -1}, {"made_up": 1}])
def test_a_malformed_contract_is_refused(change):
    contract = {**FIELDS["input_contract"], **change}
    with pytest.raises(ValueError):
        money.monetary_contract({**FIELDS, "input_contract": contract})


def test_the_contract_covers_every_currency_in_the_data_and_catalog():
    seen = {row["currency"] for row in ROWS} | set(FIELDS["receipt.employee_country"]["home_currency"].values())
    assert seen <= set(FIELDS["input_contract"]["minor_units"])


def test_a_result_says_which_contract_it_was_checked_against():
    assert run_one(receipt_data()).report.monetary.contract_version == FIELDS["input_contract"]["version"]


@pytest.mark.parametrize("row", ROWS, ids=lambda row: row["receipt_id"])
def test_no_benchmark_receipt_is_refused_changed_or_left_unverified(row):
    given = without_answers(row)
    admitted = admit_receipt(given, FIELDS)
    found = money.reconcile(admitted, FIELDS)
    assert found.problems == () and found.unsupported == ()
    assert found.fx_rate == "reconciled" and found.line_conversion == "reconciled" and found.total_conversion == "reconciled"
    assert found.line_sum == ("reconciled" if row["itemized"] else "not_applicable")
    assert money.without_unverified_usd(admitted, found) is admitted
    for line, source in zip(admitted.line_items, given["line_items"], strict=True):
        assert line.amount == Decimal(str(source["amount"]))
        assert line.amount_usd == Decimal(str(source["amount_usd"]))


def test_the_check_names_no_rule_category_or_threshold():
    text = Path(money.__file__).read_text()
    code = re.sub(r'""".*?"""', "", text, flags=re.S)
    assert not re.search(r"\b[A-Z]{2,}-\d{2}\b", text)
    for category in FIELDS["item.category"]["values"]:
        assert f'"{category}"' not in code
    assert "helper" not in code
    # Precision and tolerances are in the contract, and not here.
    assert not re.search(r'"0\.\d+"', code) and "JPY" not in code


def test_no_fixture_in_the_monetary_suite_asks_for_totals_from_its_lines():
    text = (Path(__file__).parent.parent / "cases" / "engine" / "submission_integrity.yaml").read_text()
    assert "$lines\"" not in text.split("templates:")[1]


# --- Audit of Module 11C.1: unverified USD values reach nothing ---

SENTINEL = "987654.32"


def with_usd(receipts, value):
    """The receipts, with this USD value wherever no conversion can be verified, as the view must ignore."""
    changed = []
    for receipt in receipts:
        rate_known = receipt.get("currency") == "USD" or (receipt.get("currency") is not None and receipt.get("fx_rate_to_usd") is not None)
        if rate_known:
            if receipt.get("total_amount") is None:
                receipt = {**receipt, "total_amount_usd": value}
            changed.append(receipt)
            continue
        changed.append({**receipt, "total_amount_usd": value, "line_items": [{**line, "amount_usd": value} for line in receipt["line_items"]]})
    return changed


def evaluated(receipts, complete=True):
    return evaluate_receipts("c", receipts, RULESET, FIELDS, complete=complete)


def without_states(run):
    dumped = run.model_dump(mode="json")
    for result in dumped["results"]:
        del result["report"]["monetary"]
    return dumped


@pytest.mark.parametrize("case", load_cases("submission_integrity", "unverified_usd"))
def test_an_unverified_usd_value_changes_nothing_anywhere(case):
    empty = evaluated(with_usd(case["receipts"], None), case["complete"])
    for value in ("1.00", SENTINEL):
        given = evaluated(with_usd(case["receipts"], value), case["complete"])
        # The states may differ, as a value given and none given are not the same input. Nothing else may.
        assert without_states(given) == without_states(empty)
        assert SENTINEL not in given.model_dump_json()
    assert_expected(empty, case["expected"])


@pytest.mark.parametrize("case", load_cases("submission_integrity", "decided"))
def test_monetary_uncertainty_stays_visible_beside_a_flag(case):
    result = run_one(case["receipt"])
    wanted = dict(case["expected"])
    codes = wanted.pop("reasons")
    assert sorted(reason.code for reason in result.reasons) == sorted(codes)
    assert_evaluation(result, wanted)


@pytest.mark.parametrize("value", ["absent", "true", "false"])
@pytest.mark.parametrize("case", load_cases("submission_integrity", "itemization_claims"))
def test_a_claim_about_itemization_can_only_add_scrutiny(case, value):
    claim = {"absent": {}, "true": {"itemization_complete": True}, "false": {"itemization_complete": False}}[value]
    wanted = case["expected"][value]
    if wanted == "refused":
        with pytest.raises(ValueError, match="contradicts its complete itemization"):
            run_one({**case["receipt"], **claim})
    else:
        assert run_one({**case["receipt"], **claim}).disposition == wanted


def test_a_claim_of_completeness_on_lines_that_add_up_changes_nothing():
    case = next(param.values[0] for param in load_cases("submission_integrity", "itemization_claims") if param.values[0]["id"] == "lines that add up")
    assert run_one({**case["receipt"], "itemization_complete": True}).report == run_one(case["receipt"]).report


def test_reconciled_only_means_the_amounts_given_agree():
    """A total and lines that both leave out the same item agree. Nothing here can see that."""
    case = next(param.values[0] for param in load_cases("submission_integrity", "itemization_claims") if param.values[0]["id"] == "lines that add up")
    lines = case["receipt"]["line_items"][:2]
    left_out = {**case["receipt"], "itemization_complete": True, "total_amount": "80.00", "total_amount_usd": "80.00", "line_items": lines}
    assert run_one(left_out).report.monetary.line_sum == "reconciled"


# --- Partial verification ---


def built(**changes):
    return Receipt.model_validate({**receipt_data(), "entity": "GLOBAL", **changes})


def test_a_line_with_no_usd_amount_is_never_reconciled():
    """Admission refuses this shape, so this calls the check itself: its state must not depend on that."""
    data = receipt_data()
    lines = [data["line_items"][0], {**data["line_items"][1], "amount_usd": None}]
    assert money.reconcile(built(line_items=lines), FIELDS).line_conversion == "unverified"


def test_every_line_with_no_usd_amount_is_never_reconciled():
    data = receipt_data()
    lines = [{**line, "amount_usd": None} for line in data["line_items"]]
    assert money.reconcile(built(line_items=lines), FIELDS).line_conversion == "unverified"


def test_a_contradiction_outranks_a_missing_usd_amount():
    data = receipt_data()
    lines = [{**data["line_items"][0], "amount_usd": "1.00"}, {**data["line_items"][1], "amount_usd": None}]
    assert money.reconcile(built(line_items=lines), FIELDS).line_conversion == "contradictory"


def test_a_missing_line_usd_amount_blanks_every_line_in_the_view():
    data = receipt_data()
    lines = [data["line_items"][0], {**data["line_items"][1], "amount_usd": None}]
    receipt = built(line_items=lines)
    view = money.without_unverified_usd(receipt, money.reconcile(receipt, FIELDS))
    assert [line.amount_usd for line in view.line_items] == [None, None]


@pytest.mark.parametrize("rate", ["1", "2", None])
def test_a_missing_currency_verifies_nothing_whatever_the_rate(rate):
    found = money.reconcile(built(currency=None, fx_rate_to_usd=rate), FIELDS)
    assert (found.fx_rate, found.line_conversion, found.total_conversion) == ("unverified", "unverified", "unverified")
    assert found.problems == ()


def test_a_usd_total_with_no_printed_total_is_never_reconciled():
    assert money.reconcile(built(total_amount=None), FIELDS).total_conversion == "unverified"


def test_a_printed_total_with_no_usd_total_is_never_reconciled():
    assert money.reconcile(built(total_amount_usd=None), FIELDS).total_conversion == "unverified"


# --- check_run holds it to this ---


def real_run(receipt):
    return evaluate_receipts("c", [receipt], RULESET, FIELDS, complete=True)


def checked(run):
    check_run(run, [item.receipt_id for item in run.results], disposition_directive(RULESET), RULESET)


def tampered(run, **monetary):
    result = run.results[0]
    report = result.report.model_copy(update={"monetary": result.report.monetary.model_copy(update=monetary)})
    return run.model_copy(update={"results": (result.model_copy(update={"report": report}),)})


def adding_up():
    return next(param.values[0] for param in load_cases("submission_integrity", "itemization_claims") if param.values[0]["id"] == "lines that add up")["receipt"]


def test_a_decided_receipt_cannot_have_lines_that_do_not_account_for_its_total():
    with pytest.raises(ValueError, match="a decided receipt cannot have lines"):
        checked(tampered(real_run(adding_up()), line_sum="unverified"))


@pytest.mark.parametrize("state", ["fx_rate", "line_conversion", "total_conversion"])
def test_a_pass_cannot_rest_on_an_amount_that_could_not_be_verified(state):
    with pytest.raises(ValueError, match="a pass cannot rest on an amount"):
        checked(tampered(real_run(adding_up()), **{state: "unverified"}))


def test_lines_that_do_not_account_for_the_total_cannot_lose_their_reason():
    run = real_run({**adding_up(), "total_amount": "500.00", "total_amount_usd": "500.00"})
    result = run.results[0]
    kept = tuple(reason for reason in result.reasons if reason.code != "lines_do_not_account_for_total")
    with pytest.raises(ValueError, match="need their reason"):
        checked(run.model_copy(update={"results": (result.model_copy(update={"reasons": kept}),)}))
