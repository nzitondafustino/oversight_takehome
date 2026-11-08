import type { Disposition, ReceiptResult } from "@/api/types";

const RANK: Record<Disposition, number> = { flag: 0, review: 1, pass: 2 };

/**
 * Flag, then review, then pass. Within a group the API's own order is kept (the core returns receipts in
 * receipt ID order): Array.prototype.sort is stable, and the input is never mutated.
 */
export function sortByDisposition(results: readonly ReceiptResult[]): ReceiptResult[] {
  return [...results].sort((a, b) => RANK[a.disposition] - RANK[b.disposition]);
}
