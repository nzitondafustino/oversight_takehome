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

See [policy extraction modes](POLICY_EXTRACTION.md) for full, incremental and document-removal runs, automatic ID resolution, and publishing their new versions.

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

### Describing documents

Each document has an optional profile, the same for all its revisions: a `category` (`meals_entertainment`, `gifts_awards`, `approvals_exceptions`, `travel_regional`, `receipts_documentation`, `merchant_restrictions` or `technology_equipment`; `null` is unclassified), a `description`, and a `scope` of `regions`, `entities` and `expense_types`. It may be given at upload, and is changed with `PATCH /v1/documents/{id}/profile` (`actor` required; only the fields given change). It is kept in `document_profiles`, apart from the revisions: changing it never touches a revision's text, hash or S3 object, and a document never described, such as one stored before profiles existed, is simply unclassified. A category is chosen by a person, never inferred from a filename. Documents and their summaries carry the profile.

### Duplicates

`PATCH /v1/documents/{id}/profile` with `duplicate_of` marks a document a duplicate of another, and `null` restores it. It is profile metadata only: no revision, approval or S3 object is deleted or changed. It is accepted only when both latest revisions have the same SHA-256 (422 `not_identical` otherwise: documents that are only similar are compared, not merged), never for the document itself (422 `invalid_duplicate`), and one level deep: the kept document cannot itself be a duplicate, and a document others duplicate cannot become one (409 `duplicate_chain`). The library hides a duplicate only while its text is still identical to the kept copy.

### Approving a document revision

