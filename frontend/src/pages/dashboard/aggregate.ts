/**
 * Dashboard aggregates. The API has no aggregate endpoints, so anything summed here is summed over one
 * page of a list, and carries a Scope saying exactly which records it covers. Nothing is extrapolated.
 */
import type { Disposition, EvaluationStatus, EvaluationSummary, FindingCounts, Page, RunSummary } from "@/api/types";
import { sumDecimals } from "@/lib/decimal";

/** The largest page the list endpoints return (limit le=100). */
export const SAMPLE_LIMIT = 100;

export interface Scope {
  covered: number;
  total: number;
  complete: boolean;
}

export function scopeOf<T>(page: Page<T>): Scope {
  return { covered: page.items.length, total: page.total, complete: page.offset === 0 && page.items.length >= page.total };
}

/** "all 12 runs" or "the latest 100 of 240 runs". */
export function describeScope(scope: Scope, noun: string, plural = `${noun}s`): string {
  const word = (n: number) => (n === 1 ? noun : plural);
  if (scope.complete) return scope.total === 1 ? `the only ${noun}` : `all ${scope.total} ${word(scope.total)}`;
  return `the latest ${scope.covered} of ${scope.total} ${word(scope.total)}`;
}

export interface RunAggregate {
  receipts: number;
  dispositions: Record<Disposition, number>;
  /** Summed over runs that record counts; runs stored before counts existed are counted apart. */
  counts: FindingCounts;
  runsWithCounts: number;
  runsWithoutCounts: number;
}

export function aggregateRuns(runs: readonly RunSummary[]): RunAggregate {
  const dispositions: Record<Disposition, number> = { flag: 0, review: 0, pass: 0 };
  const counts: FindingCounts = { exception: 0, review_signal: 0, open_question: 0, uncertainty: 0 };
  let receipts = 0;
  let runsWithCounts = 0;
  for (const run of runs) {
    receipts += run.receipt_count;
    for (const key of Object.keys(dispositions) as Disposition[]) dispositions[key] += run.dispositions[key] ?? 0;
    if (run.counts) {
      runsWithCounts += 1;
      for (const key of Object.keys(counts) as (keyof FindingCounts)[]) counts[key] += run.counts[key];
    }
  }
  return { receipts, dispositions, counts, runsWithCounts, runsWithoutCounts: runs.length - runsWithCounts };
}

export interface SpendAggregate {
  /** Exact sum of new_spend_usd, as decimal text. A lower bound when any evaluation's cost is incomplete. */
  newSpendUsd: string;
  incomplete: number;
  liveCalls: number;
  liveEvaluations: number;
  replayEvaluations: number;
}

export function aggregateSpend(evaluations: readonly EvaluationSummary[]): SpendAggregate {
  return {
    newSpendUsd: sumDecimals(evaluations.map((item) => item.spend_summary.new_spend_usd)),
    incomplete: evaluations.filter((item) => item.spend_summary.cost_completeness === "incomplete").length,
    liveCalls: evaluations.reduce((sum, item) => sum + item.spend_summary.live_calls, 0),
    liveEvaluations: evaluations.filter((item) => item.execution_mode === "live").length,
    replayEvaluations: evaluations.filter((item) => item.execution_mode === "replay").length,
  };
}

export function countStatuses(evaluations: readonly EvaluationSummary[]): Partial<Record<EvaluationStatus, number>> {
  const counts: Partial<Record<EvaluationStatus, number>> = {};
  for (const item of evaluations) counts[item.status] = (counts[item.status] ?? 0) + 1;
  return counts;
}
