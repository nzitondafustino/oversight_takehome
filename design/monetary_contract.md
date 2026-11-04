# Monetary input contract

Written for: anyone reading or changing how amounts are admitted. Implemented in `src/expense_validator/engine/money.py`, version 1 of `input_contract` in `rules/fields.yaml`.

## Which values come from which

| Field | Kind | Source |
|---|---|---|
| `quantity`, `unit_price` | Printed, authoritative | `data/README.md` |
| `amount` | Printed, must equal `quantity` x `unit_price` | `data/README.md` states this |
| `currency`, `fx_rate_to_usd` | Printed, authoritative | `data/README.md`: "Multiply an amount ... by this to get USD" |
| `total_amount` | Printed, authoritative | `data/README.md` |
| `amount_usd`, `total_amount_usd` | Derived, never trusted alone | `data/README.md`: "converted" |
| `itemization_complete` | **Submitted metadata**, a project extension | Not in the data dictionary. Not evidence: see below |

## States

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

## Itemization: the decision

**The source contract does not guarantee that lines add up to the total.** `data/README.md` never says so, though all 32 benchmark receipts do, exactly. Taxes, gratuities and fees have no fields of their own: the taxonomy makes them line categories (`lodging`, `service_fee`), and a discount is a negative line, so each is in the sum once.

| `itemization_complete` | Lines add up | Lines do not add up |
|---|---|---|
| `true` | `reconciled` | **Refused** with the receipt ID, expected total, submitted total and discrepancy |
| absent | `reconciled` | `unverified`: undecided, never a clean pass |
| `false` | `unverified` | `unverified` |

Non-itemized receipts are `not_applicable`, as they have no breakdown. Claiming complete itemization on one is refused.

**This is a project decision on an ambiguity, not source policy.** The alternative, refusing every mismatch, would reject real receipts with tax not itemized as a line. Ignoring every mismatch left a $500 total with one $5 line as a clean pass.

### What `itemization_complete` is, and is not

**It is submitted metadata: a claim by whoever sent the receipt. It is not independent evidence that a receipt is complete, and nothing verifies it.**

- **It can only add scrutiny.** `true` can get a receipt refused, and `false` leaves it undecided. Neither makes a receipt decided, or better than it would be with the field absent. So a false claim gains nothing, and the engine does not need to trust it. Tests check this.
- **`reconciled` means the amounts given agree, not that nothing was left off.** A total and lines that both leave out the same item agree. Catching that needs the receipt itself, which is outside this engine.
- **`true` on lines that add up changes nothing,** as such lines are reconciled anyway.

## Tolerances

With h half the currency's smallest unit, from `minor_units`:

| Check | Allowed difference | Why |
|---|---|---|
| Line arithmetic | h + quantity x min(h, half the unit price's last digit) | The amount is rounded by at most h. The true unit price is within half its last printed digit of the printed one, and quantity multiplies that. Also never more than h per unit |
| Line sum | h x (lines + 1) | Each line and the total are each rounded by at most h |
| USD value | `usd_tolerance`, half a cent | Rounding to a cent. The data dictionary says this rate is the one used |

The arithmetic bound replaced `h x (quantity + 1)`, which it equals for prices printed to the currency's unit. It only ever narrows that, for prices printed more finely. At quantity 1000 and `1.00` a difference of up to 5.005 is still accepted, because a true price of 1.004 prints as 1.00. That is what the printed values can show, and is a limit of this check.

Arithmetic is exact. The default `Decimal` context keeps 28 digits, so a large enough difference could round to nothing. Here it keeps 120 and raises rather than rounds, and amounts are limited to below 10^15 and 10 decimal places.

With no currency, the coarsest unit, 0 places, is used, as a contradiction must hold in any currency it could be. A currency not in `minor_units` is refused as unsupported, not as contradictory.

## Open questions

These are decisions someone may wish to revisit.

1. **Should `itemization_complete` be required?** A submitter can leave it out. That only ever makes a mismatch `unverified` rather than refused, so it cannot reach a clean pass.
2. **Net-negative receipts.** The existing contract refuses a negative total, so a receipt of only refunds cannot be admitted with its true total. With a total of zero it is admitted, and undecided, as its lines do not add up.
3. **`decided` and an unverified USD value no rule reads.** `decided` says every rule was evaluated. A non-USD receipt with no rate and no USD-reading rule in play is `decided`, with DOC-04 flagging and `report.monetary` showing `unverified`. A consumer wanting to know about the conversion must read `report.monetary`, not `decided`.
