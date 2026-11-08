import { History, Wallet } from "lucide-react";
import type { EvaluationBudget, EvaluationDetail, SpendTotals } from "@/api/types";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatInteger, formatUsd, humanize } from "@/lib/format";
import { newSpendText, recordedCostText, Stat } from "./display";

/** Only the limits the run was given: one left out was no limit of that kind. */
function limitsText(budget: EvaluationBudget): string {
  const parts = [
    budget.max_spend_usd != null ? `${formatUsd(budget.max_spend_usd)} spending` : null,
    budget.max_provider_calls != null ? `${formatInteger(budget.max_provider_calls)} provider requests` : null,
    budget.max_receipts != null ? `${formatInteger(budget.max_receipts)} receipts` : null,
  ].filter(Boolean);
  return parts.join(", ");
}

function tokens(value: number | null) {
  return value == null ? "—" : formatInteger(value);
}

function Breakdown({ title, rows, live }: { title: string; rows: Record<string, SpendTotals>; live: boolean }) {
  const entries = Object.entries(rows);
  if (!entries.length) return null;
  return (
    <div className="overflow-x-auto rounded-md border border-border">
      <Table>
        <caption className="sr-only">{title}</caption>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead>{title}</TableHead>
            <TableHead className="text-right">Calls</TableHead>
            <TableHead className="text-right">Live · cached · replayed</TableHead>
            <TableHead className="text-right">Failed</TableHead>
            {live ? <TableHead className="text-right">Tokens in · out</TableHead> : null}
            <TableHead className="text-right">{live ? "New spend" : "Recorded cost"}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {entries.map(([name, spend]) => (
            <TableRow key={name}>
              <TableCell className="font-mono text-xs">{name}</TableCell>
              <TableCell className="tabular text-right">{formatInteger(spend.calls.total)}</TableCell>
              <TableCell className="tabular text-right text-xs text-muted-foreground">
                {spend.calls.live} · {spend.calls.cached} · {spend.calls.replayed}
              </TableCell>
              <TableCell className="tabular text-right">{spend.calls.failed}</TableCell>
              {live ? (
                <TableCell className="tabular text-right text-xs">
                  {tokens(spend.tokens.input)} · {tokens(spend.tokens.output)}
                </TableCell>
              ) : null}
              <TableCell className="tabular text-right">{live ? newSpendText("live", spend) : recordedCostText(spend.historical)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

/**
 * What the run cost. Replay reuses recorded answers: it makes no provider call and adds no spending, and the
 * cost those answers had when recorded is shown apart, as history. Live spending is a lower bound whenever some
 * call has neither a reported cost nor a price.
 */
export function SpendingView({ job }: { job: EvaluationDetail }) {
  const { spend } = job;
  const live = job.execution_mode === "live";
  return (
    <div className="space-y-4">
      {live ? (
        <>
          <dl className="grid grid-cols-2 gap-3 md:grid-cols-5">
            <Stat
              label="New spend"
              value={newSpendText("live", spend)}
              hint={spend.cost_completeness === "incomplete" ? `${spend.unpriced_usage.calls} calls have no known cost, so this is a lower bound` : "Every live call's cost is known"}
            />
            <Stat label="Provider-reported" value={formatUsd(spend.actual_cost_usd)} hint="Costs the provider reported" />
            <Stat label="Estimated" value={formatUsd(spend.estimated_cost_usd)} hint={spend.pricing_versions.length ? `From pricing ${spend.pricing_versions.join(", ")}` : "No price estimates"} />
            <Stat
              label="Unknown cost"
              value={`${formatInteger(spend.unpriced_usage.calls)} calls`}
              hint={spend.unpriced_usage.calls ? `Not in the total: ${tokens(spend.unpriced_usage.input_tokens)} tokens in, ${tokens(spend.unpriced_usage.output_tokens)} out` : "Every call's cost is known"}
            />
            <Stat label="Live calls" value={formatInteger(spend.calls.live)} hint={`${spend.calls.failed} failed · tokens ${tokens(spend.tokens.input)} in, ${tokens(spend.tokens.output)} out`} />
          </dl>
          <div className="flex gap-2.5 rounded-md border border-border px-3 py-2 text-[13px]">
            <Wallet className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
            <p>
              {job.budget ? <>Limits: {limitsText(job.budget)}.</> : <span className="font-medium text-status-flag-text">Uncapped: no spending, request or receipt limit was set.</span>} Used{" "}
              {newSpendText("live", spend)}, {formatInteger(spend.calls.live)} calls, {formatInteger(job.progress.processed_receipts)} receipts.
              {job.status === "budget_exhausted" && job.stop_reason ? <span className="font-medium text-status-review-text"> Stopped at a limit: {humanize(job.stop_reason)}.</span> : null}
            </p>
          </div>
        </>
      ) : (
        <div className="flex gap-2.5 rounded-md border border-border bg-muted/50 px-3 py-2 text-[13px]">
          <History className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
          <p>
            <span className="font-semibold">Replay: no new spending.</span> {formatInteger(spend.calls.replayed)} recorded answers were read; no provider was called.{" "}
            {spend.historical.cost_usd == null
              ? `The recordings hold no cost for ${formatInteger(spend.historical.calls_without_cost)} of them.`
              : `When recorded, ${formatInteger(spend.historical.calls_with_cost)} of them cost ${formatUsd(spend.historical.cost_usd)}${spend.historical.calls_without_cost ? `; ${formatInteger(spend.historical.calls_without_cost)} have no recorded cost` : ""}.`}
          </p>
        </div>
      )}
      <Breakdown title="By purpose" rows={spend.by_purpose} live={live} />
      <Breakdown title="By model" rows={spend.by_model} live={live} />
      <Breakdown title="By backend" rows={spend.by_backend} live={live} />
    </div>
  );
}
