"""Run and evaluate the Jev backend on the 32 labelled receipts, with the project's own code.

Stages (python jev_run.py STAGE):
  preflight  offline: check the configuration, and seed the run's replay folder with the translations it needs
  smoke      LIVE: one receipt through ExpenseValidator with the jev backend; checks every recorded answer
  full       LIVE: all 32 receipts; answers already recorded (the smoke receipt) are not paid for again
  evaluate   offline: replay the recordings, rebuild the predictions, score them with expense_validator.evaluation

Live stages send one paid Decisions request per line not yet recorded, and never retry a failed one.
"""

import hashlib
import inspect
import json
import math
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from expense_validator import ExpenseValidator
from expense_validator.classification.backends import jev
from expense_validator.classification.backends.registry import BACKENDS
from expense_validator.classification.core.common import load_vocabulary
from expense_validator.compiler.llm_extract import cached
from expense_validator.config import ROOT, load_config
from expense_validator.evaluation.classification import classification_metrics, line_prediction
from expense_validator.evaluation.cli import policy as policy_cli
from expense_validator.evaluation.cli.robustness import SPENDING_STOP_USD, key_spend
from expense_validator.evaluation.dataset import load_dataset
from expense_validator.evaluation.predictions import PredictionSet, Source
from expense_validator.evaluation.robustness import confusion_matrix
from expense_validator.exceptions import ClassificationError, TranslationError
from expense_validator.models import ValidatorSettings
from expense_validator.normalization import LlmTranslator, normalize_receipt
from expense_validator.normalization.translation import PROMPT as TRANSLATION_PROMPT, replay_only
from expense_validator.run_receipts import load_receipts, write_jsonl

CONFIG_PATH = ROOT / "config.yaml"
CUSTOMER = "northwind"
DATA = ROOT / "data" / "receipts.jsonl"
MODEL = jev.MODEL  # typesafe/jev-1.14, answered by the reviewed snapshot in jev.SNAPSHOTS
RUN = ROOT / "evaluation" / "jev" / "runs" / "first-run"
REPLAY = RUN / "replay"
GOLDEN_REPLAY = ROOT / "evaluation" / "final" / "replay"
LLM_PREDICTIONS = ROOT / "evaluation" / "classification" / "predictions.json"
SMOKE_RECEIPT = "RCP-022"


def settings(mode: str) -> ValidatorSettings:
    return ValidatorSettings.from_config(CONFIG_PATH, mode=mode, cache_dir=REPLAY, classification_backend="jev", classification_model=MODEL)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def preflight() -> None:
    config = load_config()
    if (RUN / "receipts.jsonl").exists():
        raise SystemExit(f"{RUN} already holds a run, and a run is never overwritten")
    print("OPENROUTER_API_KEY configured:", config.openrouter_api_key is not None and bool(config.openrouter_api_key.get_secret_value()))
    BACKENDS["jev"].check_model(MODEL)
    print(f"model {MODEL}: a Jev decision model; answered only by snapshot {jev.SNAPSHOTS[MODEL]}")
    print("transport: client.alpha.decisions.create (Decisions API):", "client.alpha.decisions.create(" in inspect.getsource(jev.openrouter_decisions), "| chat completions in jev.py:", "chat.send" in inspect.getsource(jev))
    print("provider routing:", jev.ROUTING, "| retries:", jev.NO_RETRY.strategy)
    print("registry jev.connect is jev's own:", BACKENDS["jev"].connect is jev._connect, "| thresholds:", config.classification.jev_thresholds.model_dump())
    dataset = load_dataset(DATA, CUSTOMER)
    lines = sum(len(answer.lines) for answer in dataset.answers.values())
    print(f"dataset: {len(dataset.inputs)} receipts, {lines} lines")
    assert (len(dataset.inputs), lines) == (32, 79)

    # Seed only the translations these receipts need, from the golden replay: the Jev run then calls no translator.
    model = config.translation.model
    seeded = []

    def copy_recording(prompt: str, text: str) -> str:
        name = hashlib.sha256("\0".join([model, prompt, text]).encode()).hexdigest() + ".txt"
        source = GOLDEN_REPLAY / name
        if not source.exists():
            raise TranslationError(f"no recorded translation for {text[:40]!r}")
        REPLAY.mkdir(parents=True, exist_ok=True)
        (REPLAY / name).write_bytes(source.read_bytes())
        seeded.append(name)
        return source.read_text()

    translator = LlmTranslator(copy_recording)
    status = Counter(line.translation_status for receipt in dataset.inputs for line in normalize_receipt(receipt, translator).lines)
    print(f"translations seeded: {len(set(seeded))} | line translation status: {dict(status)}")
    if spend := key_spend(config):
        print(f"key spend so far: ${spend:.4f} (stop at ${SPENDING_STOP_USD})")
    if spend >= SPENDING_STOP_USD:
        raise SystemExit("at or above the spending stop: not running")


