# Evaluation workbench

Upload a labelled (or unlabelled) dataset, choose a backend, its models and an exact ruleset version, and run an evaluation as a background job. Then watch its progress, read its quality metrics and spending, compare runs, and export the results.

```text
POST /v1/evaluations/datasets ─▶ evaluation_datasets      (immutable versions)
POST /v1/evaluations         ─▶ evaluations: queued       (an immutable snapshot of what will run)
                                      │
python -m expense_api.worker  ◀───────┘ atomic claim, lease, heartbeat
   └▶ the core's ExpenseValidator.validate_batch, receipt by receipt, then the whole dataset
        ├▶ evaluation_results  (each receipt's core result, as soon as it is known)
        └▶ evaluation_usage    (each model call, once)
GET  /v1/evaluations/{id}, /metrics, /results, /export;  POST /cancel, /rescore, /compare
```

Evaluations are kept apart from validation runs (`/v1/validate`, `runs`): their own collections, endpoints and process. No classification or policy logic lives here. Every receipt goes through `ExpenseValidator.validate_batch`. Every score is the core's own, from `expense_validator.evaluation.metrics`.

## Datasets

`POST /v1/evaluations/datasets` takes a multipart upload:

| Field | |
|---|---|
| `file` | `.yaml`, `.yml`, `.json` or `.jsonl` |
| `name` | Required for JSONL. JSON and YAML may name the dataset inside the file instead |
| `description`, `context_complete` | Optional |

**A case** is one receipt in the core's raw format (`data/README.md`), with any answers inline, exactly as `data/receipts.jsonl` holds them:

- **Receipt-level `labels`:** `disposition`, plus `expected_exceptions` together with `review_signals`.
- **Line-level `labels`:** `category`, plus optionally `restricted`.

Every answer is optional, so a dataset can be unlabelled or partly labelled. Findings are labelled as a whole: give `expected_exceptions` and `review_signals` together, or neither.

The format decides how cases are written:

- **JSONL:** each non-empty line is one complete case. A line is never a bare receipt with its answers elsewhere. `data/receipts.jsonl` uploads as it is.
- **JSON and YAML:** one object, `{"name", "description", "context_complete", "receipts": [case, ...]}`. Only `receipts` is required. If the form gives a key too, the two values must agree.

`backend/examples/langgraph-golden-5.yaml` is a YAML example. `data/receipts.jsonl` is the JSONL one.

### What is checked at upload

Each receipt is checked exactly as the validator will check it:

1. The core's `ingest_record` in production mode refuses any answer left on the receipt, and any classifier field (such as `category` on a line).
2. The core's `admit_receipt` refuses contradicting amounts, unlisted countries and catalog values.

Answers are checked too:

- An expected category must be in the taxonomy.
- An expected exception may cite only the receipt's own lines, with one entry per rule.
- Receipt and line IDs may each appear once in a dataset.

A refused upload is a 422 `invalid_dataset`, which lists up to 20 problems. Each problem names where it is: `line 12` for JSONL, `receipts[3] (line 40)` for YAML, `receipts[3]` for JSON, then a field path such as `line_items[0].labels.category`. Size limits are `MAX_DATASET_BYTES` (413 `dataset_too_large`) and `MAX_DATASET_RECEIPTS`.

**YAML is read safely.**
- It uses a restricted `SafeLoader` with JSON's scalars only, so `NO` and `yes` stay text, `1:30` is not 90, and a bare date is not a timestamp.
- Numbers with a fraction are exact Decimals.
- Aliases and repeated keys are refused, so no document can expand.
- No tag constructs an object: `!!python/...` is refused, and an explicit `!!timestamp` or `!!binary` is refused as not a JSON value.

### Stored versions

Each upload is stored as an immutable version of `name` and is never updated:

- `original` with `original_sha256`: the exact bytes uploaded.
- `canonical_json` with `canonical_sha256`: sorted keys, exact decimals, answers split from receipts.

The same dataset in any format has one canonical hash. The same bytes uploaded again return the stored version (200), so a retried upload adds nothing. Different content under the same name gets the next version number.

## Creating an evaluation

```json
POST /v1/evaluations
{
  "dataset_id": "dataset_…",
  "ruleset_id": "rs_…",
  "execution_mode": "replay",
  "classification": {"backend": "jev", "model": "typesafe/jev-1.14", "replay_set": "jev"},
  "label": "jev baseline",
  "idempotency_key": "nightly-2026-11-06"
}
```