Each revision has its own approval: `draft` (every revision starts here, and none inherits another's approval), `needs_review`, or `approved`. `POST /v1/documents/{id}/revisions/{revision}/approval` takes `action` (`submit`: draft → needs review; `approve`: needs review → approved; `return_to_draft`, with a required `note`), `actor`, and `content_sha256`, which must be that revision's hash (409 `revision_mismatch` otherwise). The revision's text is read from storage and checked against its hash before any action. Steps are conditional on the status they expect, so two reviewers cannot both succeed (409 `approval_conflict`), and every step is appended to the revision's history with who (a claim), when, the note and the hash. `GET …/approval` returns the status and history; documents and summaries carry each revision's own `approval`. Document approval is separate from ruleset approval and publication: using a document in a published ruleset never approves it, and records from before approval existed are drafts.

### Where a document's text is stored

Each revision's Markdown is one object in Amazon S3, at `policy-documents/{document_id}/revisions/{revision}/document.md`. The object is private, encrypted server-side (SSE-S3, or SSE-KMS with `EXPENSE_API_S3_KMS_KEY_ID`) and typed `text/markdown; charset=utf-8`, and it is sent with its SHA-256 so S3 checks it on arrival. MongoDB keeps each revision's metadata and a `storage` reference (`provider`, `bucket`, `region`, `key`, and the `version_id` S3 returned), never the text. The API's responses are unchanged, and never include the reference.

- **Writes are create-only** (`If-None-Match: *`): no revision's object is ever replaced. Of two uploads racing for one revision number, at most one writes the object, and the unique `(document_id, revision)` index allows one record: the other is a 409 `revision_conflict`.
- **Partial failures.** A failed S3 write creates no record (503 `document_storage_unavailable`, naming the key and S3's error code, never a credential or the text). A failed MongoDB write deletes the object just written, unless a record may reference it: if MongoDB reports the write failed but made it, the request succeeds; if MongoDB cannot say, the object is kept and logged, and `--orphans` below reports it. An object an earlier failed attempt left is reused when it holds exactly the same text, so a retry succeeds.
- **Reads** take the object from the record's own reference and check it against the recorded SHA-256 and size: a mismatch is a 500 `integrity_error`, a missing object a 500 `document_content_missing`, and a denied or unreachable S3 a 503 `document_storage_unavailable`. Extraction and compilation read documents only through this, so the compiler never knows about S3.
- **Without a bucket** (`AWS_S3_BUCKET` unset), uploads and revisions are a 503, and records written before S3 storage are still read.
- **Least privilege.** The API needs `s3:PutObject`, `s3:GetObject` (and `s3:GetObjectVersion` on a versioned bucket) and `s3:DeleteObject` on `arn:aws:s3:::BUCKET/policy-documents/*` and `arn:aws:s3:::BUCKET/ruleset-documents/*`; `--orphans` also needs `s3:ListBucket` on the bucket. Keep Block Public Access on. Credentials come from boto3's standard chain: in Docker, `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` in the repository's `.env`.

### Moving documents stored before S3

Records written before S3 storage hold their text in MongoDB, and are read and checked as before until moved. Nothing moves them at startup. From the API's environment (in Docker, `docker compose exec api python -m expense_api.migrate_documents`):

```sh
python -m expense_api.migrate_documents            # a dry run: no S3 call, and nothing changed
python -m expense_api.migrate_documents --apply    # move them
python -m expense_api.migrate_documents --orphans  # S3 objects no record references: reported, never deleted
```

Each record's text must match its recorded hash, or it is reported (`hash_mismatch`) and left alone. Its object is written create-only, read back and verified, and only then is the record updated, atomically: the reference is set and the text removed in one update, with the ID, revision, timestamps and every other field unchanged. A run can be stopped at any point and run again: a moved record is no longer selected, and an object an interrupted run wrote is verified and reused, never duplicated. Output names records and keys, never text.

### A compiled version's documents

A version made by `compile` or from an extraction lists the documents it was compiled from in `compilation.documents`: `document_id`, `filename`, `sha256` (the compiler's hash of the text) and, from an extraction, `revision`. Each entry holds `size_bytes` and an S3 `storage` reference to the exact text, never the text:

- From an extraction, an S3-backed revision's own object is referenced where it is, so nothing is copied, and several versions share it. Text sent inline, or a revision not yet moved to S3, is written create-only to `ruleset-documents/{ruleset_id}/documents/{sha256}.md`. The key names the version's ID, fixed before it is stored, because its number is assigned only as it is stored.
- The objects are written before the version is stored. A failed write stores no version and removes what it wrote. A failed or lost version number removes the version's own objects, never a revision's. A store that MongoDB reports failed but made is the request's own.
- Review, approval and publication rebuild the version from its references: each text is read back, checked against its hash, and given to the compiler as before, which must reproduce the stored extraction, ruleset and review hashes. A missing object is a 500 `document_content_missing`, and a changed one a 500 `integrity_error`. The compiler never knows where the text was kept.
- The API shows each document as before, without text or reference.

A version stored before this keeps the text in its entries, and is read and rebuilt as before, until it is moved:

```sh
python -m expense_api.migrate_rulesets            # a dry run: no S3 call, and nothing changed
python -m expense_api.migrate_rulesets --apply    # move them
python -m expense_api.migrate_rulesets --orphans  # ruleset-documents objects no version references: reported, never deleted
```

Each entry's text must match its `sha256`, or the version is reported (`inconsistent_snapshot`) and left exactly as it is, whatever its status. Each text is stored as a new version would store it, read back and compared byte for byte, and only then does one atomic update, made only while the entries are still exactly as read, replace them with their references. The artifact, its hash, the approval, status and timestamps are untouched, so a published version keeps its identity and approval, and rebuilds from the same documents. It can be stopped and run again.

### Extractions

`POST /v1/documents/extractions` names exact `{document_id, revision}` pairs, one revision per document. Together they are the policy candidate. The backend loads those revisions and passes their original text to `PolicyCompiler.extract` as `PolicyDocumentInput`s. It never substitutes the repository's policy files.

| `execution_mode` | Reads | Needs |
|---|---|---|
| `replay` (the default) | A server-side `recording_set` by document hash. With `recorded_with`, only readings recorded live with that model and temperature, for this company | Nothing paid. Text with no recorded reading is a 409 `replay_recording_missing` |
| `live` | The model, with `extraction_model` and optionally `temperature`, `timeout_seconds` and `fresh_reading` | `ALLOW_LIVE`, the model in `ALLOWED_MODELS`, `OPENROUTER_API_KEY`, and a `COMPILER_LIVE_RECORDING_SET` |

A live extraction records each model reading in the live recording set, as the core does. It also stores the model and temperature as `recorded_with`. Every later step then replays exactly those readings, never calling the model again: reviews, the lifecycle's rebuilds, approval and publication.

A live extraction reuses a reading already recorded for the same document text, model and temperature, at no cost. With `fresh_reading: true` it reads every document with the model again instead, and pays for each. Each new reading is recorded under the extraction's own ID, which `recorded_with.reading_id` holds, so earlier readings are kept and every later step replays the new ones. `fresh_reading` is live only: a replay request with it is a 422.

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

Reviewing an extraction also has:

- `GET /v1/documents/extractions/{id}/reviews/{review_id}`: a stored review with its decisions, to continue from it.
- Change requests: `POST /v1/documents/extractions/{id}/change-requests` (`actor`, `comparison_id`, `requested_correction`, optional `comment`) records that one compared field should be corrected, with the rule, field, document and the requester's claimed name. `GET …/change-requests` lists them and `POST …/change-requests/{change_request_id}/resolve` (`actor`, `note`) closes one, once. A request rewrites nothing: no document, reading, review or version changes. While any is open, no version is made from the extraction (409 `changes_requested`, naming each), and no version made from it is approved. It is the reviewer's way to stop an extraction short of approval with a reason attached; the correction itself is a new revision and a new extraction.
- Versions and amendments. A decision names one extraction (`extraction_sha256`), so none is ever applied to another: a new revision is a new extraction, decided again. `GET /v1/documents/extractions/{id}/baseline` sets the extraction beside the latest published version: `rule_changes` (each rule field the draft does differently, `before` published and `after` draft, including a rule only one has as field `rule`), `changed_clauses` (rules whose clause text changed) and `decisions`: each field the published version was approved with a decision for, `unchanged` when the clause text and both readings are the same (the reviewer may save that decision again for this extraction, explicitly) or `changed` (decide again). A draft rule that does not do what the current text states is not a reviewer's error: the extraction has no candidate and its summary lists `discrepancies` (the core's `RuleDiscrepancy`: `stated` beside `compiled`). The correction is `rule_overrides` on `POST /v1/documents/extractions` with an `actor`, each with a `rationale`: compiled in, validated against the text like the rule logic, stored with who and why, and carried into every version made from it, so approval and publication rebuild it exactly. A clause new to the documents that no rule implements is a discrepancy of field `rule` (`stated`: its title, text, severities, review signal and limits); a rule override with `add: true` writes its rule, giving also `scope`, `source_quote` and `params`, and the compiler checks it like any other. An earlier version's approval never covers a new version's artifact.
- `GET /v1/documents/extractions/{id}/comparisons/{comparison_id}/source` (and `GET /v1/rulesets/{id}/comparisons/{comparison_id}/source` for a version's own snapshot) returns the field's document text with its location, verified rather than approximated: the revision's text must match its stored hash and the hash the compiler read, the comparison's `evidence` must match its `clause_hash`, and the evidence must occur exactly once, word for word. Then it gives the clause's `from_line` and `to_line`, the rule's heading line when the text confirms it, and `highlights`: each place inside the clause where a value only one reading has occurs as a whole number or phrase (or, for an unconfirmed field, each number the compiler saw). Otherwise `location` is null and `reason` says why.
- A listed extraction carries `versions` (the ruleset versions made from it, with their status) and `open_change_requests`; a single one also carries `rule_titles`, each candidate rule's title.
- Live extraction may name only a model in `policy_compiler.extraction_models` from `GET /v1/backends`: `EXPENSE_API_ALLOWED_EXTRACTION_MODELS`, or else the allowed models the compiler can send chat completions to, never a decision model such as Jev or a model allowed only for translation.

`GET /v1/documents/extractions` lists extractions, newest first (`limit` up to 100, `offset`, and `document_id` for those that read a revision of one document). Each item is the stored summary with its reviews, without `review_required`: a list never reads the core's stored results, so `GET …/extractions/{id}` gives those fields.

An extraction's `status` is what the compiler found:
- `no_candidate`: no valid ruleset could be built, as with one document alone, since the rule logic needs every policy's clauses
- `review_required`: fields await a reviewer
- `ready_for_approval`: nothing awaits one

It is never "approved": approval belongs to a ruleset version.

### Extractions in the background

`POST /v1/documents/extraction-jobs` takes the same body as `POST /v1/documents/extractions`, checks it at once (the mode, the model and every exact revision; a refusal is the same 4xx and stores nothing), and answers 202 with a job; `GET /v1/documents/extraction-jobs/{job_id}` follows it: `status` (`queued`, `running`, `succeeded`, `failed`), `progress` (`stage` `reading` or `building`, `done` of `total` documents, the `current` document), then `extraction_id`, or `error`. A live job reads the documents one at a time, so its progress names each; the core records every live reading and replays a recording it has, so the full extraction that follows calls no model again. Use it for live extraction: a synchronous request can outlast the frontend proxy's 120 s and fail as a 502 while the API is still working. The job runs in a thread of the API process; one that made no progress for 15 minutes (its process stopped) reports itself failed as `interrupted`, and is never retried automatically. Starting again reuses the readings already recorded, at no cost.

### How each limit is applied

An extraction's detail carries `rule_logic`: for each rule under review, its conditions (`applies_when`, `select`), its params, and `param_uses`, where each param is compared. A declarative rule compares a param in its own conditions (`in_conditions`). A helper compares its params in code, so the core describes each one (`expense_validator.policy.param_use`): what is measured, the comparison, what it applies to, and, for TIME-03, the rule whose threshold it is (`threshold_of`: DOC-01 for `itemization_usd`, GIFT-01 for `gift_cap_usd`). A core test fails if a helper param in the contracts has no description. The descriptions never change what the engine does.

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

## Policy compilation analytics

Policy Management's **Analytics** tab shows spending and performance for the newest 100 stored extractions,
with live/replay filtering and paginated details. New extractions expose an optional `analytics` object on both
list and detail responses: elapsed compilation time (excluding queue wait), provider request time, live/failed
calls, reused document readings, input/output tokens and exact provider-reported USD costs. Incomplete billing
is labelled as a lower bound; missing usage is unknown, never free. Replay and cache hits make no new provider calls.

Metrics are stored beside the signed compiler result, so they do not change extraction, review or approval
hashes. Historical snapshots without telemetry remain readable and are excluded from measured totals. The view
covers completed stored extractions, not failed jobs without a snapshot, human review time, publication or receipt
validation costs. No historical spending or timing is fabricated.

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

### Editing rules: a reviewed override layer

`POST /v1/rulesets/{id}/edits` changes the rules of a compiled version (any status) and stores the result as the next draft version of its name. The edited version never changes. The body is `{"actor", "rationale", "changes": [{"rule_id", "set": {...}}]}`. `set` may change only a rule's `title`, `outcome` (`exception` or `review_signal`), `severity`, `applies_when`, `select`, `waived_by_ref` (the required evidence) and `params` values; a param keeps its name, type and unit, and nothing else (ID, kind, policy document, quote, provenance) can change.

The changes are not written into a stored artifact. They are the core's rule overrides (`expense_validator.compiler.overrides`), applied to `rules/rule_logic.yaml` before the compiler builds the candidate. So the new version is compiled again from the same document revisions and readings (replay, no model call, the same S3 objects), and the compiler validates it exactly as hand-written logic: the schema, the field catalog, the runtime and evidence checks, and the faithful check. A limit, severity or review signal the policy text does not state is refused (422 `rule_edit_invalid`, with each blocker and its `rule_id`); such a change needs a revision of the document instead. A malformed change is a 422 `invalid_rule_change`, a change to what the rule already is a 422 `no_change`, and an uploaded artifact a 409 `not_compiled`.

The overrides are stored in `compilation.rule_overrides`, and every later rebuild (approval, publication) applies them again, so an approval binds the overridden artifact and its hash. The version's `edits` records each changed field before and after, who changed it and why, and the version it was based on; review decisions are carried over as the prepared review. The new version then goes through review, approval and publication like any other.

### Readiness

`GET /v1/rulesets/{id}/readiness` is the publication summary: `next_step` (`submit`, `review`, `approve`, `publish`, `none`), `rules_changed`, `review_remaining`, `validation_errors` (always 0: a stored version was validated), `approval`, `approval_mode`, `can_publish`, and `blockers`, each a plain message with the `rule_id` or `comparison_id` it concerns (an open change request, an undecided field, a rejected review). It changes nothing; `publish` still runs every safeguard itself.

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

### Export: `GET /v1/runs/{run_id}/export?format=json|csv|pdf`

- `json`, the default, is `export_schema: "run-export/2"`: the run as `GET /v1/runs/{run_id}` describes it (ruleset reference, mode, classification, setup, execution), with `receipts` in place of the core's result. Each receipt is `ReceiptResult.to_record()`: exactly the brief's fields (`receipt_id`, `disposition`, `line_items`, `exceptions`, `review_signals`, `metadata`), in the brief's order, whatever model or adapter answered. Everything else (decided, disposition reasons, open questions, uncertainty, signed quotes, translations, attributes and the adapter's own details) is in each receipt's `metadata`; see `evaluation/final/README.md`. It is compact JSON in the fields' own order, UTF-8 not escaped, with a final newline. Version 1 was the API response itself.
- `csv` is one row per finding, in the findings order, with these columns:
  - `run_id`
  - `ruleset_id`, `ruleset_name`, `ruleset_version`, `ruleset_label`, `ruleset_sha256`
  - `classification_backend`, `execution_mode`, `preview`
  - `receipt_id`, `receipt_disposition`, `receipt_decided`
  - `finding_id`, `kind`, `rule_id`, `severity`, `policy_doc`, `line_ids`
  - `explanation`, `evidence`, `source_quote`, `reason`, `reasons`, `field`, `state`, `confidence`
  - `disposition_reasons`, `record`

  Each record field fills its own column, and a field the core's record lacks is empty. The CSV keeps the core's own records, `source_quote` included. `line_ids`, `reasons`, `disposition_reasons` and `record`, the whole core record, are canonical JSON in their cells. A run with no findings is the header alone.
- The CSV is UTF-8 with no byte-order mark, RFC 4180 quoting and `\r\n` line endings.
- Every text cell starting with `=`, `+`, `-`, `@`, a tab or a line break gets a leading `'`, so a spreadsheet never runs it as a formula. The `record` cell and the JSON export keep the text exactly.
- `pdf` is the server's report of the stored run. The UI's Export PDF is different: it saves the view on screen.
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
export EXPENSE_API_REPLAY_SETS='{"openrouter": "../evaluation/final/replay", "jev": "../evaluation/jev/runs/first-run/replay", "langgraph": "../evaluation/langgraph/runs/live-claude-jev-2/replay"}'
export EXPENSE_API_DEFAULT_REPLAY_SET=openrouter
export EXPENSE_API_COMPILER_RECORDING_SETS='{"live": "../evaluation/compiler/live"}'
.venv/bin/uvicorn expense_api.main:create_app --factory --port 8000
```

`../config.yaml`'s folder holds the core's `.env`, from which the core reads `OPENROUTER_API_KEY` in live mode. Live mode stays off unless `EXPENSE_API_ALLOW_LIVE=true`.

## Container deployment

```sh
# from the repository root
cp .env.example .env        # optional: every setting has a safe default
docker compose up --build -d
```

`compose.yaml` is at the repository root; the API's image is built from `backend/Dockerfile` with the root as its context, as it holds the core package too. The API is served at http://localhost:8000/docs, `/openapi.json` and `/health`. Compose runs five services:

- `api`: the FastAPI application
- `worker`: the evaluation worker, `python -m expense_api.worker`, from the same image and with the same settings. It serves no port. It shares the `live-cache` volume with the API, where each live evaluation keeps its answers. `docker compose up --scale worker=2` runs two, as each job is claimed atomically
- `mongo`: MongoDB, on an internal network with no published port, with its data in the `mongo-data` volume
- `frontend`: the React UI, built from `frontend/Dockerfile` and served by nginx at http://localhost:3000, which proxies `/api` to the API and cannot reach MongoDB
- `mongo-express`: a MongoDB browser at http://localhost:8081, for local inspection only: it reaches the whole database, so leave it out of any shared deployment

The image runs without the repository:

- Both packages, `expense-policy-validator` and `expense-policy-validator-api`, are installed into site-packages, from a bind mount that exists only during the build. No source checkout, bind mount or `.env` is needed or present at runtime (`tests/test_container.py`).
- The `compiler-recorded` volume is the live recording set: live extractions' model readings, which every later step replays.
- The image holds the runtime assets:
  - `config.yaml`, the field catalog and the rule logic, in `/srv/core`
  - the taxonomy and the policy documents
  - the three replay sets, in `/srv/replay/*`
  - the compiler's model readings, in `/srv/recordings/compiler/live`
- Any of these folders can instead be mounted read-only over its path. The image has no `rules/ruleset.json`.
- The root `.dockerignore` admits only those files, so `.env`, `.venv`, `.git`, the tests and the labelled receipts never enter the build. The application runs as a non-root user.

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
| `AWS_S3_BUCKET`, not prefixed (or `EXPENSE_API_S3_BUCKET`) | unset | The bucket that holds policy document text. Unset: documents cannot be stored (503) |
| `AWS_REGION`, not prefixed (or `EXPENSE_API_S3_REGION`) | `us-east-1` | The bucket's region |
| `S3_DOCUMENT_PREFIX` | `policy-documents` | The key prefix of document objects |
| `S3_RULESET_PREFIX` | `ruleset-documents` | The key prefix of compiled versions' own document objects |
| `S3_KMS_KEY_ID` | unset | SSE-KMS with this key; unset: SSE-S3 (AES256) |
| `S3_ENDPOINT_URL` | unset | An S3-compatible endpoint, for local testing |
| `S3_TIMEOUT_SECONDS` | `10` | Connect and read timeout of each S3 call, with up to 3 attempts |
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
| `POST /v1/documents/extraction-jobs` | 202: the job, with its progress | 403; 404; 409; 422; 503 |
| `GET /v1/documents/extraction-jobs/{job_id}` | 200: its progress, then its `extraction_id` or `error` | 404 `extraction_job_not_found` |
| `POST /v1/rulesets/{id}/edits` | 201: the next draft version, with its `edits` | 404; 409 `not_compiled`, `compilation_changed`; 422 `rule_edit_invalid`, `invalid_rule_change`, `no_change` |
| `GET /v1/rulesets/{id}/readiness` | 200: the publication summary and its blockers | 404 |
| `POST /v1/rulesets/{id}/publish` | 200, also for a version already published with this approval | 404; 409 `invalid_transition`, `publication_conflict`, `compilation_changed`; 422 `approval_does_not_hold` |
| `POST /v1/documents` | 201: revision 1, with its text | 422 `invalid_request`, `invalid_document`; 413 `document_too_large` |
| `GET /v1/documents?filename=&limit=&offset=` | 200: each document's latest revision, without text | 422 |
| `GET /v1/documents/{id}?revision=` | 200: the revision and its exact text | 404 `document_not_found`; 500 `integrity_error` |
| `POST /v1/documents/{id}/revisions` | 201: the next revision | 404; 409 `revision_unchanged`, `revision_conflict`; 413; 422 |
| `POST /v1/documents/extractions` | 201: the extraction summary | 403 `live_inference_disabled`; 404 `document_not_found`; 409 `replay_recording_missing`, `recordings_unusable`; 422 `invalid_request`, `invalid_documents`, `repeated_document`, `unknown_recording_set`, `model_not_allowed`, `unsupported_configuration`; 503 `live_extraction_unavailable` |
| `GET /v1/documents/extractions?document_id=&limit=&offset=` | 200: a page of extraction summaries with their reviews, newest first | 422 `invalid_request` |
| `GET /v1/documents/extractions/{id}` | 200: summary, review-required fields, provenance, reviews | 404 `extraction_not_found` |
| `GET /v1/documents/extractions/{id}/comparisons?review_required=&limit=&offset=` | 200: a page of comparisons | 404; 500 `integrity_error` |
| `GET /v1/documents/extractions/{id}/baseline` | 200: the extraction beside the latest published version | 404; 500 `integrity_error` |
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

## Edit and delete records

- `PATCH /v1/evaluations/datasets/{id}` edits `description`; changed receipt contents are uploaded as a new version.
- `DELETE /v1/evaluations/datasets/{id}` hides the dataset after its active evaluations are cancelled or finished.
- `PATCH /v1/evaluations/{id}` edits `label`. `DELETE` hides a terminal evaluation; results and provider usage remain stored for history. A deleted evaluation's idempotency key cannot create another paid run.
- `PATCH /v1/documents/extractions/{id}/reviews/{review_id}` accepts the same body as a new review and records revised decisions under a new signed ID. `DELETE` hides an unused review; reviews referenced by ruleset versions are protected.
- `DELETE /v1/rulesets/{id}/rules/{rule_id}` accepts `actor` and `rationale` and creates a new draft with the check inactive (`status: not_evaluable`). It does not rewrite a published artifact. Restore it with a versioned edit setting `status: active`; process directives cannot be removed. Approval and publication remain separate steps.

The frontend exposes these actions in dataset/evaluation detail pages, compilation provenance (saved reviews), and expanded rules. Deletion requires confirmation. Deletes retain tombstones and immutable payloads; normal list/read endpoints hide deleted records, while internal history and worker reads retain their source data. Version numbers are not reused. These are management operations in the existing single-company deployment; authentication is still supplied by the deployment, not this application.


## PDF reports

The UI’s **Export PDF** captures the current rendered view as one continuous PDF page, including Northwind Global styling, charts, metric cards, filters, expanded sections and the current table page. It does not expand hidden details or request a backend report. JSON/CSV export the complete saved data.

For automation, the API also provides structured, paginated PDF reports from saved records:

- `GET /v1/runs/{run_id}/export?format=pdf` (whole batch)
- `GET /v1/evaluations/{evaluation_id}/export?format=pdf&scoring=latest` (or `original`)
- `GET /v1/evaluations/compare/export?evaluation_ids=eval_first&evaluation_ids=eval_second` (2–10 completed runs; first is the baseline)

API reports repeat table headings and paginate long evidence. API comparisons use original stored metrics. Both export methods make no provider calls; active evaluations retain their export restrictions.
