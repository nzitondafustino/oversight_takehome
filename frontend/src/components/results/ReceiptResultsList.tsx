import { Search } from "lucide-react";
import type { KeyboardEvent } from "react";
import type { Disposition, ReceiptResult } from "@/api/types";
import { DISPOSITION_ORDER, DISPOSITIONS, dispositionDisplay } from "@/components/status/badges";
import { Input } from "@/components/ui/misc";
import { type JsonObject, scalarText } from "@/lib/losslessJson";
import { cn } from "@/lib/utils";

/** The receipts a list shows, in order: the caller's sorted results, filtered by search and disposition. */
export function filterResults(results: readonly ReceiptResult[], inputs: Map<string, JsonObject> | null, query: string, disposition: Disposition | "all") {
  const needle = query.trim().toLowerCase();
  return results.filter((result) => {
    if (disposition !== "all" && result.disposition !== disposition) return false;
    if (!needle) return true;
    const merchant = inputs ? scalarText(inputs.get(result.receipt_id)?.merchant_name) : null;
    return result.receipt_id.toLowerCase().includes(needle) || Boolean(merchant?.toLowerCase().includes(needle));
  });
}

const TONE: Record<Disposition, string> = {
  flag: "border-l-status-flag",
  review: "border-l-status-review",
  pass: "border-l-status-pass",
};

/**
 * The batch's receipts as the filters show them: receipt ID, merchant when known, and disposition. Arrow keys move
 * through the shown receipts only.
 */
export function ReceiptResultsList({
  shown,
  counts,
  total,
  inputs,
  selectedId,
  onSelect,
  query,
  onQueryChange,
  disposition,
  onDispositionChange,
}: {
  shown: ReceiptResult[];
  counts: Record<Disposition, number>;
  total: number;
  inputs: Map<string, JsonObject> | null;
  selectedId: string | null;
  onSelect: (receiptId: string) => void;
  query: string;
  onQueryChange: (query: string) => void;
  disposition: Disposition | "all";
  onDispositionChange: (disposition: Disposition | "all") => void;
}) {
  function onKeyDown(event: KeyboardEvent<HTMLUListElement>) {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const current = Math.max(0, shown.findIndex((result) => result.receipt_id === selectedId));
    const next = Math.min(shown.length - 1, Math.max(0, current + (event.key === "ArrowDown" ? 1 : -1)));
    const target = shown[next];
    if (!target) return;
    onSelect(target.receipt_id);
    (event.currentTarget.querySelectorAll("button")[next] as HTMLButtonElement | undefined)?.focus();
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="space-y-2 border-b border-border p-3">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-subtle-foreground" aria-hidden />
          <Input
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
            placeholder={inputs ? "Search receipt or merchant" : "Search receipt ID"}
            aria-label="Search receipts"
            className="h-8 pl-8 text-[13px]"
          />
        </div>
        <div role="group" aria-label="Filter by disposition" className="flex gap-1">
          {(["all", ...DISPOSITION_ORDER] as const).map((value) => (
            <button
              key={value}
              type="button"
              aria-pressed={disposition === value}
              onClick={() => onDispositionChange(value)}
              className={cn(
                "h-7 flex-1 whitespace-nowrap rounded-md px-1 text-xs font-medium",
                disposition === value ? "bg-primary-soft text-primary" : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              {value === "all" ? `All ${total}` : `${DISPOSITIONS[value].label} ${counts[value]}`}
            </button>
          ))}
        </div>
      </div>
      {shown.length === 0 ? (
        <p className="px-4 py-8 text-center text-[13px] text-muted-foreground">No receipts match.</p>
      ) : (
        <ul aria-label="Receipts, flag first" className="min-h-0 flex-1 divide-y divide-border overflow-y-auto" onKeyDown={onKeyDown}>
          {shown.map((result) => {
            const selected = result.receipt_id === selectedId;
            const merchant = inputs ? scalarText(inputs.get(result.receipt_id)?.merchant_name) : null;
            const { icon: Icon, label } = dispositionDisplay(result.disposition, result.decided);
            return (
              <li key={result.receipt_id}>
                <button
                  type="button"
                  onClick={() => onSelect(result.receipt_id)}
                  aria-current={selected ? "true" : undefined}
                  aria-label={`${result.receipt_id}${merchant ? `, ${merchant}` : ""}: ${label}`}
                  className={cn(
                    "flex w-full items-center gap-2.5 border-l-4 px-3 py-2.5 text-left outline-none hover:bg-muted/50 focus-visible:bg-muted/60 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                    TONE[result.disposition],
                    selected && "bg-primary-soft hover:bg-primary-soft",
                  )}
                >
                  <span className="min-w-0 flex-1">
                    <span className={cn("block font-mono text-[13px] font-semibold", selected ? "text-primary" : "text-foreground")}>{result.receipt_id}</span>
                    {merchant ? <span className="block truncate text-xs text-muted-foreground">{merchant}</span> : null}
                  </span>
                  <span
                    className={cn(
                      "inline-flex shrink-0 items-center gap-1 text-xs font-medium",
                      result.disposition === "flag" && "text-status-flag-text",
                      result.disposition === "review" && "text-status-review-text",
                      result.disposition === "pass" && "text-status-pass",
                    )}
                  >
                    <Icon className="size-3.5" aria-hidden />
                    {label}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