The response is 201 with the queued job, or 200 when this `idempotency_key` already made one. The same key with a different body is a 409 `idempotency_conflict`.

- **Ruleset.** `ruleset_id` must be an exact version published for production. A draft needs `"preview": true` and `ALLOW_PREVIEW_VALIDATION`, exactly as `/v1/validate` requires.
- **Classification.** `classification` is the same selection `/v1/validate` takes: `backend` (`openrouter`, `jev` or `langgraph`, as the deployment allows), `model`, `translation_model`, `langgraph`, and `replay_set`. The adapters are `chat_completions` and `decisions`, both calling OpenRouter; `decisions` is the one Jev models need, as `GET /v1/backends` reports for each model in `classification_models`. Their earlier names, `openrouter` and `jev`, are still accepted and run the same adapters. `langgraph` is the orchestrator. A model its adapter does not run is refused with 422 `model_adapter_mismatch`.
  - The deployment's allowlists apply as they do to `/v1/validate`.
  - The core then checks the combination itself, so an unsupported one is a 422 `unsupported_configuration`. Examples are `jev` without a Jev model, or a single `model` given for `langgraph`.
- **Reading a run.** Each job's `configuration.setup`, and each compared run's `setup`, gives the recorded classification as `orchestrator` (`direct` or `langgraph`), `provider`, `backend` (the adapter, as recorded), `protocol` and `model`, or LangGraph's `nodes`. It is worked out from what the run recorded each time it is read, and never stored, so a run recorded as `backend: jev`, before the adapters were renamed, reads as direct, through OpenRouter, with the Decisions API adapter, is found by `backend=decisions` or `backend=jev`, and replays from its stored snapshot unchanged.
- **LangGraph.** `langgraph` changes `config.yaml`'s LangGraph configuration field by field, as the core merges it. The whole backend order, each backend's own model, the routing thresholds, and reflection with its reviewer, `max_retries` and `max_total_calls_per_line` are all kept.
  - To run one node on another model, send the whole node list in `langgraph.backends`, each node with its configured `name`, the adapter its model needs (`classification_models[].backend`) and the model, as the web app does. The list replaces `config.yaml`'s for this job only: routing and reflection stay as configured, and the deployment's configuration is never changed. Every node's model is checked against its adapter (`model_adapter_mismatch`) and, live, against the classification allowlist. The job's snapshot, `model_label` and fingerprint hold the order it ran.
  - In replay, each node reads only its own model's recordings. A node with no recording for a line would let the next node answer for it in ordinary replay. An evaluation never scores that: the receipt fails as `replay_recording_missing`, as `docs/API.md` requires.
- **The snapshot.** The job stores `configuration.snapshot`: the exact core settings it will run with, worked out by the core's own `ValidatorSettings.from_config`. The worker runs with this snapshot, not with whatever `config.yaml` says later.
- **The fingerprint.** `configuration.fingerprint` holds the hashes of the prompt, the ruleset artifact, the taxonomy and the field catalog, plus the core's version.
- **What the job checks before it runs.** It also records the dataset's `canonical_sha256` and the replay set's `sha256`, a hash of every recorded answer. A worker refuses to run the job if any of these, or anything in the fingerprint, has changed: `dataset_changed`, `ruleset_changed`, `replay_set_changed` or `configuration_changed`. A result can therefore always be traced to its exact inputs.

**Replay is the default** and never calls a model. A missing answer is that receipt's `replay_recording_missing` error. It never falls back to a live call.

**Live** needs all of these, or nothing is queued:

- `ALLOW_LIVE`
- `"execution_mode": "live"` and `"confirm_live": true`
- limits, as one of:
  - **capped:** a `budget` with at least one limit. Each limit given applies, and one left out is no limit of that kind: `max_spend_usd` (above zero), `max_provider_calls`, `max_receipts` (the dataset may not be larger: the run is refused, never cut short).
  - **uncapped:** no `budget`, and `"confirm_uncapped": true`, only where `ALLOW_UNCAPPED_EVALUATIONS` is set (off by default; otherwise 403 `uncapped_disabled`). No spending, call or receipt limit applies. Every call is still metered and reported, and cancelling still stops the run before its next call. Limits together with `confirm_uncapped` are refused (`uncapped_with_caps`); neither is `budget_required`.
