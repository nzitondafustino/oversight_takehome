# Expense Policy Validator: Python API

```sh
pip install -e .            # Python 3.12+
pip install -e ".[api]"     # adds FastAPI, for examples/fastapi_app.py only
```

```python
from pathlib import Path
from expense_validator import ExpenseValidator

validator = ExpenseValidator.from_config(Path("config.yaml"), mode="replay", cache_dir=Path("evaluation/final/replay"))
batch = validator.validate_batch(receipts, context_complete=True)

for result in batch.results:                  # receipt ID order
    print(result.receipt_id, result.disposition, [e.rule_id for e in result.exceptions])
    record = result.to_record()               # exactly one line of evaluation/final/receipts.jsonl
```

Everything below is importable from `expense_validator`. Models live in `expense_validator.models`, exceptions in `expense_validator.exceptions`.

## Construction

| | |
|---|---|
| `ExpenseValidator.from_config(config_path, *, mode, cache_dir, classification_model, translation_model, classifier_model, translator)` | Reads `config.yaml`, with paths relative to its folder. `cache_dir` defaults to the config's `llm_cache`; `None` means no cache |
| `ExpenseValidator(settings, *, classifier_model, translator, client_factory)` | From explicit `ValidatorSettings`. `client_factory(purpose, backend)`, for LangGraph, returns the OpenRouter client (or one with the same methods) that a node (`classification`), the reviewer (`review`) or a reviser (`revision`) sends through, so a caller can meter every call while the adapters build the requests |

Construction loads the ruleset, catalog, taxonomy and prompt once as an immutable snapshot. Settings that cannot work raise `ConfigurationError` here.

## Modes

| Mode | Model calls | Input receipts | Missing answer |
|---|---|---|---|
| `replay` | None | Unclassified | `ReplayAnswerMissing`; nothing is returned |
| `live` | OpenRouter, or an injected `classifier_model` | Unclassified | Requested, and kept in `cache_dir` if set |
| `deterministic` | None | Already classified | An unclassified line raises EXC-03; it is never guessed |

Replay and deterministic mode take no injected objects and need no key. `classifier_model` is any `complete(prompt, text) -> str`; `translator` is any object with `translate(text, source_language) -> str`. Cache keys hold the configured model ID, so give an injected model its own `cache_dir`, or `None`.

## Models

`classification_model` and `translation_model` are OpenRouter model IDs. Each is taken from the first that sets it: the `validate_batch` call, the validator's `from_config`, then `config.yaml`. The effective IDs choose the clients, both cache keys and `metadata`; `provenance.model_overrides` lists those chosen per request.

```python
validator = ExpenseValidator.from_config(Path("config.yaml"), mode="live", classification_model="anthropic/claude-haiku-5.5")
batch = validator.validate_batch(receipts, context_complete=True, translation_model="tencent/hy-mt2-30b-a3b")
```

- A translation model never enables translation; `translation.enabled` decides, and `metadata.translation_model` is `null` when it is off.
- Replay with a model that has no saved answers raises `ReplayAnswerMissing` and sends nothing.
- `ConfigurationError` for an empty or non-string ID, any choice in deterministic mode, and a choice that an injected object would ignore.
- Every model costs money: a service letting users choose should accept a fixed list, as `examples/fastapi_app.py` does with `EXPENSE_VALIDATOR_MODELS`.

## Classification backends

`classification.backend` is `chat_completions` (the default: an LLM through OpenRouter chat completions) or `decisions` (a decision model, TypeSafe's Jev, through OpenRouter's Decisions API and the official OpenRouter SDK). A backend is an execution adapter, named for its protocol: never a provider or a model. Both call OpenRouter, and the model decides which one it needs (`registry.backends_for`). `registry.ADAPTERS` gives each one's provider and protocol. The earlier names `openrouter` and `jev` (`config.LEGACY_BACKENDS`) are accepted wherever a backend is named and read as these adapters; results record the adapter's own name. Each is taken from the first that sets it: the `validate_batch` call, `from_config`, then `config.yaml`. Choosing a backend other than the config's needs a `classification_model` for it. A failure is never answered by another backend.

```python
validator = ExpenseValidator.from_config(Path("config.yaml"), mode="live", classification_backend="decisions", classification_model="typesafe/jev-1.14")
batch = validator.validate_batch(receipts, context_complete=True)
batch.results[0].line_items[0].classification_details   # Jev's probabilities for every question, its model and usage
```

