import { CircleDashed, Download, History, Loader2 } from "lucide-react";
import { useMemo, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { exportViewPdf } from "@/lib/exportViewPdf";
import { downloadFile } from "@/api/client";
import { runExportUrl } from "@/api/endpoints";
import { useRunJson } from "@/api/queries";
import type { Disposition, RunResponse } from "@/api/types";
import { ErrorState } from "@/components/states/states";
import { DISPOSITION_ORDER, DISPOSITIONS, isProvisionalPass } from "@/components/status/badges";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { formatDateTime, formatInteger } from "@/lib/format";
import type { JsonObject } from "@/lib/losslessJson";
import { cn } from "@/lib/utils";
import { JsonViewer } from "./JsonViewer";
import { Disclosure, ReceiptDetail } from "./ReceiptDetail";
import { filterResults, ReceiptResultsList } from "./ReceiptResultsList";
import { sortByDisposition } from "./sort";
import { setupFacts } from "@/pages/validate/config";

/** PDFs capture the current UI; JSON/CSV download the complete saved record from the API. */
export function ExportButtons({ name, urlFor, disabledReason, formats = ["json", "csv", "pdf"] }: { name: string; urlFor: (format: "json" | "csv" | "pdf") => string; disabledReason?: string; formats?: readonly ("json" | "csv" | "pdf")[] }) {
  const [busy, setBusy] = useState<"json" | "csv" | "pdf" | null>(null);
  const [error, setError] = useState<unknown>(null);
  async function download(format: "json" | "csv" | "pdf") {
    setBusy(format);
    setError(null);
    try {
      if (format === "pdf") await exportViewPdf(name);
      else await downloadFile(urlFor(format), `${name}.${format}`);
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(null);
    }
  }
  return (
    <div data-pdf-exclude className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-1.5">
        {formats.map((format) => (
          <Button key={format} type="button" variant="outline" size="sm" onClick={() => void download(format)} disabled={busy !== null || Boolean(disabledReason)} title={disabledReason ?? (format === "pdf" ? "Save the current view, with its charts and filters, as one continuous PDF page" : undefined)}>
            {busy === format ? <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden /> : <Download aria-hidden />}
            Export {format.toUpperCase()}
          </Button>
        ))}
      </div>
      {error ? <ErrorState error={error} className="max-w-md" /> : null}
    </div>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-subtle-foreground">{label}</dt>
      <dd className="break-words text-[13px] font-medium text-foreground">{children}</dd>
    </div>
  );
}

/** Everything about how the run was made, collapsed: identifiers, ruleset, classification, timing, exports. */
function RunDetails({ run, historyLink }: { run: RunResponse; historyLink: boolean }) {
  const [jsonOpen, setJsonOpen] = useState(false);
  const runJson = useRunJson(run.run_id, jsonOpen);
  const results = run.results.results;
  const backends = [...new Set(results.map((result) => result.provenance.classification_backend))];
  const requested = run.classification;
  return (
    <Disclosure title="Run details" hint={`${run.execution_mode} · ${formatDateTime(run.created_at)}`}>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-3 md:grid-cols-3">
        <Fact label="Run ID">
          <span className="break-all font-mono text-xs">{run.run_id}</span>
        </Fact>
        <Fact label="Ruleset">
          {run.ruleset.name} v{run.ruleset.version} · {run.ruleset.status}
          {run.ruleset.publication_mode ? ` (${run.ruleset.publication_mode})` : ""}
        </Fact>
        <Fact label="Ruleset artifact">
          <span className="break-all font-mono text-xs">
            {run.ruleset.ruleset_version} · {run.ruleset.artifact_sha256}
          </span>
        </Fact>
        <Fact label="Execution mode">
          {run.execution_mode}
          {run.preview ? " · preview (not published for production)" : ""}
        </Fact>
        {run.setup ? (
          setupFacts(run.setup, run.setup.backend).map(([label, value]) => (
            <Fact key={label} label={label}>
              {value}
            </Fact>
          ))
        ) : (
          <Fact label="Adapter (recorded backend)">{backends.length ? backends.map((backend) => backend ?? "none").join(", ") : "—"}</Fact>
        )}
        <Fact label="Requested classification">
          <span className="font-mono text-xs">
            {[requested.backend && `backend ${requested.backend}`, requested.model && `model ${requested.model}`, requested.translation_model && `translation ${requested.translation_model}`, requested.replay_set && `replay set ${requested.replay_set}`]
              .filter(Boolean)
              .join(" · ") || "deployment defaults"}
          </span>
        </Fact>
        <Fact label="Context">{run.context_complete ? "Complete: the batch holds every related receipt" : "Incomplete: cross-receipt checks may stay open"}</Fact>
        <Fact label="Started · completed">
          {formatDateTime(run.execution.started_at)} · {formatInteger(run.execution.duration_ms)} ms
        </Fact>
        <Fact label="Versions">
          core {run.execution.core_version} · API {run.execution.api_version}
        </Fact>
      </dl>
      <div className="mt-4 flex flex-wrap items-start gap-2 border-t border-border pt-3">
        <ExportButtons name={run.run_id} urlFor={(format) => runExportUrl(run.run_id, format)} formats={["json", "csv"]} />
        {historyLink ? (
          <Button asChild variant="ghost" size="sm">
            <Link to={`/runs/${encodeURIComponent(run.run_id)}`}>
              <History aria-hidden />
              Open in Run History
            </Link>
          </Button>
        ) : null}
      </div>
      <div className="mt-3">
        <Disclosure title="Run JSON" hint="the run export, exactly as stored" onOpen={() => setJsonOpen(true)}>
          <JsonViewer
            source={runJson}
            label={`JSON of run ${run.run_id}`}
            note={<>As GET /v1/runs/{"{id}"}/export?format=json returns it, from the stored run.</>}
          />
        </Disclosure>
      </div>
    </Disclosure>
  );
}

/** The run's outcome at a glance. In a batch, the counts filter the list. */
function DispositionSummary({
  run,
  onFilter,
  activeFilter,
}: {
  run: RunResponse;
  onFilter?: (disposition: Disposition | "all") => void;
  activeFilter?: Disposition | "all";
}) {
  const results = run.results.results;
  const undecided = results.filter((result) => !result.decided).length;
  const provisional = results.filter(isProvisionalPass).length;
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2" role={onFilter ? "group" : undefined} aria-label={onFilter ? "Show receipts by disposition" : undefined}>
      <span className="text-sm font-semibold text-foreground">
        {formatInteger(results.length)} receipt{results.length === 1 ? "" : "s"}
      </span>
      <span className="flex flex-wrap gap-1.5">
        {DISPOSITION_ORDER.map((key) => {
          const count = results.filter((result) => result.disposition === key).length;
          const { icon: Icon, label } = DISPOSITIONS[key];
          const chip = cn(
            "inline-flex h-7 items-center gap-1 rounded-md border px-2 text-xs font-medium",
            key === "flag" && "border-status-flag-border bg-status-flag-soft text-status-flag-text",
            key === "review" && "border-status-review-border bg-status-review-soft text-status-review-text",
            key === "pass" && "border-status-pass-border bg-status-pass-soft text-status-pass",
            activeFilter === key && "ring-2 ring-ring/40",
          );
          const content = (
            <>
              <Icon className="size-3.5" aria-hidden />
              {label} <span className="tabular font-semibold">{count}</span>
            </>
          );
          return onFilter ? (
            <button key={key} type="button" className={chip} aria-pressed={activeFilter === key} onClick={() => onFilter(activeFilter === key ? "all" : key)}>
              {content}
            </button>
          ) : (
            <span key={key} className={chip}>
              {content}
            </span>
          );
        })}
      </span>
      {undecided ? (
        <span className="inline-flex items-center gap-1 text-xs text-primary">
          <CircleDashed className="size-3.5" aria-hidden />
          {undecided} not fully evaluated
          {provisional ? `, ${provisional} of them ${provisional === 1 ? "a provisional pass" : "provisional passes"}` : ""}
        </span>
      ) : (
        <span className="text-xs text-subtle-foreground">All fully evaluated</span>
      )}
    </div>
  );
}

