import type { UseQueryResult } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Wallet } from "lucide-react";
import type { EvaluationSummary, Page } from "@/api/types";
import { EmptyState, ErrorState, LoadingRows } from "@/components/states/states";
import { formatInteger, formatUsd } from "@/lib/format";
import { aggregateSpend, describeScope, scopeOf } from "./aggregate";
import { Section } from "./Section";

export function SpendingSummary({ evaluations }: { evaluations: UseQueryResult<Page<EvaluationSummary>> }) {
  let body;
  let description = "New provider spending reported by evaluation jobs.";
  if (evaluations.isPending) {
    body = <LoadingRows rows={3} label="Loading spending" />;
  } else if (evaluations.isError) {
    body = <ErrorState error={evaluations.error} onRetry={() => void evaluations.refetch()} />;
  } else if (evaluations.data.items.length === 0) {
    body = <EmptyState icon={<Wallet className="size-5" aria-hidden />} title="No spending recorded" description="Spending is tracked per evaluation job, call and receipt." />;
  } else {
    const scope = scopeOf(evaluations.data);
    const spend = aggregateSpend(evaluations.data.items);
    const lowerBound = spend.incomplete > 0;
    description = `Summed exactly over ${describeScope(scope, "evaluation")}.`;
    body = (
      <div className="space-y-4">
        <div>
          <p className="text-[13px] text-muted-foreground">New spend{lowerBound ? " (at least)" : ""}</p>
          <p className="tabular mt-0.5 text-3xl font-semibold tracking-tight text-foreground">
            {lowerBound ? "≥ " : ""}
            {formatUsd(spend.newSpendUsd)}
          </p>
        </div>

        {lowerBound ? (
          <div className="flex gap-2 rounded-md border border-status-review-border bg-status-review-soft px-3 py-2 text-xs leading-5">
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-status-review-text" aria-hidden />
            <p>
              <span className="font-medium text-status-review-text">Accounting incomplete.</span> {spend.incomplete} evaluation{spend.incomplete === 1 ? " has" : "s have"} live calls
              with no reported or estimated cost, so this total is a lower bound.
            </p>
          </div>
        ) : (
          <div className="flex gap-2 rounded-md border border-status-pass-border bg-status-pass-soft px-3 py-2 text-xs leading-5">
            <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-status-pass" aria-hidden />
            <p>
              <span className="font-medium text-status-pass">Accounting complete.</span> Every live call in scope has a provider-reported or estimated cost.
            </p>
          </div>
        )}

        <dl className="grid grid-cols-3 gap-3 border-t border-border pt-3 text-[13px]">
          <div>
            <dt className="text-xs text-subtle-foreground">Live calls</dt>
            <dd className="tabular mt-0.5 font-semibold">{formatInteger(spend.liveCalls)}</dd>
          </div>
          <div>
            <dt className="text-xs text-subtle-foreground">Live jobs</dt>
            <dd className="tabular mt-0.5 font-semibold">{formatInteger(spend.liveEvaluations)}</dd>
          </div>
          <div>
            <dt className="text-xs text-subtle-foreground">Replay jobs</dt>
            <dd className="tabular mt-0.5 font-semibold">{formatInteger(spend.replayEvaluations)}</dd>
          </div>
        </dl>
        <p className="text-xs leading-4 text-subtle-foreground">
          Replay jobs reuse recorded answers and add no new spend; their historical cost is shown per evaluation. Validation runs report no spending.
        </p>
      </div>
    );
  }

  return (
    <Section id="spending" title="Spending" description={description}>
      {body}
    </Section>
  );
}