- every model the run may call allowed for its role (`ALLOWED_CLASSIFICATION_MODELS` or `ALLOWED_TRANSLATION_MODELS`, each `ALLOWED_MODELS` where unset), including those `config.yaml` supplies, such as the translator
  - **Translation.** With `translation.enabled`, the translator is one of these models, though it is called only for non-English lines. `classification.translation_model` chooses another for a live run; it must answer chat completions (else `translation_model_unsupported`) and be allowed. A replay always reads the recorded translator's answers: recorded translations are keyed by model, so another translator has none, and its non-English receipts fail with `replay_recording_missing`, never falling back to a live call. Each job records `configuration.translation`: whether it was enabled, the model, and whether it came from the request or `config.yaml`. `GET /v1/backends` reports the default translator, whether it is allowed, and the allowed chat models that could replace it.
- `OPENROUTER_API_KEY`
- `LIVE_CACHE_DIR`

LangGraph reflection is set per evaluation through `langgraph.reflection` (`enabled`, `max_retries`, `max_total_calls_per_line`, `feedback_context`; see `docs/API.md`). The deployment's maximums, `EXPENSE_API_MAX_REFLECTION_RETRIES` (default 3, at most 10) and `EXPENSE_API_MAX_REFLECTION_CALLS_PER_LINE` (default 12, at most 50), apply to the request merged with `config.yaml`; anything over them is refused with 422 `reflection_limit_exceeded`, whose details give the `limits` and what was `requested`. `GET /v1/backends` reports them under the LangGraph backend's `capabilities.reflection.limits`. A line's attempts, reviews and stop reason are in its `classification_details.orchestration`.

Live LangGraph runs under the same limits and confirmations as a direct run. Each node's model, and the reviewer's when reflection is on, must be allowed for classification.

## Background execution

The worker is its own process, `python -m expense_api.worker`. Compose runs it as the `worker` service from the API's image, with the API's settings. It shares only MongoDB with the API. A job is persisted before any worker sees it, so jobs survive restarts of either process. `--once` runs queued jobs until none is left, then exits.

| Status | Meaning |
|---|---|
| `queued` | Persisted; no worker has it yet |
| `running` | A worker holds its lease |
| `cancel_requested` | Asked to stop; the worker stops before its next receipt or provider call |
| `completed` | Every receipt was run, though some may have failed: see `progress.failed_receipts` and `error_summary` |
| `failed` | The job itself could not run or finish: `error.code` says why, such as `worker_lost` or `configuration_changed` |
| `cancelled` | Stopped on request |
| `budget_exhausted` | A live run reached a budget limit: `stop_reason` is `max_spend_usd`, `max_provider_calls` or `budget_unverifiable` |

The last four are terminal. Whatever the end, the results and spending a job stored are kept, and its metrics are computed over the receipts it ran.

**How a job runs.**
1. **Claiming.** Each claim is one atomic `findAndModify` from `queued` to `running`, oldest first. Two workers never run the same job: `test_real_mongo.py` races eight workers for twenty jobs on a real MongoDB.
2. **The lease.** It lasts `WORKER_LEASE_SECONDS`, and a heartbeat renews it before every receipt and every provider call. Every write a worker makes to its job is conditional on still holding the lease, so a worker that lost it stops without overwriting anything.
3. **Pass 1, receipt by receipt.** Each receipt is its own `validate_batch` call, and its core result is stored as soon as it is known. Live calls happen here, and progress, cancellation and budgets follow this pass.
4. **Pass 2, the whole dataset.** One `validate_batch` call replays exactly the answers pass 1 used: the replay set, or the live run's own folder. It calls no model. It gives what a batch run gives, rules spanning receipts such as TIME-03 included, and its results replace pass 1's (`result_context: dataset`).
   - `context_complete` is the dataset's own (true unless it says otherwise), and false when a receipt failed.
   - If pass 2 cannot run, the results stay as evaluated alone (`result_context: isolated_receipts`), and comparisons say so. This happens to a stopped run, or when a live line's call failed so its answer was never recorded.

