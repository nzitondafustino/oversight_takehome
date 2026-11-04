# Regional Addendum

Regional rules **override** the corresponding global rule where they conflict.

## APAC entity — employees in IN, JP, SG

**REG-01 — Alcohol.** Alcohol is **never** reimbursable for APAC-entity employees, including
within client entertainment. This overrides ALC-01 and ALC-02: where those rules would have
permitted alcohol with an attendee count, REG-01 still makes it a **high** exception. For APAC employees REG-01 replaces ALC-01, ALC-02 and
ALC-03: report REG-01 instead of those three. Rules about the merchant, such as MCC-01, still apply.

**REG-02 — Meal cap.** The APAC daily meal cap is **$60 per person**, replacing both MEAL-01
figures for APAC employees. Meal spend, per person and per day are defined as in MEAL-01. A breach
is a **medium** exception, reported as REG-02, not MEAL-01.

## EMEA entity — employees in GB, DE, FR, NL, IE, PT, ES

**REG-03 — Meal cap.** The EMEA daily meal cap is **EUR 70 per person**, replacing both MEAL-01
figures for EMEA employees. Convert EUR 70 to USD at the euro rate on the transaction date — for a euro
receipt, that is the receipt's own rate — before comparing. Meal spend, per person and per day are
defined as in MEAL-01. A breach is a **medium** exception, reported as REG-03, not MEAL-01.

**REG-04 — VAT documentation.** Receipts above EUR 50 — converted at the euro rate on the transaction date for a receipt in another
currency — must be valid VAT receipts showing the
merchant VAT number. A missing VAT number is a **low** exception. Where the expense record does
not capture a VAT number, treat it as missing.

## All regions

**REG-05 — Conversion rate.** Currency conversion uses the rate on the transaction date, which
must be recorded on the expense record. REG-05 says which rate to use and raises nothing on its
own; a missing rate is reported under DOC-04.
