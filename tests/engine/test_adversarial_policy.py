"""Runs cases/engine/adversarial_policy.yaml, through evaluate_receipts apart from the bounds it reads.

The expected outcomes are in the YAML, worked out from the policy.
"""

import copy
import itertools
from decimal import Decimal
from pathlib import Path

import pytest
from test_helpers import FIELDS, RULESET
from yaml_cases import exact, load_cases

from expense_validator.config import load_config
from expense_validator.engine.evaluation import evaluate_receipts
from expense_validator.engine.helpers import _Job, build_context

RANK = {"compliant": 0, "undetermined": 1, "confirmed": 2}
UNCERTAIN_WAYS = ({"confidence": "0.30"}, {"category": None}, {"category": "made_up"})
RULES = {rule.rule_id: rule for rule in RULESET.rules}
POLICY_DOCS = Path(load_config().paths.ruleset).parent.parent / "data" / "policy-docs"


def plain(section):
    return load_cases("adversarial_policy", section)


def run_of(receipts, complete=True, customer="northwind"):
    return evaluate_receipts(customer, receipts, RULESET, FIELDS, complete=complete)


def status_of(result, rule_id):
    if rule_id in {group.rule_id for group in result.active_exceptions}:
        return "confirmed"
    still_open = {group.rule_id for group in result.unresolved} | {item.rule_id for item in result.open_questions}
    return "undetermined" if rule_id in still_open else "compliant"


def is_status(result, rule_id, wanted):
    """compliant may leave an open question, as the engine keeps item rules open on an uncertain line."""
    found = status_of(result, rule_id)
    if wanted != "compliant":
        return found == wanted
    return found != "confirmed" and rule_id not in {group.rule_id for group in result.unresolved}


def group_lines(result, resolution):
    return [
        {"rule_id": group.rule_id, "line_ids": list(group.line_ids), "resolved_by": source}
        for group in result.report.groups
        if group.resolution == resolution
        for source in sorted({finding.resolved_by for finding in group.sources})
    ]


def evidence_of(result):
    found = {}
    for group in result.report.groups:
        if group.kind != "exception":
            continue
        for finding in group.sources:
            for line_id in finding.line_ids:
                facts = found.setdefault(group.rule_id, {}).setdefault(line_id, {})
                facts.update({item.fact: item.value for item in finding.evidence})
    return found


def assert_result(result, wanted):
    name = result.receipt_id
    active = result.active_exceptions
    checks = {
        "disposition": lambda: result.disposition,
        "decided": lambda: result.decided,
        "active": lambda: [group.rule_id for group in active],
        "severities": lambda: {group.rule_id: group.severity for group in active},
        "cited": lambda: {group.rule_id: list(group.line_ids) for group in active},
        "unresolved": lambda: [group.rule_id for group in result.unresolved],
        "audit": lambda: [{"rule_id": group.rule_id, "resolution": group.resolution} for group in result.audit_history],
        "replaced": lambda: group_lines(result, "replaced"),
        "waived": lambda: group_lines(result, "waived"),
        "uncertainty": lambda: [
            {"line_id": item.line_id, "field": item.field, "state": item.state}
            for item in (result.uncertainty.uncertainty if result.uncertainty else ())
        ],
    }
    for key, want in wanted.items():
        if key == "rules":
            for rule_id, status in want.items():
                assert is_status(result, rule_id, status), (name, rule_id, status_of(result, rule_id))
        elif key == "notes":
            for rule_id, text in want.items():
                said = [note for group in result.report.groups if group.rule_id == rule_id for note in group.source_notes if note]
                assert any(text in note for note in said), (name, rule_id, said)
        elif key == "cites_include":
            cited = {group.rule_id: group.line_ids for group in active}
            for rule_id, lines in want.items():
                assert set(lines) <= set(cited[rule_id]), (name, rule_id)
        elif key == "explains":
            for rule_id, text in want.items():
                said = [finding.explanation for group in active if group.rule_id == rule_id for finding in group.sources]
                assert said and all(text in explanation for explanation in said), (name, rule_id, said)
        elif key == "evidence":
            found = evidence_of(result)
            for rule_id, lines in want.items():
                for line_id, facts in lines.items():
                    for fact, value in facts.items():
                        assert found[rule_id][line_id].get(fact) == value, (name, rule_id, line_id, fact)
        else:
            if key == "severities":
                found = {rule_id: severity for rule_id, severity in checks[key]().items() if rule_id in want}
            elif key == "cited":
                found = {rule_id: lines for rule_id, lines in checks[key]().items() if rule_id in want}
            else:
                found = checks[key]()
            assert exact(found) == exact(want), (name, key)


