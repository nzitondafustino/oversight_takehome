# Expense Policy Validator

Reads itemized expense receipts, classifies each line, checks the receipt against written policies, and reports exceptions, review signals and a disposition of `flag`, `review` or `pass`. The brief is `validator-brief-senior.html`, and the data is defined in `data/README.md`.

## Commands

Run from this directory.

```sh
.venv/bin/python -m pytest -q                 # all tests under tests/
.venv/bin/python -m pytest -q tests/engine    # the engine only
.venv/bin/python -m pytest -q design          # the diagram script, not part of the default run
PYTHONPATH=src .venv/bin/python -m expense_validator.compiler.build_rules  # rewrites rules/ruleset.json
PYTHONPATH=src .venv/bin/python -m expense_validator.offline_eval           # rewrites evaluation/output: makes no network call
PYTHONPATH=src .venv/bin/python -m expense_validator.classification.evaluate # rewrites evaluation/classification: sends line text to OpenRouter
PYTHONPATH=src .venv/bin/python -m expense_validator.classification.robustness --check   # checks the robustness labels: makes no network call
```

## Layout

| Path | What it holds |
|---|---|
| `src/expense_validator/policy/` | `schema.py` and `vocabulary.py` define rules. `evaluate.py` decides conditions on flat facts. |
| `src/expense_validator/compiler/` | Offline: turns the policy docs and `rules/rule_logic.yaml` into `rules/ruleset.json`. |
| `src/expense_validator/engine/` | Runtime: `models.py`, `inputs.py`, `facts.py`, `helpers/`, `checks.py`, `replacements.py`, `approvals.py`, `carveouts.py`, `money.py`, `uncertainty.py`, `report.py`, `inevitable.py`, `evaluation.py`. |
| `src/expense_validator/ingestion/` | Runtime: turns raw records and JSONL into a validated `Receipt`, with answers returned apart in evaluation mode. Imports `engine.models`, and nothing in the engine imports it. `offline_eval` loads its data through it. |
| `src/expense_validator/normalization/` | Runtime: prepares line descriptions for any classifier. `text.py` cleans text deterministically, `translation.py` holds the optional translators, `receipt.py` has `normalize_receipt`, and `models.py` the results. Predicts nothing. |
| `src/expense_validator/classification/` | Runtime: `classifier.py` reads each normalized line into one taxonomy category, a coarse confidence and any attributes the line states, with one request. It takes `restricted` from `data/category-taxonomy.md`. Confidence is one of five levels and is the model's own estimate, not a calibrated probability. A null attribute is missing, and the engine decides what that means. A recipient name must be in the line's text, and an attribute must suit the category. A line that fails has an error and nothing else: never `other`, and never a stand-in confidence. The request holds the line's deterministically normalized `original_description` (not necessarily character for character as printed) and `description_language`, its `english_translation` when there is one, and receipt context: `receipt_language` (inferred from the lines: the language of more than half of them, else null; not a verified receipt attribute), merchant, MCC description, itemization, quantity, unit price and currency. The data has no product name apart from the description, so none is sent. `submitted_expense_type` and the facts rules decide on (attendees, trips, references) stay out, so DOC-03 stays an independent check. `evaluate.py` and `robustness.py` are the only modules here that read answers, and production code must not import them. |
| `src/expense_validator/offline_eval/` | Offline: scores classifications through the engine, and changes none of its semantics. Only predictions of kind `actual` are ever classifier performance. |
| `evaluation/robustness/` | A developer-authored challenge set, not an independent benchmark. A run in `runs/` is written once, and `first-run` is the record of the prompt before any change. See its README. |
| `evaluation/` | Its inputs, and in `output/` its metrics, per-receipt results and report. |
| `rules/fields.yaml` | The catalog of fields a rule may read, their allowed values, and the country-to-entity mapping. |
| `tests/cases/` | YAML test cases. See `tests/README.md`. |

Keep the engine's responsibilities apart:

