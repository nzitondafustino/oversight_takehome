# Receipt and Documentation Requirements

**DOC-01 — Itemization threshold.** Any receipt with a total above **$75** (in USD) must be itemized. A
non-itemized receipt above that threshold is a **medium** exception, because the contents cannot
be audited.

**DOC-02 — Legibility.** A receipt must show the merchant name, the transaction date, and the
total. A receipt missing any of these is a **medium** exception.

**DOC-03 — Expense type must match the contents.** A receipt is misclassified when **none** of its
line items belongs to a category that its declared expense type covers. Service fees, gratuities
and items in the `other` category are ignored for this test; if no items remain, DOC-03 does not
apply. A misclassification is a **medium**
exception, reported in addition to any exception raised by the items themselves, not instead of it.

| Declared expense type | Categories it covers |
|---|---|
| Meals - Individual, Meals - Team, Client Entertainment | `food_beverage`, `alcohol` |
| Client Gifts | `gift`, `gift_card`, `jewelry` |
| Lodging | `lodging`, `ground_transport`, `laundry_service` |
| Travel - Other | `laundry_service`, `health_otc`, `ground_transport`, `clothing`, `personal_care` |
| Office Supplies | `office_supplies`, `computer_accessory` |
| Computer Equipment | `computer_accessory`, `personal_electronics`, `software_subscription` |
| Software | `software_subscription` |

Every expense type used in the dataset appears in this table. The table says what each
declaration describes, not what is reimbursable: headphones declared as
Computer Equipment are correctly declared and still not reimbursable under TECH-01.

**DOC-04 — Foreign currency.** Receipts in a currency other than USD must record the transaction
currency and the conversion rate used; a receipt missing either is a **medium** exception. All policy thresholds are evaluated on the **USD**
converted amount.