- Jev answers one request per line: a choice of category from the taxonomy, and a choice for each classified attribute from the field catalog, each with a `not_stated` option. It writes no text: a `recipient_name` comes only from the shared deterministic extraction every classifier ends with.
- A line's `confidence` is the probability Jev gives the chosen category: the model's own number, not proof the category is right. EXC-03 judges it like any other. Jev's `confidence` statistic and every distribution are in `classification_details`, which `to_record()` puts in `metadata.line_items`.
- Jev judges no confidence. An attribute it chooses is passed on with that choice's probability, however low, as `<attribute>_confidence` (in the line's `attributes` too); only `not_stated` leaves it missing. EXC-03 alone judges it, against the same `min_confidence` as the category: below it, the value is left out of the view the rules read and is treated as missing, with a review signal (`low_confidence`) where a rule may need the field. The LLM gives no attribute confidence, so its stated attributes stand.
- A malformed answer, an option that was not offered, probabilities that do not sum to 1, a choice that is not the most probable option, an unreviewed model snapshot, an attribute that contradicts the category, or a provider error fails that line, with no category, which EXC-03 then reviews. Each request is sent once, routed to TypeSafe only, with no retry and no fallback.
- Live mode needs `OPENROUTER_API_KEY` and a model ID starting `typesafe/jev`; it shares the validator's one OpenRouter client. Replay and deterministic modes never create a client. Answers are cached and replayed exactly as LLM answers are, by the same utilities, but in `cache_dir/jev/`: each file is named by the hash of the model, the questions and the line's input, so a changed question never reuses an old answer, and is written whole or not at all. A failed request or an answer that breaks the contract is never cached. Replay reads only those answers, and a missing one raises `ReplayAnswerMissing`.
- A cached answer is the SDK's JSON of the response: model, ID, provider, every answer and the token usage and cost. Fields the SDK does not model, such as `usage.is_byok`, are not kept; nothing reads them.
- Translation, when enabled, still uses OpenRouter, as it does for every backend.
- Each backend's results are saved on their own, in the same per-receipt JSONL: `python -m expense_validator.run_receipts --classification-backend decisions --classification-model typesafe/jev-1.14 --out jev.jsonl` (add `--replay DIR` to answer only from recordings). Comparing backends is not part of the application: compare the saved files separately.

Every classifier ends each line with `finalize`: it reads recipient names from the line's own words (never the merchant), keeps a single name in its original spelling, and marks a line whose words name someone the model did not read as `recipient_type: unidentified`, so EXC-03 asks for review and GIFT-04 stays open. Two different names, or a predicted name the words contradict, are a conflict: the engine gets no name and the `unidentified` type, and `classification_details.evidence_conflict` keeps every mention. A line that breaks the taxonomy, the catalog or the consistency rules, or names a known product such as Dr Pepper as a recipient, becomes an error with no category.

### Adding a backend

