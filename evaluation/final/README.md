# Final per-receipt output

`receipts.jsonl` has one object per receipt, all 32, sorted by `receipt_id`, in the brief's shape exactly, with the extensions below inside `metadata`. `replay/` holds the model answers it was built from:

```sh
PYTHONPATH=src .venv/bin/python -m expense_validator.run_receipts --replay evaluation/final/replay   # no network
```

All 32 receipts are evaluated in one context, as TIME-03, MEAL-01 and HEALTH-01 read across receipts. See `docs/API.md` for the API that produces it.

## Fields

| Field | Notes |
|---|---|
| `receipt_id`, `disposition` | `flag`, `review` or `pass`, by EXC-01 |
| `line_items[]`: `line_id`, `description_raw` | Description as printed, even when a translation was classified |
| `line_items[]`: `category`, `restricted`, `confidence` | `restricted` comes from the taxonomy. `confidence` is the model's own estimate, not a probability |
| `exceptions[]`: `rule_id`, `policy_doc`, `severity`, `line_ids` | Active exceptions only |
| `exceptions[]`: `evidence` | The clause sentence holding the signed quote, or the quote |
| `exceptions[]`: `explanation` | Each finding's explanation, joined by `; ` |
| `review_signals[]`: `rule_id`, `reason` | Includes EXC-03 |
| `metadata` | `model`, `retriever` (`"none"`), `latency_ms` (`null` without a live model call), then the extensions below |

There is no `reasoning`: the classifier does not produce one.

Extensions, all in `metadata`, so every model and adapter gives the same fields everywhere else:

- `translation_model`, `prompt_sha256`, `ruleset_version`, `replayed`, `classification_backend` (the adapter)
- `decided`, `disposition_reasons`, `open_questions` (not violations) and `uncertainty` (EXC-03 details by line)
- `source_quotes`: each exception's signed quote, in the order of `exceptions`
- `line_items`: by line ID, only where there is something: `english_translation`, `attributes`, `classification_error`, and `classification_details`, what one adapter adds (Jev's probabilities, LangGraph's attempts)

The API's JSON exports use the same records: `GET /v1/runs/{id}/export` (`run-export/2`, under `receipts`) and `GET /v1/evaluations/{id}/export` (`evaluation-export/2`, each `result`). An open question never removes an established exception: RCP-013 keeps GIFT-02 while GIFT-04 stays open.

## Evidence

Evidence comes only from the exception's own clause, found by `policy_doc` and `rule_id`. The sentence holding the signed quote is used when the quote is unique in the clause and the sentence states a restriction; otherwise the verified quote is kept:

| Rule | Kept as the quote, because |
|---|---|
| GIFT-04, REG-01, TIME-03 | The quote's sentence describes reporting, not the restriction |
| HEALTH-01 | The clause states two severities |

An unverifiable document, clause or quote stops the run.

## Remaining gaps

- Kept quotes are true but thin; better ones need a reviewer to choose sentences in `rules/rule_logic.yaml`.
- Some expanded sentences refer back, such as TECH-01's "Purchase of any of these".
- The replayed answers come from one run of `anthropic/claude-haiku-5.5` on 2026-11-05; live runs may differ.