def assert_expected(run, expected):
    assert sorted(item.receipt_id for item in run.results) == sorted(expected)
    for receipt_id, wanted in expected.items():
        assert_result(run.result_for(receipt_id), wanted)


def semantic(run):
    """What a result means, without the order things were listed in."""
    return {
        result.receipt_id: {
            "disposition": result.disposition,
            "decided": result.decided,
            "reasons": sorted((item.code, item.basis, *sorted(item.rule_ids)) for item in result.reasons),
            "groups": sorted(
                (
                    group.rule_id,
                    group.kind,
                    group.resolution,
                    str(group.severity),
                    tuple(sorted(group.line_ids)),
                    tuple(
                        sorted(
                            (
                                tuple(sorted(finding.line_ids)),
                                finding.status,
                                str(finding.resolved_by),
                                finding.explanation,
                                tuple(sorted((item.fact, str(item.value)) for item in finding.evidence)),
                            )
                            for finding in group.sources
                        )
                    ),
                    tuple(sorted(str(note) for note in group.source_notes)),
                    tuple(sorted((item.line_id, item.field, item.state, item.reason) for item in group.uncertainty)),
                )
                for group in result.report.groups
            ),
            "open": sorted(
                (item.rule_id, item.whole_receipt, tuple(sorted(item.line_ids)), item.entries, tuple(sorted(item.reasons)))
                for item in result.open_questions
            ),
        }
        for result in run.results
    }


def with_amount(receipts, vary, amount):
    changed = copy.deepcopy(receipts)
    for receipt in changed:
        for line in receipt["line_items"]:
            if receipt["receipt_id"] == vary["receipt_id"] and line["line_id"] == vary["line_id"]:
                line.update(unit_price=amount, amount=amount, amount_usd=amount)
    return changed


# Part 1


def worked_through(bound):
    """Lowest and highest per-unit amounts. None has no bound. This is the case's arithmetic, not the engine's."""
    known = Decimal(bound["known"])
    uncertain = bound["uncertain"]
    if uncertain == "no_usd_at_all":
        low, high = None, None
    elif uncertain == "no_usd_negative":
        low, high = None, known
    elif uncertain == "no_usd_positive":
        low, high = known, None
    else:
        amount = Decimal(uncertain or 0)
        low, high = known + min(amount, Decimal(0)), known + max(amount, Decimal(0))
    return tuple(None if value is None else value / bound["per"] for value in (low, high))


@pytest.mark.parametrize("case", plain("bounds"))
def test_the_bound_uses_the_real_threshold_and_gives_the_stated_status(case):
    bound = case["bound"]
    rule = RULES[bound["rule"]]
    threshold = rule.params[bound["param"]].value
    assert threshold == Decimal(bound["threshold"])
    assert rule.source_quote in " ".join((POLICY_DOCS / rule.policy_doc).read_text().split())
    low, high = worked_through(bound)
    if low is not None and low > threshold:
        status = "confirmed"
    elif high is not None and high <= threshold:
        status = "compliant"
    else:
        status = "undetermined"
    assert status == bound["status"]


@pytest.mark.parametrize("case", plain("bounds"))
def test_the_aggregate_outcome_follows_the_bound(case):
    assert_expected(run_of(case["receipts"], case["complete"]), case["expected"])