**Crashes and stale leases.**
- **A replay job is requeued** when its worker's lease expires, up to `MAX_EVALUATION_ATTEMPTS`. It resumes after the receipts it already stored. Replay calls no model, so running again is free and gives the same answers.
- **A live job is never retried.** That could pay for calls again, so it fails as `worker_lost` and keeps the results and spending it recorded.
- **On SIGTERM** (`docker compose stop`), the worker stops before its next receipt or call. A replay job goes back to the queue, and a live job fails as `worker_stopped`.

**Polling.** `GET /v1/evaluations/{id}` returns `status` and `progress`, along with the job's times and its spending so far:

| Field | Holds |
|---|---|
| `progress` | `total_receipts`, `processed_receipts`, `failed_receipts`, `percent` |
| times | `created_at`, `started_at`, `updated_at`, `completed_at` |
| `lease` | `worker_id`, `heartbeat_at`, `expires_at` |
| `spend` | worked out from the calls stored so far |

## Cancellation

`POST /v1/evaluations/{id}/cancel` with `{}`:

- A queued job is cancelled at once.
- A running job becomes `cancel_requested`. Its worker stops before the next receipt or provider call, and the job is `cancelled`.
- A finished job is a 409 `not_cancellable`.

Cancellation is cooperative. A provider request already sent is not interrupted, and nothing is refunded. Its cost is recorded like any other call's.

## Metrics

`GET /v1/evaluations/{id}/metrics`: the metrics a finished job stored, with their recorded `schema_version` (currently `evaluation-metrics/3`). While a job runs, the response is `provisional: true`: metrics over the receipts stored so far, never stored. Before any receipt has run, every scored block says `no receipt has been run yet`.

Original metrics are immutable. `POST /v1/evaluations/{id}/rescore` scores a finished job again with the current version, from its stored results and dataset, with no model call, and stores that beside the original in `rescorings`; asking again for a version already stored changes nothing, and a job still running is a 409 `evaluation_not_finished`. `?scoring=latest` (the default) returns the newest scoring and `?scoring=original` the one the job finished with. Each response says which in `scored`: `{kind: original | rescored | provisional, original_schema_version, current_schema_version, rescorings}`.

Every rate is the core's `rate`: `{numerator, denominator, value, wilson_95}`. A rate over nothing has `value: null`, never 0. A block with nothing to score, such as category metrics on an unlabelled dataset, is `{"available": false, "reason": ...}`.

### Schema history

- Version 2 added per-rule exception detection, evidence correctness, the restricted
  matrix, keyword baseline, error analysis and rule coverage. Unevaluated receipts
  count as missing their expected findings.
- Version 3 classifies uncertain fields and findings without cited lines under
  error analysis rules 2 and 3, rather than rule logic.

### `classification`

Over lines with an expected category. Each such line has exactly one outcome:

| Outcome | |
|---|---|
| `correct` | Has the expected category |
| `incorrect` | Has another category |
| `missing` | No category and no error, such as a LangGraph abstention |
| `classification_error` | The classifier failed on the line |
| `execution_error` | Its receipt could not be evaluated |

Only `correct` counts as correct.

| Metric | Holds |
|---|---|
| `accuracy` | correct / all labelled lines |
| `accuracy_when_predicted` | correct / (correct + incorrect) |
| `coverage` | lines with a category / labelled lines |
| `macro`, `per_category` | The core's `classification_scores`: precision, recall and F1 per expected category, with `support`, and their mean. A category never predicted has precision 0, as the brief says |
| `confusion_matrix` | Expected categories by predicted ones. `(none)` is the column for a line with no category, whatever the reason |
| `restricted` | Over lines whose `restricted` flag is labelled |

### `disposition`

Over receipts with an expected disposition. `accuracy` counts a receipt that could not be evaluated as not correct; `accuracy_when_evaluated` leaves it out. These come from the core's `disposition_scores`:

- `per_disposition`: precision, recall, F1 and support per disposition
- `matrix_gold_by_predicted`
- `undetermined`: receipts not fully decided
- `false_pass`, `false_flag`, `missed_flag_*`, `review`, `abstention`

### `findings`

Only over receipts whose findings are labelled. These come from the core's `rule_scores`:

- `micro` is strict: an EXC-03 uncertainty signal counts as a false positive, as the answers never hold one.
- `micro_without_uncertainty_signals` leaves those signals out.
- `evidence_exact`: the cited lines match exactly.
- `per_rule`.

The core's breakdown of misses by kind is left out. The public `ReceiptResult` does not say which open rules were unresolved or covered by a proof, and those kinds would otherwise be guessed.

