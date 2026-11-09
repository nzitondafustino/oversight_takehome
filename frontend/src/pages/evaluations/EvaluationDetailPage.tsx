import { RecordActions } from "@/components/management/RecordActions";
import { ArrowLeft, Ban, Loader2, RadioTower, RefreshCw } from "lucide-react";
import { useState } from "react";
import { Link, useParams, useNavigate } from "react-router-dom";
import { evaluationExportUrl } from "@/api/endpoints";
import { useCancelEvaluation, useEvaluation, useEvaluationMetrics } from "@/api/queries";
import { isActiveEvaluation, type EvaluationDetail } from "@/api/types";
import { PageHeader } from "@/components/layout/AppLayout";
import { ExportButtons } from "@/components/results/ValidationResults";
import { ErrorState, LoadingRows } from "@/components/states/states";
import { EvaluationStatusBadge, ModeBadge } from "@/components/status/badges";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/misc";
import { formatDateTime, formatInteger, formatRelative, humanize, shortHash } from "@/lib/format";
import { Section } from "@/pages/dashboard/Section";
import { Facts } from "./display";
import { MetricsView } from "./Metrics";
import { EvaluationResults } from "./Results";
import { SpendingView } from "./Spending";
import { adapterText, modelText, nodeText, orchestratorName, providerName } from "@/pages/validate/config";

function Overview({ job }: { job: EvaluationDetail }) {
  const active = isActiveEvaluation(job.status);
  const { progress } = job;
  return (
    <div className="space-y-4">
      <div>
        <div className="flex items-center gap-3">
          <Progress value={progress.percent} label="Evaluation progress" className="flex-1" />
          <span className="tabular text-xs text-muted-foreground">
            {formatInteger(progress.processed_receipts)}/{formatInteger(progress.total_receipts)} receipts · {progress.percent}%
          </span>
        </div>
        <p className="mt-1 text-xs text-subtle-foreground">
          {active ? (
            <span className="inline-flex items-center gap-1 text-primary">
              <RadioTower className="size-3" aria-hidden /> Refreshing every 2 s
              {job.lease?.heartbeat_at ? ` · worker heartbeat ${formatRelative(job.lease.heartbeat_at)}` : job.status === "queued" ? " · waiting for a worker" : ""}
            </span>
          ) : (
            "Finished: execution results are final."
          )}
          {progress.failed_receipts ? <span className="text-status-flag-text"> · {progress.failed_receipts} receipts failed</span> : null}
        </p>
      </div>
      {job.error ? (
        <p role="alert" className="rounded-md border border-status-flag-border bg-status-flag-soft px-3 py-2 text-[13px]">
          <span className="font-mono font-medium text-status-flag-text">{job.error.code ?? "error"}</span> {job.error.message ?? ""}
        </p>
      ) : null}
      <Facts
        items={[
          ["Dataset", `${job.dataset.name} v${job.dataset.version} · ${formatInteger(job.dataset.receipts)} receipts · ${job.dataset.context_complete ? "complete" : "incomplete"} context`],
          ["Ruleset", <>{job.ruleset.name} v{job.ruleset.version} · <span className="font-mono text-xs" title={job.ruleset.artifact_sha256}>{shortHash(job.ruleset.artifact_sha256)}</span></>],
          ["Orchestrator", orchestratorName(job.configuration.setup)],
          ["Provider", job.configuration.setup?.orchestrator === "langgraph" ? (job.configuration.setup.nodes ?? []).map(nodeText).join(" → ") || "—" : providerName(job.configuration.setup?.provider)],
          ["Adapter", <span title="The execution adapter, as the run recorded its backend">{adapterText(job.configuration.setup, job.configuration.classification_backend)}</span>],
          ["Classification model", <span className="font-mono text-xs">{modelText(job.configuration.setup, job.configuration.model_label)}</span>],
          ["Models it may call", <span className="font-mono text-xs">{job.configuration.models.join(", ") || "—"}</span>],
          [
            "Translation",
            job.configuration.translation
              ? job.configuration.translation.enabled
                ? <><span className="font-mono text-xs">{job.configuration.translation.model}</span> · {job.configuration.translation.source === "request" ? "chosen for this run" : "the configured default"}</>
                : "Off"
              : "Not recorded (an older job)",
          ],
          ["Recording set", job.replay ? <>{job.replay.set} · <span className="font-mono text-xs" title={job.replay.sha256}>{shortHash(job.replay.sha256)}</span></> : "— (live)"],
          ["Created", formatDateTime(job.created_at)],
          ["Started · finished", `${formatDateTime(job.started_at)} · ${formatDateTime(job.completed_at)}`],
          ["Results", job.result_context === "dataset" ? "Every receipt evaluated together" : job.result_context === "isolated_receipts" ? "Each receipt evaluated alone" : "Not yet stored"],
          ["Stop reason", job.stop_reason ? humanize(job.stop_reason) : "—"],
          ...(job.error_summary && Object.keys(job.error_summary).length ? [["Receipt errors", Object.entries(job.error_summary).map(([code, count]) => `${code}: ${count}`).join(", ")] as [string, string]] : []),
          ...(job.note ? [["Note", job.note] as [string, string]] : []),
        ]}
      />
    </div>
  );
}

