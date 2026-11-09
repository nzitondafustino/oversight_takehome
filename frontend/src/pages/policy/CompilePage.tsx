/**
 * Steps 1 and 2: confirm the exact document revisions, then compile them by replay or live. Only the settings the
 * policy compiler supports are offered: never a classification, decision or translation model.
 */
import { ArrowLeft, CircleAlert, Loader2, RadioTower, Wand2, X, Zap } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useDocumentLibrary, useExtractionJob, usePolicyCompiler, useRuleset, useRulesets, useStartExtractionJob } from "@/api/queries";
import type { ExtractionCreate, ExtractionJob } from "@/api/types";
import { PageHeader } from "@/components/layout/AppLayout";
import { EmptyState, ErrorState, LoadingRows } from "@/components/states/states";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/misc";
import { formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Section } from "@/pages/dashboard/Section";
import { SELECT } from "@/pages/evaluations/display";
import { compileLink } from "./DocumentsPage";
import { categoryLabel, displayTitle } from "./library";
import { WorkflowSteps } from "./WorkflowSteps";

/** The d= parameter: document_id:revision, comma-separated, each document once. */
export function parseSelection(text: string | null): { document_id: string; revision: number }[] {
  const seen = new Set<string>();
  const out: { document_id: string; revision: number }[] = [];
  for (const part of (text ?? "").split(",")) {
    const match = /^(doc_[0-9a-f]{24}):(\d+)$/.exec(decodeURIComponent(part.trim()));
    if (match && !seen.has(match[1]!)) {
      seen.add(match[1]!);
      out.push({ document_id: match[1]!, revision: Number(match[2]) });
    }
  }
  return out;
}

export const MODE_EXPLAINED = {
  replay: {
    title: "Reproduce from recordings (replay)",
    text: "Reads the compiler's recorded readings of these exact texts. No model is called and nothing is spent, so the same documents always compile to the same rules. A document with no recording is refused.",
  },
  live: {
    title: "Read with a model (live)",
    text: "Sends each document's text to a policy extraction model, which costs money. Its readings are recorded, so every later step replays exactly them.",
  },
} as const;

/** How far a background compilation is: reading each document, then building the candidate ruleset. */
export function jobPercent(job: ExtractionJob): number {
  const { stage, done, total } = job.progress;
  if (job.status === "succeeded") return 100;
  if (!total) return 0;
  return Math.round((stage === "building" ? 0.95 : (done / total) * 0.9) * 100);
}

function JobProgress({ job, onRetry }: { job: ExtractionJob; onRetry: () => void }) {
  const active = job.status === "queued" || job.status === "running";
  const { progress } = job;
  const now =
    job.status === "queued" ? "Waiting to start" :
    progress.stage === "reading" ? `Reading ${progress.current} (${progress.done + 1} of ${progress.total})` :
    progress.stage === "building" ? "Building and checking the candidate ruleset" :
    job.status === "succeeded" ? "Compiled: opening the review" : "Stopped";
  return (
    <div className="mt-3 space-y-2" role="region" aria-label="Compilation progress">
      <div className="flex items-center gap-3">
        <Progress value={jobPercent(job)} label="Compilation progress" className="flex-1" />
        <span className="tabular text-xs text-muted-foreground">
          {progress.done}/{progress.total} documents · {jobPercent(job)}%
        </span>
      </div>
      <p className="text-[13px]" aria-live="polite">{now}</p>
      <p className="text-xs text-subtle-foreground">
        {active ? (
          <span className="inline-flex items-center gap-1 text-primary">
            <RadioTower className="size-3" aria-hidden /> Refreshing every second · started {formatRelative(job.started_at ?? job.created_at)}. It keeps running if you leave this page.
          </span>
        ) : null}
      </p>
      {job.status === "failed" && job.error ? (
        <div role="alert" className="space-y-1 rounded-md border border-status-flag-border bg-status-flag-soft px-3 py-2 text-[13px]">
          <p className="flex items-center gap-1.5 font-semibold text-status-flag-text">
            <CircleAlert className="size-4" aria-hidden />
            Compilation failed
          </p>
          <p>{job.error.message}</p>
          <p className="font-mono text-xs text-muted-foreground">{job.error.code}</p>
          {job.execution_mode === "live" ? <p className="text-xs text-muted-foreground">Readings already made were recorded: compiling again reuses them, at no cost.</p> : null}
          <Button type="button" size="sm" variant="outline" onClick={onRetry}>
            Try again
          </Button>
        </div>
      ) : null}
    </div>
  );
}

