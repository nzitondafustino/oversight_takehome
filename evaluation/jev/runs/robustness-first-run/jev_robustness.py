"""Run and evaluate the Jev backend on the robustness set (evaluation/robustness), with the project's own code.

Stages (python jev_robustness.py STAGE):
  preflight  offline but for the key's usage lookup: configuration, request count, cost estimate; seeds translations
  smoke      LIVE: one isolated line
  full       LIVE: the 60 isolated lines and the 12 whole receipts; recorded answers are not paid for again
  evaluate   offline: replay the recordings and score them with expense_validator.evaluation

A live request that fails stops the run: nothing is retried. The questions, snapshot, thresholds and routing are jev.py's.
"""

import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from openrouter import OpenRouter

from expense_validator.classification.backends import jev
from expense_validator.classification.backends.registry import BACKENDS
from expense_validator.classification.core.common import ITEM_KIND_CATEGORIES, TOILETRY_CATEGORY, VENUE_CATEGORY, load_vocabulary
from expense_validator.compiler.llm_extract import cached
from expense_validator.config import ROOT, load_config
from expense_validator.evaluation import metrics as eval_metrics
from expense_validator.evaluation.cli import robustness as cli
from expense_validator.evaluation.classification import line_prediction
from expense_validator.evaluation.dataset import load_dataset
from expense_validator.evaluation.policy_outputs import evaluate_predictions
from expense_validator.evaluation.predictions import PredictionSet, Source
from expense_validator.evaluation.robustness import answer_is_acceptable, line_receipt, score_receipts, score_rows, scored_row, without_placeholders
from expense_validator.exceptions import ClassificationError, TranslationError
from expense_validator.normalization import LlmTranslator, normalize_receipt
from expense_validator.normalization.translation import replay_only

MODEL = jev.MODEL
RUN = ROOT / "evaluation" / "jev" / "runs" / "robustness-first-run"
REPLAY = RUN / "replay"
ANSWERS = REPLAY / BACKENDS["jev"].cache_folder
SMOKE_CASE = "RB-001"
COST_PER_REQUEST = 0.00529 / 79  # observed on the first run
LLM_BASELINES = ("safeguard-rep1", "safeguard-rep2")  # the saved LLM runs with the current prompt and the same set
ATTRIBUTES = ("recipient_type", "venue_type", "item_kind", "essential_toiletry")


class LiveRequestFailed(Exception):
    """Not a ClassificationError, so that jev.py cannot turn it into a line error: the run stops."""


def setup():
    config = load_config()
    vocabulary = load_vocabulary(config.paths.taxonomy, config.paths.rule_fields)
    return config, vocabulary, config.classification.jev_thresholds


def translator(config, missing):
    return LlmTranslator(cached(replay_only(missing, TranslationError), config.translation.model, REPLAY))


def live_transport(config, counter):
    client = OpenRouter(api_key=config.openrouter_api_key.get_secret_value(), timeout_ms=config.llm.timeout_seconds * 1000)
    ask = jev.openrouter_decisions(client, MODEL)

    def complete(questions, state):
        counter["sent"] += 1
        try:
            answer = ask(questions, state)
        except ClassificationError as error:
            raise LiveRequestFailed(str(error)) from None
        counter["succeeded"] += 1
        return answer

    return complete


def classify(transport, config, vocabulary, thresholds, cases, dataset, missing):
    """Classify the lines and receipts through the cache, capturing each line's raw response. Mirrors classify_cases
    and classify_receipts in evaluation/robustness.py, with Jev's classify_receipt in place of the LLM's."""
    answers = cached(transport, BACKENDS["jev"].cache_key(MODEL, vocabulary), ANSWERS)
    raw: list[str] = []

    def capture(questions, state):
        raw.append(answers(questions, state))
        return raw[-1]

    tr = translator(config, missing)
    rows, line_raw = [], {}
    send = without_placeholders(capture)
    for case in cases:
        normalized = normalize_receipt(line_receipt(case["id"], case["input"]), tr)
        raw.clear()
        classified = jev.classify_receipt(normalized, send, MODEL, vocabulary, thresholds)[0]
        line = normalized.lines[0]
        rows.append(scored_row(case, classified, line.translation or line.translation_error))
        line_raw[case["id"]] = (raw[0] if raw else None, classified)
    receipt_lines, receipt_raw = [], {}
    for receipt in dataset.inputs:  # Only the receipt reaches the classifier: the answers stay in dataset.answers.
        normalized = normalize_receipt(receipt, tr)
        raw.clear()
        classified = jev.classify_receipt(normalized, capture, MODEL, vocabulary, thresholds)
        receipt_lines.extend(classified)
        receipt_raw.update({line.line_id: (text, line) for line, text in zip(classified, raw)})
    return rows, line_raw, receipt_lines, receipt_raw


