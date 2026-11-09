import { RecordActions } from "@/components/management/RecordActions";
import { FlaskConical, GitCompare, RadioTower } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useActiveEvaluations, useEvaluations } from "@/api/queries";
import { isActiveEvaluation, type EvaluationStatus, type EvaluationSummary } from "@/api/types";
import { EmptyState, ErrorState, LoadingRows } from "@/components/states/states";
import { EvaluationStatusBadge, ModeBadge } from "@/components/status/badges";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/misc";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDateTime, formatInteger, formatRate, formatRelative, humanize } from "@/lib/format";
import { Pager, Section } from "@/pages/dashboard/Section";
import { adapterText, modelText, setupHeadline } from "@/pages/validate/config";
import { newSpendText } from "./display";

const PAGE_SIZE = 15;
const MAX_COMPARED = 10;
const STATUSES: EvaluationStatus[] = ["queued", "running", "cancel_requested", "completed", "failed", "cancelled", "budget_exhausted"];

function JobRow({ job, selected, onToggle, onDeleted }: { job: EvaluationSummary; selected: boolean; onToggle: () => void; onDeleted: () => void }) {
  const active = isActiveEvaluation(job.status);
  const name = job.label ?? job.evaluation_id;
  return (
    <TableRow>
      <TableCell className="w-8 pl-5">
        <input
          type="checkbox"
          aria-label={`Compare ${name}`}
          checked={selected}
          disabled={job.status !== "completed"}
          title={job.status === "completed" ? undefined : "Only completed evaluations are compared"}
          onChange={onToggle}
          className="size-3.5 accent-primary"
        />
      </TableCell>
      <TableCell className="min-w-48">
        <Link to={`/evaluations/${encodeURIComponent(job.evaluation_id)}`} className="font-medium text-primary hover:underline">
          {name}
        </Link>
        <p className="text-xs text-subtle-foreground" title={formatDateTime(job.created_at)}>
          Created {formatRelative(job.created_at)}
          {job.completed_at ? ` · finished ${formatRelative(job.completed_at)}` : job.started_at ? ` · started ${formatRelative(job.started_at)}` : ""}
        </p>
      </TableCell>
      <TableCell className="min-w-40">
        <div className="flex flex-wrap items-center gap-1">
          <EvaluationStatusBadge status={job.status} />
          {active ? <RadioTower className="size-3 text-primary" aria-label="Live updates" /> : null}
        </div>
        <Progress value={job.progress.percent} label={`${name} progress`} className="mt-1.5" />
        <p className="tabular mt-0.5 text-xs text-muted-foreground">
          {formatInteger(job.progress.processed_receipts)}/{formatInteger(job.progress.total_receipts)} receipts
          {job.progress.failed_receipts ? <span className="text-status-flag-text"> · {job.progress.failed_receipts} failed</span> : null}
        </p>
        {job.stop_reason ? <p className="text-xs text-subtle-foreground">Stopped: {humanize(job.stop_reason)}</p> : null}
        {job.error?.code ? <p className="font-mono text-xs text-status-flag-text">{job.error.code}</p> : null}
      </TableCell>
      <TableCell className="min-w-44">
        <ModeBadge mode={job.execution_mode} preview={job.preview} />
        <p className="mt-1 text-xs text-foreground">
          <span title={adapterText(job.configuration.setup, job.configuration.classification_backend)}>{setupHeadline(job.configuration.setup, job.configuration.classification_backend)}</span>
          <span className="block max-w-56 truncate font-mono text-[11px] text-subtle-foreground" title={modelText(job.configuration.setup, job.configuration.model_label)}>
            {modelText(job.configuration.setup, job.configuration.model_label)}
          </span>
        </p>
      </TableCell>
      <TableCell className="min-w-40 text-xs">
        <p>
          {job.dataset.name} v{job.dataset.version} · {formatInteger(job.dataset.receipts)} receipts
        </p>
        <p className="text-subtle-foreground">
          {job.ruleset.name} v{job.ruleset.version}
        </p>
      </TableCell>
      <TableCell className="tabular text-right text-xs">
        {job.summary ? (
          <>
            <p title="Category accuracy">{formatRate(job.summary.category_accuracy)} category</p>
            <p title="Disposition accuracy">{formatRate(job.summary.disposition_accuracy)} disposition</p>
            <p className="text-subtle-foreground" title="Receipts with an EXC-03 signal">
              {formatRate(job.summary.uncertainty_rate)} uncertain
            </p>
          </>
        ) : (
          <span className="text-subtle-foreground">{active ? "When finished" : "—"}</span>
        )}
      </TableCell>
      <TableCell className="pr-5 text-right text-xs">{newSpendText(job.execution_mode, job.spend_summary)}</TableCell>
      <TableCell><RecordActions path={`/v1/evaluations/${encodeURIComponent(job.evaluation_id)}`} title="evaluation" recordName={name} deleteDisabled={active} onDeleted={onDeleted} /></TableCell>
    </TableRow>
  );
}

