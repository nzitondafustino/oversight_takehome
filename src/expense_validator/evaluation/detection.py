"""Exception detection and evidence correctness, scored per rule against the answers. Reads no file.

Each receipt's expected findings (exceptions and review signals) are matched to the findings it raised one to one,
on receipt and rule ID. A receipt-level disposition is never taken as proof of a finding: a receipt rightly
flagged for ALC-01 that should also have raised TIME-03 still misses TIME-03.

- A rule raised more often on a receipt than expected: each extra is a false positive, marked duplicate.
- A receipt that could not be evaluated raised nothing: each of its expected findings is missed, as not_evaluated.
- Precision with nothing raised, recall with nothing expected, and F1 with neither are undefined (None), never 0
  or 1. A rule with no expected finding is not tested: it is listed apart and never averaged as perfect.
- Macro scores are means over the tested rules only. A tested rule never raised has precision 0, as a category
  never predicted does in classification_scores.
- Evidence is scored only on matched exceptions whose answer lists its lines: the cited lines are compared with the
  expected ones as sets. An answer without line IDs is not scorable, never assumed correct. Signals cite no lines.
"""

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence

from expense_validator.evaluation.metrics import UNCERTAINTY_RULE, prf, rate


def _ordered(findings: Iterable[Mapping]) -> list[Mapping]:
    return sorted(findings, key=lambda finding: (finding["rule_id"], sorted(finding.get("line_ids") or [])))


def _overlap(one: Mapping, other: Mapping) -> list[str]:
    return sorted(set(one.get("line_ids") or []) & set(other.get("line_ids") or []))


def detection_scores(receipts: Sequence[Mapping], rules: Iterable[str] = ()) -> dict:
    """Score raised findings against expected ones.

    Args:
        receipts: Each {receipt_id, evaluated, expected, predicted}. A finding is {rule_id, kind, line_ids, severity};
            line_ids is None in an answer that does not say which lines it cites.
        rules: Rule IDs to list even when nothing expected or raised them, such as every rule of the ruleset.
    """
    counts: dict[str, Counter] = defaultdict(Counter)
    for rule_id in rules:
        counts[rule_id]  # listed, at zero
    missing, unexpected, attribution, evidence = [], [], [], []
    for receipt in receipts:
        expected = defaultdict(list)
        for finding in _ordered(receipt["expected"]):
            expected[finding["rule_id"]].append(finding)
        predicted = defaultdict(list)
        for finding in _ordered(receipt["predicted"] if receipt["evaluated"] else ()):
            predicted[finding["rule_id"]].append(finding)
        missed_here, extra_here = [], []
        for rule_id in sorted(set(expected) | set(predicted)):
            wanted, raised, row = expected[rule_id], predicted[rule_id], counts[rule_id]
            row["expected"] += len(wanted)
            row["predicted"] += len(raised)
            for gold, found in zip(wanted, raised):  # matched one to one
                row["tp"] += 1
                if gold["kind"] == "exception":
                    evidence.append({"receipt_id": receipt["receipt_id"], "rule_id": rule_id, "expected": gold, "found": found})
                    if gold.get("line_ids") is not None:
                        row["evidence_scorable"] += 1
                        row["evidence_exact"] += set(gold["line_ids"]) == set(found.get("line_ids") or [])
            for gold in wanted[len(raised):]:
                row["fn"] += 1
                missed_here.append({"receipt_id": receipt["receipt_id"], "rule_id": rule_id, "kind": gold["kind"], "expected_line_ids": gold.get("line_ids"),
                                    "reason": "raised" if raised else "not_raised" if receipt["evaluated"] else "not_evaluated"})
            for found in raised[len(wanted):]:
                row["fp"] += 1
                duplicate = len(raised) > 1
                row["duplicates"] += duplicate
                extra_here.append({"receipt_id": receipt["receipt_id"], "rule_id": rule_id, "kind": found["kind"], "line_ids": found.get("line_ids") or [], "duplicate": duplicate})
        # A rule raised in place of another on the same lines: the right lines, the wrong rule.
        for gold in missed_here:
            for found in extra_here:
                shared = _overlap({"line_ids": gold["expected_line_ids"]}, found)
                if shared and found["rule_id"] != gold["rule_id"]:
                    attribution.append({"receipt_id": receipt["receipt_id"], "expected_rule": gold["rule_id"], "raised_rule": found["rule_id"], "shared_line_ids": shared})
        missing += missed_here
        unexpected += extra_here
    per_rule = {rule_id: _rule_row(counts[rule_id]) for rule_id in sorted(counts)}
    tested = [row for row in per_rule.values() if row["status"] == "tested"]
    total = lambda name, skip=(): sum(row[name] for rule_id, row in counts.items() if rule_id not in skip)
    return {
        "micro": prf(total("tp"), total("fp"), total("fn")),
        # EXC-03 is never in the answers: every uncertainty signal is a false positive in micro, and left out here.
        "micro_without_uncertainty_signals": prf(total("tp"), total("fp", (UNCERTAINTY_RULE,)), total("fn")),
        "macro": {
            "rules": len(tested),
            "precision": _mean([row["precision"] if row["precision"] is not None else 0.0 for row in tested]),
            "recall": _mean([row["recall"] for row in tested]),
            "f1": _mean([row["f1"] for row in tested]),
        },
        "expected": total("expected"),
        "predicted": total("predicted"),
        "per_rule": per_rule,
        "rules_by_status": {status: sorted(rule_id for rule_id, row in per_rule.items() if row["status"] == status) for status in ("tested", "raised_without_answers", "not_tested")},
        "missing": missing,
        "unexpected": unexpected,
        "attribution": attribution,
        "attribution_counts": dict(sorted(Counter(f"{item['expected_rule']} → {item['raised_rule']}" for item in attribution).items())),
        "evidence": evidence_scores(evidence),
    }


