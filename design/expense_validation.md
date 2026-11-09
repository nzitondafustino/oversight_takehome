# End-to-end expense validation

Status: implemented Python package. `ExpenseValidator.validate_batch` turns a submitted batch of structured receipts into source-verified policy results. See the [diagram](expense_validation.html), [PNG](expense_validation.png), [rules compilation](policy_compilation.md), and [API reference](../docs/API.md).

## Lifecycle

| Step | Behavior | Code | Output |
|---|---|---|---|
| 1. Configure | Load rules, catalog, taxonomy, and backend settings; validate per-call overrides | `ExpenseValidator`, `PolicyEngine`, `models.settings` | Immutable policy snapshot |
| 2. Ingest | Validate shape and IDs; reject evaluation answers and preclassified raw input in live/replay | `ingestion.loader`, `_admit` | Structured receipts |
| 3. Prepare | Normalize descriptions and optionally translate without altering source receipts | `normalize_receipt`, `translate_description` | `NormalizedReceipt` |
| 4. Classify | Invoke a registered backend, parse output, and finalize shared evidence | `classification.backends`, `core.common.finalize` | `ClassifiedLine` |
| 5. Admit and assess | Check monetary/catalog semantics, build batch context, and assess uncertainty | `engine.inputs`, `money`, `build_context`, `assess_context` | Admitted data and evaluation view |
| 6. Check policy | Run checks/helpers, replacements, approval-reference waivers, and carve-outs in order | `engine.evaluation._report_for` | Findings and unresolved states |
| 7. Decide | Group findings, apply disposition, optional inevitable-violation proof, and invariants | `build_report`, `decide_disposition`, `check_run` | `ReceiptEvaluation` |
| 8. Verify and return | Verify exception citations against source clauses and return sorted typed results | `PolicyEvidence`, `_result` | `BatchResult` and provenance |

Engine admission validates every receipt, including typed objects. Classification attachment also validates its fields. Deterministic mode bypasses preparation and inference but retains engine admission, uncertainty, and evaluation.

## Data authority

| Kind | Examples | Contract |
|---|---|---|
| Supplied source | Description, printed amounts, merchant, employee country, transaction time | Preserve; refuse contradictions rather than repair |
| Deterministic preparation/facts | NFC text, supported monetary derivations, country/entity mapping | Derive from inputs and versioned contracts |
| Model output | Translation, category, recipient type, venue/item attributes | Validate; retain missing and failed states |
| Source-backed evidence | Recipient mentions and policy clause quotes | Keep supporting text and conflicts |
| Policy decisions | Exceptions, review signals, disposition, completeness | Deterministic engine only |
| Evaluation answers | Labels and notes | Exclude from inference and decision inputs |

Evaluation labels are available only in evaluation mode. Production ingestion rejects them; evaluation ingestion keeps them separate from inference and policy inputs.

JSONL parsing uses exact decimals and rejects duplicate keys, invalid JSON numbers, and repeated receipt/line IDs. The facade also accepts mappings and typed receipts without JSONL parsing. Ingestion validates structure; engine admission checks semantics.

## Preparation and translation

Normalization uses NFC, not NFKC, without spell correction or predictions. `NormalizedReceipt` holds unchanged source receipts beside prepared lines in source order. Translation never overwrites the original description.

Translation status is `not_requested`, `skipped`, `translated`, or `failed`. Classifiers use successful translation when present, otherwise normalized source. Output validation does not prove translation fidelity. Line languages are supplied; receipt language in classifier input is inferred from them.

## Classification and recipient evidence

| Backend | Inference | Confidence semantics |
|---|---|---|
| `openrouter` | OpenRouter structured chat completion | Five coarse self-estimates; uncalibrated |
| `jev` | OpenRouter Decisions, `typesafe/jev-1.14`, TypeSafe-only routing | Selected-category probability; distributions and native statistic stay in details |
| `langgraph` | Ordered registered backends; optional bounded reflection | Retained answer's confidence, unless unresolved review withholds it |

Jev asks five bounded questions: category, recipient type, venue type, item kind, and essential toiletry. Parsing checks model identity, complete option distributions, maximal selected choice, and probability sums within tolerance. Attributes require probability strictly above their threshold. Decisions requests never use chat completions or automatic retries.

All backends use `ClassifiedLine` and shared finalization. Restriction flags come from taxonomy. Inference or consistency failure produces an error-only line, never a fabricated category or confidence. Classifier inputs exclude submitted expense type and policy-decision facts so independent checks remain meaningful.

Recipient detection uses conservative patterns in descriptions and available translations, not merchant context. Preserve source-relative spans, spelling, and conflicts. A name alone never establishes type. A detected recipient without a safe complete name or type remains unidentified. Conflicting identities must not drive per-recipient totals. Known product exclusions and service-fee behavior retain their existing meanings; multilingual patterns are deliberately incomplete.

LangGraph routes by configured fallback conditions without comparing confidence across backends. Keep the last usable answer, otherwise the last abstention or failure. Optional review accepts, revises, or remains uncertain. Failed review retains the answer; unresolved objections withhold confidence for the existing engine uncertainty path. Call/recursion bounds ensure termination. Each candidate, including revisions, is finalized once.