/** Background jobs, newest first. Only queued, running and cancelling jobs are polled, each until it finishes. */
export function EvaluationList() {
  const navigate = useNavigate();
  const [offset, setOffset] = useState(0);
  const [status, setStatus] = useState<EvaluationStatus | "">("");
  const [selected, setSelected] = useState<string[]>([]);
  const evaluations = useEvaluations({ limit: PAGE_SIZE, offset, ...(status ? { status } : {}) });
  const items = evaluations.data?.items ?? [];
  const polled = useActiveEvaluations(items);
  const latest = new Map(polled.flatMap(({ evaluationId, query }) => (query.data ? [[evaluationId, query.data] as const] : [])));

  function toggle(evaluationId: string) {
    setSelected((current) => (current.includes(evaluationId) ? current.filter((id) => id !== evaluationId) : [...current, evaluationId].slice(-MAX_COMPARED)));
  }

  const compare = (
    <Button
      type="button"
      size="sm"
      variant="outline"
      disabled={selected.length < 2}
      title={selected.length < 2 ? "Select 2 to 10 completed evaluations" : undefined}
      onClick={() => navigate(`/evaluations/compare?ids=${selected.map(encodeURIComponent).join(",")}`)}
    >
      <GitCompare aria-hidden />
      Compare{selected.length ? ` (${selected.length})` : ""}
    </Button>
  );

  return (
    <Section id="evaluation-list" title="Evaluation jobs" description="Newest first. Active jobs refresh every 2 s until they finish." actions={compare}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <label htmlFor="eval-status" className="text-xs font-medium text-muted-foreground">
          Status
        </label>
        <select
          id="eval-status"
          value={status}
          onChange={(event) => {
            setStatus(event.target.value as EvaluationStatus | "");
            setOffset(0);
          }}
          className="h-8 rounded-md border border-input bg-card px-2 text-[13px]"
        >
          <option value="">Any</option>
          {STATUSES.map((item) => (
            <option key={item} value={item}>
              {humanize(item)}
            </option>
          ))}
        </select>
        {evaluations.data ? <span className="ml-auto text-xs text-subtle-foreground">{evaluations.data.total} evaluations</span> : null}
      </div>
      {evaluations.isPending ? (
        <LoadingRows rows={5} label="Loading evaluations" />
      ) : evaluations.isError ? (
        <ErrorState error={evaluations.error} onRetry={() => void evaluations.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState
          icon={<FlaskConical className="size-5" aria-hidden />}
          title={status ? "No evaluations with this status" : "No evaluations yet"}
          description="Choose a dataset and a ruleset version, then start a replay evaluation."
          className="py-6"
        />
      ) : (
        <>
          <div className="-mx-5 overflow-x-auto border-y border-border" role="region" aria-label="Evaluation jobs, scrolls sideways" tabIndex={0}>
            <Table className="min-w-[960px]">
              <caption className="sr-only">Evaluation jobs</caption>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="pl-5">
                    <span className="sr-only">Compare</span>
                  </TableHead>
                  <TableHead>Evaluation</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Mode · classification</TableHead>
                  <TableHead>Dataset · ruleset</TableHead>
                  <TableHead className="text-right">Key metrics</TableHead>
                  <TableHead className="pr-5 text-right">New spend</TableHead><TableHead>Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((job) => (
                  <JobRow onDeleted={() => { setSelected(current => current.filter(id => id !== job.evaluation_id)); setOffset(0); }} key={job.evaluation_id} job={latest.get(job.evaluation_id) ?? job} selected={selected.includes(job.evaluation_id)} onToggle={() => toggle(job.evaluation_id)} />
                ))}
              </TableBody>
            </Table>
          </div>
          <Pager offset={offset} limit={PAGE_SIZE} total={evaluations.data.total} onChange={setOffset} busy={evaluations.isFetching} />
        </>
      )}
    </Section>
  );
}