### `operational`

These need no answers:

| Metric | Holds |
|---|---|
| receipt counts | `receipts` in the dataset, `run`, `not_run` (a stopped run's unreached receipts: neither errors nor wrong), `evaluated` |
| `execution_errors` | with their `execution_error_codes` |
| `coverage` | lines with a category / lines evaluated |
| `classification_errors` | the classification error rate |
| `uncertainty` | receipts with an EXC-03 signal, lines with any uncertainty, `by_reason` |
| `undetermined_receipts`, `dispositions` | |
| `latency` | wall-clock time per receipt in pass 1, models included |

## Results

`GET /v1/evaluations/{id}/results` returns pages in dataset order (`limit`, `offset`). Each item holds:

- the core's `ReceiptResult`, unchanged, with its evidence
- the answers (`expected`)
- how it scored (`scoring`: each labelled line's outcome, `classification_correct`, `disposition_correct`)
- what it cost (`spend`)
- its latency

The filters combine:

| Filter | Matches |
|---|---|
| `receipt_id` | Repeatable |
| `classification_correct` | `true`: every labelled line correct. `false`: any not. A receipt with no labelled line matches neither |
| `category` | A predicted category on any line. Repeatable |
| `disposition` | The predicted disposition. Repeatable |
| `error` | The receipt or one of its lines failed |
| `uncertain` | An EXC-03 signal |

## Spending

Spending is tracked per call (`evaluation_usage`), per receipt (`results[].spend`) and per run (`spend`, with `by_model`, `by_backend` and `by_purpose`). History pages carry `spend_summary`.

### Where the numbers come from

This is verified against the core's code:

- **Live calls are metered.** For `chat_completions` and `decisions`, the worker hands the core live completions built by the core's own public functions over a `MeteredClient`. Those functions are `llm.openrouter_completion`, `jev.openrouter_decisions` and `translation.openrouter_completion`. For `langgraph`, it hands the core a `client_factory`: every node, the reviewer and each reviser sends through its own `MeteredClient`, recorded under the node's adapter and the call's purpose (`classification`, `review` or `revision`). A limit reached mid-order stops the run before the refused call, and never routes the line on to the next node. The core's requests, parsing and checks are unchanged. The client sees each response and records:
  - call purpose, backend and model
  - input and output tokens, plus reasoning, cached and cache-write tokens when reported
  - the provider-reported cost
  - the generation ID, latency, and success or failure

  SDK retries are off for evaluations, so one record is one request the provider may bill.
- **Replayed calls are worked out from the results**, because replay makes no call. The count is:
  - one call per line for a single backend: a replayed receipt has every answer it asked for, since a missing one raises
  - every answered attempt, revision and review in the LangGraph orchestration audit (a reused review verdict made no call)
  - one translation per translated line

  Their new spend is zero. Their **historical cost**, what the recorded call cost when it was made, is shown apart, and only where the recording holds one. Jev's recordings are whole Decisions responses with the provider's usage, which the core passes on in `classification_details`. OpenRouter chat recordings hold only the answer text, so their historical cost is unknown (`null`), never zero.
- **Cached calls** are answers a live run's own cache gave, as when two lines read the same. They are counted as `cached`, at no cost.

### Per run

| Field | Meaning |
|---|---|
| `actual_cost_usd` | Sum of provider-reported costs of live calls |
| `estimated_cost_usd` | Sum of estimates for live calls that reported tokens but no cost, from the versioned pricing table (`PRICING_TABLE`; `pricing_versions` lists the versions used) |
| `unpriced_usage` | Live calls with neither, and their tokens |
| `new_spend_usd` | `actual + estimated`: this run's new spending. 0 for replay |
| `cost_completeness` | `complete` when no live call is unpriced. Otherwise `incomplete`, and `new_spend_usd` is a lower bound |
| `historical` | Known cost of the recorded calls a replay reused, with how many had none |

Money is `Decimal`, kept as plain decimal text (`"0.00073"`) in every record and response. Each call is one record whose ID names it: a metered call by evaluation, attempt and sequence, and a worked-out call by evaluation, receipt and position. So a call is stored once, and a resumed job or a repeated write adds nothing.

### The pricing table

The table is optional. Its absence means no estimates, so a live call without a reported cost is unpriced. No prices are shipped, because this project has no verified price list to ship.

```yaml
version: openrouter-2026-11-01
currency: USD
models:
  tencent/hy-mt2-30b-a3b: {input_per_million_tokens: "0.10", output_per_million_tokens: "0.20"}
```

### Budgets (live)

Before each provider call, the worker checks these in order, each only when the run has that limit:

1. cancellation (always)
2. `max_provider_calls`
3. with `max_spend_usd`: whether any earlier call left the spend unverifiable
4. `max_spend_usd` against `actual + estimated` so far

Without a spending limit, a call with no known cost does not stop the run: `cost_completeness` is then `incomplete` and `new_spend_usd` a lower bound.

The first limit reached stops the run as `budget_exhausted`, keeping its work. The limits are **best effort**:

- A call's cost is known only once it returns, so the last call can take spend past `max_spend_usd` by up to that call's cost. No per-call maximum can be reserved in advance.
- Under a spending limit, a call with no cost and no price, a failed request (which may still be billed), and a call made with the company's own provider key (BYOK, where OpenRouter's reported cost is only its fee) all leave the budget unverifiable. The next call is then refused rather than risk unbounded spend.
- A zero budget is refused at creation (`budget_zero`).

## Comparing runs

`POST /v1/evaluations/compare` with `{"evaluation_ids": [2 to 10 completed runs]}`. For each run it gives:

- the dataset and ruleset with their hashes, the backend, models and LangGraph configuration
- accuracy, macro F1, disposition accuracy and uncertainty rate
- total and mean latency
- provider calls by kind, new spend, and historical cost
- cost per receipt and per correct prediction. These are given only for a live run whose cost is complete. A replay adds no spend to divide, and an incomplete total would understate the cost.

`comparability.directly_comparable` is false, with the reasons in `issues`, when runs differ in any of these:

- dataset, ruleset, taxonomy or field catalog, or core version
- whether results are dataset or isolated
- whether every receipt was evaluated
- replay or live

Even when they agree, this is a side-by-side view, not a controlled benchmark: one dataset, no repeated trials, no significance test. Each run's Wilson intervals show how wide its uncertainty is. Runs replaying different recordings repeat what those models said when they were recorded, not what they would say now.

## Exports

`GET /v1/evaluations/{id}/export?format=json|csv|pdf`, once the job has finished (409 `evaluation_not_finished` before that). `?scoring=original|latest` picks the metrics a PDF shows.

- **JSON:** `export_schema: "evaluation-export/2"`: the job, its stored metrics, every receipt with its answers and scoring, and every call record. Each receipt's `result` is `ReceiptResult.to_record()`: exactly the brief's fields, in the brief's order, for every model, with everything else in its `metadata` (version 1 held the core's whole result). Compact JSON in the fields' own order; the same evaluation exports to the same bytes.
- **CSV:** one row per line, in dataset and line order, plus one row for a receipt that could not be evaluated. Every text cell starting with `=`, `+`, `-`, `@`, a tab or a newline gets a leading `'`, so no spreadsheet runs it as a formula.

