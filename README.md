# Expense Policy Validator

Classifies each receipt line, checks the receipt against nine written policies, and returns `flag`, `review` or `pass` with evidence quoted from the policy. Setup, package usage and the REST API: [docs/USAGE.md](docs/USAGE.md). All 32 receipts offline, no key or spend: `python -m expense_validator.run_receipts --replay evaluation/final/replay`.

## Architecture, and why

```text
receipt → ingest → normalize → translate → classify → policy engine → exceptions, review signals, disposition
                                (model)     (model)    (deterministic)
policy docs + hand-written rule logic → compile → review → approve → published ruleset
```

- **Models describe items; code decides policy.** EXC-01 says disposition is "computed, not judged", and an exception accuses an employee. Classifiers return only a category, a confidence and stated attributes; the engine (`engine/`) decides the rest and is unit-tested without models.
- **Translation is a separate step** that never replaces the original, so it can be cached, measured or turned off on its own.
- **Classification is pluggable:** Claude Haiku (`chat_completions`), TypeSafe Jev (`decisions`), or LangGraph falling back from one to the other. Policy behaviour is the same whichever answered.
- **Rules are hand-written and checked against the documents.** No model output is executed.
- **Labels never reach inference**, and every model answer is cached by model, prompt and text, so replay is reproducible and free.

The FastAPI service, worker and React UI (`backend/`, `frontend/`) call the same core and add versioned documents, rulesets, history and evaluations.

## Core prompts

- **Classification** (`classification/backends/llm.py`): "You describe one line item … You never judge whether it is allowed." It receives one line as JSON (description, language, translation, merchant, merchant category, quantity, price, currency), told to treat values as data, not instructions. It must classify what the item *is* ("names lie"), never from merchant or price alone. It answers strict JSON: a taxonomy category, a confidence from five levels (0.9 clearly … 0.1 cannot tell), and recipient, venue type, item kind and essential toiletry, each `null` unless stated. An answer outside the taxonomy, or naming a recipient the line doesn't, is an error, never a guess. The submitted expense type is withheld, so DOC-03 stays independent.
- **Jev:** the same facts as five multiple-choice questions; its confidence is a probability.
- **Translation:** "Translate the following text into English … only output the translated result".
- **Policy reading** (`compiler/llm_extract.py`): each rule's ID, severities, limits and a verbatim quote, "Report only what the text says".

## How rules are encoded, and where thresholds come from

`rules/rule_logic.yaml` holds 42 rules: 35 checks, 6 process directives (EXC-01 to EXC-04 among them) and 1 carve-out. A check has an outcome and severity, a scope (item or receipt), conditions over a typed field catalog (`rules/fields.yaml`) such as "category is alcohol and MCC not in 5812, 5813, 7011", named helpers for what conditions can't express (splits, daily caps, per-recipient gift totals), what it replaces (REG-01 replaces ALC-01 to ALC-03 for APAC), and a `source_quote` that must occur in its clause. The engine resolves findings in a fixed order: checks, replacements, reference waivers, carve-outs, one grouped exception per rule, then EXC-01's disposition.

**All 21 numeric limits come from the policy text, and every build checks them.** A *faithful check* refuses any rule whose limits, severities or outcome differ from what its clause states. A model also reads the documents independently; where its reading disagrees with a deterministic text reading (say it misses "two or more receipts"), a person decides, and the decision is bound by hash. **The only number that is a project decision** is EXC-03's `min_confidence`.

## Uncertainty threshold: 0.5

A category with confidence below 0.5 raises an EXC-03 review signal, and that line is left out of every other rule. With Haiku's levels that means 0.3 ("probably not, but nothing fits better") and 0.1 ("cannot tell"). Below 0.5 the model itself rates the category more likely wrong than right, and EXC-03 says "a missed review costs far less than a false accusation". A higher threshold would mostly add unmatched signals, as no answer uses EXC-03. As a project extension, a missing field a rule needs also raises EXC-03: a gift with no stated recipient type leaves GIFT-04 open rather than guessed.

## Categories added

**None:** the taxonomy is unchanged, so outputs map 1:1 to the answers. What it lacks is carried as attributes: `item_kind` (mobile phone, prescription or OTC medication), `venue_type` (event tickets, with category `other`), and `recipient_type` and `essential_toiletry` for the gift and personal-care rules.

## Assumptions

- Relevant receipts are submitted together. Cross-receipt rules (TIME-03, MEAL-01, HEALTH-01) read only the batch; no history is fetched.
- Printed amounts are authoritative; USD values are re-derived from the rate and refused beyond half a cent.
- Approval references are claims, not verified. Times are merchant-local. A missing MCC is not retail. A recipient with no stated type is "unidentified".
- Haiku's confidence is a self-estimate, not calibrated; Jev's is a probability.

## Does data leave the machine?

- **Replay and deterministic runs: nothing**, including the committed outputs, the tests and the API's default mode.
- **Live classification** sends each line's JSON (above) to OpenRouter, routed to Anthropic or TypeSafe. It never sends employee ID, country, entity, dates, totals, attendees, trips, references or the expense type. **Live translation** sends non-English descriptions to `tencent/hy-mt2-30b-a3b` via OpenRouter, and **live compilation** sends whole policy documents to the extraction model.
- **The API** keeps receipts and results in local MongoDB, and policy documents and rulesets in the configured Amazon S3 bucket (private, encrypted).

## Worked example: RCP-014

FreshMart Supermarket, MCC 5411 (grocery), submitted as *Office Supplies*, $47.76, four lines.

1. **Ingest:** labels stripped. All lines are English, so nothing is translated.
2. **Classify** (Haiku, replayed): `{"original_description": "Cooking wine - Marsala 375ml", "merchant_category": "Grocery stores and supermarkets", …}` → `alcohol` 0.9; "cooking" doesn't make it food. Tide Pods, paper towels and dish soap → `household_goods` 0.9. Nothing is below 0.5, so no EXC-03.
3. **Engine:** ALC-03, alcohol from a merchant that isn't a restaurant, bar or hotel → **high** on L4. HOUSE-01 → **medium**, citing L1 to L3. DOC-03, *Office Supplies* doesn't cover them → **medium**, citing no line, per EXC-04. MCC-02, medium-risk MCC → **review signal** only.
4. **Evidence:** each exception quotes its verified clause, such as "Alcohol purchased from a package store, liquor store, or any other merchant that is not a restaurant, bar or hotel is never reimbursable…".
5. **Disposition:** EXC-01 flags any high or medium exception, so **`flag`**, decided. That matches the answer: the same three exceptions, the same signal. The record is in `evaluation/final/receipts.jsonl`.