def check_recordings() -> dict:
    """Check every recorded Jev answer the way the project parses it, and total the usage."""
    vocabulary = load_vocabulary(load_config().paths.taxonomy, load_config().paths.rule_fields)
    questions = jev.build_questions(vocabulary)
    totals = Counter()
    models = Counter()
    for path in sorted((REPLAY / "jev").glob("*.txt")):
        body = json.loads(path.read_text())
        model, answers, usage = jev.parse_response(path.read_text(), questions, MODEL)  # raises on any contract break
        assert set(answers) == set(questions) and set(body["answers"]) == set(questions), path.name
        for name, answer in answers.items():
            assert answer["choice"] in questions[name]["criteria"]
            assert all(0 <= p <= 1 and math.isfinite(p) for p in answer["probabilities"].values())
            assert abs(sum(answer["probabilities"].values()) - 1) <= jev.PROBABILITY_SUM_TOLERANCE
        models[model] += 1
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cost"):
            if isinstance(usage, dict) and isinstance(usage.get(key), (int, float)):
                totals[key] += usage[key]
    return {"recordings": sum(models.values()), "models": dict(models), "usage": dict(totals), "provider": sorted({json.loads(p.read_text()).get("provider") for p in (REPLAY / "jev").glob("*.txt")}, key=str)}


def live(receipts, label: str):
    validator = ExpenseValidator(settings("live"))
    before = len(list((REPLAY / "jev").glob("*.txt"))) if (REPLAY / "jev").exists() else 0
    batch = validator.validate_batch(CUSTOMER, receipts, context_complete=len(receipts) == 32)
    after = len(list((REPLAY / "jev").glob("*.txt")))
    lines = [line for result in batch.results for line in result.line_items]
    failed = [(line.line_id, line.classification_error) for line in lines if line.classification_error]
    print(f"{label}: {len(batch.results)} receipts, {len(lines)} lines, new recordings {after - before}, failed lines {len(failed)}")
    for line_id, error in failed:
        print("   FAILED", line_id, error)
    return batch, failed


def smoke() -> None:
    receipt = next(item for item in load_receipts() if item.receipt_id == SMOKE_RECEIPT)
    batch, failed = live([receipt], "smoke")
    for line in batch.results[0].line_items:
        details = line.classification_details or {}
        print(f"   {line.line_id} {line.description_raw[:50]!r}: category={line.category} restricted={line.restricted} confidence={line.confidence} attributes={line.attributes}")
        print(f"      backend={details.get('backend')} requested={details.get('requested_model')} answered_by={details.get('model')} questions={sorted(details.get('answers', {}))}")
        for name, answer in details.get("answers", {}).items():
            print(f"      {name}: {answer['choice']} p={answer['probabilities'][answer['choice']]} jev_confidence={answer['confidence']}")
    print("disposition:", batch.results[0].disposition, [e.rule_id for e in batch.results[0].exceptions], [s.rule_id for s in batch.results[0].review_signals])
    print("recordings:", json.dumps(check_recordings()))
    # The same receipt again, offline: answered from the recordings alone.
    again = ExpenseValidator(settings("replay")).validate_batch(CUSTOMER, [receipt], context_complete=False)
    print("replay matches live:", again.results[0].to_record() == batch.results[0].to_record())
    if failed:
        raise SystemExit("smoke test failed: not continuing")


