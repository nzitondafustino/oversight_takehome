# Approval and Exception Handling

## Severity definitions

| Severity | Meaning |
|---|---|
| **critical** | Legal or regulatory exposure. Routed to Compliance immediately, never to the standard audit queue. |
| **high** | Clear policy violation with material risk. Always reviewed by a human auditor. |
| **medium** | Policy violation, routine. Enters the audit queue. |
| **low** | Minor or documentation-only issue. Sampled rather than reviewed exhaustively. |

## Disposition

**EXC-01 — Receipt disposition.** A receipt's disposition is determined by the most severe
exception raised against it:

- any **critical**, **high**, or **medium** exception → **flag**
- only **low** exceptions, or only review signals, and no exception above low → **review**
- no exceptions and no review signals → **pass**

Disposition is computed, not judged. The same set of exceptions must always produce the same
disposition.

**EXC-02 — Approval references.** Every approval or exception reference — a pre-approval, a VP or
manager approval, or a baggage-delay reference — is recorded in one field on the expense
(`pre_approval_ref` in the data). Where a rule requires such a reference, a recorded one satisfies
that rule and no exception is raised — provided it is the kind the rule asks for: a pre-approval
for TECH-02, a VP approval for ENT-01, a manager approval for TECH-04, a baggage-delay reference for
the PERS-01 and PERS-02 carve-outs. A reference does not waive any other rule.

**EXC-03 — Uncertainty.** Where the category of an item cannot be determined with sufficient
confidence, raise a **review signal** with rule ID `EXC-03` rather than an exception, and leave that
item out of every other rule's calculation for the receipt. A non-itemized charge is not an
uncertain category: it is `other` by definition, and raises no EXC-03 signal. An unnecessary exception costs a
reviewer's time and erodes trust in the system; a missed review costs far less than a false
accusation against an employee.

**EXC-04 — Evidence.** Every exception must cite the rule identifier, the policy document it
comes from, and the line items it concerns. An exception that cannot name its evidence must not be
raised.

- Rules about specific items cite every item that breaks them, in one exception — the items
  themselves, never a gratuity or service fee charged alongside them.
- Rules about the receipt as a whole — MCC-01, MEAL-01, MEAL-02, DOC-01, DOC-02, DOC-03, DOC-04,
  REG-02, REG-03, REG-04 and TIME-03 — cite no line items.
- Each rule appears at most once per receipt.