## Uncertainty and deterministic decisions

`assess_context` builds one evaluation view for all later stages, blanking uncertain categories while preserving admitted data. Category-independent rules can still run. Missing secondary fields request review where structured rule requirements need them. There is no backend-specific uncertainty engine.

Policy resolution order is fixed:

**Checks and helpers → replacements → approval-reference waivers → carve-outs → grouped report → disposition and invariants.**

Findings remain atomic through resolution. EXC-04 groups active exceptions while engine audit structures retain replaced, waived, excused, uncitable, and unresolved outcomes. The public result is a projection, not the complete audit history.

EXC-01 maps configured severities to dispositions. Low exceptions do not automatically flag. Incomplete evidence can require review without proving a violation. The inevitable-violation proof can flag when every supported reading of unresolved recipient type flags; it records a project-decision reason, not an invented active exception.

`disposition` and `decided` differ: a flagged receipt may be undecided. Empty findings alone do not establish a clean pass. Missing values are distinct from false and zero.

## Context and concurrency

Evaluate relevant receipts for the company together. Cross-receipt rules can depend on employee, time, trip, and reference scopes. Do not split arbitrarily by receipt or employee. `context_complete=False` leaves affected aggregate decisions open; independent evidence can still flag.

Approval references are supplied claims, not verified external approvals. Transaction times remain merchant-local without invented timezones or midnight.

The validator retains no request state. Per-call settings and model wrappers isolate overrides. Injected providers must support the caller's concurrency model. Source documents must remain aligned with the loaded ruleset because final citation checks read their configured directory.

## Execution, caching, and replay

| Mode | Behavior |
|---|---|
| `live` | Raw receipts; optional cached provider calls; explicit line-level inference errors |
| `replay` | Cached responses only; no provider client; missing required answers raise |
| `deterministic` | Explicit engine inputs; no normalization/inference; all engine checks remain |

Prompts, model identities, request text, and capability-specific prefixes/folders separate cached answers. Reviews/revisions have distinct identities. Later routing can satisfy a line missing an earlier recording; required answers still missing at the end raise without a partial returned batch.

Replay does not authenticate recordings or guarantee future live outputs. Jev is single-attempt, while translation has its own bounded SDK retries. The package has no universal spending reservation or paid-request attempt journal; an integrating service must account for every model path.

## Evidence and results

For each active exception, `PolicyEvidence` checks `source_quote` in the matching document and rule clause. It expands to an unambiguous restrictive sentence only when safe, otherwise keeps the verified quote. Invalid evidence raises `EvidenceError`; no semantic retrieval stage is involved.

| Output | Contents |
|---|---|
| `LineResult` | Source, classification, translation, attributes, errors, backend details |
| `ReceiptResult` | Disposition, `decided`, exceptions, signals, questions, uncertainty, metadata, provenance |
| `BatchResult` | Mode, completeness, receipt-ID-sorted results |
| `to_record()` | The brief's shape exactly, for every model and adapter; the extensions and each adapter's own `classification_details` are in `metadata`, provenance is left out |

Use `model_dump(mode="json")` to retain full typed evidence. Metadata is not a complete provider billing ledger.

`InvalidInputError`, `ConfigurationError`, `ReplayAnswerMissing`, and `EvidenceError` reject request-wide invalid conditions. Classification errors stay on affected lines and reach engine uncertainty without invented categories.

## Evaluation and regression

Scoring keeps labels separate and uses the unchanged engine. Prediction sources are `actual`, `oracle`, `synthetic`, and `none`; only authentic actual predictions support model-quality claims. A provenance label alone is not proof.

Compare category/attribute behavior, active exceptions, disposition, uncertainty, and `decided` on pinned datasets and rulesets. Separate golden receipts, isolated robustness lines, and contextual receipts. Report denominators, missing predictions, model identities, and context limitations. Confidence is not calibrated across backends.

Strict evaluation rejects missing orchestration recordings even when fallback answered, so replay gaps cannot silently change the measured model. Scoring modules have no I/O; CLI wrappers load inputs and can initiate live inference when selected.

Offline entry points include `expense_validator.offline_eval`, `expense_validator.run_receipts --replay DIR`, and `expense_validator.classification.robustness --check`. Tests cover ingestion, normalization, classification, engine, facade, citations, and evaluation using replay or fake providers. Regression stability is not proof of live provider reliability.

## Package limits

The package requires structured receipts and supported executable rules. OCR, document conversion, durable jobs, notifications, authenticated ownership, AWS services, and ruleset activation belong to the integrating backend.

## Monetary input contract

Written for: anyone reading or changing how amounts are admitted. Implemented in `src/expense_validator/engine/money.py`, version 1 of `input_contract` in `rules/fields.yaml`.

### Which values come from which

