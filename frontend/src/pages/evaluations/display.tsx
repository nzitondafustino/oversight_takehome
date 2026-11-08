/** Small display pieces shared by the evaluation pages. Values are shown as the API stored them. */
import type { ReactNode } from "react";
import type { Rate, SpendTotals } from "@/api/types";
import { formatInteger, formatRate, formatUsd } from "@/lib/format";
import { cn } from "@/lib/utils";

export const SELECT =
  "h-9 w-full rounded-md border border-input bg-card px-2 text-[13px] text-foreground focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring disabled:opacity-60";

/** "92.4%", or "—" for a rate over nothing, which is never 0. */
export function rateText(rate: Rate | null | undefined): string {
  return formatRate(rate?.value);
}

/** The Wilson 95% interval and the counts behind a rate. */
export function rateDetail(rate: Rate | null | undefined): string {
  if (!rate) return "nothing to score";
  if (rate.denominator === 0) return "0 scored";
  const interval = rate.wilson_95 ? ` · 95% CI ${formatRate(rate.wilson_95[0])}–${formatRate(rate.wilson_95[1])}` : "";
  return `${formatInteger(rate.numerator)}/${formatInteger(rate.denominator)}${interval}`;
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="rounded-md border border-border bg-background/60 px-3 py-2.5">
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd className="tabular mt-1 text-lg font-semibold tracking-tight text-foreground">{value}</dd>
      {hint ? <dd className="mt-0.5 text-[11px] leading-4 text-subtle-foreground">{hint}</dd> : null}
    </div>
  );
}

export function RateStat({ label, rate, hint }: { label: string; rate: Rate | null | undefined; hint?: string }) {
  return <Stat label={label} value={rateText(rate)} hint={hint ? `${hint} · ${rateDetail(rate)}` : rateDetail(rate)} />;
}

export function Facts({ items, className }: { items: [string, ReactNode][]; className?: string }) {
  return (
    <dl className={cn("grid gap-x-6 gap-y-2 text-[13px] sm:grid-cols-2", className)}>
      {items.map(([label, value]) => (
        <div key={label} className="min-w-0">
          <dt className="text-xs text-subtle-foreground">{label}</dt>
          <dd className="break-words text-foreground">{value ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * A run's new spending in words. Replay reuses recorded answers, so it is never described as a new provider call
 * or a charge; live spending is a lower bound whenever some call has no known cost.
 */
export function newSpendText(mode: string, spend: Pick<SpendTotals, "new_spend_usd" | "cost_completeness">): string {
  if (mode !== "live") return "No new spend (replay)";
  return `${formatUsd(spend.new_spend_usd)}${spend.cost_completeness === "incomplete" ? " (lower bound)" : ""}`;
}

/** The cost recorded with replayed answers: history, and a lower bound when some recorded calls hold no cost. */
export function recordedCostText(historical: SpendTotals["historical"]): string {
  if (historical.cost_usd == null) return historical.calls_without_cost ? `Not recorded (${historical.calls_without_cost} calls)` : "—";
  const total = formatUsd(historical.cost_usd);
  return historical.calls_without_cost ? `at least ${total} (${historical.calls_without_cost} calls without a recorded cost)` : total;
}

export const MODE_TEXT: Record<"replay" | "live", string> = {
  replay: "Reads recorded model answers. No provider is called and nothing new is spent; a receipt with no recorded answer fails, it is never answered live.",
  live: "Calls the classification providers for every receipt. It costs money, up to the limits you set below, and needs the deployment's permission.",
};