def preflight():
    config, vocabulary, thresholds = setup()
    if (RUN / "metrics.json").exists():
        raise SystemExit(f"{RUN} already holds a run, and a run is never overwritten")
    print("OPENROUTER_API_KEY configured:", config.openrouter_api_key is not None and bool(config.openrouter_api_key.get_secret_value()))
    BACKENDS["jev"].check_model(MODEL)
    print(f"model {MODEL} -> snapshot {jev.SNAPSHOTS[MODEL]} | routing {jev.ROUTING} | retries {jev.NO_RETRY.strategy} | thresholds {thresholds.model_dump()}")
    cases, dataset = cli.load_cases(), load_dataset(cli.RECEIPTS, cli.CUSTOMER)
    requests = len(cases) + sum(len(receipt.line_items) for receipt in dataset.inputs)
    print(f"robustness set: {len(cases)} isolated lines, {len(dataset.inputs)} receipts with {requests - len(cases)} lines -> {requests} requests, about ${requests * COST_PER_REQUEST:.4f}")
    # Seed only the translations these inputs need, from the cache the LLM robustness runs filled.
    model, seeded = config.translation.model, set()

    def copy_recording(prompt, text):
        name = hashlib.sha256("\0".join([model, prompt, text]).encode()).hexdigest() + ".txt"
        source = config.paths.llm_cache / name
        if not source.exists():
            raise TranslationError(f"no recorded translation for {text[:40]!r}")
        REPLAY.mkdir(parents=True, exist_ok=True)
        (REPLAY / name).write_bytes(source.read_bytes())
        seeded.add(name)
        return source.read_text()

    tr = LlmTranslator(copy_recording)
    for case in cases:
        normalize_receipt(line_receipt(case["id"], case["input"]), tr)
    for receipt in dataset.inputs:
        normalize_receipt(receipt, tr)
    print(f"translations seeded: {len(seeded)}")
    spend = cli.key_spend(config)
    print(f"key spend so far: ${spend:.4f} (stop at ${cli.SPENDING_STOP_USD})")
    if spend >= cli.SPENDING_STOP_USD:
        raise SystemExit("at or above the spending stop: not running")


def live(cases, dataset):
    config, vocabulary, thresholds = setup()
    counter, missing = Counter(), []
    before = len(list(ANSWERS.glob("*.txt"))) if ANSWERS.exists() else 0
    try:
        rows, line_raw, receipt_lines, _ = classify(live_transport(config, counter), config, vocabulary, thresholds, cases, dataset, missing)
    except LiveRequestFailed as error:
        raise SystemExit(f"STOPPED: live request failed after {counter['succeeded']} successes: {error}. Not retried.")
    assert not missing, f"translations missing: {len(missing)}"
    print(f"requests sent {counter['sent']}, succeeded {counter['succeeded']}, new recordings {len(list(ANSWERS.glob('*.txt'))) - before}")
    return rows, line_raw, receipt_lines


def smoke():
    case = next(item for item in cli.load_cases() if item["id"] == SMOKE_CASE)
    empty = load_dataset(cli.RECEIPTS, cli.CUSTOMER)
    rows, line_raw, _ = live([case], empty.__class__(empty.customer_id, empty.sha256, (), {}))
    text, classified = line_raw[SMOKE_CASE]
    body = json.loads(text)
    print(f"{SMOKE_CASE} {case['input']['description']!r}: gold {case['gold']['category']} -> {classified.category} p={classified.confidence} error={classified.error}")
    print("answered by", body["model"], "| provider", body.get("provider"), "| questions answered", sorted(body["answers"]), "| usage", body.get("usage"))


def full():
    live(cli.load_cases(), load_dataset(cli.RECEIPTS, cli.CUSTOMER))


def raw_answers(text, vocabulary):
    return jev.parse_response(text, jev.build_questions(vocabulary), MODEL)[1]


def applies(attribute, value, category):
    """What the engine and finalize already read: an attribute is used only with its category."""
    if attribute == "venue_type":
        return category == VENUE_CATEGORY
    if attribute == "item_kind":
        return ITEM_KIND_CATEGORIES.get(value) == category
    if attribute == "essential_toiletry":
        return category == TOILETRY_CATEGORY
    return True


def as_value(attribute, choice):
    if choice == jev.NOT_STATED:
        return None
    return choice == "essential" if attribute == "essential_toiletry" else choice


