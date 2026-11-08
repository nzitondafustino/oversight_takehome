import { ChevronDown, FileCheck2, Loader2, Plus, XCircle } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, fieldErrors } from "@/api/client";
import { useBackends, useRulesets, useValidate } from "@/api/queries";
import type { ExecutionMode } from "@/api/types";
import { PageHeader } from "@/components/layout/AppLayout";
import { ValidationResults } from "@/components/results/ValidationResults";
import { EmptyState } from "@/components/states/states";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { backendFor, buildValidationBody, configIssues, defaultOrchestrator, defaultReplaySet, defaultRuleset, MODE_COPY, choiceText } from "./config";
import { useValidationDraft } from "./draft";
import { ReceiptInput } from "./ReceiptInput";
import { ReceiptPreview } from "./ReceiptPreview";
import { parseReceiptInput, previewOf } from "./receipts";
import { ValidationConfiguration } from "./ValidationConfiguration";

/** What a refusal means and what to try, for the codes POST /v1/validate returns (backend/README.md). */
const ERROR_HINTS: Record<string, string> = {
  invalid_receipts: "The core refused a receipt. The message names the problem; nothing was validated or stored.",
  invalid_request: "The request did not match the API's schema. The fields it refused are listed below.",
  replay_recording_missing: "The replay set has no recorded answer for some lines, and replay never calls a model. Choose the set recorded for these receipts.",
  ruleset_not_published: "Only a version published for production can be validated against.",
  unsupported_configuration: "The core does not support this orchestrator and model combination.",
  model_not_allowed: "This deployment does not allow that model. In live mode, only its allowed models can be named.",
  backend_not_allowed: "This deployment does not allow the adapter this model needs, or that orchestrator.",
  model_adapter_mismatch: "The chosen model cannot run on that execution adapter: choose the model again, so its own adapter is used.",
  live_inference_disabled: "This deployment does not allow live inference.",
  live_inference_unavailable: "Live mode is allowed, but the API could not start it, for example without a provider key.",
  provider_failure: "A model provider failed. Nothing was stored.",
  too_many_receipts: "Split the batch: the deployment limits receipts per request.",
};

function SubmitError({ error }: { error: unknown }) {
  const api = error instanceof ApiError ? error : null;
  const unknownOutcome = !api || api.isNetwork || api.isTransient || api.code === "invalid_response";
  const fields = api ? fieldErrors(api.details) : [];
  const otherDetails = api && !fields.length && api.details != null ? JSON.stringify(api.details) : null;
  return (
    <div role="alert" className="rounded-lg border border-status-flag-border bg-status-flag-soft px-4 py-3">
      <p className="flex items-center gap-2 text-sm font-semibold text-status-flag-text">
        <XCircle className="size-4" aria-hidden />
        {unknownOutcome ? "Validation outcome could not be confirmed" : `Validation failed (${api.status}): no result was produced`}
      </p>
      {unknownOutcome ? (
        <p className="mt-2 text-[13px] leading-5">
          The server may have completed this request. Check <Link to="/runs" className="font-medium underline">Run History</Link> before submitting again.
          This request will not be retried automatically.
        </p>
      ) : null}
      <p className="mt-1 break-words text-[13px] leading-5 text-foreground">{error instanceof Error ? error.message : String(error)}</p>
      {api ? <p className="mt-1 font-mono text-[11px] text-muted-foreground">{api.code}</p> : null}
      {api && ERROR_HINTS[api.code] ? <p className="mt-1.5 text-xs leading-4 text-foreground/80">{ERROR_HINTS[api.code]}</p> : null}
      {fields.length ? (
        <ul className="mt-2 space-y-0.5 text-xs leading-5">
          {fields.map((field, index) => (
            <li key={index}>
              <span className="font-mono text-muted-foreground">{field.path || "body"}</span>: {field.message}
            </li>
          ))}
        </ul>
      ) : null}
      {otherDetails ? <p className="mt-1.5 break-all font-mono text-[11px] text-muted-foreground">{otherDetails}</p> : null}
    </div>
  );
}