## Replay demo

`demo/evaluations.sh` calls no paid model. It publishes a ruleset through the full lifecycle, uploads `data/receipts.jsonl` (32 labelled receipts) and the YAML example, evaluates the 32 receipts with openrouter and with Jev, and evaluates the five with LangGraph and reflection. It then polls, prints metrics and spending, compares and exports.

```sh
docker compose up --build -d   # from the repository root
mkdir -p /tmp/demo && cd /tmp/demo && bash ~/path/to/backend/demo/evaluations.sh
```

This is its output against the Compose stack, abridged:

```text
ruleset rs_…: published for production
{"label":"openrouter","status":"completed","result_context":"dataset","progress":32,"summary":{"category_accuracy":1.0,"macro_f1":1.0,"disposition_accuracy":1.0,"uncertainty_rate":0.03125,"coverage":1.0,"execution_error_rate":0.0},"spend":{"new_spend_usd":"0","cost_completeness":"complete","live_calls":0}}
{"label":"jev","status":"completed","result_context":"dataset","progress":32,"summary":{"category_accuracy":0.987342,"macro_f1":0.997494,"disposition_accuracy":1.0,"uncertainty_rate":0.0625,"coverage":0.987342,"execution_error_rate":0.0},"spend":{"new_spend_usd":"0","cost_completeness":"complete","live_calls":0}}
{"label":"langgraph-reflection","status":"completed","result_context":"dataset","progress":5,"summary":{"category_accuracy":1.0,"macro_f1":1.0,"disposition_accuracy":1.0,"uncertainty_rate":0.0,"coverage":1.0,"execution_error_rate":0.0},"spend":{"new_spend_usd":"0","cost_completeness":"complete","live_calls":0}}
{"category_accuracy":"79/79","macro_f1":1.0,"disposition_accuracy":"32/32","findings":{"tp":37,"fp":1,"fn":0,"precision":0.973684,"recall":1.0,"f1":0.986667},…}
{"new_spend_usd":"0","cost_completeness":"complete","historical":{"cost_usd":null,"calls_with_cost":0,"calls_without_cost":90},"by_purpose":{"classification":79,"translation":11}}
{"category_accuracy":"78/79","macro_f1":0.997494,"disposition_accuracy":"32/32","findings":{"tp":36,"fp":2,"fn":1,"precision":0.947368,"recall":0.972973,"f1":0.96},"outcomes":{"correct":78,"incorrect":0,"missing":0,"classification_error":1,"execution_error":0}}
{"new_spend_usd":"0","cost_completeness":"complete","historical":{"cost_usd":"0.005225094","calls_with_cost":78,"calls_without_cost":12},"by_purpose":{"classification":79,"translation":11}}
{"category_accuracy":"9/9","macro_f1":1.0,"disposition_accuracy":"5/5","findings":{"tp":8,"fp":0,"fn":0,"precision":1.0,"recall":1.0,"f1":1.0},…}
{"new_spend_usd":"0","cost_completeness":"complete","historical":{"cost_usd":"0.0004704","calls_with_cost":7,"calls_without_cost":13},"by_purpose":{"classification":11,"review":8,"translation":1}}
{"receipt_id":"RCP-017","lines":{"RCP-017-L2":{"expected":"service_fee","predicted":null,"outcome":"classification_error"}}}
{"directly_comparable":true,"issues":[],"note":"a side-by-side view of runs, not a controlled head-to-head benchmark"}
{"backend":"openrouter","model_label":"anthropic/claude-haiku-5.5","accuracy":1.0,"macro_f1":1.0,"disposition":1.0,"calls":90,"new_spend_usd":"0","historical":null}
{"backend":"jev","model_label":"typesafe/jev-1.14","accuracy":0.987342,"macro_f1":0.997494,"disposition":1.0,"calls":90,"new_spend_usd":"0","historical":"0.005225094"}
{"directly_comparable":false,"issues":["different datasets"],"note":"a side-by-side view of runs, not a controlled head-to-head benchmark"}
```

