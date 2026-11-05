# Robustness set

A **developer-authored** challenge set for the classifier. It is not an externally validated or fully independent benchmark: its author also wrote the prompt, and knows the benchmark and the taxonomy.

```sh
PYTHONPATH=src .venv/bin/python -m expense_validator.classification.robustness --check          # no network
PYTHONPATH=src .venv/bin/python -m expense_validator.classification.robustness --run <new-name> # sends line text
```

| File | What it is |
|---|---|
| `lines.yaml` | 60 isolated lines. Only `input` reaches the classifier. These are scored on classification and uncertainty only. |
| `receipts.jsonl` | 12 whole receipts in the format of `data/receipts.jsonl`, with results written from the policy text. A separate population, scored through the engine. |
| `receipt_attributes.yaml` | What those receipts' lines state beyond their category, read by hand. |
| `runs/<name>/` | One run: metrics, each line's result, and its predictions. A run refuses to overwrite an existing folder. |

## Runs

| Run | What it is |
|---|---|
| `first-run` | The prompt as Phase 3.1 left it, before any change. Also holds that prompt's benchmark outputs. Keep it as it is. |
| `after-vague-fix` | One added sentence: a line that does not identify its item is `other` at 0.1, whatever the merchant sells. |

## Reading the numbers

- Strict accuracy reads `gold.category`. Acceptable accuracy also allows the readings in `gold.acceptable`, so report them apart. A failed line is wrong on both.
- The same prompt gives different answers on repeated uncached calls, even at temperature 0, so one run's difference on one line can be chance.
- A run has too few errors to judge calibration. Its ECE and Brier score say only that the model reported less confidence than it was right.
- The isolated lines record no quantity, price or currency. A `Receipt` needs them, so the script fills in placeholders and then removes them from every request: the model never sees them. The whole receipts send their real amounts.
- The labels and the prompt are by one author, and the set holds no `lodging` or `software_subscription` gold lines.
