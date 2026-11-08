# Jev pilot lines

`pilot.json` is a reference list of **10 lines across 9 receipts** that cover the hard cases for Jev, to try first when a live Jev run is authorized. No code reads it. The first live run, on all 32 receipts, is in [runs/first-run](runs/first-run/README.md), with its recordings, predictions and metrics.

| Receipt / line | Coverage |
|---|---|
| RCP-001-L1 | Ordinary meal |
| RCP-004-L1 | Beer-battered food, misleading alcohol word |
| RCP-010-L2 | Actual alcohol and ALC-01 |
| RCP-012-L1 | Restricted electronics, TECH-01 / DOC-03 |
| RCP-013-L1 | Gift card, missing recipient type, established GIFT-02 with GIFT-04 open |
| RCP-018-L1 | Named client, jewelry, GIFT-01 cap / identifiability |
| RCP-022-L1 | Meal for named government official |
| RCP-022-L2 | Gift for the same official and GIFT-04 replacement |
| RCP-023-L1 | Unknown non-itemized contents, low confidence exemption, DOC-01 |
| RCP-025-L2 | German alcohol description and cached translation |

Category and restricted expectations are labels from `data/receipts.jsonl`. Names, recipient types, policy applicability and uncertainty are derived expectations, from the descriptions and the LLM golden replay, not independent annotations. The file's `model` field (`jev-1.14.0`) records the direct TypeSafe model it was written for; Jev now runs only through OpenRouter, as `typesafe/jev-1.14`.

A live run is an ordinary run with the Jev backend, such as `python -m expense_validator.run_receipts --classification-backend jev --classification-model typesafe/jev-1.14 --out jev.jsonl`, which writes the same per-receipt JSONL as the LLM run. It sends one paid Decisions request per line, which needs explicit authorization. Every answer is recorded once in `cache_dir/jev/`, and the same receipts then replay offline with `mode="replay"`. See [docs/API.md](../../docs/API.md#classification-backends).