- `models.py`: immutable validated data, with no rule logic.
- `inputs.py`: admission and vocabulary validation.
- `money.py`: whether a receipt's amounts agree with each other. Generic: it reads no rule, category or threshold.
- `facts.py`: deterministic flat facts, with no policy decisions.
- `helpers/`: the named helpers, and the `ReceiptContext` for those that read other receipts. Import from the package, not its modules. Inside it, `common.py` is what helpers produce, `context.py` is `ReceiptContext` and `HelperJob`, `registry.py` dispatches by name, and each policy domain has a module: `meals.py`, `gifts.py`, `healthcare.py`, `travel.py`, `documentation.py`. `splits.py` (TIME-03) is the only one that reads other domains, and only their public names. `tests/cases/engine/helpers_package.yaml` holds these boundaries.
- `checks.py`: candidate findings from all active check rules, plus what it could not decide.
- `replacements.py`: which candidates another rule replaced, at the level the ruleset declares.
- `approvals.py`: which active findings an approval reference waives, by EXC-02's mapping.
- `carveouts.py`: which active findings a carve-out excuses, rejects or leaves undetermined.
- `uncertainty.py`: EXC-03 signals, and the view of each receipt the rules read.
- `report.py`: EXC-04 groups, with every source finding kept whole.
- `inevitable.py`: the proof that every reading of an unresolved recipient type flags a receipt. Adds no finding.
- `evaluation.py`: `evaluate_receipts`, the public entry point and the only source of authoritative results. The stage functions are for unit tests.
- `policy_engine.py`: `PolicyEngine` holds an immutable configuration snapshot and delegates every call to `evaluate_receipts`, with a fresh context and no retained customer state.
- `config_validation.py`: checks runtime catalog and rule compatibility without policy-source documents or receipt evaluation.

## Working rules

- **One module at a time.** Build only the module asked for, then stop so it can be reviewed before the next one starts.
- **The policy engine is deterministic code,** never an LLM call. Thresholds live in rule params, never in engine code.
- **Test cases go in YAML** under `tests/cases/`, loaded with `load_cases`. Do not hardcode case tables in Python. Python holds thin runners and checks that loop over the real data, catalog or ruleset. A new kind of case needs a section in `tests/yaml_cases.py` and a row in `tests/README.md`.
- **Answers are for evaluation only.** `labels`, `line_items[].labels` and `notes` in `data/receipts.jsonl` must never reach the system or a prompt. Tests may use them to measure results.
- **Do not commit** `.env` or the API key. Other settings are in `config.yaml`.
- **Sign-off stays out of the compiler.** It is planned as separate code that shows rules and their sources for a developer to confirm.

## Data semantics

- A value of `None` is missing, while `False` and `0` are values. Keep them apart, including in tests, where Python's `False == 0` can hide a bug.
- A comparison against a missing value is false, so `not` of it is true. A false condition does not establish compliance, and missing information is not a violation.
- Money is `Decimal`. A transaction time is local to the merchant with no time zone, and a bare date is refused rather than read as midnight.
- Unknown values are kept as written or refused, never corrected.

## Trust boundaries

- `customer_id` scopes a run, but does not authenticate ownership: receipts carry no owner, so the caller must establish that. Do not claim cross-tenant authorization, or add a tenant field, without trusted ownership information.
- `evaluate_receipts` admits every receipt, even an existing `Receipt`, and takes nothing already worked out, so no caller can skip a step. `check_run` confirms each run holds together before it is returned, and raises if not. It works out no disposition of its own. A directly built `Receipt` is not necessarily admitted, and `facts.py` and `checks.py` do not check this themselves, so never treat a stage function's output as a final result.
- `iter_jsonl` and `ingest_record` are the way into the pipeline for raw data. Only `IngestedReceipt.receipt` goes downstream, and `ground_truth` belongs to whoever scores the results. A raw line carrying a field in `CLASSIFIED_FIELDS` is refused in both modes, as the model would accept it. Ingestion validates shape only: `evaluate_receipts` still admits every receipt. A receipt ID or line ID may appear once in a stream, as predictions are keyed by line ID. `entity` is required at ingestion, as `Receipt` requires it, although `admit_receipt` can derive one left out: deriving stays in admission. An `IngestionError` is safe to log, so a validator on the ingestion models must not put a value in its message.
- `normalize_receipt` takes only a `Receipt`, so pass `IngestedReceipt.receipt`, and refuses one that already has classifier fields. `NormalizedReceipt` holds the receipt unchanged beside its normalized lines: never add normalized or translated text to `Receipt` or `LineItem`.
- Normalized text uses NFC, never NFKC, and is not spell-corrected. Translation goes through the OpenRouter SDK to `translation.model` when `translation.enabled` is set and a translator from `build_translator` is passed, and sends the line text to the model, nothing else. `normalize_receipt` alone does not translate. The compiler's LLM calls are separate and use `llm.model`. Classifiers use a line's `translation` when its status is `translated`, and otherwise read `description_normalized`. Translation is not scored separately, a project decision: `translated` means it passed the checks in `translation.py`. Printed text stays the record.
- `employee_country` decides `entity`. A contradicting entity or an unlisted country is refused. `GLOBAL = US` only is a project decision, because no document lists its countries.
- Attach classifier output with `with_classification` or `with_classifications`, which validate. Never use `model_copy(update=...)` for it.
- A negative receipt total is unsupported input, not a policy violation. Negative line amounts are allowed.
- Printed amounts are authoritative: `quantity`, `unit_price`, `amount`, `total_amount`, `currency`, `fx_rate_to_usd`. `amount` and the USD values derive from them, and one that contradicts them is refused, never repaired: see `money.py` for the tolerances. A missing rate is not a contradiction: no rate is assumed, DOC-04 reports it, and any USD value nothing supports is blanked in the view rules read, so rules needing it stay open. `data/README.md` does not say lines add up to a total: `itemization_complete` can say so, and with it unset, lines that do not add up leave a receipt undecided. Precision and limits are the versioned `input_contract` in `rules/fields.yaml`. See `design/monetary_contract.md`.
- A `ReceiptContext` comes from `build_context`, which admits every receipt. It holds one customer's receipts and must include the receipt being evaluated. `Receipt` has no customer field, so keeping customers apart rests on building one context per customer. `complete=False` means receipts may be missing: a total spanning receipts is then neither confirmed nor cleared, while what rests on one receipt stands.

