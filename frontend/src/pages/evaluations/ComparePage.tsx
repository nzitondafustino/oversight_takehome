import { evaluationComparisonExportUrl } from "@/api/endpoints";
import { ExportButtons } from "@/components/results/ValidationResults";
import { AlertTriangle, ArrowLeft, CheckCircle2, GitCompare } from "lucide-react";
import type { ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useEvaluationComparison } from "@/api/queries";
import type { EvaluationComparison, EvaluationComparisonRow, Rate } from "@/api/types";
import { PageHeader } from "@/components/layout/AppLayout";
import { EmptyState, ErrorState, LoadingRows } from "@/components/states/states";
import { ModeBadge } from "@/components/status/badges";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { formatInteger, formatScore, formatUsd, shortHash } from "@/lib/format";
import { cn } from "@/lib/utils";
import { adapterIdentity, adapterText, modelText, setupHeadline } from "@/pages/validate/config";
import { CompareReport } from "./CompareReport";
import { newSpendText, rateDetail, rateText, recordedCostText } from "./display";

type Row = { label: string; hint?: string; cell: (run: EvaluationComparisonRow, baseline: EvaluationComparisonRow) => ReactNode; differs?: (run: EvaluationComparisonRow, baseline: EvaluationComparisonRow) => boolean };

/** A signed difference from the first run, in percentage points for a rate. */
function delta(value: number | null | undefined, base: number | null | undefined, unit: "pp" | "ms" | "") {
  if (value == null || base == null || value === base) return null;
  const difference = unit === "pp" ? (value - base) * 100 : value - base;
  const text = unit === "ms" ? formatInteger(Math.abs(difference)) : Math.abs(difference).toFixed(unit === "pp" ? 1 : 3);
  return <span className="block text-[11px] text-subtle-foreground">{`${difference > 0 ? "+" : "−"}${text}${unit ? ` ${unit}` : ""} vs first`}</span>;
}

function RateCell({ rate, base }: { rate: Rate | null; base: Rate | null }) {
  return (
    <>
      <span className="font-medium">{rateText(rate)}</span>
      <span className="block text-[11px] text-subtle-foreground">{rateDetail(rate)}</span>
      {delta(rate?.value, base?.value, "pp")}
    </>
  );
}

