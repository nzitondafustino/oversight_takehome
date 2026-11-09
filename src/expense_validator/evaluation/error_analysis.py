"""Observed failures of one evaluation, each attributed to a stage: translation, classification, retrieval or rule logic.

Attribution follows fixed rules, so it is reproducible, and each failure says which rule placed it, for a person to
confirm (it is a diagnosis aid, not a proof):

1. A line whose category is wrong or missing, or that the classifier failed on, is a root cause.
   - translation: the description is not English and no English translation was made, so the classifier read text
     it was not given in English.
   - classification: anything else, including a translated line (its translation is shown, to check by hand).
2. A line the engine was uncertain about (EXC-03: a low confidence, or a secondary field such as the recipient type
   it could not settle), with its category right, is a root cause in classification: rules needing that field
   were left open.
3. A missed or unexpected finding, an uncertainty signal, or a wrong disposition that a root cause explains is listed
   under it, not as a failure of its own: one on the root's lines, or, citing no line (a receipt-level finding, an
   uncertainty signal, a disposition), on the same receipt as the root.
4. Otherwise, a missed or unexpected finding is rule logic: every line involved was read as the answers say, with
   nothing left uncertain, and the rule still did not decide as expected.
5. Otherwise, a wrong disposition is rule logic.
A receipt that could not be evaluated is not diagnosed: no stage ran on it, and the operational metrics report it.
Retrieval is attributed only when the engine retrieved policy text at run time. This one does not (its metadata
says retriever "none": rules cite compiled clauses), so no failure is placed there; the report says why.
"""

from collections.abc import Mapping, Sequence

STAGES = ("translation", "classification", "retrieval", "rule_logic")


def _line_failure(receipt: Mapping, line: Mapping) -> dict:
    untranslated = line.get("language") not in (None, "en") and not line.get("translation")
    stage = "translation" if untranslated else "classification"
    actual = "no category" if line["category"] is None else line["category"]
    if stage == "translation":
        description = f"The {line['language']} description was not translated, and was classified as {actual} instead of {line['gold_category']}."
        recommendation = f"Translate {line['language']} lines before classifying them, and check why no translation was made here."
        placed_by = "rule 1: not English, and no translation"
    else:
        read = f" (read from its English translation “{line['translation']}”)" if line.get("translation") else ""
        description = (f"The classifier failed on this line ({line['error']}), so it has no category; expected {line['gold_category']}." if line.get("error")
                       else f"Classified as {actual} instead of {line['gold_category']}{read}.")
        recommendation = f"Add “{line['description']}” as {line['gold_category']} to the classifier's guidance or examples, and to the robustness set" + (", after checking its translation." if line.get("translation") else ".")
        placed_by = "rule 1: a wrong or missing category" + ("; translated, so check the translation by hand" if line.get("translation") else "")
    return {
        "receipt_id": receipt["receipt_id"],
        "line_ids": [line["line_id"]],
        "rule_id": None,
        "expected": {"category": line["gold_category"], "restricted": line["gold_restricted"]},
        "actual": {"category": line["category"], "restricted": line["restricted"], "confidence": line.get("confidence")},
        "description": description,
        "stage": stage,
        "evidence": {"text": line["description"], "language": line.get("language"), "translation": line.get("translation")},
        "consequences": [],
        "recommendation": recommendation,
        "placed_by": placed_by,
    }


def _uncertain(receipt: Mapping, line: Mapping, items: list[Mapping]) -> dict:
    fields = ", ".join(f"{item['field']} ({item['reason']})" for item in items)
    return {
        "receipt_id": receipt["receipt_id"],
        "line_ids": [line["line_id"]],
        "rule_id": None,
        "expected": {"category": line["gold_category"]},
        "actual": {"category": line["category"], "uncertain": [f"{item['field']}: {item['reason']}" for item in items]},
        "description": f"The category is right, but the engine could not rely on {fields} for this line, so rules that need it were left open.",
        "stage": "classification",
        "evidence": {"text": line["description"], "uncertainty": [dict(item) for item in items]},
        "consequences": [],
        "recommendation": f"Improve how the classifier reads {', '.join(sorted({item['field'] for item in items}))} on lines like “{line['description']}”, or add it to the classifier's guidance.",
        "placed_by": "rule 2: an uncertain classified field",
    }


