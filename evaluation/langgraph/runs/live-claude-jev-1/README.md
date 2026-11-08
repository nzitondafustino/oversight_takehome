# Live LangGraph evaluation: Claude and Jev

Written for: reviewers of this project, and whoever decides the next, larger evaluation.

The first live run of LangGraph with real Claude and Jev calls, made on 2026-11-06. It covers five configurations on 13 lines: 7 whole receipts (5 golden, 2 robustness) and 1 isolated robustness line. It is written once and never overwritten.

- **Models:** Claude is `anthropic/claude-haiku-5.5` through OpenRouter chat completions, as classifier, reviewer and reviser. Jev is `typesafe/jev-1.14` through OpenRouter's Decisions API; every answer came from snapshot `-20261014`.
- **Settings:** routing threshold 0.90 with both fallbacks on, reflection with `max_retries: 2` and `max_total_calls_per_line: 6`, all unchanged.
- **Cost:** 40 paid requests and **$0.008694**, as reported by the provider per request and matching the key's spend (`spend.json` was read before that figure caught up). The budget was $0.10. No request failed and none was retried: SDK retries were turned off for the run.

## Economy, and what it means for the comparison

All five configurations share one recording folder, so each unique request was sent once, live, and later configurations reused it. The same Jev and Claude answers therefore flow through every configuration, so the differences below come from routing and reflection, not from a model answering differently. Only the one Spanish translation was reused from the golden replay.

## Results

| | A Jev | B Claude | C Jev→Claude | D Jev→Claude + reflection | E Claude→Jev + reflection |
|---|---|---|---|---|---|
| Category accuracy | 9/13 | **13/13** | 12/13 | **13/13** | 12/13 |
| Lines classified | 11/13 | 13/13 | 13/13 | 13/13 | 13/13 |
| Correct dispositions (7 receipts) | 5/7 | 7/7 | 6/7 | 7/7 | 7/7 |
| Fully decided receipts | 5/7 | 7/7 | 7/7 | 7/7 | 7/7 |
| False flags / false passes / silent misses | 1 / 0 / 1 | 0 / 0 / 0 | 1 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| EXC-03 findings / unresolved findings | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| Open questions | 30 | 0 | 0 | 0 | 0 |
| Logical calls (per line) | 13 (1.0) | 13 (1.0) | 17 (1.3) | 30 (2.3) | 26 (2.0) |
| Cost, run alone (estimated) | $0.0009 | $0.0038 | $0.0020 | $0.0058 | $0.0071 |
| Latency, run alone, 13 lines one after another (estimated) | 2.6 s | 16.0 s | 7.5 s | 23.5 s | 30.0 s |

Every row except the last two is measured. The last two are each configuration's logical calls multiplied by the provider-reported mean cost ($0.000067 per Jev call, $0.00029 per Claude call) and the measured median latency (0.2 s per Jev call, 1.2 s per Claude call). Rule precision and recall were 1.0 / 1.0 for B, D and E. Two cases fell short:

| Configuration | Receipts | Precision / recall |
|---|---|---|
| A | golden | 0.875 / 0.875 |
| A | robustness | 0 / 0 |
| C | robustness | 0.5 / 1.0 |

## Misclassified lines

| Configuration | Line | Gold | Predicted | Why |
|---|---|---|---|---|
| A | RCP-017-L2 Gratuity | service_fee | failed | Jev's line fails the consistency check, as in every earlier run |
| A | RBX-009-L1 Stadium suite tickets | other | failed | Jev's line fails the consistency check |
| A, C | RBX-001-L1 Guinness 0.0 | food_beverage | alcohol, at 0.95 | a confident error, so no fallback: a false ALC-01 flag |
| A, E | RB-002 Seedlip and tonic | food_beverage | alcohol, at 0.83 | in E, Claude's right 0.7 answer went on to Jev, whose wrong 0.83 replaced it by order |

## Reflection (configuration D)

| Question | Count |
|---|---|
| Jev classifications reviewed | 9 |
| Accepted / revise / uncertain | 8 / 1 / 0 |
| Rejections corrected | 1 of 1: Guinness 0.0, revised by Claude to food_beverage at 0.9 and then accepted |
| Correct classifications wrongly challenged | 0 |
| Revisions that improved / worsened accuracy | 1 / 0 |
| Uncertainty resolved by fallback | 0, as no review was uncertain |
| EXC-03 findings added | 0 |
| Extra logical calls over C | 13: 12 reviews and 1 revision |
| Extra cost over C, estimated | about $0.004 |
| Extra latency over C, estimated | about 16 s |

In D, reflection turned C's one wrong line (Guinness 0.0) and its one wrong disposition (RBX-001's false flag) into right ones. Lines below 0.90 go on by routing and are not reviewed, so D's reflection could not change RB-002 or RCP-023.

In E, Claude reviewed its own answers and accepted all 11; that is self-review, and it changed nothing. E's one error comes from routing: both answers for RB-002 were below 0.90, and order kept the later one, Jev's.

## Replay

Every configuration replays from `replay/`, with the network blocked and the OpenRouter client unbuildable. Each matches the live run: classifications, attempts, reviews, revisions, findings and dispositions. It makes no provider call, and `missing_recordings` is empty. The golden fixtures are unchanged.

## Limits

- **Small sample:** 13 lines and 7 receipts. One line is a whole percentage point several times over, and the lines were chosen for being hard.
- **Little ground truth:** only categories and dispositions have gold here. The secondary attributes of the isolated line are not scored.
- **One answer per request:** the comparison has no run-to-run variation.
- **Claude is not pinned to a snapshot,** unlike Jev.

## Files

| Path | What it holds |
|---|---|
| `replay/` | Every recording: `jev/` holds the Decisions responses; `reflection/` and `revision/` hold Claude's reviews and revisions; the folder root holds Claude's classifications and the one translation |
| `live/<A-E>/results.json` | Each configuration's validator results, with the full routing audit per line |
| `usage.jsonl` | Each paid request's kind, model, tokens, provider-reported cost and latency, with no prompt or response |
| `spend.json` | The key's usage before and after the run, and the guard's conservative estimate |
| `metrics.json` | Scores per configuration and line, policy outcomes, and the run-alone estimates |
| `live_eval.py` | The driver: `preflight`, `dry-run` (no network), `live` (paid, guarded), `evaluate` and `verify` (offline) |
