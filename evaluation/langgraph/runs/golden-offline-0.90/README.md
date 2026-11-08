# LangGraph offline evaluation on the golden receipts

Written for: reviewers of this project, and whoever decides whether to fund a live LangGraph evaluation.

This compares direct Jev, direct LLM, and LangGraph in both orders on the 32 labelled receipts (79 lines). It was made on 2026-11-06 from recordings only, with the network blocked and no new model call. Every request was answered from a recording made for exactly that model, prompt and input, and every run was checked to have no missing recording before it was scored.

- **Recordings:** the LLM (`anthropic/claude-haiku-5.5`) from `evaluation/final/replay`, the golden replay; Jev (`typesafe/jev-1.14`, snapshot `-20261014`) from `evaluation/jev/runs/first-run/replay`.
- **Routing:** the configured threshold of 0.90, both fallbacks on, not tuned.
- **Scoring:** the existing policy-output harness (`policy/`) with the production ruleset.

These 32 receipts are not held out: they were to hand when the prompt and questions were written. The comparison is one recording per line, so it cannot show run-to-run variation.

## Results

| | Direct Jev | Direct LLM | LangGraph, Jev first | LangGraph, LLM first |
|---|---|---|---|---|
| Category accuracy | 78/79 | 79/79 | 79/79 | 79/79 |
| Lines rejected | 1 (RCP-017-L2) | 0 | 0 | 0 |
| Lines routed on, and extra backend calls | 0 | 0 | 2 | 3 |
| Dispositions right | 32/32 | 32/32 | 32/32 | 32/32 |
| False flags / false passes / silent misses | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| Decided | 30/32 | 31/32 | 31/32 | 31/32 |
| Unresolved findings / open questions | 0 / 15 | 0 / 1 | 0 / 1 | 0 / 1 |
| Rules, strict P / R | 0.947 / 0.973 | 0.974 / 1.0 | 0.974 / 1.0 | 0.974 / 1.0 |

On this set, either order matches direct LLM on every figure, while making the LLM call for only 2 of 79 lines when Jev goes first. The routed lines:

| Order | Line | First backend | Then |
|---|---|---|---|
| Jev first | RCP-017-L2 | Jev's line failed the consistency check | the LLM answered `service_fee` at 0.9 |
| Jev first | RCP-005-L3 | Jev at 0.81 | the LLM at 0.7, kept, as no backend was left |
| LLM first | RCP-004-L2 | the LLM at 0.7 | Jev at 0.9 |
| LLM first | RCP-023-L1 | the LLM at 0.1 | Jev at 0.92 |
| LLM first | RCP-005-L3 | the LLM at 0.7 | Jev at 0.81 |

The one remaining EXC-03 signal, which is also the LLM's, is the low-confidence line RCP-005-L3.

## The robustness set cannot be compared offline

Jev's recordings cover all 60 isolated lines and all 17 lines of the 12 receipts. No LLM recording exists for any of them under the current request. The local cache holds 701 LLM classification answers, but the cache key covers the model, response schema, prompt and exact line text, and none matches. Which part differs was not established. Answers to a different request cannot stand in.

| | Lines | LLM answers needed, Jev first | LLM answers needed, LLM first |
|---|---|---|---|
| Isolated lines | 60 | 17 | 60 |
| Whole receipts | 17 | 3 | 17 |

`metrics.json` (`robustness_coverage`) lists every needed line. With Jev first, 20 LLM requests would make the robustness comparison complete. Those requests are not made here.

## Files

| Path | What it holds |
|---|---|
| `<configuration>/receipts.jsonl`, `<configuration>/predictions.json` | Each configuration's validator output, and its predictions of kind `actual` |
| `policy/` | The harness's metrics, per-receipt results and report, with the oracle and synthetic scenarios |
| `metrics.json` | The summary above, the routed lines, per-rule errors, and the robustness coverage |
| `hybrid_offline.py` | The driver. It refuses an existing run folder; regenerated, it reproduces this one byte for byte |
