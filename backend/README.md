# Expense Policy Validator API

A REST API around the Expense Policy Validator:

```text
policy document → extraction → review → approval → publication → expense validation → findings → export
```

- stores policy documents as immutable revisions
- extracts and reviews interpretations of chosen revisions through the core's `PolicyCompiler`, and compiles them into ruleset versions
- takes each version through review, approval and publication
- validates batches of receipts synchronously against one exact published version, and persists every completed run in MongoDB
- searches run history, explores and exports findings from the stored result, and reports which backends and catalog values the deployment supports
- evaluates labelled datasets as background jobs, with quality metrics, spending per call, receipt and run, comparisons and exports: see [EVALUATIONS.md](EVALUATIONS.md)

## Architecture

```text
HTTP ─▶ api/v1 (FastAPI routes, typed schemas)
          └▶ services (documents, extractions, rulesets, lifecycle, compilation, validation, runs, discovery, datasets, evaluations): the adapter
                ├▶ repositories (documents, extractions, rulesets, runs, evaluations) ─▶ MongoDB
                └▶ expense_validator (the core library, unchanged)

worker (python -m expense_api.worker) ─▶ MongoDB: claims queued evaluations
          └▶ services (evaluation_runner, evaluation_metrics, spending) ─▶ expense_validator
```

- `expense_api` imports `expense_validator`. The core never imports `expense_api`, FastAPI or PyMongo (`tests/test_core_separation.py`).
- The backend is its own package, `expense-policy-validator-api` (import name `expense_api`), with its own `pyproject.toml`, dependencies, tests and image. It installs the core as a local package.
- No policy, compilation or approval logic lives here. Every decision comes from the core:

| The backend uses | For |
|---|---|
| `ExpenseValidator(ValidatorSettings)`, `validate_batch(...)` | Every run, one new validator per request |
| `ValidatorSettings.from_config(config.yaml, mode=..., cache_dir=...)` | The catalog, taxonomy, policy documents and translation settings |
| `PolicyCompiler.from_config(config.yaml, mode="replay", recordings_dir=...)` | `extract`, `review`, `approve` and `publish`: compilation, review, approval and its verification |
| `Approval`, `FieldComparison`, `PolicyDocumentInput` | The approval record, the comparisons a review decides, and the documents a version compiles from. `PolicyDocumentInput` also checks each uploaded document's filename and text |
| `ExtractionResult`, `ReviewResult`, `CompilerSettings`, `RecordedWith` | The stored extraction snapshot and review, a live compiler's settings, and the settings a replay of live readings must use |
| `expense_validator.policy.schema.RuleSet` | The rule schema, and the canonical artifact (`RuleSet.to_json()`) |
| `ExpenseValidator` in deterministic mode | The runtime check of a new version against the field catalog |
| `expense_validator.evidence.PolicyEvidence` | Each exception rule's quote is in its clause in the deployed policy documents |
| `BatchResult` and the public exceptions | The stored result, findings and exports, and the HTTP errors |
| `classification.backends.registry` (`BACKENDS`, `REVIEWERS`, `REVISERS`), `config.LANGGRAPH`, each `Backend.check_model` and `cache_folder` | Backend discovery |
| `compiler.validate.read_fields`, `classification.core.common.load_taxonomy` | Taxonomy discovery: `rules/fields.yaml` and `data/category-taxonomy.md`, read as the validator reads them |
| `ingestion.ingest_record` (production mode), `engine.inputs.admit_receipt` | Checking each evaluation dataset's receipts at upload, as the validator will |
| `evaluation.metrics` (`rate`, `prf`, `classification_scores`, `disposition_scores`, `rule_scores`) | Every evaluation metric |
| `llm.openrouter_completion`, `jev.openrouter_decisions`, `translation.openrouter_completion`, `LlmTranslator`, `llm_extract.cached`, passed to `ExpenseValidator` as `classifier_model` and `translator` | A live evaluation's completions, over a metered client: the core's own requests, with their usage and cost recorded |

The validator reads its ruleset from a file (`ValidatorSettings.ruleset_path`), not from an object. So each stored artifact is written to `<artifact_dir>/<sha256>.json`, and the validator is pointed at that file. After every run, each receipt's `provenance.ruleset_sha256` must equal the stored version's hash, or the run is refused with `integrity_error` and not saved. The image contains no `rules/ruleset.json`, so the repository's default rules can never stand in for a stored version.

## Policy documents and extraction

```text
POST /v1/documents ──▶ revision 1 ──POST …/revisions──▶ revision 2 …       (immutable, exact text)
        │ choose exact revisions
        ▼
POST /v1/documents/extractions ──▶ extraction snapshot (the core's ExtractionResult)
        │ POST …/extractions/{id}/review   (the core's review contract; every attempt kept)
        ▼
POST /v1/rulesets {"extraction": {extraction_id, review_id}} ──▶ draft version ──▶ the ruleset lifecycle below
```

### Documents

- A document is UTF-8 Markdown, sent as JSON. Its filename and text are checked against the core's `PolicyDocumentInput`: a bare `.md` name, and text that is not blank. The filename may be at most 120 characters, and the text at most `MAX_DOCUMENT_BYTES`.
- The text is stored exactly as sent, with its SHA-256. Line endings, whitespace and Unicode forms are never normalized. A stored revision that no longer matches its hash is refused.
- A change is `POST /v1/documents/{id}/revisions`. The new revision keeps the filename, because rules cite documents by filename. Earlier revisions never change.
  - Sending the latest revision's exact text again is a 409 `revision_unchanged`.
  - `based_on_revision` makes a stale edit a 409 `revision_conflict`.
- `GET /v1/documents` lists each document's latest revision, without its text. `GET /v1/documents/{id}?revision=` returns one revision with its exact text.

### Extractions

`POST /v1/documents/extractions` names exact `{document_id, revision}` pairs, one revision per document. Together they are the policy candidate. The backend loads those revisions and passes their original text to `PolicyCompiler.extract` as `PolicyDocumentInput`s. It never substitutes the repository's policy files.

| `execution_mode` | Reads | Needs |
|---|---|---|
| `replay` (the default) | A server-side `recording_set` by document hash. With `recorded_with`, only readings recorded live with that model and temperature, for this company | Nothing paid. Text with no recorded reading is a 409 `replay_recording_missing` |
| `live` | The model, with `extraction_model` and optionally `temperature` and `timeout_seconds` | `ALLOW_LIVE`, the model in `ALLOWED_MODELS`, `OPENROUTER_API_KEY`, and a `COMPILER_LIVE_RECORDING_SET` |

A live extraction records each model reading in the live recording set, as the core does. It also stores the model and temperature as `recorded_with`. Every later step then replays exactly those readings, never calling the model again: reviews, the lifecycle's rebuilds, approval and publication.

The snapshot stores the core's whole `ExtractionResult` as JSON, with its SHA-256:
- the candidate ruleset
- the agreement output
- the review items
- every comparison
- the clauses and validation
- the hashes

Beside it, it stores:
- the exact revisions read
- the provenance: mode, recording set, reading kind, model, temperature, timeout, overrides, prompt hash
- the blockers
- the agreement summary

