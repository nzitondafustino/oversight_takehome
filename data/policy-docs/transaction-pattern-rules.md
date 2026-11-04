# Transaction Pattern Rules

These rules look at when and how a transaction happened rather than what was bought. With the
exception of TIME-03, they produce **review signals**, not exceptions: on their own they are not
evidence of a policy violation, and reporting them as violations is the most common source of
false positives in audit.

**TIME-01 — Overnight transactions.** A transaction timed from **00:00 up to, but not including,
05:00** raises a **review signal**. Transaction times are recorded in local time at the merchant.

**TIME-02 — No associated trip.** An individual travel expense — declared as Meals - Individual,
Lodging, or Travel - Other — with no associated trip record raises a **review signal**. Team meals
and client entertainment often happen without travel and are not covered.

**TIME-03 — Split transactions.** Two or more receipts by the same employee, at the same merchant
(by merchant name), on the same calendar date, are treated as a deliberate split when each one is
at or below one of these thresholds but together they are above it: the $75 itemization threshold,
when every receipt in the group is non-itemized (DOC-01); the employee's meal cap, compared per person (MEAL-01, REG-02, REG-03); or the $50 gift cap for
one recipient (GIFT-01). This **is** an exception, at **high** severity,
and applies to every receipt in the group. It replaces the threshold rule being split around: report
the split, not a separate breach of that threshold.

**TIME-04 — Weekend spend on a weekday trip.** A transaction dated on a weekend that falls
outside the trip's recorded start and end dates raises a **review signal**. It applies only when
the trip record includes those dates; where there is no trip record at all, TIME-02 covers it.
