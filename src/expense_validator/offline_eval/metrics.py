"""The scores, from plain outcomes. Nothing here reads the engine, so each is easy to check by hand.

An outcome is a mapping for one receipt, as runner.py builds them:

    receipt_id, expense_type, disposition, gold_disposition, decided, reason_codes
    exceptions       predicted active exceptions: rule to {line_ids, severity}
    gold_exceptions  the same, from the answers
    signals, gold_signals   rule IDs
    uncertain        whether any EXC-03 signal stands
    uncertainty      each signal's line_id, field and state
    unresolved, open        rule IDs
    proof            null, or {rule_ids, readings}

A rate says its numerator and denominator, and has no value when its denominator is zero: it is not
zero, as nothing was measured. A rule never raised has recall 0 and precision n/a, as data/README.md
says. EXC-03 is never in the answers, so each receipt with a signal is one false positive for it.
"""

import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal

DISPOSITIONS = ("flag", "review", "pass")
UNCERTAINTY_RULE = "EXC-03"
ABSTAINING_REASONS = {"uncertainty", "incomplete_evidence", "lines_do_not_account_for_total"}
Z_95 = 1.959963984540054


def _round(value: float) -> float:
    return round(value, 6)


def rate(numerator: int, denominator: int) -> dict:
    """With a 95% Wilson interval, as these denominators are small."""
    if denominator == 0:
        return {"numerator": numerator, "denominator": 0, "value": None, "wilson_95": None}
    value = numerator / denominator
    z2 = Z_95**2
    centre = value + z2 / (2 * denominator)
    margin = Z_95 * math.sqrt(value * (1 - value) / denominator + z2 / (4 * denominator**2))
    scale = 1 + z2 / denominator
    low, high = max(0.0, (centre - margin) / scale), min(1.0, (centre + margin) / scale)
    return {"numerator": numerator, "denominator": denominator, "value": _round(value), "wilson_95": [_round(low), _round(high)]}


def prf(tp: int, fp: int, fn: int) -> dict:
    """F1 is the count form, so it is 0 rather than undefined once anything was predicted or expected."""
    f1 = None if tp + fp + fn == 0 else _round(2 * tp / (2 * tp + fp + fn))
    return {"tp": tp, "fp": fp, "fn": fn, "precision": rate(tp, tp + fp)["value"], "recall": rate(tp, tp + fn)["value"], "f1": f1}


def _miss_kind(outcome: Mapping, rule_id: str) -> str:
    """Why an expected rule was not raised. Silent is the one nothing on the result warns of."""
    if rule_id in outcome["unresolved"]:
        return "unresolved"
    if rule_id in outcome["open"]:
        return "open"
    if outcome["proof"] and rule_id in outcome["proof"]["rule_ids"]:
        return "in_proof"
    return "silent"


