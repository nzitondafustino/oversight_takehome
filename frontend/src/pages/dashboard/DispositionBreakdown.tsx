import type { UseQueryResult } from "@tanstack/react-query";
import { PieChart as PieIcon } from "lucide-react";
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { Page, RunSummary } from "@/api/types";
import { EmptyState, ErrorState, LoadingRows } from "@/components/states/states";
import { DISPOSITION_ORDER, DISPOSITIONS } from "@/components/status/badges";
import { formatInteger, formatRate } from "@/lib/format";
import { aggregateRuns, describeScope, scopeOf } from "./aggregate";
import { Section } from "./Section";

interface Row {
  key: (typeof DISPOSITION_ORDER)[number];
  label: string;
  value: number;
  share: number;
}

function ChartTooltip({ active, payload }: { active?: boolean; payload?: { payload: Row }[] }) {
  const row = payload?.[0]?.payload;
  if (!active || !row) return null;
  return (
    <div className="rounded-md border border-border bg-popover px-3 py-2 text-xs shadow-md">
      <p className="font-medium text-foreground">{row.label}</p>
      <p className="tabular text-muted-foreground">
        {formatInteger(row.value)} receipts · {formatRate(row.share)}
      </p>
    </div>
  );
}

const FINDING_LABELS = {
  exception: "Exceptions",
  review_signal: "Review signals",
  open_question: "Open questions",
  uncertainty: "Uncertainty records",
} as const;

export function DispositionBreakdown({ runs }: { runs: UseQueryResult<Page<RunSummary>> }) {
  let body;
  let description: string = "Receipt dispositions from stored runs.";
  if (runs.isPending) {
    body = <LoadingRows rows={4} label="Loading dispositions" />;
  } else if (runs.isError) {
    body = <ErrorState error={runs.error} onRetry={() => void runs.refetch()} />;
  } else if (runs.data.items.length === 0) {
    body = <EmptyState icon={<PieIcon className="size-5" aria-hidden />} title="No dispositions yet" description="Validate receipts to see how they were disposed." />;
  } else {
    const scope = scopeOf(runs.data);
    const aggregate = aggregateRuns(runs.data.items);
    const total = DISPOSITION_ORDER.reduce((sum, key) => sum + aggregate.dispositions[key], 0);
    const rows: Row[] = DISPOSITION_ORDER.map((key) => ({
      key,
      label: DISPOSITIONS[key].label,
      value: aggregate.dispositions[key],
      share: total ? aggregate.dispositions[key] / total : 0,
    }));
    description = `Receipts across ${describeScope(scope, "run")}. A receipt in several runs counts once per run.`;
    body = (
      <div className="space-y-4">
        <div className="h-36" role="img" aria-label={rows.map((row) => `${row.label}: ${row.value}`).join(", ")}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={rows} layout="vertical" margin={{ top: 0, right: 12, bottom: 0, left: 0 }} barCategoryGap={8}>
              <XAxis type="number" hide allowDecimals={false} />
              <YAxis
                type="category"
                dataKey="label"
                width={56}
                axisLine={false}
                tickLine={false}
                tick={{ fill: "var(--color-muted-foreground)", fontSize: 12 }}
              />
              <Tooltip cursor={{ fill: "var(--color-muted)" }} content={<ChartTooltip />} />
              <Bar dataKey="value" radius={[0, 4, 4, 0]} maxBarSize={22} isAnimationActive={false}>
                {rows.map((row) => (
                  <Cell key={row.key} fill={DISPOSITIONS[row.key].fill} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* The same numbers as text: identity never rests on color alone. */}
        <table className="w-full text-[13px]">
          <caption className="sr-only">Receipt dispositions</caption>
          <tbody>
            {rows.map((row) => {
              const Icon = DISPOSITIONS[row.key].icon;
              return (
                <tr key={row.key} className="border-b border-border last:border-0">
                  <th scope="row" className="py-1.5 text-left font-normal">
                    <span className="inline-flex items-center gap-2 text-foreground">
                      <span aria-hidden className="size-2.5 rounded-sm" style={{ background: DISPOSITIONS[row.key].fill }} />
                      <Icon className="size-3.5 text-subtle-foreground" aria-hidden />
                      {row.label}
                    </span>
                  </th>
                  <td className="tabular py-1.5 text-right font-medium">{formatInteger(row.value)}</td>
                  <td className="tabular w-16 py-1.5 text-right text-subtle-foreground">{formatRate(row.share)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>

        <div>
          <p className="mb-1.5 text-[11px] font-medium uppercase tracking-wider text-subtle-foreground">Findings</p>
          {aggregate.runsWithCounts > 0 ? (
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-[13px]">
              {(Object.keys(FINDING_LABELS) as (keyof typeof FINDING_LABELS)[]).map((key) => (
                <div key={key} className="flex items-baseline justify-between gap-2">
                  <dt className="text-muted-foreground">{FINDING_LABELS[key]}</dt>
                  <dd className="tabular font-medium text-foreground">{formatInteger(aggregate.counts[key])}</dd>
                </div>
              ))}
            </dl>
          ) : null}
          <p className="mt-2 text-xs leading-4 text-subtle-foreground">
            {aggregate.runsWithoutCounts > 0
              ? `${aggregate.runsWithoutCounts} run${aggregate.runsWithoutCounts === 1 ? " was" : "s were"} stored before counts were recorded and ${aggregate.runsWithoutCounts === 1 ? "is" : "are"} not included. `
              : ""}
            Open questions are undecided rules, not violations.
          </p>
        </div>
      </div>
    );
  }

  return (
    <Section id="dispositions" title="Disposition breakdown" description={description}>
      {body}
    </Section>
  );
}