export function EvaluationDetailPage() {
  const navigate = useNavigate();
  const { evaluationId = "" } = useParams();
  const job = useEvaluation(evaluationId);
  const [scoring, setScoring] = useState<"latest" | "original">("latest");
  const metrics = useEvaluationMetrics(evaluationId, scoring);
  const cancel = useCancelEvaluation();

  const back = (
    <Button asChild variant="outline" size="sm">
      <Link to="/evaluations">
        <ArrowLeft aria-hidden />
        All evaluations
      </Link>
    </Button>
  );

  if (job.isPending) return <><PageHeader title="Evaluation" actions={back} /><LoadingRows rows={6} label="Loading evaluation" /></>;
  if (job.isError) return <><PageHeader title="Evaluation" actions={back} /><ErrorState error={job.error} onRetry={() => void job.refetch()} /></>;

  const current = job.data;
  const active = isActiveEvaluation(current.status);
  return (
    <>
      <PageHeader
        title={current.label ?? current.evaluation_id}
        description={`${current.evaluation_id} · ${current.dataset.name} v${current.dataset.version} against ${current.ruleset.name} v${current.ruleset.version}`}
        actions={
          <>
            {back}
            <RecordActions path={`/v1/evaluations/${encodeURIComponent(current.evaluation_id)}`} title="evaluation" field="label" value={current.label} deleteDisabled={active} onDeleted={() => navigate("/evaluations")} />
            {active ? (
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => cancel.mutate(current.evaluation_id)}
                disabled={cancel.isPending || current.status === "cancel_requested"}
                title={current.status === "running" ? "The worker stops before its next receipt or provider call" : undefined}
              >
                {cancel.isPending ? <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden /> : <Ban aria-hidden />}
                {current.status === "cancel_requested" ? "Cancelling…" : "Cancel"}
              </Button>
            ) : null}
          </>
        }
      />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <EvaluationStatusBadge status={current.status} />
        <ModeBadge mode={current.execution_mode} preview={current.preview} />
        <div className="ml-auto">
          <ExportButtons name={current.evaluation_id} urlFor={(format) => evaluationExportUrl(current.evaluation_id, format, scoring)} disabledReason={active ? "Exports are available once the evaluation finishes" : undefined} />
        </div>
      </div>
      {cancel.isError ? <ErrorState error={cancel.error} className="mb-4" /> : null}

      <div className="space-y-4">
        <Section id="overview" title="Progress and configuration">
          <Overview job={current} />
        </Section>

        <Section
          id="metrics"
          title="Metrics"
          description={metrics.data?.provisional ? "Provisional while the job runs" : metrics.data ? "Final" : undefined}
          actions={
            metrics.data?.provisional ? (
              <Button type="button" size="sm" variant="outline" onClick={() => void metrics.refetch()} disabled={metrics.isFetching}>
                <RefreshCw aria-hidden className={metrics.isFetching ? "animate-spin motion-reduce:animate-none" : undefined} />
                Refresh
              </Button>
            ) : null
          }
        >
          {metrics.isPending ? (
            <LoadingRows rows={4} label="Loading metrics" />
          ) : metrics.isError ? (
            <ErrorState error={metrics.error} onRetry={() => void metrics.refetch()} />
          ) : (
            <MetricsView metrics={metrics.data} scoring={scoring} onScoring={setScoring} evaluationId={evaluationId} />
          )}
        </Section>

        <Section id="spending" title="Spending" description={current.execution_mode === "live" ? "Live provider calls, metered per call" : "Replay of recorded answers"}>
          <SpendingView job={current} />
        </Section>

        <Section id="results" title="Receipt results" description="Expected answers against what the run gave, in dataset order.">
          <EvaluationResults evaluationId={current.evaluation_id} mode={current.execution_mode} />
        </Section>
      </div>
    </>
  );
}