## Findings

- An item finding targets exactly one line, and a receipt finding may name any number of lines.
- EXC-04 and the answers want one exception per rule listing all its lines. `build_report` does this in separate `ReportGroup`s, and `Finding` stays atomic. Only `report.exceptions` count as exceptions: replaced, waived, excused and uncitable groups are audit history, and unresolved ones are still in question. `report.decided` is the one answer to whether a receipt was fully evaluated. `open_questions` are not violations.
- `run_checks` returns candidates. An APAC receipt gets ALC-01 as well as REG-01 until replacements apply.
- A `CheckResult` is `complete` only when no rule is in `pending_helpers` or `undetermined`. A rule that depends on something not known gives no finding and is listed as undetermined, not passed. Never treat `findings` alone as the whole answer.
- Helpers report what they catch before later steps run, so MEAL-01 still fires on a receipt in a TIME-03 split.
- `resolve_replacements` keeps every candidate, and reports `active`, `replaced` and `possible` apart. A receipt-level replacement needs only an open gate on the replacing rule. A line-level one needs that rule to have caught the line. A finding-level target is only eligible until the finding names it in `Finding.replaces`, which is how TIME-03 records the threshold it was split around. Never parse explanations for this.
- A replacement resting on a pending or undetermined rule is possible, not confirmed: the finding stays active and the result is not complete.
- Each reference kind has its own receipt field: `pre_approval_ref`, `vp_approval_ref`, `manager_approval_ref`, `baggage_delay_ref`. This is a project decision, as EXC-02 and `data/README.md` describe one field. Never read a kind from a reference's format, such as `PA-`. A value is a supplied claim, not an authenticated approval. A baggage-delay reference is for the carve-out step and waives no check.
- The baggage-delay carve-out needs a reference, a stated `baggage_delay_hours` strictly above `min_delay_hours`, as the policy says "more than 24 hours", and a total within the allowance across the employee's receipts sharing that reference. Going over the allowance excuses nothing, as the policy cites every line. A missing duration is undetermined, never assumed. Personal care counts only when `essential_toiletry` is true, as the same category holds massage, and missing is undetermined.

## Disposition

- EXC-01 decides, from the severities in its rule: any exception of a flag severity is `flag`, and low exceptions or review signals are `review`. An EXC-03 signal is `review`. Never make every active exception a flag: a low-only receipt is `review`. There must be exactly one active directive, mapping every severity exactly once.
- A receipt not fully decided with nothing else against it is `review`. This is a project decision, not EXC-01, and its reason says `project_decision`.
- A flagged receipt can still be undecided, so read `decided` as well as `disposition`.

## EXC-03

These are project decisions informed by the ruleset, not requirements in the source policy. Present them that way.

- Read the threshold from the active EXC-03 rule's `min_confidence`. Confidence is per line and means confidence in the category.
- A missing, unknown or low-confidence category, or a missing confidence, raises an EXC-03 signal with the reason `category_uncertain`. Non-itemized `other` lines are exempt. These lines are left out of other item rules, and receipt-level checks stay.
- Secondary fields are uncertain only where structured facts say a rule may need them, with no MCC mapping or description keywords. The signal uses EXC-03 as a documented extension with the reason `secondary_field_uncertain`, and the line stays in ordinary rules.
- `assess_context` is the one place this is decided. Run every later step on its `evaluation` context and each assessment's `evaluation_receipt`: the same lines and totals with uncertain categories blanked, so checks, helpers and carve-outs read one decision. The admitted receipt stays the record.
- Rules that do not read the category, such as ENT-01, TECH-03 and GIFT-04, still run on an uncertain line. The policy says "every other rule", so this narrowing is a project decision.
- `venue_type` and `item_kind` count as needed only on `other` lines, on Client Entertainment or on an expense type DOC-03 says covers medication or technology. A value outside the catalog always raises a signal.
- The engine must account for uncertainty before returning a clean `pass`: use `fully_decided`, not a later step's `complete` alone.