export function CompilePage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const selection = useMemo(() => parseSelection(params.get("d")), [params]);
  const documents = useDocumentLibrary();
  const compiler = usePolicyCompiler();
  const create = useStartExtractionJob();
  // The running job is in the address, so a reload or a return keeps following it.
  const jobId = params.get("job");
  const job = useExtractionJob(jobId);
  const setJob = (id: string | null) =>
    setParams((current) => {
      const next = new URLSearchParams(current);
      if (id) next.set("job", id);
      else next.delete("job");
      return next;
    }, { replace: true });
  useEffect(() => {
    if (job.data?.status === "succeeded" && job.data.extraction_id) navigate(`/documents/extractions/${encodeURIComponent(job.data.extraction_id)}?tab=review`);
  }, [job.data, navigate]);
  const running = !!jobId && (job.isPending || job.data?.status === "queued" || job.data?.status === "running");
  const [mode, setMode] = useState<"replay" | "live">("replay");
  const [recordingSet, setRecordingSet] = useState("");
  const [model, setModel] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  // The published version's corrections of the rule logic, kept by default so a new compilation starts from them.
  const versions = useRulesets({ limit: 100, offset: 0 });
  const published = (versions.data?.items ?? []).filter((item) => item.status === "published").sort((a, b) => (b.published_at ?? "").localeCompare(a.published_at ?? ""))[0];
  const corrections = useRuleset(published?.ruleset_id).data?.compilation?.rule_overrides ?? [];
  const [keep, setKeep] = useState(true);

  const back = (
    <Button asChild variant="outline" size="sm">
      <Link to="/documents">
        <ArrowLeft aria-hidden />
        Documents
      </Link>
    </Button>
  );
  const header = <PageHeader title="Compile policy rules" description="Choose the exact document revisions, then compile them into a candidate ruleset to review." actions={back} />;
  if (documents.isPending || compiler.isPending) return <>{header}<LoadingRows rows={5} label="Loading documents and compiler settings" /></>;
  const failed = documents.error ?? compiler.error;
  if (failed || !documents.data || !compiler.data) return <>{header}<ErrorState error={failed} /></>;

  const byId = new Map(documents.data.map((document) => [document.document_id, document]));
  const chosen = selection.map((item) => ({ ...item, document: byId.get(item.document_id) }));
  const replace = (next: { document_id: string; revision: number }[]) => setParams(next.length ? { d: compileLink(next).split("?d=")[1]! } : {}, { replace: true });
  const sets = compiler.data.replay.sets;
  const live = compiler.data.live;
  const models = compiler.data.extraction_models ?? [];

  const issues: string[] = [];
  if (!chosen.length) issues.push("Select documents in the library: together they are one policy candidate.");
  for (const item of chosen) if (!item.document) issues.push(`${item.document_id} is not in the library.`);
  if (mode === "replay" && !recordingSet) issues.push(sets.length ? "Choose the recordings to reproduce from." : "This deployment has no compiler recordings.");
  if (mode === "live") {
    if (!live.available) issues.push("Live compilation is not available here.");
    if (!model) issues.push("Choose a policy extraction model.");
    if (!confirmed) issues.push("Confirm that live compilation calls a paid model.");
  }

  function submit() {
    if (issues.length) return;
    const refs = chosen.map(({ document_id, revision }) => ({ document_id, revision }));
    const carried = keep && corrections.length && published ? { carry_overrides_from: published.ruleset_id } : {};
    const body: ExtractionCreate = mode === "live" ? { documents: refs, execution_mode: "live", extraction_model: model, ...carried } : { documents: refs, recording_set: recordingSet, ...carried };
    create.mutate(body, { onSuccess: (started) => setJob(started.job_id) });
  }

  return (
    <>
      {header}
      <div className="mb-4">
        <WorkflowSteps current={chosen.length ? 1 : 0} done={chosen.length ? 1 : 0} />
      </div>
      <div className="grid items-start gap-4 lg:grid-cols-5">
        <Section id="selected" title="1. Selected documents" description="Each document is compiled at exactly the revision shown." className="lg:col-span-3">
          {chosen.length === 0 ? (
            <EmptyState title="No documents selected" description="Select documents in the library, then choose Compile selected." action={<Button asChild size="sm"><Link to="/documents">Open the library</Link></Button>} className="py-6" />
          ) : (
            <ul className="divide-y divide-border rounded-md border border-border" aria-label="Selected documents">
              {chosen.map((item) => {
                const document = item.document;
                const revisions = document ? Array.from({ length: document.revision }, (_, index) => document.revision - index) : [item.revision];
                return (
                  <li key={item.document_id} className="flex flex-wrap items-center gap-3 px-3 py-2">
                    <div className="min-w-0 flex-1">
                      <p className="text-[13px] font-medium">{document ? displayTitle(document) : item.document_id}</p>
                      <p className="text-xs text-subtle-foreground">
                        <span className="font-mono">{document?.filename ?? "unknown"}</span>
                        {document ? ` · ${categoryLabel(document.category ?? null)}` : ""}
                      </p>
                    </div>
                    <label className="flex items-center gap-1.5 text-xs">
                      <span className="sr-only">Revision of {document?.filename ?? item.document_id}</span>
                      <select
                        aria-label={`Revision of ${document?.filename ?? item.document_id}`}
                        className={cn(SELECT, "h-8 w-40")}
                        value={item.revision}
                        onChange={(event) => replace(chosen.map((other) => (other.document_id === item.document_id ? { document_id: other.document_id, revision: Number(event.target.value) } : other)))}
                      >
                        {revisions.map((revision) => (
                          <option key={revision} value={revision}>
                            Revision {revision}
                            {revision === document?.revision ? " (latest)" : ""}
                          </option>
                        ))}
                      </select>
                    </label>
                    <Button type="button" size="sm" variant="ghost" aria-label={`Remove ${document?.filename ?? item.document_id}`} onClick={() => replace(chosen.filter((other) => other.document_id !== item.document_id))}>
                      <X aria-hidden />
                    </Button>
                  </li>
                );
              })}
            </ul>
          )}
        </Section>
        <Section id="compile" title="2. Compile" description="How the compiler reads the documents." className="lg:col-span-2">
          <div role="radiogroup" aria-label="Compilation mode" className="space-y-2">
            {(["replay", "live"] as const).map((item) => {
              const unavailable = item === "live" && !live.available;
              return (
                <button
                  key={item}
                  type="button"
                  role="radio"
                  aria-checked={mode === item}
                  disabled={unavailable}
                  onClick={() => {
                    setMode(item);
                    setConfirmed(false);
                  }}
                  className={cn("w-full rounded-md border px-3 py-2 text-left disabled:cursor-not-allowed disabled:opacity-50", mode === item ? "border-primary bg-primary-soft" : "border-border hover:bg-muted")}
                >
                  <span className="flex items-center gap-1.5 text-[13px] font-medium">
                    {item === "live" ? <Zap className="size-3.5" aria-hidden /> : <Wand2 className="size-3.5" aria-hidden />}
                    {MODE_EXPLAINED[item].title}
                  </span>
                  <span className="mt-0.5 block text-xs leading-4 text-muted-foreground">{unavailable ? "Not available in this deployment." : MODE_EXPLAINED[item].text}</span>
                </button>
              );
            })}
          </div>
          <div className="mt-3 space-y-2">
            {mode === "replay" ? (
              <>
                <label htmlFor="recording-set" className="block text-xs font-medium">
                  Recordings
                </label>
                <select id="recording-set" className={SELECT} value={recordingSet} onChange={(event) => setRecordingSet(event.target.value)}>
                  <option value="">Choose recordings</option>
                  {sets.map((set) => (
                    <option key={set.name} value={set.name} disabled={!set.present}>
                      {set.name}
                      {set.live_target ? " (model readings)" : ""}
                      {set.present ? "" : " · missing"}
                    </option>
                  ))}
                </select>
              </>
            ) : (
              <>
                <label htmlFor="extraction-model" className="block text-xs font-medium">
                  Policy extraction model
                </label>
                <select id="extraction-model" className={SELECT} value={model} onChange={(event) => setModel(event.target.value)}>
                  <option value="">{models.length ? "Choose a model" : "No extraction model is allowed here"}</option>
                  {models.map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>
                <label className="flex cursor-pointer gap-2 rounded-md border border-status-review-border bg-status-review-soft p-2 text-[13px]">
                  <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} className="mt-0.5 size-3.5 accent-primary" />
                  <span>I understand live compilation calls a paid model</span>
                </label>
              </>
            )}
          </div>
          {corrections.length && published ? (
            <label className="mt-3 flex cursor-pointer gap-2 rounded-md border border-border p-2 text-[13px]">
              <input type="checkbox" className="mt-0.5 size-3.5 accent-primary" checked={keep} onChange={(event) => setKeep(event.target.checked)} />
              <span>
                Keep the {corrections.length} rule {corrections.length === 1 ? "correction" : "corrections"} of {published.name} v{published.version}, the published version
                <span className="block text-xs text-muted-foreground">
                  {corrections.map((item) => `${item.rule_id}: ${item.rationale ?? "no reason given"}${item.reviewer ? ` (${item.reviewer})` : ""}`).join("; ")}. Each keeps who made it and why; the compiler checks it against these revisions again.
                </span>
              </span>
            </label>
          ) : null}
          {issues.length && chosen.length ? (
            <ul className="mt-3 space-y-0.5 text-xs text-status-review-text" aria-label="Before compiling">
              {issues.map((issue) => (
                <li key={issue}>{issue}</li>
              ))}
            </ul>
          ) : null}
          {create.isError ? <ErrorState error={create.error} className="mt-3" /> : null}
          {job.isError ? <ErrorState error={job.error} className="mt-3" /> : null}
          {jobId && job.data ? <JobProgress job={job.data} onRetry={() => { setJob(null); create.reset(); }} /> : null}
          <Button type="button" className="mt-3 w-full" disabled={issues.length > 0 || create.isPending || running} onClick={submit}>
            {create.isPending || running ? <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden /> : mode === "live" ? <Zap aria-hidden /> : <Wand2 aria-hidden />}
            {running ? "Compiling…" : mode === "live" ? "Compile live (paid)" : "Compile by replay"}
          </Button>
        </Section>
      </div>
    </>
  );
}