def full() -> None:
    batch, failed = live(load_receipts(), "full")
    write_jsonl([result.to_record() for result in batch.results], RUN / "receipts.jsonl")
    details = {line.line_id: {"classification_error": line.classification_error, **(line.classification_details or {})} for result in batch.results for line in result.line_items}
    (RUN / "line_details.json").write_text(json.dumps(details, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    print("dispositions:", Counter(result.disposition for result in batch.results))


def evaluate() -> None:
    config = load_config()
    vocabulary = load_vocabulary(config.paths.taxonomy, config.paths.rule_fields)
    dataset = load_dataset(DATA, CUSTOMER)
    thresholds = config.classification.jev_thresholds

    # Offline: the translator and Jev answer only from the run's recordings.
    missing: list[str] = []
    translator = LlmTranslator(cached(replay_only(missing, TranslationError), config.translation.model, REPLAY))
    complete = cached(replay_only(missing, ClassificationError), BACKENDS["jev"].cache_key(MODEL, vocabulary), REPLAY / BACKENDS["jev"].cache_folder)
    classified = []
    for receipt in dataset.inputs:  # Only the receipt reaches the classifier: the answers stay in dataset.answers.
        normalized = normalize_receipt(receipt, translator)
        classified.append((normalized, jev.classify_receipt(normalized, complete, MODEL, vocabulary, thresholds)))
    assert not missing, f"{len(missing)} answers missing from the recordings"

    # The replayed classification must equal the live run's, line for line.
    live_lines = {line["line_id"]: line for record in map(json.loads, (RUN / "receipts.jsonl").read_text().splitlines()) for line in record["line_items"]}
    for _, lines in classified:
        for line in lines:
            recorded = live_lines[line.line_id]
            assert (line.category, line.restricted, None if line.confidence is None else float(line.confidence)) == (recorded["category"], recorded["restricted"], recorded["confidence"]), line.line_id

    metrics = classification_metrics(dataset, classified, model=MODEL, translation_model=config.translation.model)
    rows = []
    for normalized, lines in classified:
        answers = {answer.line_id: answer for answer in dataset.answers[normalized.receipt_id].lines}
        rows += [{"line_id": line.line_id, "gold_category": answers[line.line_id].category, "category": line.category, "error": line.error} for line in lines]
    metrics["confusion_matrix"] = confusion_matrix(rows)
    metrics["recordings"] = check_recordings()
    (RUN / "metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False, default=str) + "\n")

    source = Source(kind="actual", name="jev-first-run", description=f"Jev decision model, one Decisions request per line, through OpenRouter on TypeSafe; snapshot {jev.SNAPSHOTS[MODEL]}", model=MODEL)
    predictions = PredictionSet(customer_id=CUSTOMER, source=source, predictions=tuple(line_prediction(line) for _, lines in classified for line in lines if line.error is None))
    (RUN / "predictions.json").write_text(predictions.model_dump_json(indent=2, exclude_none=True) + "\n")

    metadata = {
        "run": "first-run",
        "date": date.today().isoformat(),
        "backend": jev.BACKEND_ID,
        "requested_model": MODEL,
        "reviewed_snapshot": jev.SNAPSHOTS[MODEL],
        "provider_routing": jev.ROUTING,
        "retries": "none",
        "attribute_thresholds": thresholds.model_dump(),
        "questions_sha256": hashlib.sha256(jev.questions_text(vocabulary).encode()).hexdigest(),
        "dataset_sha256": dataset.sha256,
        "ruleset_sha256": sha(config.paths.ruleset),
        "taxonomy_sha256": sha(config.paths.taxonomy),
        "fields_sha256": sha(config.paths.rule_fields),
        "translation_model": config.translation.model,
        "receipts": len(dataset.inputs),
        "lines": sum(len(lines) for _, lines in classified),
    }
    (RUN / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")

    # Policy outputs, through the existing harness: oracle and synthetic scenarios, the saved LLM predictions and Jev's.
    policy_cli.main(["--predictions", str(RUN / "predictions.json"), "--predictions", str(LLM_PREDICTIONS), "--out", str(RUN / "policy")])
    print("evaluated: wrote metrics.json, predictions.json, metadata.json and policy/")


if __name__ == "__main__":
    {"preflight": preflight, "smoke": smoke, "full": full, "evaluate": evaluate}[sys.argv[1]]()