Nothing is truncated. `GET …/extractions/{id}` returns the summary and the fields a review must decide. `GET …/extractions/{id}/comparisons` pages through every comparison.

`GET /v1/documents/extractions` lists extractions, newest first (`limit` up to 100, `offset`, and `document_id` for those that read a revision of one document). Each item is the stored summary with its reviews, without `review_required`: a list never reads the core's stored results, so `GET …/extractions/{id}` gives those fields.

An extraction's `status` is what the compiler found:
- `no_candidate`: no valid ruleset could be built, as with one document alone, since the rule logic needs every policy's clauses
- `review_required`: fields await a reviewer
- `ready_for_approval`: nothing awaits one

It is never "approved": approval belongs to a ruleset version.

### Reviews

`POST …/extractions/{id}/review` sends the core's `ReviewDecision` records, which support `confirm`, `accept_regex`, `accept_llm`, `override` and `reject`. It also sends the snapshot's `extraction_sha256`; another value is a 409 `stale_extraction`. The backend reloads the stored snapshot and calls `PolicyCompiler.review` with the original revisions. The core refuses a snapshot altered since it was made.

Every review is stored, ready or not:
- the decisions and policy
- the core's whole `ReviewResult`
- the remaining fields and issues
- `review_sha256` and the candidate hash

Only a review the core reports `ready_for_approval` can become a candidate. One that leaves a field undecided, rejects one, or makes an invalid decision never can.

### Candidate versions

`POST /v1/rulesets` with `{"extraction": {"extraction_id", "review_id"}}` makes a draft version. Omit `review_id` only if the extraction needed no review. The version keeps:
- the candidate artifact
- the exact revisions and their text
- the extraction's hashes
- its provenance
- the prepared review

It then enters the lifecycle below unchanged. Its lifecycle review may omit `decisions` to accept the prepared ones, which the compiler re-runs and must reproduce.

## Ruleset lifecycle

```text
                    submit              review: accepted         approve                 publish
  (create) ─▶ draft ───────▶ in_review ─────────────────▶ in_review ───────▶ approved ───────▶ published
                ▲               │       (review recorded)
                └───────────────┘
                 review: rejected (reason required)
```

| Step | Route | From | To | What must hold |
|---|---|---|---|---|
| create | `POST /v1/rulesets` | | `draft` | The artifact passes the schema, runtime and evidence checks. From an extraction, a ready review too |
| submit | `POST /v1/rulesets/{id}/submit` | `draft` | `in_review` | Nothing else. A new submission clears the previous review |
| review | `POST /v1/rulesets/{id}/review` | `in_review` | `in_review` or `draft` | One decision per submission. `accepted` needs the compiler to accept the review. `rejected` needs a reason |
| approve | `POST /v1/rulesets/{id}/approve` | `in_review` with an accepted review | `approved` | The core's approval of this exact version passes the core's publication safeguards |
| publish | `POST /v1/rulesets/{id}/publish` | `approved` | `published` | The same approval still holds when checked again |

Any other step is a 409 (`invalid_transition`, `review_required`, `already_reviewed`), and the version stays as it was.

Each transition is one conditional, atomic MongoDB update. It matches the version's ID, current status and content hash, sets only lifecycle fields, and appends its audit event to `transitions`. Two requests can never both move a version, and the history can never miss a move.

### Versions and immutability

- A `ruleset_id` names one exact, immutable version, as in Phase 1. The versions of one name form the company history: `(name, version)` is unique. List them with `GET /v1/rulesets?name=`.
- An edit is a new version: `POST /v1/rulesets` again. Give `based_on_version` to say which version the edit replaces. If another version was created since, the request is a 409 `version_conflict`, so two edits of one version can never both become the next.
- The artifact, its hash, its source and its compilation are written once. A transition may set only `status`, `submitted_at`, `review`, `approved_at`, `approval_json`, `approval_reference`, `approval_mode`, `published_at` and `publication_mode`; the repository refuses anything else.
- Approved and published versions accept no step that changes them. Publishing an already published version again returns it unchanged. Giving a different `approval_reference` is a 409 `publication_conflict`.

### Schema-valid is not policy-approved

A version enters in one of three ways.

| | `artifact`: an uploaded compiled ruleset | `compile`: documents sent inline, with a recording set | `extraction`: a reviewed extraction of stored revisions |
|---|---|---|---|
| Checked | The core's schema, the runtime check and the evidence check | The same, on the compiler's candidate | The same, on the snapshot's candidate |
| Has an extraction the compiler can approve | No | Yes: `compilation` holds the documents, the extraction hash and every comparison | Yes, as for `compile`, with the revisions and the prepared review |
| Can be approved and published | **Never**: `approve` is a 409 `approval_unverifiable` | Yes, once reviewed | Yes, once its lifecycle review accepts |
| Can be validated | Only as a preview | Once published for production | Once published for production |

Passing the schema means the rules can run. Approval means a reviewer accepted how the compiler read the policy text, and the core bound that acceptance to the exact candidate.

### How approval is verified

The core's contract, which the backend follows without changing it:

1. **Compile.** `PolicyCompiler.extract(documents)` reads each document from a server-side recording set. It never calls a model. The candidate ruleset becomes the version's artifact, and its hash is the version's content hash. Every field comparison is kept, with its `comparison_id`, under the `extraction_sha256`.
2. **Review.** `GET /v1/rulesets/{id}/comparisons?review_required=true` lists the fields that need a decision. The review sends the core's `ReviewDecision` records. `PolicyCompiler.review` must report the review `ready_for_approval`, or the review is a 422 `review_not_ready`. That covers decisions for an undecided field, and a value the source text does not support. The review and its `review_sha256` are stored.
3. **Approve.** The backend first runs the stored extraction and review again, and requires the same `extraction_sha256`, candidate hash and `review_sha256`. Then:
   - the approval is `PolicyCompiler.approve` of that exact review, with the server's time; or
   - the request supplies an approval record made elsewhere with the core.

   Either way, the backend then runs the core's `publish` into a scratch folder, with the approval, the documents, the decisions and the requested `mode`. That rebuilds the candidate and applies `publication_blockers`. It must publish, and the file it would write must be byte for byte this version's artifact. Otherwise the result is a 422 `approval_does_not_hold`, with the compiler's reasons. The core's approval record is stored exactly as `approval_json`, and its SHA-256 is the `approval_reference`.
4. **Publish.** The same verification runs again before the version is published. An approval that no longer holds is not published, for example when a recording behind it changed (409 `compilation_changed`).

An approval of another review, of another extraction, of the unreviewed extraction, or with another candidate hash never covers a version. Simulation readings never authorize production. Production publication needs model readings and a model approval, as `publication_blockers` says.

### Validation restrictions