/**
 * The results of one run, read as stored. One receipt: its detail, full width. A batch: the receipts flag first on
 * the left and the selected receipt on the right (one at a time below desktop width). Run details stay collapsed.
 */
export function ValidationResults({ run, inputs, historyLink = true }: { run: RunResponse; inputs: JsonObject[] | null; historyLink?: boolean }) {
  const results = run.results.results;
  const sorted = useMemo(() => sortByDisposition(results), [results]);
  const [selectedId, setSelectedId] = useState<string | null>(sorted[0]?.receipt_id ?? null);
  const [query, setQuery] = useState("");
  const [disposition, setDisposition] = useState<Disposition | "all">("all");
  const [compactView, setCompactView] = useState<"list" | "detail">("list");
  const workspaceRef = useRef<HTMLDivElement>(null);

  const inputMap = useMemo(() => {
    if (!inputs) return null;
    const map = new Map<string, JsonObject>();
    for (const receipt of inputs) if (typeof receipt.receipt_id === "string") map.set(receipt.receipt_id, receipt);
    return map;
  }, [inputs]);

  const header = (onFilter?: (value: Disposition | "all") => void) => (
    <Card className="space-y-3 px-5 py-4">
      <DispositionSummary run={run} onFilter={onFilter} activeFilter={onFilter ? disposition : undefined} />
      <ExportButtons name={run.run_id} urlFor={(format) => runExportUrl(run.run_id, format)} formats={["pdf"]} />
      <RunDetails run={run} historyLink={historyLink} />
    </Card>
  );

  if (sorted.length === 1) {
    const only = sorted[0]!;
    return (
      <div className="space-y-4">
        {header()}
        <Card>
          <ReceiptDetail result={only} input={inputMap?.get(only.receipt_id) ?? null} run={{ runId: run.run_id, setup: run.setup }} />
        </Card>
      </div>
    );
  }

  const counts = Object.fromEntries(DISPOSITION_ORDER.map((key) => [key, sorted.filter((result) => result.disposition === key).length])) as Record<Disposition, number>;
  const shown = filterResults(sorted, inputMap, query, disposition);
  // Navigation follows exactly what the list shows: a filtered-out receipt is never selected or stepped onto.
  const index = Math.max(0, shown.findIndex((result) => result.receipt_id === selectedId));
  const selected = shown[index];
  // When the filters hide the selection, the shown receipt becomes the selection, so clearing them keeps it.
  if (selected && selected.receipt_id !== selectedId) setSelectedId(selected.receipt_id);

  function select(receiptId: string) {
    setSelectedId(receiptId);
    setCompactView("detail");
    if (window.matchMedia?.("(max-width: 1023px)").matches) requestAnimationFrame(() => workspaceRef.current?.scrollIntoView?.({ block: "start" }));
  }

  function filter(next: Disposition | "all") {
    setDisposition(next);
    setCompactView("list");
  }

  return (
    <div className="space-y-4">
      {header(filter)}
      <Card ref={workspaceRef} className="scroll-mt-20 lg:grid lg:grid-cols-[280px_minmax(0,1fr)] lg:divide-x lg:divide-border">
        <aside
          aria-label="Receipts"
          className={cn("overflow-hidden rounded-l-lg lg:sticky lg:top-14 lg:block lg:max-h-[calc(100vh-4rem)] lg:self-start", compactView === "detail" && "max-lg:hidden")}
        >
          <ReceiptResultsList
            shown={shown}
            counts={counts}
            total={sorted.length}
            inputs={inputMap}
            selectedId={selected?.receipt_id ?? null}
            onSelect={select}
            query={query}
            onQueryChange={setQuery}
            disposition={disposition}
            onDispositionChange={filter}
          />
        </aside>
        <div className={cn("min-w-0", compactView === "list" && "max-lg:hidden")}>
          {selected ? (
            <ReceiptDetail
              key={selected.receipt_id}
              result={selected}
              input={inputMap?.get(selected.receipt_id) ?? null}
              run={{ runId: run.run_id, setup: run.setup }}
              navigation={{
                position: index + 1,
                total: shown.length,
                onPrevious: () => shown[index - 1] && setSelectedId(shown[index - 1]!.receipt_id),
                onNext: () => shown[index + 1] && setSelectedId(shown[index + 1]!.receipt_id),
                onBack: () => setCompactView("list"),
              }}
            />
          ) : (
            <p className="px-5 py-10 text-center text-[13px] text-muted-foreground">No receipts match the filters.</p>
          )}
        </div>
      </Card>
    </div>
  );
}