# Part 2


@pytest.mark.parametrize("case", plain("replacement"))
def test_gift_04_replaces_gift_01_on_its_lines_before_the_recipient_is_totalled(case):
    assert_expected(run_of(case["receipts"], case["complete"]), case["expected"])


@pytest.mark.parametrize("case", plain("replacement"))
def test_every_group_carries_its_own_rules_source_and_provenance(case):
    for result in run_of(case["receipts"], case["complete"]).results:
        for group in result.report.groups:
            if group.kind != "exception":
                continue
            rule = RULES[group.rule_id]
            assert (group.policy_doc, group.source_quote, group.provenance) == (rule.policy_doc, rule.source_quote, rule.provenance)
            for finding in group.sources:
                assert (finding.rule_id, finding.provenance) == (group.rule_id, rule.provenance)
            if group.resolution == "replaced":
                assert {finding.status for finding in group.sources} == {"replaced"}
                assert all(group.source_notes), group.group_id


@pytest.mark.parametrize("case", plain("replacement"))
def test_no_finding_is_both_active_and_replaced_on_one_line(case):
    for result in run_of(case["receipts"], case["complete"]).results:
        active = {(group.rule_id, line) for group in result.active_exceptions for line in group.line_ids}
        replaced = {(group.rule_id, line) for group in result.report.groups if group.resolution == "replaced" for line in group.line_ids}
        assert not active & replaced


# Part 3


@pytest.mark.parametrize("case", plain("isolation"))
def test_isolation(case):
    assert_expected(run_of(case["receipts"], case["complete"]), case["expected"])


@pytest.mark.parametrize("case", plain("conflicts_refused"))
def test_conflicting_duplicates_are_refused(case):
    with pytest.raises(ValueError, match=case["expected"]):
        run_of(case["receipts"])


@pytest.mark.parametrize("case", plain("separate_runs"))
def test_separate_runs_do_not_touch_each_other(case):
    customers = list(case["runs"])
    for order in (customers, customers[::-1]):
        for customer in order:
            run = run_of(case["runs"][customer], customer=customer)
            assert_expected(run, case["expected"][customer])
            assert {result.customer_id for result in run.results} == {customer}


# Part 4


@pytest.mark.parametrize("case", plain("ladders"))
def test_a_ladder_gives_the_status_its_bound_says_at_each_step(case):
    for amount, wanted in zip(case["amounts"], case["expected"]["statuses"], strict=True):
        result = run_of(with_amount(case["receipts"], case["vary"], amount), case["complete"]).result_for(case["vary"]["receipt_id"])
        assert is_status(result, case["rule"], wanted), (amount, status_of(result, case["rule"]))


@pytest.mark.parametrize("case", plain("ladders"))
def test_a_status_only_moves_one_way_along_a_ladder(case):
    """A charge going up cannot make a breach less certain, and a discount going deeper cannot make one more so."""
    found = [
        RANK[status_of(run_of(with_amount(case["receipts"], case["vary"], amount), case["complete"]).result_for(case["vary"]["receipt_id"]), case["rule"])]
        for amount in sorted(case["amounts"], key=Decimal)
    ]
    assert found == sorted(found)


def bounds_at(case, amount):
    receipts = with_amount(case["receipts"], case["vary"], amount)
    context = build_context("northwind", receipts, FIELDS, complete=True)
    receipt = next(item for item in context.receipts if item.receipt_id == case["vary"]["receipt_id"])
    return _Job(RULES["MEAL-01"], receipt, context, RULESET, FIELDS).spend_bounds(receipt)


@pytest.mark.parametrize("case", plain("spend_bounds"))
def test_spend_bounds_are_exact_at_each_step(case):
    found = [bounds_at(case, amount) for amount in case["amounts"]]
    assert [least for least, _ in found] == [Decimal(value) for value in case["expected"]["least"]]
    assert [most for _, most in found] == [Decimal(value) for value in case["expected"]["most"]]