def rule_scores(outcomes: Iterable[Mapping]) -> dict:
    """Per rule, then micro totals: strict counts EXC-03 as unmatched, and diagnostic leaves it out."""
    counts: dict[str, Counter] = defaultdict(Counter)
    for outcome in outcomes:
        expected = {**outcome["gold_exceptions"], **{rule: None for rule in outcome["gold_signals"]}}
        raised = {**outcome["exceptions"], **{rule: None for rule in outcome["signals"]}}
        for rule_id in sorted(set(expected) | set(raised)):
            row = counts[rule_id]
            if rule_id in expected and rule_id in raised:
                row["tp"] += 1
                gold, found = expected[rule_id], raised[rule_id]
                if gold is not None and found is not None:
                    row["evidence_total"] += 1
                    row["evidence_exact"] += sorted(found["line_ids"]) == gold["line_ids"]
                    row["severity_exact"] += found["severity"] == gold["severity"]
            elif rule_id in expected:
                row["fn"] += 1
                row[f"fn_{_miss_kind(outcome, rule_id)}"] += 1
            else:
                row["fp"] += 1
        if outcome["uncertain"]:
            counts[UNCERTAINTY_RULE]["fp"] += 1
    rules = {}
    for rule_id in sorted(counts):
        row = counts[rule_id]
        rules[rule_id] = {
            **prf(row["tp"], row["fp"], row["fn"]),
            "evidence_exact": rate(row["evidence_exact"], row["evidence_total"]),
            "severity_exact": rate(row["severity_exact"], row["evidence_total"]),
            "fn_by_kind": {kind: row[f"fn_{kind}"] for kind in ("unresolved", "open", "in_proof", "silent")},
        }
    total = lambda name, skip=(): sum(row[name] for rule_id, row in counts.items() if rule_id not in skip)
    strict = prf(total("tp"), total("fp"), total("fn"))
    diagnostic = prf(total("tp"), total("fp", (UNCERTAINTY_RULE,)), total("fn"))
    return {
        "rules": rules,
        "strict": strict,
        "diagnostic": diagnostic,
        "evidence_exact": rate(total("evidence_exact"), total("evidence_total")),
        "fn_by_kind": {kind: total(f"fn_{kind}") for kind in ("unresolved", "open", "in_proof", "silent")},
    }


def exact_match(outcomes: Sequence[Mapping]) -> dict:
    """Receipts whose whole answer is right. The rules-only form ignores which lines are cited."""
    rules_only = lines_too = 0
    for outcome in outcomes:
        same_signals = set(outcome["signals"]) == set(outcome["gold_signals"]) and not outcome["uncertain"]
        same_rules = set(outcome["exceptions"]) == set(outcome["gold_exceptions"])
        rules_only += same_rules and same_signals
        lines_too += (
            same_rules
            and same_signals
            and all(sorted(found["line_ids"]) == outcome["gold_exceptions"][rule]["line_ids"] for rule, found in outcome["exceptions"].items())
        )
    return {"rules": rate(rules_only, len(outcomes)), "rules_and_lines": rate(lines_too, len(outcomes))}


def is_abstaining(outcome: Mapping) -> bool:
    """A review with nothing behind it but not knowing: no exception and no signal from a rule."""
    return outcome["disposition"] == "review" and set(outcome["reason_codes"]) <= ABSTAINING_REASONS


def disposition_scores(outcomes: Sequence[Mapping]) -> dict:
    matrix = {gold: {got: 0 for got in DISPOSITIONS} for gold in DISPOSITIONS}
    for outcome in outcomes:
        matrix[outcome["gold_disposition"]][outcome["disposition"]] += 1
    count = lambda test: sum(1 for outcome in outcomes if test(outcome))
    size = len(outcomes)
    gold_not_pass = count(lambda o: o["gold_disposition"] != "pass")
    gold_flag = count(lambda o: o["gold_disposition"] == "flag")
    gold_not_flag = size - gold_flag
    return {
        "matrix_gold_by_predicted": matrix,
        "accuracy": rate(count(lambda o: o["disposition"] == o["gold_disposition"]), size),
        # A pass on what should not pass: the failure that reaches no human.
        "false_pass": rate(count(lambda o: o["gold_disposition"] != "pass" and o["disposition"] == "pass"), gold_not_pass),
        "missed_flag_passed": rate(count(lambda o: o["gold_disposition"] == "flag" and o["disposition"] == "pass"), gold_flag),
        "missed_flag_any": rate(count(lambda o: o["gold_disposition"] == "flag" and o["disposition"] != "flag"), gold_flag),
        "false_flag": rate(count(lambda o: o["gold_disposition"] != "flag" and o["disposition"] == "flag"), gold_not_flag),
        "review": rate(count(lambda o: o["disposition"] == "review"), size),
        "abstention": rate(count(is_abstaining), size),
        "decided": rate(count(lambda o: o["decided"]), size),
        "flagged_undecided": rate(count(lambda o: o["disposition"] == "flag" and not o["decided"]), count(lambda o: o["disposition"] == "flag")),
    }


