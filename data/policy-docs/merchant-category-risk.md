# Merchant Category Code (MCC) Risk

Merchant category codes describe where the money was spent, not what was bought. Most codes are
context for line-item analysis; the high-risk codes are the exception, raising an exception on
their own under MCC-01.

| MCC | Merchant category | Risk tier |
|---|---|---|
| 5921 | Package stores — beer, wine, liquor | **High** |
| 5993 | Cigar stores and stands | **High** |
| 7995 | Betting, casino gaming, lottery | **High** |
| 7297 | Massage parlors | **High** |
| 5944 | Jewelry, watch, and silverware stores | **High** |
| 5813 | Bars, taverns, cocktail lounges | Medium |
| 5912 | Drug stores and pharmacies | Medium |
| 5311 | Department stores | Medium |
| 5411 | Grocery stores and supermarkets | Medium |
| 5732 | Electronics stores | Medium |
| 5812 | Eating places and restaurants | Low |
| 7011 | Hotels, motels, resorts | Low |
| 4111 | Local and suburban transit | Low |
| 4121 | Taxicabs, limousines, rideshare | Low |
| 5942 | Book stores | Low |
| 5943 | Office and stationery supply | Low |
| 5734 | Computer software stores | Low |
| 7216 | Dry cleaners and laundry services | Low |

Any MCC not listed in this table is **low** risk.

**MCC-01 — High-risk merchant category.** A transaction at a high-risk MCC raises a **high**
exception on its own, whatever the line items say. These categories are not consistent with any
routine business purpose.

**MCC-02 — Medium-risk merchant category.** A medium-risk MCC raises a **review signal** only.
It is not an exception by itself and must never be reported as one; it exists to prioritise
sampling and to add context to an exception raised by another rule. It is always reported as its
own review signal, even when another rule raises an exception on the same receipt.

**MCC-03 — Bars and taverns.** A bar (MCC 5813) serves alcohol on the premises, so ALC-01 and
ALC-02 apply to it exactly as they do to a restaurant, and it is never treated as retail under
ALC-03.