const ROWS: { group: string; rows: Row[] }[] = [
  {
    group: "What ran",
    rows: [
      { label: "Mode", cell: (run) => <ModeBadge mode={run.execution_mode} />, differs: (run, base) => run.execution_mode !== base.execution_mode },
      { label: "Orchestrator · provider", cell: (run) => setupHeadline(run.setup, run.backend), differs: (run, base) => setupHeadline(run.setup, run.backend) !== setupHeadline(base.setup, base.backend) },
      // Matched on the adapter itself, so a run recorded as jev and one as decisions are the same adapter.
      { label: "Adapter", hint: "The execution adapter each run's model needed", cell: (run) => adapterText(run.setup, run.backend), differs: (run, base) => adapterIdentity(run.setup, run.backend) !== adapterIdentity(base.setup, base.backend) },
      {
        label: "Classification model",
        cell: (run) => <span className="font-mono text-xs">{modelText(run.setup, run.model_label)}</span>,
        differs: (run, base) => (run.classification_identity ?? run.model_label) !== (base.classification_identity ?? base.model_label),
      },
      {
        label: "Translation model",
        hint: "The model each run translated with",
        cell: (run) => (run.translation_known === false ? <span className="text-status-review-text">Not recorded (an older evaluation)</span> : <span className="font-mono text-xs">{run.translation_model ?? "Off"}</span>),
        // An unrecorded translator never counts as the same.
        differs: (run, base) => run.translation_known === false || base.translation_known === false || run.translation_model !== base.translation_model,
      },
      {
        label: "Dataset",
        cell: (run) => <>{run.dataset.name} v{run.dataset.version} <span className="block font-mono text-[11px] text-subtle-foreground">{shortHash(run.dataset.canonical_sha256)}</span></>,
        differs: (run, base) => run.dataset.canonical_sha256 !== base.dataset.canonical_sha256,
      },
      {
        label: "Ruleset",
        cell: (run) => <>{run.ruleset.name} v{run.ruleset.version} <span className="block font-mono text-[11px] text-subtle-foreground">{shortHash(run.ruleset.artifact_sha256)}</span></>,
        differs: (run, base) => run.ruleset.artifact_sha256 !== base.ruleset.artifact_sha256,
      },
      { label: "Results", cell: (run) => (run.result_context === "dataset" ? "Dataset as one context" : "Receipts evaluated alone"), differs: (run, base) => run.result_context !== base.result_context },
    ],
  },
  {
    group: "Quality",
    rows: [
      { label: "Category accuracy", cell: (run, base) => <RateCell rate={run.category_accuracy} base={base.category_accuracy} /> },
      { label: "Macro F1", cell: (run, base) => <><span className="font-medium">{formatScore(run.macro_f1)}</span>{delta(run.macro_f1, base.macro_f1, "")}</> },
      { label: "Disposition accuracy", cell: (run, base) => <RateCell rate={run.disposition_accuracy} base={base.disposition_accuracy} /> },
      { label: "Uncertain receipts", hint: "With an EXC-03 signal", cell: (run, base) => <RateCell rate={run.uncertainty_rate} base={base.uncertainty_rate} /> },
    ],
  },
  {
    group: "Latency",
    rows: [
      { label: "Mean per receipt", cell: (run, base) => <>{run.mean_latency_per_receipt_ms == null ? "—" : `${formatInteger(run.mean_latency_per_receipt_ms)} ms`}{delta(run.mean_latency_per_receipt_ms, base.mean_latency_per_receipt_ms, "ms")}</> },
      { label: "Total", cell: (run, base) => <>{run.total_latency_ms == null ? "—" : `${formatInteger(run.total_latency_ms)} ms`}{delta(run.total_latency_ms, base.total_latency_ms, "ms")}</> },
    ],
  },
  {
    group: "Spending",
    rows: [
      {
        label: "Provider calls",
        hint: "live · cached · replayed (failed)",
        cell: (run) => (
          <span className="tabular">
            {run.provider_calls.live} · {run.provider_calls.cached} · {run.provider_calls.replayed} ({run.provider_calls.failed})
          </span>
        ),
      },
      { label: "New spend", cell: (run) => newSpendText(run.execution_mode, { new_spend_usd: run.new_spend_usd, cost_completeness: run.cost_completeness }) },
      {
        label: "Recorded cost of replayed answers",
        hint: "History, not new spending",
        cell: (run) => recordedCostText(run.historical_cost_usd),
      },
      {
        label: "Cost per receipt",
        hint: "Live runs with complete costs only",
        cell: (run) => (run.cost_per_receipt_usd == null ? <span className="text-subtle-foreground">{run.execution_mode === "live" ? "— (cost incomplete)" : "— (replay)"}</span> : formatUsd(run.cost_per_receipt_usd)),
      },
      {
        label: "Cost per correct prediction",
        cell: (run) => (run.cost_per_correct_prediction_usd == null ? <span className="text-subtle-foreground">—</span> : formatUsd(run.cost_per_correct_prediction_usd)),
      },
    ],
  },
];

const TRANSLATION_CODES = ["translation_model", "translation_model_unknown"];

function Comparability({ comparability }: { comparability: EvaluationComparison["comparability"] }) {
  const codes = comparability.issue_codes ?? [];
  const onlyTranslation = codes.length > 0 && codes.every((code) => TRANSLATION_CODES.includes(code));
  const issues = comparability.issues.map((text, index) => (TRANSLATION_CODES.includes(codes[index] ?? "") ? `Configuration differs — translation model: ${text}` : text));
  return comparability.directly_comparable ? (
    <div className="flex gap-2.5 rounded-md border border-status-pass-border bg-status-pass-soft px-3 py-2 text-[13px]">
      <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-status-pass" aria-hidden />
      <p>
        <span className="font-semibold">Directly comparable:</span> same dataset, ruleset, taxonomy, field catalog, core version and mode, and every receipt evaluated as one dataset. Still {comparability.note}.
      </p>
    </div>
  ) : (
    <div role="alert" className="flex gap-2.5 rounded-md border border-status-review-border bg-status-review-soft px-3 py-2 text-[13px]">
      <AlertTriangle className="mt-0.5 size-4 shrink-0 text-status-review-text" aria-hidden />
      <div>
        <p className="font-semibold text-status-review-text">{onlyTranslation ? "Configuration differs — translation model" : "Not directly comparable"}</p>
        <ul className="mt-1 list-inside list-disc">
          {issues.map((issue) => (
            <li key={issue}>{issue}</li>
          ))}
        </ul>
        <p className="mt-1 text-xs text-foreground/80">Differences below may come from these, not from the classifier. This is {comparability.note}.</p>
      </div>
    </div>
  );
}