def proof_scores(outcomes: Sequence[Mapping], critical_rules: Iterable[str]) -> dict:
    """What inevitable_violation proofs did. Without one, each of these receipts would have been a review."""
    critical = set(critical_rules)
    proved = [outcome for outcome in outcomes if outcome["proof"]]
    possible_critical = [outcome for outcome in proved if critical & set(outcome["proof"]["rule_ids"])]
    gold_flag = [outcome for outcome in proved if outcome["gold_disposition"] == "flag"]
    covered = [
        outcome
        for outcome in proved
        if set(outcome["gold_exceptions"]) & set(outcome["proof"]["rule_ids"])
    ]
    return {
        "receipts": rate(len(proved), len(outcomes)),
        "readings_total": sum(outcome["proof"]["readings"] for outcome in proved),
        "flag_without_active_exception": sum(1 for outcome in proved if not outcome["exceptions"]),
        "correct_flag": rate(len(gold_flag), len(proved)),
        "false_flag": rate(len(proved) - len(gold_flag), len(proved)),
        "gold_rule_in_proof": rate(len(covered), len(proved)),
        "disposition_accuracy_gain": len(gold_flag) - sum(1 for outcome in proved if outcome["gold_disposition"] == "review"),
        "possible_critical": rate(len(possible_critical), len(proved)),
        "possible_critical_receipts": sorted(outcome["receipt_id"] for outcome in possible_critical),
        "actually_critical": sum(1 for outcome in possible_critical if critical & set(outcome["gold_exceptions"])),
        "receipt_ids": sorted(outcome["receipt_id"] for outcome in proved),
    }


def classification_scores(lines: Sequence[Mapping]) -> dict:
    """Per the brief, over every line: macro over the categories in the answers. A line is a mapping of

    line_id, gold_category, gold_restricted, category, restricted, confidence.

    A line with no prediction is a miss for its true category, and is counted as missing too.
    """
    golds = sorted({line["gold_category"] for line in lines})
    per_category = {}
    exact_scores = {"precision": [], "recall": [], "f1": []}
    for category in golds:
        tp = sum(1 for line in lines if line["gold_category"] == category and line["category"] == category)
        fp = sum(1 for line in lines if line["gold_category"] != category and line["category"] == category)
        fn = sum(1 for line in lines if line["gold_category"] == category and line["category"] != category)
        scores = prf(tp, fp, fn)
        # The brief counts a category never predicted as zero precision.
        per_category[category] = {**scores, "precision": 0.0 if tp + fp == 0 else scores["precision"]}
        # Unrounded, so a mean is not of rounded values. Every category here is in the answers, so tp + fn > 0.
        exact_scores["precision"].append(tp / (tp + fp) if tp + fp else 0.0)
        exact_scores["recall"].append(tp / (tp + fn))
        exact_scores["f1"].append(2 * tp / (2 * tp + fp + fn))
    macro = {name: None if not golds else _round(sum(values) / len(values)) for name, values in exact_scores.items()}
    restricted_tp = sum(1 for line in lines if line["gold_restricted"] is True and line["restricted"] is True)
    restricted_fp = sum(1 for line in lines if line["gold_restricted"] is False and line["restricted"] is True)
    restricted_fn = sum(1 for line in lines if line["gold_restricted"] is True and line["restricted"] is not True)
    return {
        "lines": len(lines),
        "accuracy": rate(sum(1 for line in lines if line["category"] == line["gold_category"]), len(lines)),
        "missing_category": rate(sum(1 for line in lines if line["category"] is None), len(lines)),
        "missing_confidence": rate(sum(1 for line in lines if line["confidence"] is None), len(lines)),
        "missing_restricted": rate(sum(1 for line in lines if line["restricted"] is None), len(lines)),
        "macro": macro,
        "per_category": per_category,
        "restricted": prf(restricted_tp, restricted_fp, restricted_fn),
    }