A backend is one module in `classification/backends/` that builds a `classification.core.models.Backend` from the shared core, plus one line in `classification/backends/registry.py`. `llm.py`, `jev.py`, `core/`, the orchestrator, the validator and the policy engine stay unchanged: the validator counts, caches (in the backend's own `cache_folder`) and replays every backend's model call alike, and `--classification-backend`, `classification.backend` and a LangGraph order take any registered name. A future decision API, `backends/acme.py`, would look like this:

```python
from expense_validator.classification.core.common import finalize, line_input
from expense_validator.classification.core.models import Backend, ClassifiedLine

def classify_receipt(normalized, complete, settings, vocabulary, only=None):
    lines = []
    for line in normalized.lines:
        if only is not None and line.line_id not in only:  # LangGraph asks about routed lines alone, by ID
            continue
        answer = complete(INSTRUCTIONS, line_input(normalized, line))  # cached and replayed by the validator
        result = ...  # the backend's own parsing into a ClassifiedLine; any failure is ClassifiedLine(line_id, error=...)
        lines.append(finalize(result, line, vocabulary))  # once per line: recipients and the whole-line checks
    return tuple(lines)

BACKEND = Backend(check_model=check_model, prompt=lambda vocabulary: INSTRUCTIONS, cache_key=lambda model, vocabulary: model,
                  cache_folder="acme", connect=connect, classify_receipt=classify_receipt)
```

and in `classification/backends/registry.py`: `"acme": acme.BACKEND`, imported with `from expense_validator.classification.backends import acme, jev, llm`. It must honour `only`, a set of line IDs, by classifying those lines alone, each still in its whole receipt's context, as above: that is how LangGraph routes single lines to it. Its own settings, if any, are a configuration change. `tests/classification/test_classifier.py::test_a_new_backend_needs_only_its_module_and_registration` registers such a stand-in and runs it live, cached and replayed through the unchanged validator and engine.

### LangGraph orchestration

`classification.backend: langgraph` classifies through an ordered list of registered backends. It needs the optional extra: `pip install '.[langgraph]'`; nothing else imports LangGraph. The graph (`classification/orchestrator.py`) has one node, which runs whichever backend comes next, and one routing edge, which reads only each line's `ClassifiedLine`.

Each attempt has an outcome: `accepted` (a category, at any confidence), `no_category` (an abstention, which `finalize` keeps), `error`, or `missing_recording` (replay had no answer). A line goes on only when its outcome is `error`, `missing_recording` or `no_category` and `fallback_on_error` is true. **Confidence never routes a line**: routing has no threshold, and an answer at any confidence, or with none, is accepted where it stands. Only the rules judge confidence, through EXC-03's `min_confidence` in `rules/rule_logic.yaml`. The configured order alone decides the answer kept, and no confidence is compared across backends: a usable answer (a category, no error) replaces any earlier one, even at a lower confidence; an abstention replaces only a failure; a failure never replaces anything. A line no backend answered stays a failed line, with only its error and no category or confidence.

Lines are routed by their IDs. A backend after the first is asked, through `classify_receipt(..., only={line_id})`, about each routed line alone, so nothing is sent, read or recorded for any other line, even one whose request would be the same text. A backend that returns other lines than it was asked about is refused with a `ConfigurationError`.

Every line's `classification_details.orchestration`, failed lines included, is its audit: `answered_by` (the alias whose answer it kept), `fallback` (whether it went past the first backend), `result` (`accepted`, `exhausted` when no backend was left, or `routing_off` when the routing kept a line it would not accept), and each attempt's `name`, `backend`, `model`, `outcome`, `routed_on`, `answered` (whether the model returned an answer, recorded or live: an `error` that was answered is an answer the backend's own checks refused; one that was not is a failed call, and a missing recording is never answered), `confidence` and `error`. No prompt, response or trace is kept. The answer's own `classification_details` stay beside it. `metadata.model` names the order, such as `primary=decisions:typesafe/jev-1.14, fallback=chat_completions:anthropic/claude-haiku-5.5`, and `metadata.prompt_sha256` covers every backend's prompt, alias and model, and the routing.

```yaml
classification:
  backend: langgraph
  langgraph:
    backends:
      - {name: primary, backend: decisions, model: typesafe/jev-1.14}
      - {name: fallback, backend: chat_completions, model: anthropic/claude-haiku-5.5}
    routing:
      fallback_on_error: true   # a line a backend fails or abstains on goes on to the next
```

Precedence, highest first: `validate_batch(classification_backend=..., langgraph_config=...)`, then `ExpenseValidator.from_config(..., classification_backend=..., langgraph_config=...)`, then `config.yaml`, then the defaults (no backends, `fallback_on_error` on). An override is a mapping that gives only what it changes: a `backends` list replaces the whole list, in its order, and a `routing` field replaces only that field. Unknown fields (a routing `min_confidence` or `fallback_on_low_confidence` among them: routing takes no threshold), duplicate aliases, text or flags where numbers belong, an unregistered backend, a model its backend refuses, `langgraph` as one of its own backends, `classification_model` or `classifier_model` with `langgraph`, and `langgraph_config` for any other backend are all a `ConfigurationError`. The effective configuration is a new frozen object for each request: the validator, `config.yaml`'s values, the registry and the compiled graph never change, so concurrent requests with different orders share nothing.

Each backend keeps its own model call, parsing, probabilities, cache folder and model-keyed cache, replay, `finalize` and errors, exactly as when chosen alone, so a LangGraph replay reads the same recordings as each backend alone. A missing recording is missing evidence, never a classification failure: its outcome is `missing_recording`, and `ReplayAnswerMissing` is raised for a line no backend answered. In ordinary replay such a line may follow the configured fallback. **Evaluation must not allow that**: `expense_validator.evaluation.classification.missing_recordings(results)` lists every such attempt, and must be empty before any result is scored, so a gap never reads as a model error and no fallback answers in a model's place. Nothing ever falls back to the network, and nothing is retried. `result_predictions` and `routing_summary` in the same module turn results into scored predictions and count fallbacks and extra backend calls.

#### Reflection

Off by default. `langgraph.reflection` adds classify, reflect, then accept, revise or fall back:

```yaml
    reflection:
      enabled: true
      reviewer: {backend: chat_completions, model: anthropic/claude-haiku-5.5}
      max_retries: 2                 # feedback-guided revisions per line: one classification and at most two revisions
      max_total_calls_per_line: 6    # every classification, revision and review of a line, failed, cached or replayed
      feedback_context: reviewer_feedback   # or structured, below
```

`max_retries: 0` turns revision off: an objection then ends revising at once (`retries_exhausted`). Both limits are checked before each call.

A reviewer judges only whether a classification is right, never whether an expense is allowed. It returns a `ReflectionResult`: `verdict` (`accept`, `revise` or `uncertain`), `reason`, and with `revise`, `feedback` and an optional `suggested_category` from the taxonomy. A malformed result, a revise with no feedback, a category outside the taxonomy, or another verdict suggesting a different category is a reviewer failure. The reviewer never changes a classification: a classifier revises it from the line, its previous classification and the feedback, and the revision is finalized and checked like any classification. Reviewing and revising are optional capabilities, registered apart in `classification/backends/registry.py`: `REVIEWERS` and `REVISERS` (`core.models.Reviewer` and `Reviser`). Only the LLM has both. Jev has neither: its Decisions API answers bounded choices about typed questions, writes no reason or feedback, and has no channel for feedback, so it is never asked to revise; the first backend in the order that can revise does so instead.

Precedence, for each line:
1. A failure, a missing recording or an abstention goes on to the next backend under `fallback_on_error`, as without reflection, and is not reviewed. An answer at any confidence is reviewed where it stands.
2. A new usable answer is reviewed, unless the same classification was already reviewed for this line: its verdict then stands again, with no call and no budget spent. `accept` ends the line. A review that fails or has no recording ends it with the answer kept: a reviewer's failure is no judgement of the answer.
3. `revise` asks for a revision, from the answering backend if it can take feedback, else from the first backend in the order that can, and the revision is reviewed again.
4. Instead, the line goes on to the next backend when the reviewer is `uncertain` (neither accept nor revise: nothing is revised, and the same classification is never reviewed twice), when the objection repeats one already made about the same classification, when the retries are spent, or when no backend can revise. It also goes on when a revision fails, abstains, has no recording, or says the same as before. A usable revision, at any confidence, is reviewed again. With no backend left, the line ends with its kept answer and that reason.
5. Before every call, the line's budget is checked: when it is spent, the line ends at once with its kept answer, as `call_budget`.

The kept answer follows the ordered selection above, so a later usable answer, revised or not, replaces an earlier one, and no confidence is compared across backends. A review is **unresolved** when the reviewer said `revise` or `uncertain` and the line never ended with `accept`. The kept answer then keeps its category, never replaced by the reviewer's, but not its confidence: the engine receives a category with no confidence, its existing `no_confidence` state, which raises EXC-03 and leaves the category out of the policy rules, as it does for any classification without a usable confidence. No confidence is invented, and no rule or finding is added. `reflection.unresolved` and `reflection.withheld_confidence` record it, beside every review's feedback. A line that was never reviewed, because the budget ran out first, keeps its confidence. A line with no usable answer stays a failed line or an abstention. Every superstep spends a call on, or ends, each line it handles, so a line ends within its budget; LangGraph's recursion limit is set from the same bound, as a second guard.

##### Structured feedback context

`feedback_context: structured` changes three things, and nothing else; the default, `reviewer_feedback`, is every earlier run's, unchanged.

- **What a revision is told.** Instead of `reviewer_feedback` alone, the request holds `line` (the line's own input, as classified: no other receipt content), `previous_classification` with its `confidence`, `review` (`verdict`, `reason`, `feedback`, `suggested_category`), `categories_in_question` (the taxonomy's definitions of the previous and suggested categories), `retry` (`number` and `max`), and, when retrying a failed revision, `failed_revision` (its validation error, at most 200 characters). The instructions say the review is advice, not evidence, and to invent nothing the line does not state. A reviser takes it through `Reviser.revise(..., context=RevisionContext(...))` and names its instructions in `Reviser.structured_prompt`; a reviser without one cannot revise in this mode.
- **A revision that fails validation is retried** with its error, while retries and the call budget remain. A missing recording is never retried.
- **Only a reviewer's `accept` verifies an answer.** A line ending any other way (a limit, a failed or missing review, an objection never withdrawn) is unresolved, and its confidence is withheld as above.

Each revision attempt records `answers_review` (its review's place in `reviews`), `max_retries` and `retried_validation_error`; the line's `reflection` records `feedback_context`, `stop_reason` (the same as `result`), `max_retries` and `max_calls`. Structured requests are new requests, so they replay only from recordings made with them. The prompt identity includes `feedback_context` only when it is `structured`, so every earlier reflection order keeps its hash.

Reviews and revisions are cached apart from classifications, in `reflection/` and `revision/` under the cache folder, each under its model's key. A review's request holds the line and the proposed classification, and a revision's the line, its previous classification and the feedback, so no first-pass answer is ever taken for a revision. A missing review or revision recording is `missing_recording`, never a verdict or a classification, and `missing_recordings` reports it with its `call`.

With reflection enabled, each line's `orchestration` adds `reflection`: `calls` (every classification, revision and review that was made), `revisions`, `unresolved`, `withheld_confidence` when unresolved, each review's `of` (the alias of the answer reviewed), `revision`, `reviewer`, `model`, `outcome` (`verdict`, `reused` for a verdict that stood again with no call, `error` or `missing_recording`), `answered`, `verdict`, `reason`, `suggested_category`, `feedback` and `error`, and each reflection `fallback`'s `from` and `reason`. A revision appears in `attempts` with its `revision` number. `result` then also names how reflection ended: `reviewer_uncertain`, `reviewer_failed`, `review_missing_recording`, `retries_exhausted`, `repeated_objection`, `revision_unchanged`, `revision_failed`, `revision_missing_recording`, `no_reviser` or `call_budget`. With reflection off, nothing about a line changes.

```python
validator = ExpenseValidator.from_config("config.yaml", mode="replay", classification_backend="langgraph")  # 1. config.yaml's order

validator.validate_batch(receipts, context_complete=True, langgraph_config={"routing": {"fallback_on_error": False}})  # 2. one routing field only

validator.validate_batch(receipts, context_complete=True, langgraph_config={"backends": [  # 3. the LLM first, Jev for the lines it fails
    {"name": "primary", "backend": "chat_completions", "model": "anthropic/claude-haiku-5.5"},
    {"name": "fallback", "backend": "decisions", "model": "typesafe/jev-1.14"},
]})

validator.validate_batch(receipts, context_complete=True, langgraph_config={"backends": [  # 4. one backend: its own result, with attempts recorded
    {"name": "only", "backend": "decisions", "model": "typesafe/jev-1.14"},
]})

validator.validate_batch(receipts, context_complete=True, langgraph_config={"backends": [  # 5. a third, future decision API, once registered
    {"name": "primary", "backend": "decisions", "model": "typesafe/jev-1.14"},
    {"name": "second", "backend": "acme", "model": "acme/decide-2"},
    {"name": "last", "backend": "chat_completions", "model": "anthropic/claude-haiku-5.5"},
]})
```

## `validate_batch`

- Accepts `Receipt` objects or raw records in the format of `data/README.md`. Records holding answers (`labels`, `notes`) are refused, as are classified receipts outside deterministic mode.
- Evaluates all receipts in one context, as TIME-03, MEAL-01 and HEALTH-01 read across receipts. Nothing is kept between calls. Submit all relevant receipts together; `context_complete` defaults to true.
- `context_complete` is `True` by default, in `validate_batch`, `PolicyEngine.evaluate`, `evaluate_receipts` and `POST /v1/validate`: the caller is expected to submit all relevant receipts (same dates, trips and baggage references) in one batch. `False` leaves totals spanning receipts that a missing receipt could change neither confirmed nor cleared, so the receipt is undecided; with nothing else against it, it is a provisional pass (`pass` with `decided` false), not a clearance. Claiming `True` with receipts missing can wrongly pass a split transaction.

## Results

`BatchResult` holds one frozen `ReceiptResult` per receipt:

| Field | |
|---|---|
| `disposition`, `decided`, `disposition_reasons` | EXC-01's outcome. A flag can be undecided, and a `pass` with `decided` false is provisional: nothing was found, but `open_questions` are still open |
| `line_items` | Printed description, category, restricted flag and confidence, with any translation, attributes or classification error |
| `exceptions` | Active only: rule, document, severity, lines, clause `evidence`, signed `source_quote`, explanation |
| `review_signals`, `open_questions`, `uncertainty` | Including EXC-03 details by line |
| `metadata` | Models, retriever (`"none"`), prompt and ruleset version, replayed, and `latency_ms` only when a model was called |
| `provenance` | Mode, context completeness, receipts in context, ruleset sha256, model overrides. Not in `to_record()` |

## Errors

All are `ValidatorError`; library code never exits the process.

| Error | Raised when |
|---|---|
| `ConfigurationError` | A file, model, key or cache is missing, or a mode or model choice cannot apply |
| `InvalidInputError` | A non-boolean `context_complete`, repeated receipt ID, or invalid receipt |
| `ReplayAnswerMissing` | Replay lacks an answer; `.missing` lists the requests |
| `EvidenceError` | A policy document or clause needed for evidence cannot be verified |

A model failing on one line is a result: the line has `classification_error`, no category, and EXC-03 review.

## Concurrency

One validator can serve concurrent requests: each call builds its own model wrappers, missing-answer list and evidence reader, and resolves its own models. Cache answers are written aside and renamed into place, so readers never see partial answers. Injected objects are called as given and must be thread-safe.

## Command line

```sh
python -m expense_validator.run_receipts --replay evaluation/final/replay                                  # rebuilds evaluation/final/receipts.jsonl
python -m expense_validator.run_receipts --classification-model MODEL_ID --translation-model MODEL_ID       # live
python -m expense_validator.run_receipts --replay evaluation/final/replay --classification-model MODEL_ID  # fails unless that model was saved
```

## Policy compiler

`PolicyCompiler` runs the strict agreement gate on documents held in memory. `extract` reads, reconciles and validates, and writes nothing. `publish` reads the documents again and writes only when the approval covers that exact output and candidate ruleset.

```python
from expense_validator import PolicyCompiler

compiler = PolicyCompiler.from_config(Path("config.yaml"), mode="replay", recordings_dir=Path("tests/fixtures/llm_responses"))
documents = [{"document_id": "meals", "filename": "meals.md", "content": text}]

extraction = compiler.extract(documents)              # review_items, blockers, candidate
extraction = compiler.extract(documents, resolutions=resolutions)
approval = compiler.approve(extraction, approved_by="j.doe", approved_at="2026-11-05T15:00:00Z")
result = compiler.publish(documents, approval=approval, resolutions=resolutions, out=Path("out/ruleset.json"), mode="simulation")
```

- Modes: `replay` reads recordings by document hash, makes no network call, and raises `ReplayAnswerMissing` (`.missing` holds file names) for an unrecorded document. `live` calls OpenRouter, and caches by model, temperature, prompt and document. The timeout changes no answer, so it is not part of the cache key.

- Recorded `simulation` readings, and any injected `completion`, never authorize `mode="production"`. A simulation never writes the configured production ruleset.
- `publish` never replaces an existing file unless `replace=True`, and a blocked publication is a result with `reasons`, not an error.
- Saving the approval is up to the caller. `approved_by` is whoever the caller has authenticated.
- An approval binds the exact extraction or review and candidate ruleset hashes. A pre-migration approval requires fresh review and approval because its output identity has changed.
- A resolved threshold must be stated in its unit's form (`$75`, `EUR 70`, `four nights`, `05:00`, `attendee count of two`) and must equal the limits the executable rules hold for that clause. Otherwise the candidate is rejected until `rules/rule_logic.yaml` is updated.
- Errors: `InvalidInputError` for documents, resolutions or publication arguments, `ConfigurationError` for settings or an override that is invalid or cannot apply, and `ApprovalRefused` when something blocks approval. No message quotes policy text.

`extraction_model`, `temperature` (0 to 2) and `timeout_seconds` are each taken from the first that sets it: the `extract` or `publish` call, `from_config`, then `config.yaml`'s `llm`. The compiler's settings never change, and the result reports the effective values and which ones the request chose (`overrides`). Replay and an injected `completion` refuse them, as nothing would use them. Mode, credentials, paths and rule logic are not per request.

```python
compiler = PolicyCompiler.from_config(Path("config.yaml"), mode="live", extraction_model="vendor/default-model", temperature=0.0)
extraction = compiler.extract(documents, extraction_model="vendor/request-model", temperature=0.2, timeout_seconds=60)
```

### A live extraction

Live mode with `recordings_dir` saves each OpenRouter answer as a `model` recording, named `configured-<key>.json`. The key is the SHA-256 of the document, prompt, provider, URL, model, temperature, response format. A recording is written once: an identical one is reused without a request, and a different one under the same key raises `RecordingConflict`. The folder must hold no simulation recordings. The run below sends the nine documents to the model once, which costs money.

```python
documents = [{"document_id": p.stem, "filename": p.name, "content": p.read_text()} for p in sorted(Path("data/policy-docs").glob("*.md"))]
live = PolicyCompiler.from_config(Path("config.yaml"), mode="live", extraction_model="anthropic/claude-haiku-5.5", recordings_dir=Path("evaluation/compiler/live"))
extraction = live.extract(documents)
print(extraction.reading_kind, extraction.blockers)                               # model, then anything that blocks approval
print([(i.rule_id, i.field, i.status, i.regex, i.llm) for i in extraction.review_items])
print(extraction.candidate and extraction.candidate.ruleset.to_json() == Path("rules/ruleset.json").read_text())

from expense_validator import RecordedWith

replay = PolicyCompiler.from_config(Path("config.yaml"), mode="replay", recordings_dir=Path("evaluation/compiler/live"),
                                    recorded_with=RecordedWith(extraction_model="anthropic/claude-haiku-5.5", temperature=0.0))
```

Replay with `recorded_with` uses only recordings made with exactly those settings, and fails closed on anything else.

A live compiler replays a recording it already has for the same document, prompt, model and temperature. To ask the model again on purpose, give `CompilerSettings.reading_id` (live mode, with `recordings_dir`): every document is read again, the answer cache is skipped, and each reading is recorded under that ID beside the earlier ones. Replay it with `RecordedWith(..., reading_id=...)`. Without it, replay reads recordings named by document hash, such as the test fixtures, as before. Those record no generation settings, so the result reports none.

### Reviewing every field

`extraction.comparisons` lists every field of every rule, agreed or not, each with a stable `comparison_id`, both readings (`regex_value`, `llm_value`), the `agreed_value`, the clause text and its location. `review` applies decisions to it and changes nothing: no model call, no file, no publication.

```python
meal = next(c for c in extraction.comparisons if c.rule_id == "MEAL-01" and c.field == "thresholds")
decide = lambda c, action, **more: {"comparison_id": c.comparison_id, "extraction_sha256": extraction.extraction_sha256, "action": action, "reviewer": "j.doe", **more}
decisions = [decide(c, "accept_regex") for c in extraction.comparisons if c.status != "agreed"] + [decide(meal, "confirm")]
review = compiler.review(extraction, decisions, policy="exceptions_only", documents=documents)
review.remaining, review.issues, review.ready_for_approval     # what still needs a decision, and what blocks approval
approval = compiler.approve(review, approved_by="j.doe", approved_at="2026-11-06T10:00:00Z")
compiler.publish(documents, approval=approval, decisions=decisions, review_policy="exceptions_only", out=Path("out/ruleset.json"), mode="simulation")
```

- Actions: `confirm` takes the agreed value and only applies where there is one. `accept_regex` and `accept_llm` take that reading. `override` needs a `value`, where null counts as a value, and a `rationale`. `reject` needs a `rationale` and always blocks approval.
- Policies: `exceptions_only` needs a decision for every field that is not agreed. `all_fields` needs one for every field. A partial review is allowed, but it is never complete.
- A decision for another extraction, an unknown comparison, or a comparison that more than one decision names is refused and left out.
- A reviewer's value is checked like any other. It must be supported by the source text, with the same checks as resolutions. Rule presence and title must be what the candidate was built from. Limits, severities and review signals go through the candidate's faithful check again. A value that disagrees with `rules/ruleset.json`'s logic blocks approval until `rules/rule_logic.yaml` changes. Section, references and quote are checked against the source only, and a change to one of them is reported as a note.
- The approval holds the whole review, its policy and decisions included, so `publish` with any other decision, policy, or document is refused. Review an extraction made without `resolutions`: the two never stack.