export function ComparePage() {
  const [params] = useSearchParams();
  const ids = (params.get("ids") ?? "").split(",").filter(Boolean);
  const comparison = useEvaluationComparison(ids);
  const back = (
    <Button asChild variant="outline" size="sm">
      <Link to="/evaluations">
        <ArrowLeft aria-hidden />
        All evaluations
      </Link>
    </Button>
  );

  let body: ReactNode;
  if (ids.length < 2) body = <EmptyState icon={<GitCompare className="size-5" aria-hidden />} title="Choose 2 to 10 completed evaluations" description="Select them in the evaluation list, then choose Compare." />;
  else if (comparison.isPending) body = <LoadingRows rows={6} label="Comparing evaluations" />;
  else if (comparison.isError) body = <ErrorState error={comparison.error} onRetry={() => void comparison.refetch()} />;
  else {
    const runs = comparison.data.evaluations;
    const baseline = runs[0]!;
    body = (
      <div className="space-y-4">
        <Comparability comparability={comparison.data.comparability} />
        <p className="text-xs text-subtle-foreground">Differences are against the first run. A highlighted setting differs from it. Replayed runs repeat what the models said when recorded, not what they would say now.</p>
        <Card>
          <CardContent className="overflow-x-auto p-0">
            <table className="w-full text-[13px]">
              <caption className="sr-only">Evaluations side by side</caption>
              <thead>
                <tr className="border-b border-border">
                  <th scope="col" className="px-4 py-2 text-left text-xs font-medium text-muted-foreground">
                    Evaluation
                  </th>
                  {runs.map((run) => (
                    <th key={run.evaluation_id} scope="col" className="min-w-44 px-4 py-2 text-left">
                      <Link to={`/evaluations/${encodeURIComponent(run.evaluation_id)}`} className="font-medium text-primary hover:underline">
                        {run.label ?? run.evaluation_id}
                      </Link>
                    </th>
                  ))}
                </tr>
              </thead>
              {ROWS.map(({ group, rows }) => (
                <tbody key={group}>
                  <tr>
                    <th colSpan={runs.length + 1} scope="colgroup" className="bg-muted/50 px-4 py-1.5 text-left text-[11px] font-medium uppercase tracking-wider text-subtle-foreground">
                      {group}
                    </th>
                  </tr>
                  {rows.map((row) => (
                    <tr key={row.label} className="border-t border-border">
                      <th scope="row" className="px-4 py-2 text-left align-top text-xs font-medium text-muted-foreground">
                        {row.label}
                        {row.hint ? <span className="block font-normal text-subtle-foreground">{row.hint}</span> : null}
                      </th>
                      {runs.map((run) => {
                        const differs = run !== baseline && row.differs?.(run, baseline);
                        return (
                          <td key={run.evaluation_id} className={cn("px-4 py-2 align-top", differs && "bg-status-review-soft")} data-differs={differs || undefined}>
                            {row.cell(run, baseline)}
                            {differs ? <span className="block text-[11px] font-medium text-status-review-text">differs</span> : null}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              ))}
            </table>
          </CardContent>
        </Card>
        <CompareReport rows={runs} />
      </div>
    );
  }

  return (
    <>
      <PageHeader title="Compare evaluations" description="Completed runs side by side: what ran, what makes them comparable, and their full evaluation report." actions={<div className="flex flex-wrap gap-2">{back}<ExportButtons name="evaluation-comparison" formats={["pdf"]} urlFor={() => evaluationComparisonExportUrl(ids)} disabledReason={!comparison.data || comparison.isError ? "Choose 2 to 10 completed evaluations before exporting" : undefined} /></div>} />
      {body}
    </>
  );
}