def confidence_bucket(confidence: Decimal | None, threshold: Decimal) -> str:
    """The first edge is the EXC-03 threshold in the ruleset, below which a line is left out of the rules."""
    if confidence is None:
        return "missing"
    if confidence < threshold:
        return f"below_{threshold}"
    if confidence < Decimal("0.7"):
        return f"{threshold}_to_0.7"
    if confidence < Decimal("0.9"):
        return "0.7_to_0.9"
    return "0.9_to_1"


def line_bucket_scores(lines: Sequence[Mapping], threshold: Decimal) -> dict:
    grouped: dict[str, list[Mapping]] = defaultdict(list)
    for line in lines:
        grouped[confidence_bucket(line["confidence"], threshold)].append(line)
    return {
        bucket: {
            "lines": len(members),
            "category_correct": rate(sum(1 for line in members if line["category"] == line["gold_category"]), len(members)),
        }
        for bucket, members in sorted(grouped.items())
    }


def group_scores(outcomes: Sequence[Mapping], key) -> dict:
    """Receipt results for each group, where key gives a receipt's groups. A receipt can be in several."""
    grouped: dict[str, list[Mapping]] = defaultdict(list)
    for outcome in outcomes:
        for group in key(outcome):
            grouped[group].append(outcome)
    result = {}
    for group, members in sorted(grouped.items()):
        scores = disposition_scores(members)
        result[group] = {
            "receipts": len(members),
            "accuracy": scores["accuracy"],
            "false_pass": scores["false_pass"],
            "false_flag": scores["false_flag"],
            "abstention": scores["abstention"],
            "decided": scores["decided"],
            "rules": rule_scores(members)["strict"],
        }
    return result


def rule_family(rule_id: str) -> str:
    return rule_id.split("-")[0].split(".")[0]


def family_scores(rules: Mapping[str, Mapping]) -> dict:
    grouped: dict[str, Counter] = defaultdict(Counter)
    for rule_id, row in rules.items():
        for name in ("tp", "fp", "fn"):
            grouped[rule_family(rule_id)][name] += row[name]
    return {family: prf(row["tp"], row["fp"], row["fn"]) for family, row in sorted(grouped.items())}


def missing_field_scores(outcomes: Sequence[Mapping]) -> dict:
    """Per classified field and state: how many lines, and how the receipts holding them came out."""
    lines: Counter = Counter()
    for outcome in outcomes:
        for item in outcome["uncertainty"]:
            lines[f"{item['field']}:{item['state']}"] += 1
    groups = group_scores(outcomes, lambda outcome: sorted({f"{item['field']}:{item['state']}" for item in outcome["uncertainty"]}))
    return {name: {"lines": lines[name], **groups[name]} for name in sorted(groups)}


def calibration(points: Sequence[tuple[Decimal, bool]], bins: int = 10) -> dict:
    """Expected calibration error and Brier score over (confidence, was it right) pairs."""
    if not points:
        return {"points": 0, "ece": None, "brier": None, "bins": []}
    grouped: dict[int, list[tuple[float, bool]]] = defaultdict(list)
    for confidence, correct in points:
        value = float(confidence)
        grouped[min(int(value * bins), bins - 1)].append((value, correct))
    rows = []
    ece = 0.0
    for index in sorted(grouped):
        members = grouped[index]
        mean = sum(value for value, _ in members) / len(members)
        accuracy = sum(1 for _, correct in members if correct) / len(members)
        ece += len(members) / len(points) * abs(mean - accuracy)
        rows.append({"low": index / bins, "high": (index + 1) / bins, "points": len(members), "mean_confidence": _round(mean), "accuracy": _round(accuracy)})
    brier = sum((float(confidence) - (1.0 if correct else 0.0)) ** 2 for confidence, correct in points) / len(points)
    return {"points": len(points), "ece": _round(ece), "brier": _round(brier), "bins": rows}