def line_analysis(case, text, classified, vocabulary, thresholds):
    answers = raw_answers(text, vocabulary)
    category = answers["category"]
    ranked = sorted(category["probabilities"].items(), key=lambda item: -item[1])
    gold = case["gold"]
    acceptable = [gold["category"], *gold.get("acceptable", [])]
    entry = {
        "id": case["id"], "description": case["input"]["description"], "ambiguity": case["ambiguity"], "challenges": case["challenges"],
        "gold_category": gold["category"], "acceptable": acceptable, "jev_category": category["choice"], "final_category": classified.category,
        "selected_p": category["probabilities"][category["choice"]], "jev_confidence": category["confidence"], "top3": ranked[:3],
        "category_right": category["choice"] in acceptable, "error": classified.error, "attributes": {},
    }
    unlabelled = gold.get("unlabelled", [])
    for name in ATTRIBUTES:
        answer = answers[name]
        p = answer["probabilities"][answer["choice"]]
        asserted = answer["choice"] != jev.NOT_STATED and p > getattr(thresholds, name)
        value = as_value(name, answer["choice"])
        item = {"choice": answer["choice"], "p": p, "jev_confidence": answer["confidence"], "not_stated_p": answer["probabilities"][jev.NOT_STATED]}
        if answer["choice"] != jev.NOT_STATED and not asserted:
            item["class"] = "suppressed_by_threshold"
        elif asserted and not applies(name, answer["choice"], category["choice"]):
            # Asserted for an attribute that does not apply to Jev's own category: finalize rejects the line.
            item["class"] = "contradictory" if applies(name, answer["choice"], gold["category"]) else "irrelevant"
        elif name in unlabelled:
            item["class"] = "unlabelled"
        else:
            truth = gold.get(name)
            got = value if asserted else None
            item["class"] = "right" if got == truth else "fabricated" if truth is None else "missing" if got is None else "wrong_value"
        entry["attributes"][name] = item
    return entry


def check_recordings(vocabulary):
    questions, usage, models, providers = jev.build_questions(vocabulary), Counter(), Counter(), Counter()
    for path in sorted(ANSWERS.glob("*.txt")):
        body = json.loads(path.read_text())
        model, answers, used = jev.parse_response(path.read_text(), questions, MODEL)
        assert set(answers) == set(questions)
        for answer in answers.values():
            assert all(math.isfinite(p) and 0 <= p <= 1 for p in answer["probabilities"].values())
        models[model] += 1
        providers[body.get("provider")] += 1
        if isinstance(used, dict) and isinstance(used.get("cost"), (int, float)):
            usage["cost"] += used["cost"]
    return {"recordings": sum(models.values()), "models": dict(models), "providers": dict(providers), "usage": dict(usage)}