Jev's 78/79 and macro F1 0.997494 are the same as the project's own record of that run, `evaluation/jev/runs/first-run/metrics.json`. Its one miss is RCP-017-L2 "Gratuity", which fails the core's consistency check, as in every earlier Jev run.

The historical cost of $0.005225094 covers 78 of Jev's 79 recorded calls. The core rejected the 79th answer, so its usage is not kept. The run's own record gives $0.005291874 for all 79. The 11 translations have no recorded cost.

Replaying the same evaluation again gives the same results and metrics byte for byte (`test_replay_is_deterministic`).

## Limitations

- **Live LangGraph is metered per call, not per line.** As for direct runs, a request names no line, so live LangGraph calls are attributed to the receipt and purpose; replayed ones are itemized per line from the orchestration audit.
- **The OpenRouter LLM backend discards usage in the core.** `llm._send` keeps only `message.content`, and the answer cache stores only that text. A live call is still fully metered here, through the `MeteredClient`. But a recorded OpenRouter answer has no historical cost.
- **Replayed calls are worked out, not observed.** Replay accepts no injected model, so calls come from the results. A translation whose recorded answer failed the core's checks leaves no trace in a result, so it is not counted. For the same reason, a live run's count of `cached` calls is a lower bound.
- **Live calls are attributed to receipts, not lines.** A request carries no line ID. Replayed calls are attributed to lines.
- **One worker runs one job at a time,** receipt by receipt. More workers (`docker compose up --scale worker=2`) run more jobs at once.
- **Results and metrics are read from MongoDB in full for one job.** That is fine at `MAX_DATASET_RECEIPTS` (500). A much larger dataset would want aggregation in the database.
- **Pagination is offset and limit.**
