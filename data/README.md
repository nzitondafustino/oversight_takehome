# Data dictionary

This folder holds everything your system works from:

- `policy-docs/` — nine expense policies defining 41 rules, each with an identifier such as `ALC-01`.
- `receipts.jsonl` — 32 itemized receipts, one JSON object per line, each with its correct answer.
- `category-taxonomy.md` — the item categories used in the correct answers.

## Receipt fields

| Field | Meaning |
|---|---|
| `receipt_id` | Unique receipt identifier, e.g. `RCP-014`. |
| `employee_id` | The employee who submitted the expense. The same employee can appear on several receipts. |
| `employee_country` | The employee's home country (ISO code). Decides which regional rules apply. |
| `entity` | The employee's regional entity — `GLOBAL`, `APAC` (IN, JP, SG) or `EMEA` (GB, DE, FR, NL, IE, PT, ES) — derived from `employee_country`. |
| `submitted_expense_type` | The expense type the employee declared. Compared with the contents under DOC-03. |
| `merchant_name` | The merchant as printed on the receipt. TIME-03 groups receipts by this name. |
| `mcc` / `mcc_description` | The merchant category code and its name. Risk tiers are in `merchant-category-risk.md`. |
| `transaction_datetime` | Date and time of the transaction, in **local time at the merchant**, with no time zone. |
| `currency` | The receipt's currency (`USD`, `EUR`, `INR` or `JPY`). |
| `fx_rate_to_usd` | The rate on the transaction date (REG-05). Multiply an amount in the receipt's currency by this to get USD. |
| `total_amount` / `total_amount_usd` | The receipt total in its own currency, and converted to USD. |
| `attendee_count` | How many people the receipt covers, **including the employee**. `null` means no count was recorded. |
| `trip_id` / `trip_nights` | The business trip the receipt belongs to, and that trip's length in nights. `null` means the receipt is not linked to a trip. Trip records hold no start or end dates. |
| `pre_approval_ref` | The single field for **any** approval or exception reference — pre-approval, VP or manager approval, or a baggage-delay reference (see EXC-02). `null` means none was recorded. Every reference in this dataset is a pre-approval, written `PA-` followed by the year and a number. |
| `itemized` | `true` when the receipt lists its individual items; `false` when it shows only a total. |
| `line_items` | The items on the receipt (see below). |
| `labels` | **The correct answer for this receipt** (see below). |
| `notes` | **Part of the correct answer:** why the answer is what it is, and the trap the receipt contains. |

The data holds no VAT number and no payment-card type. Whether a recipient is a government
official, a client or an employee is stated only in the line text — reading it is part of the task. REG-04 says how to treat a missing VAT
number; no rule depends on the card type.

## Line-item fields

| Field | Meaning |
|---|---|
| `line_id` | Unique item identifier, e.g. `RCP-014-L4`. |
| `description_raw` | The item text exactly as printed, in its original language. |
| `language` | The language of `description_raw`: `en`, `es`, `fr`, `de`, `pt` or `ja`. |
| `quantity`, `unit_price`, `amount` | Quantity, price per unit, and line total, in the receipt's currency. `amount` = `quantity` × `unit_price`. |
| `amount_usd` | The line total converted to USD. |
| `labels.category` | **Correct answer:** the item's category from `category-taxonomy.md`. |
| `labels.restricted` | **Correct answer:** whether that category is restricted. |

## The correct answers

| Field | Meaning |
|---|---|
| `labels.expected_exceptions` | Every policy exception the receipt should raise. Each has a `rule_id`, the `policy_doc` it comes from, its `severity`, the `line_ids` it concerns, and a `reason`. |
| `labels.review_signals` | Softer signals that are **not** exceptions. Each has a `rule_id`, `policy_doc` and `reason`, and no severity. |
| `labels.disposition` | `flag`, `review` or `pass`, computed from the exceptions and signals by EXC-01. |

**Conventions the answers follow:**

- Each rule appears at most once per receipt. When several items break the same rule, one exception
  lists them all.
- Rules about the receipt as a whole — MCC-01, MEAL-01, MEAL-02, DOC-01, DOC-02, DOC-03, DOC-04,
  REG-02, REG-03, REG-04 and TIME-03 — have an empty `line_ids` list. Every other exception
  lists the items it concerns (EXC-04), and never a gratuity or service fee charged alongside them.
- MCC-02 is always its own review signal, even on a receipt that also has an exception.
- A review signal raised because an item's category is uncertain uses rule ID `EXC-03`. No answer in
  the dataset uses it, because the answers assume every category is known — so an `EXC-03` signal
  you raise counts as unmatched, and any change it makes to a disposition counts too. A non-itemized
  charge is `other`, not uncertain.
- Each rule that raises an exception states its severity in its policy text. One rule has two: HEALTH-01 is low for
  over-the-counter medication and medium for prescription medication.

**Comparing your output with the answers:** an exception you raise matches an answer when it is on
the same receipt with the same `rule_id`. It also cites the correct evidence when its line items
equal the answer's `line_ids` exactly — both empty for whole-receipt rules. Review signals match on
receipt and `rule_id`. Category answers are per line item. Evidence correctness is measured over
the exceptions that match an answer: the share whose line items equal the answer's `line_ids`. Per-rule figures cover every rule that appears in the answers
or in your output; a rule in the answers that you never raise has recall 0 and precision n/a. A macro average is the mean of the per-category scores.

## Use the answers for evaluation only

`labels`, `line_items[].labels` and `notes` are the correct answers. Use them **only** to measure
your system — never as input to it, and never in a prompt.