- `POST /v1/validate` uses only the requested version, and only when it is `published` with `publication_mode: production`. Otherwise the request is a 409 `ruleset_not_published`. Nothing ever substitutes another version.
- `"preview": true` validates any other version, such as a draft, or a version published by simulation. This works only where the deployment sets `EXPENSE_API_ALLOW_PREVIEW_VALIDATION=true`; elsewhere it is a 403 `preview_disabled`. The run records `preview` and the version's status at run time.
- Every run stores the exact `ruleset_id`, `version`, `status`, `publication_mode`, `artifact_sha256` and `ruleset_version`. A version's content never changes, so a historical run stays reproducible.

## Run history, findings and exports

These endpoints are read-only. Each reads the stored run: nothing is validated again, and no model or validator is called.

### History: `GET /v1/runs`

| Parameter | |
|---|---|
| `ruleset_id` | One exact version |
| `ruleset_name` and `ruleset_version` | A name's version number. `ruleset_version` alone is a 422 `invalid_filter` |
| `classification_backend` | The backend the receipts' provenance reports. A deterministic run reports none |
| `execution_mode` | `replay`, `live` or `deterministic` |
| `preview` | `true` or `false` |
| `created_from`, `created_to` | ISO 8601 with a time zone: from is inclusive, to is exclusive. A time with no zone, or a range ending before it starts, is a 422 |
| `limit`, `offset` | 1–100, default 20; offset from 0 |

Runs come newest first. Ties in `created_at` are broken by the run ID, which is unique and increasing, so pages never shift or repeat. A summary never holds the results:

```json
{"run_id": "run_6aee…", "status": "completed", "created_at": "2026-11-06T23:27:05.937000Z",
 "ruleset": {"ruleset_id": "rs_6aee…", "name": "policies", "version": 1, "status": "published", "publication_mode": "production", "artifact_sha256": "83c9…", "ruleset_version": "v1"},
 "execution_mode": "replay", "preview": false, "context_complete": true, "classification": {"backend": null, "model": null, "translation_model": null, "langgraph": null, "replay_set": "openrouter"},
 "classification_backend": "openrouter", "receipt_count": 2, "dispositions": {"flag": 2, "review": 0, "pass": 0},
 "counts": {"exception": 4, "review_signal": 3, "open_question": 1, "uncertainty": 2}}
```

A run now stores a small `summary`, holding its effective backend and the counts, and a `results_sha256` beside its result. Runs stored before this phase have neither:
- They show `classification_backend: null` and `counts: null`, rather than a value guessed from the requested backend.
- A backend filter does not find them.
- Their findings and exports still work, read from their stored results.

Backfilling would mean adding `summary` from each run's own `results_json`, without touching the result. It is not done here.

### Findings: `GET /v1/runs/{run_id}/findings`

The core does not have one finding shape. Each receipt result holds four kinds of record, and each finding is one of them, never merged:

| `kind` | The core record | `rule_id` | `severity` | `line_ids` |
|---|---|---|---|---|
| `exception` | `ExceptionResult`: an active policy exception | yes | the rule's, may be null | the lines, `[]` for a receipt-level finding |
| `review_signal` | `ReviewSignalResult`, including EXC-03's uncertainty signal | yes | null | null: it names none |
| `open_question` | `OpenQuestionResult`: a rule left undecided, not a violation | yes | null | yes |
| `uncertainty` | `UncertaintyResult`: EXC-03's detail for one line and field | null: it names none | null | its one line |

Each item has:
- `finding_id`: `receipt:kind:index`, stable for a stored run
- `receipt_id`, and the receipt's `receipt_disposition` and `receipt_decided`
- `disposition_reasons`: the receipt's reasons that name this rule, which is the core's link from a rule to the disposition
- `record`: the core's record, whole, with its evidence, signed quote, explanation or reasons

No severity, confidence or evidence is added. A receipt that breaks several rules has a finding for each.