def evaluate():
    config, vocabulary, thresholds = setup()
    cases, dataset = cli.load_cases(), load_dataset(cli.RECEIPTS, cli.CUSTOMER)
    ruleset, fields = cli._rules(config)
    missing = []
    rows, line_raw, receipt_lines, receipt_raw = classify(replay_only(missing, ClassificationError), config, vocabulary, thresholds, cases, dataset, missing)
    assert not missing, f"{len(missing)} answers missing from the recordings"

    source = Source(kind="actual", name="jev-robustness-first-run", description=f"Jev decision model, one Decisions request per line; snapshot {jev.SNAPSHOTS[MODEL]}", model=MODEL)
    receipts, receipt_predictions = score_receipts(dataset, receipt_lines, cli.load_receipt_attributes(), ruleset, fields, source)
    policy = evaluate_predictions(dataset, receipt_predictions, ruleset, fields)

    by_case = {case["id"]: case for case in cases}
    analysis = [line_analysis(by_case[case_id], text, classified, vocabulary, thresholds) for case_id, (text, classified) in line_raw.items()]
    receipt_cases = {line.line_id: line for answer in dataset.answers.values() for line in answer.lines}
    labelled = cli.load_receipt_attributes()
    receipt_analysis = [
        line_analysis({"id": line_id, "input": {"description": ""}, "ambiguity": "receipt", "challenges": [], "gold": {"category": receipt_cases[line_id].category, **labelled.get(line_id, {})}}, text, classified, vocabulary, thresholds)
        for line_id, (text, classified) in receipt_raw.items()
    ]

    by_ambiguity = defaultdict(list)
    for entry in analysis:
        by_ambiguity[entry["ambiguity"]].append(entry)
    probability = {
        "selected_p_by_ambiguity": {name: {"lines": len(group), "mean_selected_p": round(sum(e["selected_p"] for e in group) / len(group), 3), "min_selected_p": min(e["selected_p"] for e in group), "category_right": sum(e["category_right"] for e in group)} for name, group in sorted(by_ambiguity.items())},
        "confidently_wrong_p_ge_0_9": [(e["id"], e["gold_category"], e["jev_category"], e["selected_p"]) for e in analysis if not e["category_right"] and e["selected_p"] >= 0.9],
        "wrong": [(e["id"], e["gold_category"], e["jev_category"], e["selected_p"], e["ambiguity"]) for e in analysis if not e["category_right"]],
        "selected_p_below_0_9": [(e["id"], e["jev_category"], e["selected_p"], e["category_right"], e["ambiguity"]) for e in analysis if e["selected_p"] < 0.9],
        "jev_confidence_vs_selected_p": [(e["id"], e["selected_p"], e["jev_confidence"]) for e in analysis if abs(e["selected_p"] - e["jev_confidence"]) > 0.05],
        "line_confidence_buckets": eval_metrics.line_bucket_scores([{"category": r["category"], "gold_category": r["gold_category"], "confidence": r["confidence"]} for r in rows], cli_threshold(ruleset)),
    }
    classes = {scope: {name: dict(Counter(e["attributes"][name]["class"] for e in entries)) for name in ATTRIBUTES} for scope, entries in (("lines", analysis), ("receipts", receipt_analysis))}
    applicability = {
        "classes": classes,
        "rejected_lines": [
            {"id": e["id"], "error": e["error"], "jev_category": e["jev_category"], "category_right": e["category_right"], "selected_p": e["selected_p"],
             "offending": {name: {k: a[k] for k in ("choice", "p", "not_stated_p", "class")} for name, a in e["attributes"].items() if a["class"] in ("irrelevant", "contradictory")}}
            for e in analysis + receipt_analysis if e["error"]
        ],
        "suppressed_by_threshold": [(e["id"], name, a["choice"], a["p"]) for e in analysis + receipt_analysis for name, a in e["attributes"].items() if a["class"] == "suppressed_by_threshold"],
        "attribute_errors": [(e["id"], name, a["class"], a["choice"], a["p"]) for e in analysis + receipt_analysis for name, a in e["attributes"].items() if a["class"] in ("fabricated", "missing", "wrong_value")],
    }
    applicability["correct_categories_discarded"] = [r["id"] for r in applicability["rejected_lines"] if r["category_right"]]

    lines_metrics = score_rows(rows)
    baselines = {}
    for name in LLM_BASELINES:
        saved = json.loads((cli.FOLDER / "runs" / name / "metrics.json").read_text())
        baselines[name] = {"model": saved["model"], "lines": {k: saved["lines"][k] for k in ("scores", "confidence_separation", "attributes", "safety")}, "receipts": {k: saved["receipts"][k] for k in ("disposition", "rules", "exact_match", "classification_accuracy", "attributes", "failed")}}

    RUN.mkdir(parents=True, exist_ok=True)
    write = lambda name, value: (RUN / name).write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n")
    write("metrics.json", {
        "run": RUN.name, "date": date.today().isoformat(), "backend": jev.BACKEND_ID, "requested_model": MODEL, "reviewed_snapshot": jev.SNAPSHOTS[MODEL],
        "provider_routing": jev.ROUTING, "retries": "none", "attribute_thresholds": thresholds.model_dump(),
        "questions_sha256": hashlib.sha256(jev.questions_text(vocabulary).encode()).hexdigest(),
        "lines_sha256": hashlib.sha256(cli.LINES.read_bytes()).hexdigest(), "receipts_sha256": hashlib.sha256(cli.RECEIPTS.read_bytes()).hexdigest(),
        "ruleset_sha256": hashlib.sha256(config.paths.ruleset.read_bytes()).hexdigest(), "translation_model": config.translation.model,
        "recordings": check_recordings(vocabulary),
        "lines": lines_metrics, "receipts": receipts, "probability": probability, "applicability": applicability, "llm_baselines": baselines,
    })
    write("line_rows.json", rows)
    write("line_analysis.json", analysis)
    write("receipt_analysis.json", receipt_analysis)
    write("receipt_policy_run.json", policy)
    (RUN / "receipt_predictions.json").write_text(receipt_predictions.model_dump_json(indent=2, exclude_none=True) + "\n")
    line_source = Source(kind="actual", name="jev-robustness-lines", description="Jev on the isolated robustness lines", model=MODEL)
    (RUN / "line_predictions.json").write_text(PredictionSet(customer_id="robustness-lines", source=line_source, predictions=tuple(line_prediction(c) for _, c in line_raw.values() if c.error is None)).model_dump_json(indent=2, exclude_none=True) + "\n")
    print("evaluated:", sorted(p.name for p in RUN.iterdir()))


def cli_threshold(ruleset):
    from expense_validator.evaluation.policy_outputs import _threshold

    return _threshold(ruleset)


if __name__ == "__main__":
    {"preflight": preflight, "smoke": smoke, "full": full, "evaluate": evaluate}[sys.argv[1]]()
