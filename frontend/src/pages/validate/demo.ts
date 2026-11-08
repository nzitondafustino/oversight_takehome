/**
 * Demo input: receipts RCP-013 and RCP-014 from data/receipts.jsonl (lines 13-14), with their answers
 * (labels, notes) removed, as backend/README.md's walkthrough sends them. The deployment's "openrouter"
 * replay set holds recorded answers for them, so they validate in replay mode without calling a model.
 * Labelled as demo input in the UI: it is never shown as a result.
 */
export const DEMO_RECEIPTS_JSON = `[
  {
    "receipt_id": "RCP-013",
    "employee_id": "E-1128",
    "employee_country": "US",
    "entity": "GLOBAL",
    "submitted_expense_type": "Client Gifts",
    "merchant_name": "MarketPlace Retail",
    "mcc": "5311",
    "mcc_description": "Department stores",
    "transaction_datetime": "2026-04-20T14:05:00",
    "currency": "USD",
    "fx_rate_to_usd": 1.0,
    "total_amount": 104.99,
    "total_amount_usd": 104.99,
    "attendee_count": null,
    "trip_id": null,
    "trip_nights": null,
    "pre_approval_ref": null,
    "itemized": true,
    "line_items": [
      {
        "line_id": "RCP-013-L1",
        "description_raw": "Amazon.com gift card $100",
        "language": "en",
        "quantity": 1,
        "unit_price": 100.0,
        "amount": 100.0,
        "amount_usd": 100.0
      },
      {
        "line_id": "RCP-013-L2",
        "description_raw": "Greeting card",
        "language": "en",
        "quantity": 1,
        "unit_price": 4.99,
        "amount": 4.99,
        "amount_usd": 4.99
      }
    ]
  },
  {
    "receipt_id": "RCP-014",
    "employee_id": "E-1156",
    "employee_country": "US",
    "entity": "GLOBAL",
    "submitted_expense_type": "Office Supplies",
    "merchant_name": "FreshMart Supermarket",
    "mcc": "5411",
    "mcc_description": "Grocery stores and supermarkets",
    "transaction_datetime": "2026-04-11T18:15:00",
    "currency": "USD",
    "fx_rate_to_usd": 1.0,
    "total_amount": 47.76,
    "total_amount_usd": 47.76,
    "attendee_count": null,
    "trip_id": null,
    "trip_nights": null,
    "pre_approval_ref": null,
    "itemized": true,
    "line_items": [
      {
        "line_id": "RCP-014-L1",
        "description_raw": "Tide Pods laundry detergent 42ct",
        "language": "en",
        "quantity": 1,
        "unit_price": 19.99,
        "amount": 19.99,
        "amount_usd": 19.99
      },
      {
        "line_id": "RCP-014-L2",
        "description_raw": "Bounty paper towels 6 rolls",
        "language": "en",
        "quantity": 1,
        "unit_price": 14.49,
        "amount": 14.49,
        "amount_usd": 14.49
      },
      {
        "line_id": "RCP-014-L3",
        "description_raw": "Dish soap 25oz",
        "language": "en",
        "quantity": 1,
        "unit_price": 4.29,
        "amount": 4.29,
        "amount_usd": 4.29
      },
      {
        "line_id": "RCP-014-L4",
        "description_raw": "Cooking wine - Marsala 375ml",
        "language": "en",
        "quantity": 1,
        "unit_price": 8.99,
        "amount": 8.99,
        "amount_usd": 8.99
      }
    ]
  }
]
`;
