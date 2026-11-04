# Offline evaluation

Scores classifications through the frozen policy engine. It makes no network call and changes none of the engine's semantics.

```sh
PYTHONPATH=src .venv/bin/python -m expense_validator.offline_eval                         # every built-in scenario
PYTHONPATH=src .venv/bin/python -m expense_validator.offline_eval --predictions run.json  # and real predictions
```

It writes `output/report.md`, `output/metrics.json` and `output/receipts/<scenario>.jsonl`. These hold no times or machine paths, so the same inputs give the same bytes.

**No real classifier has been measured yet.** Scenarios A to I are oracle or synthetic.

## Evaluating a classifier

Write its output to a JSON file, then pass it with `--predictions`. It is then reported as measured, and calibrated.

```json
{
  "customer_id": "northwind",
  "source": {"kind": "actual", "name": "haiku-v1", "description": "First prompt", "model": "the model ID"},
  "predictions": [
    {"line_id": "RCP-014-L4", "category": "alcohol", "restricted": true, "confidence": "0.82"}
  ]
}
```

Each prediction may also hold `recipient_name`, `recipient_type`, `venue_type`, `item_kind` and `essential_toiletry`. Leave out what is not known: a line with no prediction stays visibly unclassified. Never send `labels` or `notes` to the classifier.

## Files here

| File | What it is |
|---|---|
| `oracle_recipients.yaml` | Recipients read by hand from three printed lines. Not gold labels. |
| `baseline_10_6.yaml` | Benchmarks A to F at the Module 10.6 freeze, which the harness must reproduce. |
| `synthetic/proof_smoke.jsonl` | Six hand-written receipts that exercise inevitable-violation proofs. Not real data. |