Filters combine, and a repeated filter means any of its values:
- `kind`
- `severity` (only exceptions have one)
- `rule_id`
- `receipt_id`
- `disposition` (the receipt's: `flag`, `review` or `pass`)
- `limit` (1–100, default 50) and `offset`

The order is fixed: receipts in receipt ID order, then the kinds in the order above, then the core's own order. Uncertainty alone is `kind=uncertainty`.

### Export: `GET /v1/runs/{run_id}/export?format=json|csv`

- `json`, the default, is the run exactly as `GET /v1/runs/{run_id}` returns it: the core's full result, dispositions, uncertainty, provenance and the ruleset reference. It is canonical JSON, with sorted keys, no extra spaces, UTF-8 not escaped, and a final newline.
- `csv` is one row per finding, in the findings order, with these columns:
  - `run_id`
  - `ruleset_id`, `ruleset_name`, `ruleset_version`, `ruleset_label`, `ruleset_sha256`
  - `classification_backend`, `execution_mode`, `preview`
  - `receipt_id`, `receipt_disposition`, `receipt_decided`
  - `finding_id`, `kind`, `rule_id`, `severity`, `policy_doc`, `line_ids`
  - `explanation`, `evidence`, `source_quote`, `reason`, `reasons`, `field`, `state`, `confidence`
  - `disposition_reasons`, `record`

  Each record field fills its own column, and a field the core's record lacks is empty. `line_ids`, `reasons`, `disposition_reasons` and `record`, the whole core record, are canonical JSON in their cells. A run with no findings is the header alone.
- The CSV is UTF-8 with no byte-order mark, RFC 4180 quoting and `\r\n` line endings.
- Every text cell starting with `=`, `+`, `-`, `@`, a tab or a line break gets a leading `'`, so a spreadsheet never runs it as a formula. The `record` cell and the JSON export keep the text exactly.
- The same run always exports to the same bytes.
- Responses have `Content-Disposition: attachment; filename="<run_id>.<format>"`, `X-Content-Type-Options: nosniff` and `Cache-Control: no-store`.
- No key or secret is ever in a run, so none can be in an export.
- An unknown format is a 422 `unsupported_export_format`.

A stored result that is damaged is a 500 `corrupt_run` from the findings, export and run endpoints, never an empty page. That covers one that no longer matches its hash, cannot be read, or is missing.

## Discovery

### `GET /v1/backends`

Four things are kept apart, so a client never presents one as another:

- **Orchestrator** (`orchestrators`): `direct`, where one model classifies every line, or `langgraph`, which runs its configured model nodes in order. Each LangGraph node lists its `provider`, `backend` (adapter), `protocol` and `model`.
- **Provider** (`providers`): the API a request goes to, with the adapters that call it. Every registered adapter calls OpenRouter, so `openrouter` is the only provider. OpenRouter is never a backend, and Jev is a model through OpenRouter, not a provider.
- **Execution adapter** (a registered backend in `backends`, `kind: "adapter"`), named for its protocol: `chat_completions` sends chat completions with a JSON schema; `decisions` sends typed choice questions to OpenRouter's Decisions API, pinned to TypeSafe's provider with no retries, and checks the probabilities that come back. The two contracts differ, and neither falls back to the other. Each lists its earlier name in `legacy_ids`: `openrouter` and `jev`.
- **Model** (`classification_models`): every model the deployment names for classification (`config.yaml`'s default, each LangGraph node's model, and the classification allowlist), with its `provider`, and with the one `backend` its ID needs, by that adapter's own `check_model`. A model no adapter accepts is left out. Translation models are listed apart, in `translation.models`, from the translation allowlist.

The adapter follows from the model, so a client chooses an orchestrator, a provider and a model, and sends the adapter as `classification.backend`. A request naming a model its adapter does not run is refused before anything runs with 422 `model_adapter_mismatch`, whose details name each model, its adapter, the LangGraph node if any, and the adapters that would run it; the adapter is never swapped. The adapters' earlier names, `openrouter` and `jev`, are still accepted wherever a backend is named (requests, `config.yaml`, LangGraph orders, `ALLOWED_BACKENDS`, history filters) and run the same adapter. A record made before the rename keeps its name: it reads, filters and replays as that adapter, and is never rewritten. Recordings are keyed by model and prompt, so replay sets need no change; their names (`openrouter`, `jev`) are folder names. A LangGraph run's prompt hash and model label do name the adapters, so they differ between runs made before and after the rename.

`backends` lists each registered adapter, plus `langgraph`. Three things are reported apart:

| Field | Means |
|---|---|
| `installed` | In the installed core's registry. For `langgraph`, its optional package is importable too |
| `enabled` | In this deployment's `ALLOWED_BACKENDS` |
| `available` | Installed and enabled, for replay or deterministic use |
| `live.permitted` | Enabled, and `ALLOW_LIVE` |
| `live.credential_configured` | A key is set. This is not proof the provider would answer, and the value is never shown |
| `live.available` | Permitted with a key |
| `allowed_models` | The deployment's allowed models that this backend's own `check_model` accepts |
| `replay.sets` | Each replay set: whether the backend's answer folder exists, and how many files it holds. This is presence, not coverage. The LLM's answers sit in the root folder with the translations |
| `capabilities` | `review` and `revise`, from `REVIEWERS` and `REVISERS`. For `langgraph`, its configured order (each node with its provider and protocol), routing and reflection |
| `kind`, `provider`, `protocol` | `adapter` with its provider and protocol, or `orchestrator` with neither |

No provider is called. The display names are the only text the backend adds.

`policy_compiler` says what `POST /v1/documents/extractions` can use here:

| Field | Means |
|---|---|
| `replay.sets` | Each of `COMPILER_RECORDING_SETS` by name, never by path: whether its folder exists (presence, not which documents it has readings for) and whether it is the `live_target` that live extraction records into |
| `live.permitted` | `ALLOW_LIVE` |
| `live.configured` | `COMPILER_LIVE_RECORDING_SET` names a set |
| `live.credential_configured` | A key is set, never shown |
| `live.available` | All three |

`evaluations.uncapped_allowed` is `ALLOW_UNCAPPED_EVALUATIONS`: whether a live evaluation may run with no limit.

`translation` reports `config.yaml`'s translator (`enabled`, `default_model`) and `models`: the default and every allowed model the core accepts for chat completions, each with whether it is `allowed`. A live run counts the translator among its models whenever translation is enabled; see EVALUATIONS.md.

`rulesets` says which versions requests may use here: `preview_allowed` is `ALLOW_PREVIEW_VALIDATION` (validating or evaluating a version not published for production), and `publication_modes` is `ALLOWED_PUBLICATION_MODES` (what an approval may authorize). The requests that use them still check them.

### `GET /v1/taxonomy`

The core's field catalog, `rules/fields.yaml`, and category taxonomy, `data/category-taxonomy.md`, are read from the core's configuration, as the validator reads them. Each comes with its SHA-256 and the input contract version. The response has:
- `categories`: in catalog order, each with its taxonomy `restricted` flag and examples, or null if it has no row
- `entities`, with their countries
- `countries`, with their home currencies
- `expense_types`
- `currencies`, with their minor units
- `vocabularies`: every catalog field that lists the only values it may take
- `fields`: every field a rule may read, with its type and unit

Identifiers and values are exactly the catalog's. A section the catalog lacks is null, and an unreadable catalog is a 503 `taxonomy_unavailable`.

## Local installation

Run from `backend/`, with Python 3.12:

```sh
python3.12 -m venv .venv
.venv/bin/pip install -e "..[langgraph]" -e ".[dev]"   # the core and the backend in one command
```

Install both in one command, so that pip takes the core from `..`. The core is not published to any package index, so pip must never look for `expense-policy-validator` on one.

```sh
docker run --rm -d -p 127.0.0.1:27017:27017 mongo:7.0
export EXPENSE_API_CORE_CONFIG=../config.yaml
export EXPENSE_API_REPLAY_SETS='{"openrouter": "../evaluation/final/replay", "jev": "../evaluation/jev/runs/first-run/replay", "langgraph": "../evaluation/langgraph/runs/live-claude-jev-1/replay"}'
export EXPENSE_API_DEFAULT_REPLAY_SET=openrouter
export EXPENSE_API_COMPILER_RECORDING_SETS='{"live": "../evaluation/compiler/live"}'
.venv/bin/uvicorn expense_api.main:create_app --factory --port 8000
```

`../config.yaml`'s folder holds the core's `.env`, from which the core reads `OPENROUTER_API_KEY` in live mode. Live mode stays off unless `EXPENSE_API_ALLOW_LIVE=true`.

## Container deployment

```sh
cd backend
cp .env.example .env        # optional: every setting has a safe default
docker compose up --build -d
```

The API is served at http://localhost:8000/docs, `/openapi.json` and `/health`. Compose runs three services:

- `api`: the FastAPI application
- `worker`: the evaluation worker, `python -m expense_api.worker`, from the same image and with the same settings. It serves no port. It shares the `live-cache` volume with the API, where each live evaluation keeps its answers. `docker compose up --scale worker=2` runs two, as each job is claimed atomically
- `mongo`: MongoDB, on an internal network with no published port, with its data in the `mongo-data` volume

The image runs without the repository:

- Both packages, `expense-policy-validator` and `expense-policy-validator-api`, are installed into site-packages, from a bind mount that exists only during the build. No source checkout, bind mount or `.env` is needed or present at runtime (`tests/test_container.py`).
- The `compiler-recorded` volume is the live recording set: live extractions' model readings, which every later step replays.
- The image holds the runtime assets:
  - `config.yaml`, the field catalog and the rule logic, in `/srv/core`
  - the taxonomy and the policy documents
  - the three replay sets, in `/srv/replay/*`
  - the compiler's model readings, in `/srv/recordings/compiler/live`
- Any of these folders can instead be mounted read-only over its path. The image has no `rules/ruleset.json`.
- `Dockerfile.dockerignore` admits only those files, so `.env`, `.venv`, `.git`, the tests and the labelled receipts never enter the build. The application runs as a non-root user.

`docker compose down` stops the stack and keeps the data. Add `-v` to delete it.

## End-to-end demo

From `backend/`, with the stack up. It calls no paid model: every step replays recordings. The script below was run against the Compose stack as written.

```sh
# Run from backend/ with the stack up. Writes its working files to the current folder.
API=localhost:8000
post() { curl -sf -X POST "$API$1" -H 'content-type: application/json' --data-binary "${2:-@-}"; }

# 1. Upload the policy documents
for f in ../data/policy-docs/*.md; do
  jq -n --arg fn "$(basename "$f")" --rawfile t "$f" '{filename: $fn, content: $t}' | post /v1/documents | jq -c '{document_id, revision}'
done | jq -s . > refs.json

# 2. Extract interpretations of those exact revisions (replay: no model call)
jq '{documents: ., recording_set: "live"}' refs.json | post /v1/documents/extractions > extraction.json
EXTRACTION=$(jq -r .extraction_id extraction.json)
jq -c '{status, needs_review: [.review_required[] | "\(.rule_id) \(.field)"]}' extraction.json

# 3. Review the discrepancies with the core's decisions
jq --arg q "is a **high** exception, regardless of stated business purpose" '{actor: "r.eviewer", extraction_sha256,
    decisions: [.extraction_sha256 as $x | .review_required[] | {comparison_id, extraction_sha256: $x, reviewer: "r.eviewer"}
      + (if .rule_id == "TECH-01" then {action: "override", value: $q, rationale: "the quote the executable rule signs"} else {action: "accept_regex"} end)]}' extraction.json \
  | post /v1/documents/extractions/$EXTRACTION/review > review.json
jq -c '{review_id, ready_for_approval}' review.json

# 4. Create a draft ruleset version from the reviewed extraction
RULESET=$(jq -n --arg x "$EXTRACTION" --arg r "$(jq -r .review_id review.json)" '{name: "policies", extraction: {extraction_id: $x, review_id: $r}}' \
  | post /v1/rulesets | jq -r .ruleset_id)

# 5. Submit, accept the prepared review, approve and publish
for step in submit review approve publish; do
  jq -n --arg s $step '{} + (if $s == "review" then {actor: "r.eviewer", decision: "accepted"} elif $s == "approve" then {actor: "a.pprover"} else {} end)' \
    | post /v1/rulesets/$RULESET/$step | jq -r '"\(.status)"'
done

# 6. Validate receipts against the published version (replay, the default)
sed -n '13,14p' ../data/receipts.jsonl | jq -s --arg rs "$RULESET" \
  '{ruleset_id: $rs, context_complete: true, receipts: map(del(.labels, .notes) | .line_items |= map(del(.labels)))}' \
  | post /v1/validate > run.json
RUN=$(jq -r .run_id run.json)

# 7. Run history
curl -sf "$API/v1/runs?ruleset_id=$RULESET&limit=5" | jq -c '.items[] | {run_id, classification_backend, receipt_count, dispositions, counts}'

# 8. Filter findings: the exceptions, then the uncertainty kept apart from them
curl -sf "$API/v1/runs/$RUN/findings?kind=exception" | jq -c '.items[] | {finding_id, rule_id, severity, line_ids}'
curl -sf "$API/v1/runs/$RUN/findings?kind=uncertainty" | jq -c '.items[] | {finding_id, line_ids, reason: .record.reason}'

# 9. Export
curl -sf -OJ "$API/v1/runs/$RUN/export?format=json"
curl -sf -OJ "$API/v1/runs/$RUN/export?format=csv"
ls -l "$RUN.json" "$RUN.csv"

# Discovery
curl -sf $API/v1/backends | jq -c '.backends[] | {id, enabled, available, live: .live.available}'
curl -sf $API/v1/taxonomy | jq -c '{catalog: .catalog.sha256, categories: [.categories[].id] | length, entities: [.entities[].id]}'
```

It prints the four fields awaiting review, then a ready review and `in_review`, `in_review`, `approved`, `published`. Next comes the run's summary:

```text
{"classification_backend":"openrouter","receipt_count":2,"dispositions":{"flag":2,"review":0,"pass":0},"counts":{"exception":4,"review_signal":3,"open_question":1,"uncertainty":2}}
```

Then it prints the four exceptions (GIFT-02, ALC-03, DOC-03 with `line_ids: []`, HOUSE-01) and the two uncertainty records. Last come the two exported files and the discovery summaries.

## Environment variables

Every variable is `EXPENSE_API_*` unless noted. A request can choose none of them.

| Variable | Default | Meaning |
|---|---|---|
| `CORE_CONFIG` | required (the image sets it) | The core's `config.yaml`. Its `paths.ruleset` is never used |
| `MONGO_URI` | `mongodb://localhost:27017` | May hold credentials; never logged or stored |
| `MONGO_DATABASE` | `expense_api` | |
| `MONGO_TIMEOUT_MS` | `2000` | Server selection timeout |
| `ARTIFACT_DIR` | `<tmp>/expense_api/rulesets` | Stored artifacts as files, by hash. Rebuilt from MongoDB when missing or different |
| `COMPILER_RECORDING_SETS` | `{}` | JSON object: recording set name to a folder of the compiler's recorded readings |
| `COMPILER_LIVE_RECORDING_SET` | none | The set a live extraction records model readings into. It must hold model readings only. None: live extraction is unavailable |
| `MAX_DOCUMENT_BYTES` | `524288` | The largest document text, in UTF-8 bytes |
| `ALLOWED_PUBLICATION_MODES` | `["production"]` | What an approval may authorize. Add `"simulation"` to try the lifecycle with simulated readings |
| `ALLOW_PREVIEW_VALIDATION` | `false` | Whether a request may validate against a version not published for production |
| `REPLAY_SETS` | `{}` | JSON object: replay set name to a recordings folder, for classification |
| `DEFAULT_REPLAY_SET` | none | The set a replay request gets when it names none |
| `ALLOW_LIVE` | `false` | The deployment's switch for live inference |
| `ALLOW_UNCAPPED_EVALUATIONS` | `false` | Whether a live evaluation may run with no spending, call or receipt limit (`confirm_uncapped`); refused with 403 `uncapped_disabled` otherwise |
| `LIVE_CACHE_DIR` | none | Live answers kept for reuse. None keeps nothing |
| `ALLOWED_MODELS` | `[]` | JSON list of model IDs a request may choose: the policy compiler's extraction allowlist, and each role's below where it is unset |
| `ALLOWED_CLASSIFICATION_MODELS` | unset | Models allowed for classification: a direct model, each LangGraph node's and the reviewer's. Unset: `ALLOWED_MODELS` |
| `ALLOWED_TRANSLATION_MODELS` | unset | Models allowed for translation. Unset: `ALLOWED_MODELS` |
| `ALLOWED_BACKENDS` | `["chat_completions","decisions","langgraph"]` | Execution adapters and the orchestrator a request may choose, including inside a LangGraph order. The earlier names `openrouter` and `jev` are read as the adapters |
| `MAX_RECEIPTS_PER_REQUEST` | `500` | Also bounds an evaluation's dataset pass |
| `MAX_DATASET_BYTES` | `4194304` | The largest evaluation dataset upload |
| `MAX_DATASET_RECEIPTS` | `500` | |
| `PRICING_TABLE` | none | A versioned YAML table of per-token prices, used only to estimate a live call's cost when the provider reports none. None: no estimates |
| `WORKER_LEASE_SECONDS` | `120` | How long a claimed evaluation's lease lasts without a heartbeat |
| `WORKER_POLL_SECONDS` | `2` | How often an idle worker looks for queued evaluations |
| `MAX_EVALUATION_ATTEMPTS` | `3` | How often a replay evaluation is requeued after its worker was lost. A live one never is |
| `OPENROUTER_API_KEY` (no prefix) | none | Read by the core in live classification only |

## Endpoints

| Method and path | Success | Errors |
|---|---|---|
| `GET /health` | 200 | 503: `status: degraded`, `application: running`, `database: unavailable` |
| `POST /v1/rulesets` | 201: the new draft version | 422 `invalid_request`, `invalid_ruleset`, `invalid_documents`, `compilation_failed`, `unknown_recording_set`; 409 `version_conflict`, `replay_recording_missing`, `no_candidate`, `review_required`, `review_not_ready`, `legacy_extraction`; 404 `extraction_not_found`, `review_not_found` |
| `GET /v1/rulesets?name=&limit=&offset=` | 200: versions with their lifecycle state, newest first | 422 |
| `GET /v1/rulesets/{id}` | 200: the version, its artifact and its transitions | 404 `ruleset_not_found`; 500 `integrity_error` |
| `GET /v1/rulesets/{id}/comparisons?review_required=` | 200: the compiler's field comparisons | 404; 409 `not_compiled` |
| `POST /v1/rulesets/{id}/submit` | 200 | 404; 409 |
| `POST /v1/rulesets/{id}/review` | 200 | 404; 409; 422 `review_not_ready`, `invalid_review_decisions` |
| `POST /v1/rulesets/{id}/approve` | 200 | 404; 409 `review_required`, `approval_unverifiable`, `compilation_changed`; 422 `approval_refused`, `approval_does_not_hold`, `invalid_approval`, `publication_mode_not_allowed` |
| `POST /v1/rulesets/{id}/publish` | 200, also for a version already published with this approval | 404; 409 `invalid_transition`, `publication_conflict`, `compilation_changed`; 422 `approval_does_not_hold` |
| `POST /v1/documents` | 201: revision 1, with its text | 422 `invalid_request`, `invalid_document`; 413 `document_too_large` |
| `GET /v1/documents?filename=&limit=&offset=` | 200: each document's latest revision, without text | 422 |
| `GET /v1/documents/{id}?revision=` | 200: the revision and its exact text | 404 `document_not_found`; 500 `integrity_error` |
| `POST /v1/documents/{id}/revisions` | 201: the next revision | 404; 409 `revision_unchanged`, `revision_conflict`; 413; 422 |
| `POST /v1/documents/extractions` | 201: the extraction summary | 403 `live_inference_disabled`; 404 `document_not_found`; 409 `replay_recording_missing`, `recordings_unusable`; 422 `invalid_request`, `invalid_documents`, `repeated_document`, `unknown_recording_set`, `model_not_allowed`, `unsupported_configuration`; 503 `live_extraction_unavailable` |
| `GET /v1/documents/extractions?document_id=&limit=&offset=` | 200: a page of extraction summaries with their reviews, newest first | 422 `invalid_request` |
| `GET /v1/documents/extractions/{id}` | 200: summary, review-required fields, provenance, reviews | 404 `extraction_not_found` |
| `GET /v1/documents/extractions/{id}/comparisons?review_required=&limit=&offset=` | 200: a page of comparisons | 404; 500 `integrity_error` |
| `POST /v1/documents/extractions/{id}/review` | 201: the stored review, ready or not | 404; 409 `stale_extraction`, `legacy_extraction`; 422 `invalid_review_decisions`; 500 `integrity_error` |
| `POST /v1/validate` | 201: the persisted run | see below |
| `GET /v1/runs?…` | 200: run summaries, newest first | 422 `invalid_request`, `invalid_filter` |
| `GET /v1/runs/{run_id}` | 200: the persisted run | 404 `run_not_found`; 500 `corrupt_run` |
| `GET /v1/runs/{run_id}/findings?…` | 200: a page of findings | 404; 422; 500 `corrupt_run` |
| `GET /v1/runs/{run_id}/export?format=` | 200: a JSON or CSV file | 404; 422 `unsupported_export_format`; 500 `corrupt_run` |
| `GET /v1/backends` | 200: orchestrators, providers, classification models with their adapters, and backends, installed, enabled and available; the policy compiler's recording sets and live extraction; ruleset permissions | 503 `core_configuration_unavailable` |
| `GET /v1/taxonomy` | 200: the catalog's vocabularies and hashes | 503 `taxonomy_unavailable`, `core_configuration_unavailable` |
| `POST /v1/evaluations/datasets` (multipart) | 201: a new immutable version; 200: these exact bytes, already stored | 413 `dataset_too_large`; 415 `unsupported_dataset_format`; 422 `invalid_dataset`, with each problem's location |
| `GET /v1/evaluations/datasets?name=&limit=&offset=` | 200: versions, newest first, without content | 422 |
| `GET /v1/evaluations/datasets/{id}?include_cases=` | 200: the version, and its canonical cases on request | 404 `dataset_not_found`; 500 `integrity_error` |
| `POST /v1/evaluations` | 201: the queued job; 200: the job this `idempotency_key` made | 403 `live_inference_disabled`, `preview_disabled`; 404; 409 `ruleset_not_published`, `idempotency_conflict`; 422 `live_confirmation_required`, `budget_required`, `budget_zero`, `receipt_limit_exceeded`, `live_only_field`, `model_not_allowed`, `backend_not_allowed`, `unsupported_configuration`, `unknown_replay_set`; 503 `live_inference_unavailable` |
| `GET /v1/evaluations?dataset_id=&ruleset_id=&backend=&model=&status=&execution_mode=&created_from=&created_to=&limit=&offset=` | 200: summaries, newest first, with key metrics and new spend | 422 `invalid_filter` |
| `GET /v1/evaluations/{id}` | 200: status, progress, configuration, spending by model, backend and purpose | 404 `evaluation_not_found` |
| `GET /v1/evaluations/{id}/metrics` | 200: the stored metrics, or provisional ones while running | 404 |
| `GET /v1/evaluations/{id}/results?receipt_id=&classification_correct=&category=&disposition=&error=&uncertain=&limit=&offset=` | 200: each receipt's core result, answers, scoring and spend | 404; 422; 500 `corrupt_result` |
| `GET /v1/evaluations/{id}/export?format=` | 200: a JSON or CSV file | 404; 409 `evaluation_not_finished`; 422 `unsupported_export_format` |
| `POST /v1/evaluations/{id}/cancel` | 200: cancelled, or cancel_requested | 404; 409 `not_cancellable` |
| `POST /v1/evaluations/compare` | 200: completed runs side by side, and whether they are comparable | 404; 409 `evaluation_not_completed`; 422 |

Any endpoint can also return 503 `database_unavailable`. Every error has one shape, with no traceback, prompt, credential or receipt text:

```json
{"error": {"code": "ruleset_not_published", "message": "ruleset rs_... is draft: validate against a version published for production, or ask for a preview", "details": {"status": "draft", "publication_mode": null}}}
```

| `POST /v1/validate` error | Status | When |
|---|---|---|
| `invalid_request` | 422 | The body breaks its schema. This includes an unknown field (such as a credential), a non-boolean `context_complete`, NaN, a repeated key, or a body that is not JSON |
| `invalid_receipts` | 422 | Core ingestion or admission refused a receipt |
| `ruleset_not_found` | 404 | No such version for this company |
| `ruleset_not_published` | 409 | The version is not published for production, and the request is no preview |
| `preview_disabled` | 403 | A preview where the deployment allows none |
| `backend_not_allowed`, `model_not_allowed`, `unsupported_configuration` | 422 | The deployment or the core refuses the classification choice |
| `unknown_replay_set`, `replay_set_required`, `replay_set_not_applicable` | 422 | |
| `live_inference_disabled` | 403 | `execution_mode: live` without `ALLOW_LIVE` |
| `live_inference_unavailable` | 503 | Live mode allowed, but the core cannot start it |
| `replay_recording_missing` | 409 | A needed answer is not recorded |
| `provider_failure` | 502 | A provider error escaped the core |
| `evidence_unverifiable`, `integrity_error`, `validator_error` | 500 | |

## Example requests

From `backend/`, with the stack running. The recording set `live` holds model readings of `data/policy-docs`. They leave four fields for a reviewer.

### From uploaded documents

```sh
API=localhost:8000

# Upload the nine policy documents, then extract the exact revisions just stored
for f in ../data/policy-docs/*.md; do jq -n --arg fn "$(basename "$f")" --rawfile t "$f" '{filename: $fn, content: $t}' \
  | curl -s -X POST $API/v1/documents -H 'content-type: application/json' --data-binary @- | jq -c '{document_id, revision}'; done | jq -s . > refs.json
jq '{documents: ., recording_set: "live"}' refs.json \
  | curl -s -X POST $API/v1/documents/extractions -H 'content-type: application/json' --data-binary @- > extraction.json
X=$(jq -r .extraction_id extraction.json); jq '{status, blockers, needs: [.review_required[] | {rule_id, field, status}]}' extraction.json

# Review the four fields with the core's decisions
jq --arg q "is a **high** exception, regardless of stated business purpose" '{actor: "r.eviewer", extraction_sha256,
    decisions: [.extraction_sha256 as $x | .review_required[] | {comparison_id, extraction_sha256: $x, reviewer: "r.eviewer"}
      + (if .rule_id == "TECH-01" then {action: "override", value: $q, rationale: "the quote the executable rule signs"} else {action: "accept_regex"} end)]}' extraction.json \
  | curl -s -X POST $API/v1/documents/extractions/$X/review -H 'content-type: application/json' --data-binary @- > review.json
jq '{review_id, ready_for_approval, remaining, issues}' review.json

# A candidate version, then the lifecycle (the lifecycle review reuses the prepared decisions)
RS=$(jq -n --arg x $X --arg r $(jq -r .review_id review.json) '{name: "from-documents", extraction: {extraction_id: $x, review_id: $r}}' \
  | curl -s -X POST $API/v1/rulesets -H 'content-type: application/json' --data-binary @- | jq -r .ruleset_id)
curl -s -X POST $API/v1/rulesets/$RS/submit  -H 'content-type: application/json' -d '{}' | jq .status
curl -s -X POST $API/v1/rulesets/$RS/review  -H 'content-type: application/json' -d '{"actor": "r.eviewer", "decision": "accepted"}' | jq .status
curl -s -X POST $API/v1/rulesets/$RS/approve -H 'content-type: application/json' -d '{"actor": "a.pprover"}' | jq .status
curl -s -X POST $API/v1/rulesets/$RS/publish -H 'content-type: application/json' -d '{}' | jq .status
```

A live extraction, where the deployment allows it, sends this body instead:

```json
{"documents": [...], "execution_mode": "live", "extraction_model": "anthropic/claude-haiku-5.5", "temperature": 0}
```

### Documents compiled inline

```sh
API=localhost:8000

# 1. A draft: compile the nine policy documents
for f in ../data/policy-docs/*.md; do jq -n --arg id "$(basename "$f" .md)" --arg fn "$(basename "$f")" --rawfile c "$f" '{document_id: $id, filename: $fn, content: $c}'; done \
  | jq -s '{name: "policies", compile: {documents: ., recording_set: "live"}}' \
  | curl -s -X POST $API/v1/rulesets -H 'content-type: application/json' --data-binary @- > draft.json
RS=$(jq -r .ruleset_id draft.json); jq '{version, status, artifact_sha256, compilation: .compilation.blockers}' draft.json

# 2. Submit it
curl -s -X POST $API/v1/rulesets/$RS/submit -H 'content-type: application/json' -d '{"actor": "a.uthor"}' | jq .status

# 3. Review: one decision per field that needs one. TECH-01's quote takes the one the executable rule signs.
curl -s "$API/v1/rulesets/$RS/comparisons?review_required=true" \
  | jq --arg q "is a **high** exception, regardless of stated business purpose" '{actor: "r.eviewer", decision: "accepted",
      decisions: [.extraction_sha256 as $x | .comparisons[] | {comparison_id, extraction_sha256: $x, reviewer: "r.eviewer"}
        + (if .rule_id == "TECH-01" and .field == "quote" then {action: "override", value: $q, rationale: "the quote the executable rule signs"} else {action: "accept_regex"} end)]}' \
  | curl -s -X POST $API/v1/rulesets/$RS/review -H 'content-type: application/json' --data-binary @- | jq '.review | {decision, review_sha256}'

# A rejection instead: {"actor": "r.eviewer", "decision": "rejected", "reason": "..."}

# 4. Approve, and 5. publish
curl -s -X POST $API/v1/rulesets/$RS/approve -H 'content-type: application/json' -d '{"actor": "a.pprover"}' | jq '{status, approval_reference}'
curl -s -X POST $API/v1/rulesets/$RS/publish -H 'content-type: application/json' -d '{"actor": "a.pprover"}' | jq '{status, publication_mode}'

# Versions and their history
curl -s "$API/v1/rulesets?name=policies" | jq '.items[] | {ruleset_id, version, status}'
curl -s "$API/v1/rulesets/$RS" | jq '[.transitions[] | {from_status, to_status, actor, reason}]'

# 6. Validate two receipts against the published version (replay, the default)
sed -n '13,14p' ../data/receipts.jsonl | jq -s --arg rs "$RS" \
  '{ruleset_id: $rs, context_complete: true, receipts: map(del(.labels, .notes) | .line_items |= map(del(.labels)))}' \
  | curl -s -X POST $API/v1/validate -H 'content-type: application/json' --data-binary @- \
  | jq '{run_id, ruleset, results: [.results.results[] | {receipt_id, disposition, exceptions: [.exceptions[].rule_id]}]}'
```

Two other ways to create a version:

```json
{"name": "policies", "based_on_version": 1, "compile": {"documents": [...], "recording_set": "live"}}
{"name": "uploaded", "artifact": {"schema_version": "1", "ruleset_version": "v1", "rules": [...]}}
```

The first records an edit of version 1. The second is an uploaded artifact, which can only ever be previewed.

To choose a classification, add `classification` to the validate body, for example `{"backend": "jev", "model": "typesafe/jev-1.14", "replay_set": "jev"}`. The LangGraph replay set covers RCP-011, RCP-017, RCP-022, RCP-023 and RCP-028.

## Replay and live safeguards

- **Replay is the default.** A replay request reads one named, server-side set of recordings. A client names a set, never a path. Replay never calls a model, and a missing answer is a 409, never answered live. Only an explicit live extraction calls the compiler's model, and it needs the same two keys and an allowed model.
- **Live classification needs two keys:** `EXPENSE_API_ALLOW_LIVE=true` and `"execution_mode": "live"` in the request. A live request may choose a model only from its role's allowlist: `ALLOWED_CLASSIFICATION_MODELS` or `ALLOWED_TRANSLATION_MODELS`, each `ALLOWED_MODELS` where unset.
- **No credentials over HTTP.** Request bodies refuse unknown fields. The provider key comes from the environment, and no version or run stores it.
- **Core behaviour is untouched.** LangGraph routing, retries, call budget and reflection run as the core defines them.
- **Requests are isolated.** Each request builds its own validator and compiler from frozen settings.

## Security limitations

- **`actor` is a claim, not an identity.** Every transition records it as `{"claimed": ..., "verified": false}`. The approval's `approved_by` is the claimed actor. The core says approved_by must be authenticated by the caller, and here it is not.
- **No separation of duties is enforced.** Nothing stops one claimed actor from submitting, reviewing, approving and publishing the same version.
- **Server trust.** An approval is verified against the server's recordings, rule logic and field catalog. Whoever controls those controls what can be approved.
- There is no rate limiting.

## MongoDB

| Collection | Indexes |
|---|---|
| `rulesets` | unique `(name, version)`; `(created_at desc)`; `(name, status)`; `artifact_sha256` |
| `runs` | `(created_at desc, _id desc)`, and the same for `ruleset.ruleset_id`, `summary.classification_backend` and `execution_mode` ; `(ruleset.name, ruleset.version, created_at desc)`; and from Phase 1, `(created_at desc)`, `ruleset.ruleset_id` and `created_at desc` |
| `documents` | unique `(document_id, revision)`; `(created_at desc)`; `(filename)` |
| `extractions` | `(created_at desc)`; `extraction_sha256`; `(documents.document_id)` |
| `extraction_reviews` | `(extraction_id, created_at)` |
| `evaluation_datasets` | unique `(name, version)`; `(created_at desc, _id desc)`; `(name, original_sha256)` |
| `evaluations` | unique `(idempotency_key)`; `(status, created_at, _id)`, the queue; `(status, lease.expires_at)`; `(created_at desc, _id desc)`, and the same after each history filter |
| `evaluation_results` | unique `(evaluation_id, index)`; `(evaluation_id, receipt_id)` |
| `evaluation_usage` | `(evaluation_id, sequence)`; `(evaluation_id, receipt_id)` |

One `documents` record is one revision: its exact text, `content_sha256`, filename, title, source and creation time. An extraction holds the core's `ExtractionResult` as `result_json`, with `result_sha256`. A review holds the core's `ReviewResult` the same way. Each record is written once. The text is stored directly, since policy documents are kilobytes, and an extraction snapshot is about 350 KB. No GridFS or object store is needed.

One `rulesets` document is one version. Its fields map to the lifecycle's terms as follows:

| Field | Holds |
|---|---|
| `_id` | `ruleset_id` |
| `artifact_json`, `artifact_sha256` | The compiled artifact and its content hash |
| `source` | Source metadata |
| `compilation` | The documents, recording set, reading kind, extraction and output hashes, blockers, and every comparison as JSON text |
| `review` | Review metadata: decision, claimed reviewer, reason, policy, the core's decisions, `review_sha256` |
| `approval_json`, `approval_reference`, `approval_mode` | The core's approval record, its hash, and what it authorizes |
| `publication_mode` | How the version was published |
| `created_at`, `submitted_at`, `approved_at`, `published_at` | Timestamps |
| `transitions` | The audit trail |

Each transition records:
- the from and to statuses
- the time
- the claimed actor
- the content hash
- the approval reference
- the reason

A Phase 1 version lacks the lifecycle fields; it reads as a draft with none set, so no data migration is needed.

Timestamps are UTC, at MongoDB's millisecond precision. Artifacts, approvals, comparisons and run results are stored as JSON text, so they read back exactly.

## Tests

```sh
bash test.sh -q                                                # the complete backend suite; disposable MongoDB via Docker
.venv/bin/python -m pytest -q                                   # without Docker: real-MongoDB tests skip unless a test URI is set
cd .. && .venv/bin/python -m pytest -q                          # the core regression suite, in the core's own venv
EXPENSE_API_TEST_MONGO_URI=mongodb://127.0.0.1:27017 .venv/bin/python -m pytest -q tests/test_real_mongo.py   # against a real, disposable MongoDB
```

The evaluation tests are `test_evaluation_*.py`, with their cases in `tests/cases/evaluation_*.yaml`. Live evaluations run against `FakeOpenRouter` (`tests/evaluation_support.py`), which answers the core's real requests with chosen usage. `test_real_mongo.py` also races eight workers for twenty jobs on a real MongoDB.

The tests use mongomock and the recorded readings and answers. Lifecycle tests use `evaluation/compiler/live`, which holds model readings, and `tests/fixtures/llm_responses`, which holds simulation readings. Cases are YAML in `tests/cases/`. An autouse guard fails any test that opens a connection beyond loopback, so no test can reach a paid provider.

## Known limitations

- Without live extraction, a document can be extracted only if a recording set has a reading of its exact text. An edited document has none until it is extracted live. Live extraction calls a paid model.
- One document alone builds no candidate. The rule logic, `rules/rule_logic.yaml`, needs the clauses of every policy it covers, so a candidate needs the full set of documents.
- Only Markdown in the core's policy-document format, sent as JSON. No PDF or file upload yet.
- An extraction needing no review cannot be shown with the recordings at hand: both sets leave fields for a reviewer.
- A compiled version's evidence is checked against the deployment's policy documents, which come from the core's `config.yaml`. Documents that differ from them fail the evidence check.
- A review needs one decision per field that awaits one. A field whose regex reading is empty needs `accept_llm` or `override`, as the core requires.
- Failed validations are not persisted.
- Findings are filtered in memory from one stored run. That is fine at this scale, where a run holds at most `MAX_RECEIPTS_PER_REQUEST` receipts. A much larger run would want a findings projection.
- Runs stored before this phase have no summary: no backend filter finds them, and their counts are null (see Run history).
- Validation is synchronous. Evaluations run in the background: see [EVALUATIONS.md](EVALUATIONS.md#limitations) for their limitations, among them that live LangGraph evaluation is refused, as the core's public API cannot meter it.
- Pagination uses offset and limit.
- See [Security limitations](#security-limitations).

For existing databases, recordings and approval identities, see [Single-company upgrade](SINGLE_COMPANY_MIGRATION.md).
