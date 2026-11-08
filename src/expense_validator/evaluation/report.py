"""Write metrics.json, per-receipt JSONL and report.md, deterministically."""

import json
from collections.abc import Mapping
from pathlib import Path

SCHEMA_VERSION = 1
UNMEASURED = "**Real classifier accuracy remains unmeasured.** No predictions from any classifier exist in this repository. Every figure below comes from oracle or synthetic input, and describes how the engine responds to classifications, not how well anything classifies."
RULE_DETAIL = ("A", "B", "E", "F", "H0")
BREAKDOWN_DETAIL = ("A", "G0", "H0", "I0")
MATRIX_DETAIL = ("A", "B", "C", "D", "E", "F")
SENSITIVITY = {"G": "Right categories, uniform confidence", "H": "10% wrong, at confidence 0.95", "I": "The same lines wrong, at confidence 0.30"}


def to_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str, ensure_ascii=False) + "\n"


def _pct(item: Mapping | None) -> str:
    if item is None or item["value"] is None:
        return "n/a"
    return f"{item['numerator']}/{item['denominator']}"


def _ci(item: Mapping) -> str:
    if item["value"] is None:
        return "n/a"
    low, high = item["wilson_95"]
    return f"{item['numerator']}/{item['denominator']} ({low:.0%} to {high:.0%})"


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _table(headers: list[str], rows: list[list[object]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(lines)


def metrics_document(inputs: Mapping, runs: Mapping[str, Mapping], smoke: Mapping[str, Mapping], comparison: Mapping) -> dict:
    strip = lambda run: {name: value for name, value in run.items() if name != "receipts"}
    return {
        "schema_version": SCHEMA_VERSION,
        "real_classifier_measured": any(run["measured_classifier_performance"] for run in runs.values()),
        "inputs": dict(inputs),
        "baseline_comparison": dict(comparison),
        "scenarios": {key: strip(run) for key, run in runs.items()},
        "synthetic_dataset_scenarios": {key: strip(run) for key, run in smoke.items()},
    }


def _summary(runs: Mapping[str, Mapping]) -> str:
    rows = []
    for key, run in runs.items():
        scores, rules = run["disposition"], run["rules"]
        rows.append(
            [
                key,
                run["source"]["kind"],
                run["source"]["description"],
                _num(rules["strict"]["precision"]),
                _num(rules["strict"]["recall"]),
                _num(rules["strict"]["f1"]),
                _pct(scores["accuracy"]),
                _pct(scores["false_pass"]),
                _pct(scores["false_flag"]),
                _pct(scores["abstention"]),
                _pct(scores["decided"]),
                rules["fn_by_kind"]["silent"],
            ]
        )
    return _table(["", "Input", "What it is", "Rule P", "Rule R", "Rule F1", "Disposition right", "False pass", "False flag", "Abstained", "Decided", "Silent misses"], rows)


def _matrix(run: Mapping) -> str:
    matrix = run["disposition"]["matrix_gold_by_predicted"]
    return _table(["Answer \\ Engine", "flag", "review", "pass"], [[gold, *[matrix[gold][got] for got in ("flag", "review", "pass")]] for gold in ("flag", "review", "pass")])


def _rule_table(run: Mapping) -> str:
    rows = [
        [rule, row["tp"], row["fp"], row["fn"], _num(row["precision"]), _num(row["recall"]), _num(row["f1"]), _pct(row["evidence_exact"]), row["fn_by_kind"]["open"] + row["fn_by_kind"]["unresolved"] + row["fn_by_kind"]["in_proof"], row["fn_by_kind"]["silent"]]
        for rule, row in run["rules"]["rules"].items()
    ]
    return _table(["Rule", "TP", "FP", "FN", "P", "R", "F1", "Exact lines", "Misses with a warning", "Silent misses"], rows)


def _group_table(groups: Mapping[str, Mapping], first: str) -> str:
    rows = [[name, row["receipts"], _pct(row["accuracy"]), _pct(row["false_pass"]), _pct(row["false_flag"]), _pct(row["abstention"]), _pct(row["decided"])] for name, row in groups.items()]
    return _table([first, "Receipts", "Disposition right", "False pass", "False flag", "Abstained", "Decided"], rows) if rows else "None in this run."


def _breakdowns(key: str, run: Mapping) -> str:
    parts = [f"### Scenario {key}: {run['source']['description']}", "", f"Input kind: {run['source']['kind']}. Not classifier performance." if not run["measured_classifier_performance"] else "Measured.", ""]
    lines = run["breakdowns"]["line_confidence"]
    parts += ["Lines by category confidence:", "", _table(["Confidence", "Lines", "Category right"], [[name, row["lines"], _pct(row["category_correct"])] for name, row in lines.items()]), ""]
    parts += ["Receipts by their least trusted line:", "", _group_table(run["breakdowns"]["receipt_lowest_confidence"], "Lowest confidence"), ""]
    parts += ["Receipts holding an uncertain field (a receipt can be in several):", "", _group_table(run["breakdowns"]["missing_field"], "Field:state"), ""]
    parts += ["Receipts by declared expense type:", "", _group_table(run["breakdowns"]["expense_type"], "Expense type"), ""]
    family = run["breakdowns"]["rule_family"]
    parts += ["Rule families:", "", _table(["Family", "TP", "FP", "FN", "P", "R", "F1"], [[name, row["tp"], row["fp"], row["fn"], _num(row["precision"]), _num(row["recall"]), _num(row["f1"])] for name, row in family.items()]), ""]
    return "\n".join(parts)


def _sensitivity(runs: Mapping[str, Mapping]) -> str:
    rows = []
    for letter, label in SENSITIVITY.items():
        members = [run for key, run in runs.items() if key[0] == letter and key[1:].isdigit()]
        if not members:
            continue
        spread = lambda get: f"{min(map(get, members))} to {max(map(get, members))}"
        rows.append(
            [
                letter,
                label,
                len(members),
                spread(lambda run: run["disposition"]["accuracy"]["numerator"]),
                spread(lambda run: run["disposition"]["false_pass"]["numerator"]),
                spread(lambda run: run["disposition"]["false_flag"]["numerator"]),
                spread(lambda run: run["rules"]["diagnostic"]["fp"]),
                spread(lambda run: run["rules"]["fn_by_kind"]["silent"]),
                spread(lambda run: run["disposition"]["abstention"]["numerator"]),
                spread(lambda run: run["disposition"]["decided"]["numerator"]),
            ]
        )
    return _table(["", "Made-up input", "Seeds", "Dispositions right, of 32", "False pass, of 23", "False flag, of 14", "Rule FP, excluding EXC-03", "Silent misses", "Abstained", "Decided"], rows)


def _proof_section(runs: Mapping[str, Mapping], smoke: Mapping[str, Mapping]) -> str:
    any_on_real = sum(run["proofs"]["receipts"]["numerator"] for run in runs.values())
    parts = [
        f"On the real dataset, across all {len(runs)} scenarios: **{any_on_real} proofs**. This dataset cannot exercise the path. With the right categories, the only receipts with unresolved gift recipients are RCP-013 and RCP-018, which flag anyway on GIFT-02 and MCC-01, and RCP-022, whose gift basket has no client reading over the cap. So zero says nothing about how often a real classifier will trigger a proof.",
        "",
        "To exercise it, a six-receipt dataset was written by hand in `evaluation/synthetic/proof_smoke.jsonl`, with answers worked out from the policy for each true recipient. The input gives categories and no recipient types.",
        "",
    ]
    for key, run in smoke.items():
        proofs = run["proofs"]
        parts += [
            _table(
                ["Measure", "Result"],
                [
                    ["Receipts with a proof", _pct(proofs["receipts"])],
                    ["Readings run", proofs["readings_total"]],
                    ["Flags with no active exception", proofs["flag_without_active_exception"]],
                    ["Proof flags the answers also flag", _pct(proofs["correct_flag"])],
                    ["Proof flags the answers do not flag", _pct(proofs["false_flag"])],
                    ["Proofs naming the answer's rule", _pct(proofs["gold_rule_in_proof"])],
                    ["Proofs where a critical rule is possible", _pct(proofs["possible_critical"])],
                    ["Of those, truly critical", proofs["actually_critical"]],
                    ["Receipts where a critical rule is possible", ", ".join(proofs["possible_critical_receipts"]) or "none"],
                ],
            ),
            "",
            "Disposition matrix for this dataset:",
            "",
            _matrix(run),
            "",
        ]
    parts += [
        "Without proofs, those three flags would be reviews. The two flags the matrix shows as reviews are SYN-005, a $40 gift truly for an official, which has a compliant client reading, and SYN-006, whose category is not trusted. Neither can be proved, so each goes to a person, not to a pass. A proof is not routing: where a critical rule is only possible, no active critical exception exists, so a consumer reading only active exceptions would not send it to Compliance.",
    ]
    return "\n".join(parts)


def markdown_report(document: Mapping, runs: Mapping[str, Mapping], smoke: Mapping[str, Mapping], recommendations: str, limitations: str) -> str:
    inputs = document["inputs"]
    measured = document["real_classifier_measured"]
    comparison = document["baseline_comparison"]
    matches = sum(1 for item in comparison.values() if item["reproduced"])
    parts = [
        "# Offline classification evaluation",
        "",
        "Written for: reviewers of this project, and whoever integrates the classifier.",
        "",
        "Generated by `python -m expense_validator.offline_eval`. Do not edit by hand.",
        "",
        "## Status",
        "",
        "Results from actual classifier predictions are included below." if measured else UNMEASURED,
        "",
        f"The harness reproduces the frozen Module 10.6 benchmarks, with the figures later engine corrections changed (`evaluation/engine_corrections.yaml`): {matches} of {len(comparison)} scenarios match on every recorded figure.",
        "",
        "## Provenance",
        "",
        _table(["Input", "Identity"], [[name, f"`{value}`"] for name, value in sorted(inputs.items())]),
        "",
        "| Kind | Meaning | Reported as classifier performance |\n|---|---|---|\n| actual | Output of a real classifier, from a file naming its model | Yes |\n| oracle | The dataset's answers, plus recipients a developer read by hand | No |\n| synthetic | Made up by a seeded generator or written by hand | No |\n| none | No classification | No |",
        "",
        "## Limitations",
        "",
        limitations,
        "",
        "## All scenarios",
        "",
        "Rule scores are strict: each receipt with an EXC-03 signal counts as one false positive, as `data/README.md` says, and a low strict precision here mostly reflects that. The rule scores and rates are per scenario, and `metrics.json` holds the 95% intervals. A silent miss is an expected rule that is neither raised, unresolved, open nor in a proof.",
        "",
        _summary(runs),
        "",
        "## Comparison with benchmarks A to F",
        "",
        _table(["", "Reproduced", "Flag / review / pass", "Dispositions right", "Decided", "Rules matched", "Differences"], [
            [key, "Yes" if item["reproduced"] else "**No**", f"{item['harness']['flag']} / {item['harness']['review']} / {item['harness']['pass']}", item["harness"]["accuracy"], item["harness"]["decided"], item["harness"].get("matched", f"{runs[key]['rules']['strict']['tp']} (new)"), item["differs"] or "none"]
            for key, item in comparison.items()
        ]),
        "",
        "F's rule figures were never recorded before this harness, so they are new here and have nothing to be compared with.",
        "",
        "## Disposition matrices",
        "",
    ]
    for key in MATRIX_DETAIL:
        if key in runs:
            scores = runs[key]["disposition"]
            parts += [f"### {key}: {runs[key]['source']['description']}", "", _matrix(runs[key]), "", f"False pass {_ci(scores['false_pass'])}. False flag {_ci(scores['false_flag'])}. Abstained {_ci(scores['abstention'])}. Decided {_ci(scores['decided'])}.", ""]
    parts += [
        "## Sensitivity to how classifications behave",
        "",
        "Synthetic. These ask how the engine responds if classifications behave a certain way, and say nothing about any real classifier. H and I have the same lines wrong, so the difference between them is only whether the confidence admits it.",
        "",
        _sensitivity(runs),
        "",
        "## Rule-level scores",
        "",
    ]
    for key in RULE_DETAIL:
        if key in runs:
            run = runs[key]
            parts += [f"### {key}: {run['source']['description']}", "", _rule_table(run), "", f"Micro, strict: {_table_free(run['rules']['strict'])}. Excluding EXC-03: {_table_free(run['rules']['diagnostic'])}. Exact lines: {_pct(run['rules']['evidence_exact'])}. Whole receipt right: {_pct(run['exact_match']['rules_and_lines'])}.", ""]
    parts += ["## Breakdowns", ""]
    parts += [_breakdowns(key, runs[key]) for key in BREAKDOWN_DETAIL if key in runs]
    parts += ["## Inevitable-violation proofs", "", _proof_section(runs, smoke), "", "## Calibration", ""]
    evaluated = {key: run["calibration"] for key, run in runs.items() if run["calibration"]["status"] == "evaluated"}
    if evaluated:
        parts += [_table(["Scenario", "Points", "ECE", "Brier"], [[key, item["points"], _num(item["ece"]), _num(item["brier"])] for key, item in evaluated.items()]), ""]
    else:
        parts += ["Not evaluated. It needs real confidence values, and all here are stand-ins or generated. A score on them would describe the generator. The calculation is in place and tested, and runs as soon as a file of kind `actual` is given. Secondary fields cannot be calibrated even then: they have no confidence and no answers.", ""]
    parts += ["## Recommendations for classifier integration", "", recommendations, ""]
    return "\n".join(parts)


def _table_free(scores: Mapping) -> str:
    return f"TP {scores['tp']}, FP {scores['fp']}, FN {scores['fn']}, P {_num(scores['precision'])}, R {_num(scores['recall'])}, F1 {_num(scores['f1'])}"


def write_all(out: Path, document: Mapping, runs: Mapping[str, Mapping], smoke: Mapping[str, Mapping], report: str) -> None:
    (out / "receipts").mkdir(parents=True, exist_ok=True)
    (out / "metrics.json").write_text(to_json(document))
    (out / "report.md").write_text(report)
    for key, run in {**runs, **smoke}.items():
        rows = "".join(json.dumps(row, sort_keys=True, default=str, ensure_ascii=False) + "\n" for row in run["receipts"])
        (out / "receipts" / f"{key}.jsonl").write_text(rows)
