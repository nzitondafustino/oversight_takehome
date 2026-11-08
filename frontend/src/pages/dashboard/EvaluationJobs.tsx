import type { UseQueryResult } from "@tanstack/react-query";
import { FlaskConical, RadioTower } from "lucide-react";
import { Link } from "react-router-dom";
import { useActiveEvaluations } from "@/api/queries";
import { isActiveEvaluation, type EvaluationStatus, type EvaluationSummary, type Page } from "@/api/types";
import { EmptyState, ErrorState, LoadingRows } from "@/components/states/states";
import { EvaluationStatusBadge, ModeBadge } from "@/components/status/badges";
import { Progress } from "@/components/ui/misc";
import { formatDateTime, formatInteger, formatRelative, formatUsd, humanize } from "@/lib/format";
import { countStatuses, describeScope, scopeOf } from "./aggregate";
import { modelText, setupHeadline } from "@/pages/validate/config";
import { Section } from "./Section";

const SHOWN = 6;
const STATUS_ORDER: EvaluationStatus[] = ["running", "queued", "cancel_requested", "completed", "failed", "budget_exhausted", "cancelled"];

function JobRow({ job, live }: { job: EvaluationSummary; live: boolean }) {
  const { progress } = job;
  return (
    <li className="py-3 first:pt-0 last:pb-0">
      <div className="flex items-start justify-between gap-x-3 gap-y-1.5">
        <div className="min-w-0 flex-1">
          <Link to={`/evaluations/${encodeURIComponent(job.evaluation_id)}`} className="font-medium text-foreground hover:text-primary hover:underline">
            {job.label ?? job.evaluation_id}
          </Link>
          <p className="mt-0.5 truncate text-xs text-subtle-foreground" title={modelText(job.configuration.setup, job.configuration.model_label)}>
            {job.dataset.name} v{job.dataset.version} · {job.ruleset.name} v{job.ruleset.version} · {setupHeadline(job.configuration.setup, job.configuration.classification_backend)}
            {job.configuration.setup || job.configuration.model_label ? ` (${modelText(job.configuration.setup, job.configuration.model_label)})` : ""}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <ModeBadge mode={job.execution_mode} preview={job.preview} />
          <EvaluationStatusBadge status={job.status} />
        </div>
      </div>
      <div className="mt-2 flex items-center gap-3">
        <Progress value={progress.percent} label={`${job.label ?? job.evaluation_id} progress`} className="flex-1" />
        <span className="tabular w-40 shrink-0 text-right text-xs text-muted-foreground">
          {formatInteger(progress.processed_receipts)}/{formatInteger(progress.total_receipts)} receipts
          {progress.failed_receipts ? <span className="text-status-flag-text"> · {progress.failed_receipts} failed</span> : null}
        </span>
      </div>
      <p className="mt-1 flex flex-wrap items-center gap-x-2 text-xs text-subtle-foreground">
        <span title={formatDateTime(job.created_at)}>Created {formatRelative(job.created_at)}</span>
        {job.execution_mode === "live" ? <span>· New spend {formatUsd(job.spend_summary.new_spend_usd)}{job.spend_summary.cost_completeness === "incomplete" ? " (lower bound)" : ""}</span> : null}
        {job.stop_reason ? <span>· Stopped: {humanize(job.stop_reason)}</span> : null}
        {job.error?.code ? <span className="text-status-flag-text">· {job.error.code}</span> : null}
        {live ? (
          <span className="inline-flex items-center gap-1 text-primary">
            · <RadioTower className="size-3" aria-hidden /> Live updates
          </span>
        ) : null}
      </p>
    </li>
  );
}

export function EvaluationJobs({ evaluations }: { evaluations: UseQueryResult<Page<EvaluationSummary>> }) {
  const items = evaluations.data?.items ?? [];
  const polled = useActiveEvaluations(items);
  const latest = new Map(polled.flatMap(({ evaluationId, query }) => (query.data ? [[evaluationId, query.data] as const] : [])));

  let body;
  let description = "Background evaluation jobs, newest first.";
  if (evaluations.isPending) {
    body = <LoadingRows rows={4} label="Loading evaluations" />;
  } else if (evaluations.isError) {
    body = <ErrorState error={evaluations.error} onRetry={() => void evaluations.refetch()} />;
  } else if (items.length === 0) {
    body = (
      <EmptyState
        icon={<FlaskConical className="size-5" aria-hidden />}
        title="No evaluation jobs yet"
        description="Evaluations score a labelled dataset against one ruleset version, in the background."
      />
    );
  } else {
    const statuses = countStatuses(items);
    const scope = scopeOf(evaluations.data!);
    description = `Status counts over ${describeScope(scope, "job")}. Active jobs refresh every 2 s until they finish.`;
    body = (
      <>
        <ul className="mb-4 flex flex-wrap gap-2" aria-label="Jobs by status">
          {STATUS_ORDER.filter((status) => statuses[status]).map((status) => (
            <li key={status} className="inline-flex items-center gap-1.5 text-xs">
              <EvaluationStatusBadge status={status} />
              <span className="tabular font-semibold text-foreground">{statuses[status]}</span>
            </li>
          ))}
        </ul>
        <ul className="divide-y divide-border">
          {items.slice(0, SHOWN).map((job) => {
            const current = latest.get(job.evaluation_id) ?? job;
            return <JobRow key={job.evaluation_id} job={current} live={isActiveEvaluation(current.status)} />;
          })}
        </ul>
      </>
    );
  }

  return (
    <Section id="evaluation-jobs" title="Evaluation jobs" description={description} link={{ to: "/evaluations", label: "All evaluations" }} className="xl:col-span-2">
      {body}
    </Section>
  );
}