interface Submission {
  receiptCount: number;
  lineCount: number;
  ruleset: string;
  mode: ExecutionMode;
  backend: string | null;
  model: string | null;
  replaySet: string | null;
  contextComplete: boolean;
  startedAt: number;
}

export function ReceiptValidationPage() {
  const draft = useValidationDraft();
  const { config, setConfig, intakeOpen, setIntakeOpen } = draft;
  const rulesets = useRulesets({ limit: 100, offset: 0 });
  const backends = useBackends();
  const validate = useValidate();
  const intakeRef = useRef<HTMLDivElement>(null);
  const resultsRef = useRef<HTMLDivElement>(null);
  const statusRef = useRef<HTMLElement>(null);
  const [submission, setSubmission] = useState<Submission | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const pendingRef = useRef(false);
  pendingRef.current = validate.isPending;

  useEffect(() => {
    if (!validate.isPending || !submission) return;
    const timer = window.setInterval(() => setElapsed(Math.floor((Date.now() - submission.startedAt) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [validate.isPending, submission]);

  useEffect(() => {
    if (!validate.isPending && !validate.isError && !validate.isSuccess) return;
    const frame = requestAnimationFrame(() => {
      statusRef.current?.focus({ preventScroll: true });
      statusRef.current?.scrollIntoView?.({ block: "start" });
    });
    return () => cancelAnimationFrame(frame);
  }, [validate.isPending, validate.isError, validate.isSuccess]);

  const rulesetItems = useMemo(() => rulesets.data?.items ?? [], [rulesets.data]);

  // Default to the newest version published for production; drop a choice no longer listed.
  useEffect(() => {
    if (!rulesets.data) return;
    if (config.rulesetId && rulesetItems.some((item) => item.ruleset_id === config.rulesetId)) return;
    setConfig((current) => ({ ...current, rulesetId: defaultRuleset(rulesetItems)?.ruleset_id ?? null }));
  }, [rulesets.data, rulesetItems, config.rulesetId, setConfig]);

  // Default to the deployment's default orchestrator and model, and the replay set of the adapter they need. The mode stays as chosen (replay at first).
  useEffect(() => {
    const discovery = backends.data;
    if (!discovery || config.orchestrator) return;
    setConfig((current) => {
      const next = { ...current, orchestrator: defaultOrchestrator(discovery) };
      const backend = backendFor(next, discovery);
      return { ...next, replaySet: backend ? defaultReplaySet(backend) : "" };
    });
  }, [backends.data, config.orchestrator, setConfig]);

  const activeText = draft.source === "editor" ? draft.text : draft.file?.text ?? "";
  const activeFormat = draft.source === "file" ? draft.file?.format ?? "json" : "json";
  const parsed = useMemo(() => (activeText.trim() ? parseReceiptInput(activeText, activeFormat) : null), [activeText, activeFormat]);
  const previews = useMemo(() => (parsed && !parsed.issues.length ? parsed.receipts.map(previewOf) : []), [parsed]);

  const inputIssues: string[] = [];
  if (!parsed) inputIssues.push(draft.source === "file" ? "Upload a JSON or JSONL file." : "Enter receipts, or load the demo input.");
  else if (parsed.issues.length) inputIssues.push(`Fix ${parsed.issues.length} input problem${parsed.issues.length === 1 ? "" : "s"}.`);
  const issues = [...inputIssues, ...configIssues(config, backends.data, rulesetItems)];

  const last = draft.last;
  const open = intakeOpen || !last;
  const showPrevious = Boolean(last && (validate.isPending || validate.isError || intakeOpen));
  /** A completed validation's results are on screen: the page shows them alone. */
  const reviewing = Boolean(last && !showPrevious);

  function submit() {
    if (validate.isPending || issues.length || !parsed || !backends.data || !config.rulesetId) return;
    const receipts = parsed.receipts;
    const body = buildValidationBody({ rulesetId: config.rulesetId, receipts, config, backends: backends.data });
    const selectedRuleset = rulesetItems.find((item) => item.ruleset_id === config.rulesetId);
    setElapsed(0);
    setSubmission({
      receiptCount: receipts.length,
      lineCount: previews.reduce((total, receipt) => total + receipt.lineCount, 0),
      ruleset: selectedRuleset ? `${selectedRuleset.name} v${selectedRuleset.version}` : config.rulesetId,
      mode: config.mode,
      backend: config.mode === "deterministic" ? null : choiceText(config, backends.data),
      model: config.mode === "deterministic" ? null : config.model.trim() || backendFor(config, backends.data)?.default_model || null,
      replaySet: config.mode === "replay" ? config.replaySet || "Deployment default" : null,
      contextComplete: config.contextComplete,
      startedAt: Date.now(),
    });
    validate.mutate(body, {
      onSuccess: (run) => {
        draft.setLast({ run, sent: receipts });
        setIntakeOpen(false); // results first: the intake folds away, one click from coming back
      },
    });
  }

  function newValidation() {
    if (validate.isPending) return;
    validate.reset();
    setIntakeOpen(true);
    requestAnimationFrame(() => {
      intakeRef.current?.scrollIntoView?.({ block: "start" });
      document.getElementById("receipt-json")?.focus({ preventScroll: true });
    });
  }

  const ruleset = rulesetItems.find((item) => item.ruleset_id === config.rulesetId);
  const intakeSummary = [
    parsed ? `${parsed.receipts.length} receipt${parsed.receipts.length === 1 ? "" : "s"}` : "No receipts",
    ruleset ? `${ruleset.name} v${ruleset.version}` : null,
    MODE_COPY[config.mode].label,
    config.mode !== "deterministic" && backends.data ? choiceText(config, backends.data) : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <>
      <PageHeader
        title="Validate Receipts"
        description="Validate structured receipts against one exact published ruleset version. Each validation is stored as a run."
        actions={
          last ? (
            <Button type="button" onClick={newValidation} disabled={validate.isPending}>
              <Plus aria-hidden />
              New validation
            </Button>
          ) : null
        }
      />

      <section ref={statusRef} tabIndex={-1} aria-label="Validation status" className={cn("scroll-mt-20 space-y-3 outline-none", !reviewing && "mb-5")}>
        {/* Results first: once a validation has completed, only its results are shown, not the steps or the intake. */}
        {!reviewing ? (
        <ol aria-label="Validation steps" className="flex flex-wrap items-center gap-x-5 gap-y-2 text-[13px] text-muted-foreground">
          <li className="flex items-center gap-2"><span className="font-semibold text-primary">1</span> Prepare receipts</li>
          <li className={cn("flex items-center gap-2", validate.isPending && "font-semibold text-primary")} aria-current={validate.isPending ? "step" : undefined}>
            <span className="font-semibold text-primary">2</span> Run validation
          </li>
          <li className={cn("flex items-center gap-2", last && !showPrevious && "font-semibold text-primary")} aria-current={last && !showPrevious ? "step" : undefined}>
            <span className="font-semibold text-primary">3</span> Review findings
          </li>
        </ol>
        ) : null}
        {validate.isPending && submission ? (
          <div role="status" className="rounded-lg border border-status-info-border bg-status-info-soft px-4 py-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="flex items-center gap-2 font-semibold text-foreground"><Loader2 className="size-5 animate-spin motion-reduce:animate-none text-primary" aria-hidden />Validation in progress</p>
              <span className="tabular text-xs text-muted-foreground" aria-live="off">Elapsed {elapsed}s</span>
            </div>
            <p className="mt-2 text-sm">{submission.receiptCount} receipts · {submission.lineCount} line items · {submission.ruleset}</p>
            <p className="mt-1 text-[13px] text-foreground/85">{MODE_COPY[submission.mode].label}{submission.backend ? ` · ${submission.backend}` : ""}</p>
            {submission.model || submission.replaySet ? <p className="mt-1 break-words text-xs text-muted-foreground">{[submission.model && `Model: ${submission.model}`, submission.replaySet && `Replay set: ${submission.replaySet}`].filter(Boolean).join(" · ")}</p> : null}
            <p className="mt-2 text-[13px]">Waiting for results. Your submitted receipts and settings are locked until this request finishes.</p>
            <p className="mt-1 text-xs text-muted-foreground">{submission.contextComplete ? "Submitted as complete context." : "Submitted as incomplete context; affected cross-receipt checks may remain open."} {submission.mode === "replay" ? "Replay uses recorded responses; no paid provider calls." : ""}</p>
          </div>
        ) : validate.isError ? (
          <SubmitError error={validate.error} />
        ) : reviewing ? null : (
          // After a completed validation, the results' own summary reports the outcome: nothing is repeated here.
          <p className="text-[13px] text-muted-foreground">Add receipts, confirm the settings, then validate. Results and any information still needed appear below.</p>
        )}
      </section>

      {!reviewing ? (
      <Card ref={intakeRef} className="scroll-mt-20 overflow-hidden">
        <button
          type="button"
          onClick={() => setIntakeOpen(!open)}
          aria-expanded={open}
          aria-controls="intake"
          disabled={!last || validate.isPending}
          className="flex w-full items-center gap-3 px-5 py-3.5 text-left enabled:hover:bg-muted/40 disabled:cursor-default"
        >
          <FileCheck2 className="size-4 text-primary" aria-hidden />
          <span className="min-w-0 flex-1">
            <span className="block text-[15px] font-semibold text-foreground">{last ? "Receipts and configuration" : "New validation"}</span>
            {!open ? <span className="block truncate text-xs text-muted-foreground">{intakeSummary}</span> : null}
          </span>
          {last ? <ChevronDown className={cn("size-4 text-subtle-foreground transition-transform", open && "rotate-180")} aria-hidden /> : null}
        </button>
        {open ? (
          <fieldset
            id="intake"
            disabled={validate.isPending}
            aria-label="Receipts and settings"
            aria-busy={validate.isPending}
            onDropCapture={(event) => { if (pendingRef.current) { event.preventDefault(); event.stopPropagation(); } }}
            className={cn("grid min-w-0 gap-6 border-t border-border p-5 lg:grid-cols-[minmax(0,1fr)_340px]", validate.isPending && "opacity-70")}
          >
            <div className="min-w-0">
              <ReceiptInput
                source={draft.source}
                onSourceChange={(source) => { if (!pendingRef.current) draft.setSource(source); }}
                text={draft.text}
                onTextChange={(text) => { if (!pendingRef.current) draft.setText(text); }}
                file={draft.file}
                onFileChange={(file) => { if (!pendingRef.current) draft.setFile(file); }}
                parsed={parsed}
              />
              <ReceiptPreview previews={previews} />
            </div>
            <div className="min-w-0 lg:border-l lg:border-border lg:pl-6">
              <ValidationConfiguration
                config={config}
                setConfig={(update) => { if (!pendingRef.current) setConfig(update); }}
                rulesets={rulesets}
                backends={backends}
                issues={issues}
                receiptCount={parsed?.receipts.length ?? 0}
                pending={validate.isPending}
                onSubmit={submit}
              />
            </div>
          </fieldset>
        ) : null}
      </Card>
      ) : null}

      <section ref={resultsRef} aria-label="Validation results" className={cn("scroll-mt-20 space-y-4", !reviewing && "mt-6")}>
        {last ? (
          showPrevious ? (
            <details key={`${last.run.run_id}-${validate.status}`} className="rounded-lg border border-border bg-card">
              <summary className="cursor-pointer px-5 py-4 text-sm font-semibold">Previous successful run · {last.run.results.results.length} receipts</summary>
              <div className="space-y-4 border-t border-border p-4">
                <p className="text-[13px] text-muted-foreground">These results belong to an earlier submission, not the current attempt.</p>
                <ValidationResults key={last.run.run_id} run={last.run} inputs={last.sent} />
              </div>
            </details>
          ) : (
            <ValidationResults key={last.run.run_id} run={last.run} inputs={last.sent} />
          )
        ) : !validate.isPending && !validate.isError ? (
          <EmptyState title="Results appear here" description="Validate receipts to see each one's disposition, findings and line classifications." className="bg-card py-8" />
        ) : null}
      </section>
    </>
  );
}
