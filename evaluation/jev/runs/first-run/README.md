# Jev first run

Written for: reviewers of this project, and whoever decides how Jev is used.

The first live run of the Jev backend on the 32 labelled receipts (79 lines) in `data/receipts.jsonl`, made on 2026-11-06. It is written once and never overwritten: a new run gets a new folder under `runs/`.

- **Model:** `typesafe/jev-1.14` through OpenRouter's Decisions API, answered on every line by the reviewed snapshot `typesafe/jev-1.14-20261014`.
- **Routing:** `provider.only = ["typesafe"]`, no fallbacks, no retries.
- **Settings:** Jev's questions and thresholds are unchanged (0.5 on each attribute), as are the taxonomy, the fields and the production ruleset. Their hashes are in `metadata.json`.
- **Translations:** replayed from `evaluation/final/replay`, so this run made no translation call.

## Files

| Path | What it holds |
|---|---|
| `replay/jev/` | 79 raw Decisions responses, one per line, as the validator recorded them (the SDK's JSON, with usage and cost) |
| `replay/*.txt` | The 11 translations the receipts need, copied from `evaluation/final/replay` |
| `receipts.jsonl` | The validator's per-receipt output, in the same format as `evaluation/final/receipts.jsonl` |
| `line_details.json` | Each line's `classification_details`: Jev's answers, probabilities, the snapshot and usage. A failed line keeps only its error |
| `predictions.json` | The classifications, of kind `actual`, rebuilt from the recordings alone |
| `metrics.json` | Classification scores from `expense_validator.evaluation.classification`, with a confusion matrix and a recording check |
| `policy/` | The policy-output harness (`python -m expense_validator.offline_eval`) on these predictions and on the saved LLM predictions (`evaluation/classification/predictions.json`), with the oracle and synthetic scenarios |
| `metadata.json` | Model, snapshot, routing, thresholds and input hashes |
| `jev_run.py` | The driver that made all of this, with the project's own code: `preflight`, `smoke` and `full` (live), and `evaluate` (offline) |

## Reproduce offline

```
PYTHONPATH=src .venv/bin/python -m expense_validator.run_receipts --replay evaluation/jev/runs/first-run/replay --classification-backend jev --classification-model typesafe/jev-1.14 --out /tmp/jev.jsonl
PYTHONPATH=src .venv/bin/python -m expense_validator.offline_eval --predictions evaluation/jev/runs/first-run/predictions.json --predictions evaluation/classification/predictions.json --out /tmp/policy
```

The first command matches `receipts.jsonl` in everything except `metadata.replayed` and `metadata.latency_ms`. The second matches `policy/` byte for byte.

## Results

All 79 requests succeeded. One line failed the shared consistency check: RCP-017-L2, "Gratuity" on a massage receipt. Jev chose `service_fee` (p 0.98), but also answered `essential_toiletry: not_essential` at p 0.63, above the 0.5 threshold, for an item that is not personal care.

| | Jev | LLM (saved predictions) |
|---|---|---|
| Category accuracy | 78/79 | 79/79 |
| Failed lines | 1 | 0 |
| Macro F1 / restricted F1 | 0.9975 / 1.0 | 1.0 / 1.0 |
| Calibration ECE / Brier (78 and 79 points) | 0.0083 / 0.0008 | 0.1152 / 0.0222 |
| Dispositions right | 32/32 | 32/32 |
| Decided | 30/32 | 31/32 |
| False pass / false flag / silent misses | 0 / 0 / 0 | 0 / 0 / 0 |
| Rules, strict P / R | 0.947 / 0.973 | 0.974 / 1.0 |

RCP-017 still flags correctly, on MCC-01 and PERS-01. Because the gratuity has no category, though, DOC-03 is open instead of raised, and EXC-03 adds a review signal.

The six attributes Jev read are the same as the LLM's: two recipients, one essential-toiletry answer, and two OTC medications. The answers hold no attribute labels, so attribute accuracy is not measured. These 32 receipts are not held out: they were to hand when the questions were written. The calibration figures rest on 78 points, almost all at confidence 0.99 or 1.0.
