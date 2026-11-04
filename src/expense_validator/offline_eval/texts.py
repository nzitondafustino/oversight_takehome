"""The written parts of the report. They rest on what the harness showed at Module 11A, so reread them after a real run."""

LIMITATIONS = """- **No classifier exists yet.** Oracle scenarios use the answers themselves, and synthetic ones use a generator. Neither measures classification.
- **32 receipts and 79 lines.** Intervals are wide: 32 of 32 right still only supports about 89% to 100%. Differences of a receipt or two mean little.
- **Most rules appear once or twice in the answers,** so per-rule scores are close to anecdotes.
- **The answers cover only `category` and `restricted`.** `recipient_type`, `venue_type`, `item_kind` and `essential_toiletry` have no answers, so their accuracy cannot be scored. Recipients in the oracle scenarios were read by hand, on three lines, and are not gold.
- **The answers assume every category is known,** so any EXC-03 signal counts as unmatched, by the data dictionary's rule. Strict precision is low wherever uncertainty is raised, even when nothing is wrong.
- **The data has no negative lines, shared trips, missing rates or missing totals,** so these benchmarks cannot exercise those paths. Only the engine's regression tests do.
- **Synthetic errors are uniform at random.** A real classifier's mistakes will cluster on hard items, such as names that lie, and will affect restricted categories differently. The sensitivity results show direction, not size.
- **`restricted` is scored but drives no rule,** and is absent from the error scenarios, so their restricted F1 says nothing.
- **A file's `actual` label is a claim** that nothing here can authenticate. A file matching the answers exactly raises a warning."""

RECOMMENDATIONS = """These follow from the engine's behaviour in the scenarios above. The size of any effect must be measured on real predictions.

1. **Require a confidence on every line, and never default one.** Scenarios C and D show a right category with no usable confidence is treated as not known: only 13 of 37 rules match, and 28 of 32 receipts go to review.
2. **Treat calibration as the first thing to measure, ahead of accuracy.** H and I have the same wrong lines. Admitted errors (I) gave no false flags, no false passes and no silent misses, at the cost of fewer receipts decided: 22 to 24 of 32, against 27 to 31. Confident errors (H) gave false flags on 2 to 3 receipts, silent misses on 1 to 2, and a false pass on one seed. Dispositions right is about the same for both, 27 to 28 against 26 to 29, so accuracy alone hides this. The engine can only protect against errors it is told about.
3. **Choose the EXC-03 threshold from real predictions.** Scenario G shows under-confidence costs review time, not accusations: no false flags and no false passes, but only 6 to 10 of 32 receipts decided. 0.5 is a placeholder until then. With 79 lines, use cross-validation, and say how wide the result is.
4. **Have the classifier return null when a recipient type is not stated, never a guess.** A guessed `client` removes the engine's uncertainty, and with it GIFT-04's open question and any proof. Add a hand-labelled recipient set before claiming accuracy on it, as the answers have none.
5. **Extract recipients even when the category is hard.** Scenario E misses RCP-022's GIFT-04 with nothing listing it as open: an unnamed line on a meal receipt does not read as needing a recipient. The receipt stays undecided in review, but the only critical rule in the data gets no warning. This is frozen engine behaviour, so the classifier must carry it.
6. **Route on proofs as well as active exceptions.** A proof where GIFT-04 is possible has no active critical exception, so a consumer must read `inevitable.rule_ids` to send it to Compliance.
7. **Keep the contract narrow.** Attach output with `with_classifications`, so it is validated. Send no `labels` or `notes` in any prompt. Write predictions to a file of kind `actual` naming the model, and run them through this harness before anything else depends on them.
8. **Hold back some receipts before tuning,** or report tuning and evaluation on the same 32 as such. Commit the raw predictions, so scores can be rerun without spending on the model."""
