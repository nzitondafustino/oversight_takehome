# LangGraph replay set without a routing threshold

The `langgraph` replay set since routing stopped using confidence (2026-11-10): a line goes on to the next backend
only when a backend fails or abstains, and only EXC-03's `min_confidence` judges confidence.

`replay/` is `../live-claude-jev-1/replay` copied unchanged (that run is written once and never overwritten), plus
three answers recorded live on 2026-11-10 with `anthropic/claude-haiku-5.5`. They were missing because the old 0.9
threshold sent RCP-023-L1 (Jev: `other` at 0.89) to the next backend without a review:

| File | Call | Answer |
|---|---|---|
| `reflection/cf2e616a….txt` | review of Jev's `other` | revise, suggesting `food_beverage` |
| `revision/3363fb42….txt` | revision, reviewer feedback only | `other`, 0.1 |
| `revision/eb8a3ac5….txt` | revision, structured context | `other`, 0.1 |

The gold category is `other` (a non-itemized restaurant charge). The reviser kept it, the reviewer's objection was
never withdrawn, and the line ends unresolved with its confidence withheld, in both feedback modes.

It covers RCP-011, RCP-017, RCP-022, RCP-023 and RCP-028, with Jev then Claude, with or without reflection.