@pytest.mark.parametrize("case", plain("spend_bounds"))
def test_spend_bounds_never_move_against_the_amount(case):
    pairs = [bounds_at(case, amount) for amount in sorted(case["amounts"], key=Decimal)]
    for (low_before, high_before), (low_after, high_after) in itertools.pairwise(pairs):
        assert low_before <= low_after
        assert high_before <= high_after


@pytest.mark.parametrize("case", plain("unrelated"))
def test_an_unrelated_employee_changes_nothing(case):
    receipt_id = case["receipts"][0]["receipt_id"]
    alone = run_of(case["receipts"]).result_for(receipt_id)
    together = run_of([*case["receipts"], *case["other"]])
    assert together.result_for(receipt_id) == alone
    for rule_id, status in case["expected"]["rules"].items():
        assert is_status(alone, rule_id, status), (rule_id, status_of(alone, rule_id))


@pytest.mark.parametrize("case", plain("ineligible"))
def test_a_known_ineligible_discount_reduces_nothing(case):
    receipt_id = case["receipts"][0]["receipt_id"]
    with_discount = copy.deepcopy(case["receipts"])
    with_discount[0]["line_items"].append(case["discount"])
    for receipts in (case["receipts"], with_discount):
        result = run_of(receipts).result_for(receipt_id)
        assert status_of(result, case["rule"]) == case["expected"]["status"]
    if case["rule"] == "MEAL-01":
        context = build_context("northwind", with_discount, FIELDS, complete=True)
        job = _Job(RULES["MEAL-01"], context.receipts[0], context, RULESET, FIELDS)
        assert job.spend_bounds(context.receipts[0]) == (Decimal("80"), Decimal("80"))


@pytest.mark.parametrize("way", UNCERTAIN_WAYS, ids=lambda way: next(iter(way)) + "=" + str(next(iter(way.values()))))
@pytest.mark.parametrize("case", plain("categorical"))
def test_a_categorical_prohibition_stands_when_an_unrelated_line_turns_uncertain(case, way):
    def standing(receipts):
        result = run_of(receipts).results[0]
        group = next(item for item in result.active_exceptions if item.rule_id == case["expected"]["rule"])
        assert result.disposition == "flag"
        return group.severity, list(group.line_ids)

    changed = copy.deepcopy(case["receipts"])
    for line in changed[0]["line_items"]:
        if line["line_id"] == case["line_id"]:
            line.update(way)
    wanted = (case["expected"]["severity"], case["expected"]["lines"])
    assert standing(case["receipts"]) == wanted
    assert standing(changed) == wanted


ORDERED = [
    pytest.param(param.values[0], id=f"{section}/{param.id}")
    for section in ("bounds", "replacement", "isolation")
    for param in plain(section)
]


def assert_receipt_order_is_free(case):
    want = semantic(run_of(case["receipts"], case["complete"]))
    for order in itertools.permutations(case["receipts"]):
        assert semantic(run_of(list(order), case["complete"])) == want


def assert_line_order_is_free(case):
    want = semantic(run_of(case["receipts"], case["complete"]))
    longest = max(len(receipt["line_items"]) for receipt in case["receipts"])
    for shift in range(1, longest):
        rotated = copy.deepcopy(case["receipts"])
        for receipt in rotated:
            items = receipt["line_items"]
            if items:
                receipt["line_items"] = items[shift % len(items):] + items[:shift % len(items)]
        assert semantic(run_of(rotated, case["complete"])) == want
    reversed_lines = copy.deepcopy(case["receipts"])
    for receipt in reversed_lines:
        receipt["line_items"].reverse()
    assert semantic(run_of(reversed_lines, case["complete"])) == want


@pytest.mark.parametrize("case", ORDERED)
def test_reordering_receipts_changes_nothing_in_meaning(case):
    assert_receipt_order_is_free(case)


@pytest.mark.parametrize("case", ORDERED)
def test_reordering_lines_changes_nothing_in_meaning(case):
    assert_line_order_is_free(case)