def diagnose(receipts: Sequence[Mapping]) -> dict:
    """Every observed failure in an evaluation, grouped by root cause.

    Args:
        receipts: Each {receipt_id, evaluated, lines, missing, unexpected, expected_disposition, disposition,
            uncertain, uncertainty}. A line is {line_id, description, language, translation, gold_category,
            gold_restricted, category, restricted, confidence, error}; missing and unexpected are this receipt's
            entries from detection_scores; uncertainty is its EXC-03 items, each {line_id, field, reason}.
    """
    failures = []
    for receipt in receipts:
        if not receipt["evaluated"]:
            continue  # an execution failure, which the operational metrics report: no stage of the engine ran
        roots = {line["line_id"]: _line_failure(receipt, line) for line in receipt["lines"] if line["category"] != line["gold_category"] or line.get("error")}
        uncertain: dict[str, list] = {}
        for item in receipt.get("uncertainty", ()):
            uncertain.setdefault(item["line_id"], []).append(item)
        for line in receipt["lines"]:
            if line["line_id"] in uncertain and line["line_id"] not in roots:
                roots[line["line_id"]] = _uncertain(receipt, line, uncertain[line["line_id"]])
        ordered = list(roots.values())

        def root_of(line_ids):
            if line_ids:
                return next((roots[line_id] for line_id in line_ids if line_id in roots), None)
            return ordered[0] if ordered else None  # citing no line: anything wrong on the receipt explains it

        rule_failures = []
        for kind, items in (("missed", receipt["missing"]), ("unexpected", receipt["unexpected"])):
            for item in items:
                line_ids = item.get("expected_line_ids") if kind == "missed" else item.get("line_ids")
                root = root_of(line_ids)
                consequence = f"{item['rule_id']} {'was not raised' if kind == 'missed' else 'was raised but not expected'}" + (" (duplicate)" if item.get("duplicate") else "")
                if root is not None:
                    root["consequences"].append(consequence)
                    continue
                rule_failures.append({
                    "receipt_id": receipt["receipt_id"],
                    "line_ids": line_ids or [],
                    "rule_id": item["rule_id"],
                    "expected": {"finding": item["rule_id"] if kind == "missed" else None},
                    "actual": {"finding": None if kind == "missed" else item["rule_id"]},
                    "description": f"{consequence}, although every line involved was classified as the answers say, with nothing left uncertain.",
                    "stage": "rule_logic",
                    "evidence": {"lines": line_ids or [], "kind": item["kind"]},
                    "consequences": [],
                    "recommendation": f"Check {item['rule_id']}'s conditions and parameters against this receipt, and add it as an engine test case.",
                    "placed_by": "rule 4: a finding wrong with every line read right",
                })
        found = ordered + rule_failures
        if receipt["expected_disposition"] is not None and receipt["disposition"] != receipt["expected_disposition"]:
            consequence = f"disposition {receipt['disposition']} instead of {receipt['expected_disposition']}"
            if found:
                found[0]["consequences"].append(consequence)
            else:
                found.append({
                    "receipt_id": receipt["receipt_id"], "line_ids": [], "rule_id": None,
                    "expected": {"disposition": receipt["expected_disposition"]}, "actual": {"disposition": receipt["disposition"]},
                    "description": f"The disposition was {receipt['disposition']} instead of {receipt['expected_disposition']}, with every line and finding as the answers say.",
                    "stage": "rule_logic", "evidence": {}, "consequences": [],
                    "recommendation": "Check the disposition directive (EXC-01) against this receipt's findings.",
                    "placed_by": "rule 5: a wrong disposition with nothing else wrong",
                })
        failures.extend(found)
    for number, failure in enumerate(failures, start=1):
        failure["id"] = f"F{number}"
    return {
        "observed": len(failures),
        "by_stage": {stage: sum(1 for failure in failures if failure["stage"] == stage) for stage in STAGES},
        "failures": failures,
        "retrieval": "not attributed: the engine retrieves no policy text at run time (retriever none); each rule cites its compiled clause",
        "method": "automatic, by the rules in expense_validator.evaluation.error_analysis; each failure says which rule placed it, to confirm by hand",
    }
