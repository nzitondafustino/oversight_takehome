# Item Category Taxonomy (starting point — extend it if you need to)

Gold labels in `receipts.jsonl` use the categories below. You are **not** required to use this
exact set: the taxonomy is deliberately open-ended, and a category you invent may well describe
an item better than anything here. If you emit a category outside this list, provide a mapping
from your labels to these so the evaluation can be scored, and say in your README why the extra
category earned its place.

Your classifier must get both the category and the **restricted** flag right; the policy engine
consumes both. *Restricted* means items in the category are not
reimbursable by default: they are either never allowed, or allowed only through a specific exception —
an entertainment context, a gift allowance, a baggage-delay reference. Unrestricted categories are
ordinary business spend, allowed by default, though amount, trip and approval rules can still
limit them.
Restricted does not by itself mean an exception: wine at a documented client dinner is restricted
and allowed. The engine needs the exact category as well as the flag — rules such as TECH-02,
LAUNDRY-01, DOC-03 and the meal rules depend on it.

| Category | Restricted | Typical examples |
|---|---|---|
| `alcohol` | yes | beer, wine, spirits, cocktails, corkage |
| `tobacco` | yes | cigarettes, cigars, vaping products |
| `personal_electronics` | yes | headphones, tablets, smart watches, cameras, trackers, and accessories made for them (cases, chargers) |
| `gift` | yes | items made to be given: flowers, gift baskets, greeting cards, branded merchandise |
| `gift_card` | yes | gift cards, prepaid cards, vouchers |
| `household_goods` | yes | cleaning supplies, paper towels, kitchenware, drinkware, bedding, home décor |
| `personal_care` | yes | toiletries, cosmetics, haircuts, spa and massage |
| `clothing` | yes | apparel, footwear, fashion accessories (belts, ties, bags) |
| `gambling` | yes | casino chips, betting stakes, lottery |
| `jewelry` | yes | non-electronic watches, bracelets, precious metal items |
| `food_beverage` | no | meals, non-alcoholic drinks, snacks, catering |
| `computer_accessory` | no | cables, adapters, docks, mice, keyboards, monitors |
| `office_supplies` | no | stationery, notebooks, printing, shipping |
| `lodging` | no | room charges, resort fees, lodging tax |
| `ground_transport` | no | taxi, rideshare, transit, parking, tolls, fuel |
| `airfare` | no | tickets, seat and bag fees |
| `laundry_service` | no | laundry, dry cleaning, pressing |
| `health_otc` | no | over-the-counter medication, cough and throat lozenges, first aid |
| `software_subscription` | no | SaaS and software licences |
| `service_fee` | no | service charge, gratuity, delivery fee, booking fee |
| `other` | no | anything that fits nothing above, including a non-itemized charge whose contents are unknown; explain it in the output |

## Two things the taxonomy will not do for you

**Names lie.** Classification must reflect what the item *is*, not the words in its description.
"Beer-battered cod" is `food_beverage`. A "Moscow Mule mug" is `household_goods`. An "Arnold
Palmer" and a "virgin mojito" are `food_beverage`. "Cooking wine" bought at a supermarket is
still `alcohol`. Several items in the dataset exist only to catch keyword matching.

**Category and purpose are separate.** An item's category says what it is; whether it is a gift
depends on why it was bought. A bracelet is `jewelry` whoever it is for, and if it was bought for a
client the gift rules apply to it as well (see `gifts-and-awards-policy.md`).

**Items the policies mention but the data does not contain** — mobile phones, prescription
medication, event tickets — have no category here. If you meet one, use `other` and say what it is.

**Items are multilingual.** Line-item text appears in English, Spanish, French, German,
Portuguese, and Japanese, as it does in the real receipt stream. The `language` field records
the language of the raw description.
