import type { UseQueryResult } from "@tanstack/react-query";
import { Gauge } from "lucide-react";
import { Link } from "react-router-dom";
import type { EvaluationKeyMetrics, EvaluationSummary, Page } from "@/api/types";
import { EmptyState, ErrorState, LoadingRows } from "@/components/states/states";
import { ModeBadge } from "@/components/status/badges";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDateTime, formatRate, formatRelative, formatScore } from "@/lib/format";
import { adapterText, modelText, setupHeadline } from "@/pages/validate/config";
import { Section } from "./Section";

const SHOWN = 5;

const METRICS: { key: keyof EvaluationKeyMetrics; label: string; hint: string; format: (v: number | null) => string }[] = [
  { key: "category_accuracy", label: "Category accuracy", hint: "Correct lines over labelled lines", format: (v) => formatRate(v) },
  { key: "macro_f1", label: "Macro F1", hint: "Mean F1 over expected categories", format: (v) => formatScore(v) },
  { key: "disposition_accuracy", label: "Disposition accuracy", hint: "Receipts with the expected disposition", format: (v) => formatRate(v) },
  { key: "coverage", label: "Coverage", hint: "Lines given a category", format: (v) => formatRate(v) },
  { key: "uncertainty_rate", label: "Uncertainty rate", hint: "Receipts with an EXC-03 signal", format: (v) => formatRate(v) },
  { key: "execution_error_rate", label: "Execution errors", hint: "Receipts that could not be evaluated", format: (v) => formatRate(v) },
];

export function QualityMetrics({ evaluations }: { evaluations: UseQueryResult<Page<EvaluationSummary>> }) {
  let body;
  let description = "Stored key metrics of completed evaluations.";
  const completed = (evaluations.data?.items ?? []).filter((item) => item.status === "completed" && item.summary);
  const latest = completed[0];

  if (evaluations.isPending) {
    body = <LoadingRows rows={4} label="Loading quality metrics" />;
  } else if (evaluations.isError) {
    body = <ErrorState error={evaluations.error} onRetry={() => void evaluations.refetch()} />;
  } else if (!latest?.summary) {
    body = (
      <EmptyState
        icon={<Gauge className="size-5" aria-hidden />}
        title="No completed evaluations"
        description="Quality metrics appear once an evaluation job completes. Metrics are never averaged across runs."
      />
    );
  } else {
    const summary = latest.summary;
    description = "Each evaluation's own stored metrics, never averaged across runs. A dash means there was nothing to score.";
    body = (
      <div className="space-y-5">
        <div>
          <div className="mb-2.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px]">
            <span className="text-muted-foreground">Latest completed:</span>
            <Link to={`/evaluations/${encodeURIComponent(latest.evaluation_id)}`} className="font-medium text-primary hover:underline">
              {latest.label ?? latest.evaluation_id}
            </Link>
            <span className="text-xs text-subtle-foreground" title={formatDateTime(latest.completed_at)}>
              {latest.dataset.name} v{latest.dataset.version} · {latest.dataset.receipts} receipts · completed {formatRelative(latest.completed_at)}
            </span>
          </div>
          <dl className="grid grid-cols-2 gap-3 md:grid-cols-3 2xl:grid-cols-6">
            {METRICS.map((metric) => (
              <div key={metric.key} className="rounded-md border border-border bg-background/60 px-3 py-2.5">
                <dt className="text-xs font-medium text-muted-foreground">{metric.label}</dt>
                <dd className="tabular mt-1 text-xl font-semibold tracking-tight text-foreground">{metric.format(summary[metric.key])}</dd>
                <dd className="mt-0.5 text-[11px] leading-4 text-subtle-foreground">{metric.hint}</dd>
              </div>
            ))}
          </dl>
        </div>

        {completed.length > 1 ? (
          <div className="-mx-5 border-y border-border">
            <Table>
              <caption className="sr-only">Recent completed evaluations</caption>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="pl-5">Evaluation</TableHead>
                  <TableHead>Backend</TableHead>
                  <TableHead className="text-right">Category acc.</TableHead>
                  <TableHead className="text-right">Macro F1</TableHead>
                  <TableHead className="text-right">Disposition acc.</TableHead>
                  <TableHead className="pr-5 text-right">Uncertainty</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {completed.slice(0, SHOWN).map((item) => (
                  <TableRow key={item.evaluation_id}>
                    <TableCell className="min-w-44 pl-5">
                      <Link to={`/evaluations/${encodeURIComponent(item.evaluation_id)}`} className="font-medium text-primary hover:underline">
                        {item.label ?? item.evaluation_id}
                      </Link>
                      <p className="text-xs text-subtle-foreground">
                        {item.dataset.name} v{item.dataset.version} · {item.dataset.receipts} receipts
                      </p>
                    </TableCell>
                    <TableCell>
                      <span className="inline-flex items-center gap-1.5">
                        <span className="font-medium" title={adapterText(item.configuration.setup, item.configuration.classification_backend)}>{setupHeadline(item.configuration.setup, item.configuration.classification_backend)}</span>
                        <ModeBadge mode={item.execution_mode} />
                      </span>
                      <p className="max-w-48 truncate text-xs text-subtle-foreground" title={modelText(item.configuration.setup, item.configuration.model_label)}>
                        {modelText(item.configuration.setup, item.configuration.model_label)}
                      </p>
                    </TableCell>
                    <TableCell className="tabular text-right">{formatRate(item.summary?.category_accuracy)}</TableCell>
                    <TableCell className="tabular text-right">{formatScore(item.summary?.macro_f1)}</TableCell>
                    <TableCell className="tabular text-right">{formatRate(item.summary?.disposition_accuracy)}</TableCell>
                    <TableCell className="tabular pr-5 text-right">{formatRate(item.summary?.uncertainty_rate)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        ) : null}
        <p className="text-xs leading-4 text-subtle-foreground">
          Point estimates as stored. Runs on different datasets or rulesets are not directly comparable; full metrics with Wilson 95% intervals are in each evaluation.
        </p>
      </div>
    );
  }

  return (
    <Section id="quality" title="Quality metrics" description={description} link={{ to: "/evaluations", label: "Evaluations" }} className="xl:col-span-3">
      {body}
    </Section>
  );
}