| Field | Kind | Source |
|---|---|---|
| `quantity`, `unit_price` | Printed, authoritative | `data/README.md` |
| `amount` | Printed, must equal `quantity` x `unit_price` | `data/README.md` states this |
| `currency`, `fx_rate_to_usd` | Printed, authoritative | `data/README.md`: "Multiply an amount ... by this to get USD" |
| `total_amount` | Printed, authoritative | `data/README.md` |
| `amount_usd`, `total_amount_usd` | Derived, never trusted alone | `data/README.md`: "converted" |
| `itemization_complete` | **Submitted metadata**, a project extension | Not in the data dictionary. Not evidence: see below |

### States

Each comparison ends in one state. A state is about the input. It is not a disposition, and it decides no policy.

| State | Meaning | What happens |
|---|---|---|
| `reconciled` | Compared, and they agree | Rules read the values |
| `contradictory` | Compared, and they disagree | Admission refuses the receipt. Nothing is repaired |
| `unverified` | What would show it is missing | Accepted. See below |
| `not_applicable` | Nothing to compare | Accepted |

`ReceiptReport.monetary` holds the states for `fx_rate`, `line_arithmetic`, `line_conversion`, `total_conversion` and `line_sum`, with the contract version.

A rate with no currency verifies nothing, as what it converts from is not known. `line_conversion` is `reconciled` only if every line was compared.

**An unverified USD value** is kept in the record and blanked in the view rules read, so a rule that needs it stays open, exactly as when no value was supplied. No rate is ever assumed. DOC-04 is unchanged.

**Unverified line sums** leave the receipt undecided, with the reason `lines_do_not_account_for_total`. With nothing else against it, that is a `review`. A confirmed exception still flags.

### Itemization: the decision

**The source contract does not guarantee that lines add up to the total.** `data/README.md` never says so, though all 32 benchmark receipts do, exactly. Taxes, gratuities and fees have no fields of their own: the taxonomy makes them line categories (`lodging`, `service_fee`), and a discount is a negative line, so each is in the sum once.

| `itemization_complete` | Lines add up | Lines do not add up |
|---|---|---|
| `true` | `reconciled` | **Refused** with the receipt ID, expected total, submitted total and discrepancy |
| absent | `reconciled` | `unverified`: undecided, never a clean pass |
| `false` | `unverified` | `unverified` |

Non-itemized receipts are `not_applicable`, as they have no breakdown. Claiming complete itemization on one is refused.

**This is a project decision on an ambiguity, not source policy.** The alternative, refusing every mismatch, would reject real receipts with tax not itemized as a line. Ignoring every mismatch left a $500 total with one $5 line as a clean pass.

#### What `itemization_complete` is, and is not

**It is submitted metadata: a claim by whoever sent the receipt. It is not independent evidence that a receipt is complete, and nothing verifies it.**

- **It can only add scrutiny.** `true` can get a receipt refused, and `false` leaves it undecided. Neither makes a receipt decided, or better than it would be with the field absent. So a false claim gains nothing, and the engine does not need to trust it. Tests check this.
- **`reconciled` means the amounts given agree, not that nothing was left off.** A total and lines that both leave out the same item agree. Catching that needs the receipt itself, which is outside this engine.
- **`true` on lines that add up changes nothing,** as such lines are reconciled anyway.

### Tolerances

With h half the currency's smallest unit, from `minor_units`:

| Check | Allowed difference | Why |
|---|---|---|
| Line arithmetic | h + quantity x min(h, half the unit price's last digit) | The amount is rounded by at most h. The true unit price is within half its last printed digit of the printed one, and quantity multiplies that. Also never more than h per unit |
| Line sum | h x (lines + 1) | Each line and the total are each rounded by at most h |
| USD value | `usd_tolerance`, half a cent | Rounding to a cent. The data dictionary says this rate is the one used |

The arithmetic bound replaced `h x (quantity + 1)`, which it equals for prices printed to the currency's unit. It only ever narrows that, for prices printed more finely. At quantity 1000 and `1.00` a difference of up to 5.005 is still accepted, because a true price of 1.004 prints as 1.00. That is what the printed values can show, and is a limit of this check.

Arithmetic is exact. The default `Decimal` context keeps 28 digits, so a large enough difference could round to nothing. Here it keeps 120 and raises rather than rounds, and amounts are limited to below 10^15 and 10 decimal places.

With no currency, the coarsest unit, 0 places, is used, as a contradiction must hold in any currency it could be. A currency not in `minor_units` is refused as unsupported, not as contradictory.

### Open questions

These are decisions someone may wish to revisit.

1. **Should `itemization_complete` be required?** A submitter can leave it out. That only ever makes a mismatch `unverified` rather than refused, so it cannot reach a clean pass.
2. **Net-negative receipts.** The existing contract refuses a negative total, so a receipt of only refunds cannot be admitted with its true total. With a total of zero it is admitted, and undecided, as its lines do not add up.
3. **`decided` and an unverified USD value no rule reads.** `decided` says every rule was evaluated. A non-USD receipt with no rate and no USD-reading rule in play is `decided`, with DOC-04 flagging and `report.monetary` showing `unverified`. A consumer wanting to know about the conversion must read `report.monetary`, not `decided`.