def _rule_row(row: Counter) -> dict:
    scores = prf(row["tp"], row["fp"], row["fn"])
    status = "tested" if row["expected"] else "raised_without_answers" if row["predicted"] else "not_tested"
    return {"expected": row["expected"], "predicted": row["predicted"], **scores, "duplicates": row["duplicates"], "status": status,
            "evidence_exact": rate(row["evidence_exact"], row["evidence_scorable"])}


def _mean(values: list[float]) -> float | None:
    return None if not values else round(sum(values) / len(values), 6)


def evidence_scores(matches: Sequence[Mapping]) -> dict:
    """Score the lines each matched exception cites, as sets.

    Args:
        matches: Each {receipt_id, rule_id, expected, found}, the expected and raised exception of one match.
    """
    scorable = [match for match in matches if match["expected"].get("line_ids") is not None]
    exact = tp = fp = fn = severity_exact = 0
    mismatches = []
    for match in scorable:
        wanted, cited = set(match["expected"]["line_ids"]), set(match["found"].get("line_ids") or [])
        tp, fp, fn = tp + len(wanted & cited), fp + len(cited - wanted), fn + len(wanted - cited)
        severity_exact += match["found"].get("severity") == match["expected"].get("severity")
        if wanted == cited:
            exact += 1
        else:
            mismatches.append({"receipt_id": match["receipt_id"], "rule_id": match["rule_id"], "expected_line_ids": sorted(wanted), "cited_line_ids": sorted(cited),
                               "missing_line_ids": sorted(wanted - cited), "extra_line_ids": sorted(cited - wanted)})
    return {
        "matched_exceptions": len(matches),
        "scorable": len(scorable),
        "not_scorable": len(matches) - len(scorable),
        "exact": rate(exact, len(scorable)),
        # Over line IDs: of the lines cited, how many were expected; of the lines expected, how many were cited.
        "line_precision": rate(tp, tp + fp),
        "line_recall": rate(tp, tp + fn),
        "severity_exact": rate(severity_exact, len(scorable)),
        "mismatches": mismatches,
    }
