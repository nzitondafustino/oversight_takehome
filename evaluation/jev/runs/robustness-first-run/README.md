# Jev on the robustness set

Written for: reviewers of this project, and whoever decides how Jev's attributes are used.

The first live run of the Jev backend on the developer-authored robustness set in `evaluation/robustness`, made on 2026-11-06. It covers 60 isolated lines and 12 whole receipts with 17 lines. It is written once and never overwritten.

- **Model:** `typesafe/jev-1.14` through OpenRouter's Decisions API, answered on every request by the reviewed snapshot `typesafe/jev-1.14-20261014`.
- **Routing:** TypeSafe only, no fallbacks, no retries.
- **Settings:** the questions and thresholds are unchanged (0.5 on each attribute), as are the taxonomy, the fields and the production ruleset.
- **Translations:** replayed from the cache the LLM robustness runs filled. This run made no translation call.
- **Requests:** 77, all successful, for $0.00509 as reported by the provider.

The set was written by the same developer who wrote the labels, and it is small. Its LLM baselines, `safeguard-rep1` and `safeguard-rep2`, are the saved runs in `evaluation/robustness/runs` that used the current prompt on the same files. No LLM call was made for this run.

## Files

| Path | What it holds |
|---|---|
| `replay/jev/` | The 77 raw Decisions responses: answers, probabilities and usage |
| `replay/*.txt` | The 16 translations these inputs need |
| `metrics.json` | `score_rows` and `score_receipts` from `expense_validator.evaluation.robustness`, with the probability and attribute-applicability analysis and the LLM baselines' saved figures |
| `line_rows.json`, `line_analysis.json` | Each isolated line's scored row, and Jev's full category and attribute distributions for it |
| `receipt_analysis.json`, `receipt_policy_run.json` | The same for the whole receipts, and their complete policy-engine run (`evaluate_predictions`) |
| `line_predictions.json`, `receipt_predictions.json` | The classifications, of kind `actual` |
| `counterfactual_option_b.json` | Analysis only: what dropping an inapplicable attribute would have done to RBX-009 and to the benchmark's RCP-017. Nothing was changed |
| `jev_robustness.py` | The driver: `preflight`, `smoke` and `full` (live), and `evaluate` (offline, reproducing every file but the counterfactual) |

## Results

| | Jev | LLM `safeguard-rep1` / `-rep2` |
|---|---|---|
| Lines: strict category accuracy | 52/60 | 59/60 / 58/60 |
| Lines: acceptable answers | 54/60 | 60/60 / 59/60 |
| Lines: failed (consistency check) | 2 | 0 / 0 |
| Lines: ECE / Brier | 0.076 / 0.063 | 0.257 / 0.126; 0.220 / 0.127 |
| Receipts: dispositions right | 10/12 | 12/12 / 12/12 |
| Receipts: false flags / false passes | 1 / 0 | 0 / 0 |
| Receipts: silent misses | 1 (ENT-01) | 0 |

**Category errors:**

| Line | Description | Gold | Jev | p |
|---|---|---|---|---|
| RB-001 | Guinness 0.0 | food_beverage | alcohol | 0.98 |
| RB-002 | Seedlip and tonic | food_beverage | alcohol | 0.83 |
| RB-011 | 梅酒 (umeshu) | alcohol | household_goods | 0.50 |
| RB-019 | Apple Pencil | personal_electronics | computer_accessory | 0.52 |
| RB-046 | Samsung Galaxy | other | personal_electronics | 0.91 |
| RB-047 | Opera tickets | other | airfare | 0.74 |
| RBX-009-L1 | Stadium suite tickets | other | airfare | 0.92 |

Jev's attributes were right on every labelled attribute that reached the engine. The one "fabricated" recipient type, on RB-056, is `finalize`'s `unidentified` sentinel, which the LLM runs share.

All three lines the consistency check rejected had a **wrong category and a right attribute**: `item_kind: mobile_phone`, `venue_type: theatre` and `venue_type: sporting_event`. On this set the check discarded no correct category.
