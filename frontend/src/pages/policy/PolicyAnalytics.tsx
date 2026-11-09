import { useState } from "react";
import { Link } from "react-router-dom";
import { useExtractions } from "@/api/queries";
import type { CompilationAnalytics, ExtractionListItem } from "@/api/types";
import { EmptyState, ErrorState, LoadingRows } from "@/components/states/states";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { sumDecimals } from "@/lib/decimal";
import { formatDateTime, formatInteger, formatUsd } from "@/lib/format";
import { Pager, Section } from "@/pages/dashboard/Section";
import { Stat } from "@/pages/evaluations/display";

function duration(ms: number | null): string {
  return ms == null ? "Not recorded" : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(2)} s`;
}
function modelOf(item: ExtractionListItem): string {
  return item.provenance.extraction_model ?? item.provenance.recorded_with?.extraction_model ?? "Recorded readings";
}
function cost(analytics: CompilationAnalytics | null | undefined): string {
  return analytics ? `${analytics.cost_completeness === "incomplete" ? "At least " : ""}${formatUsd(analytics.new_spend_usd)}` : "Not recorded";
}

export function CompilationMetrics({ analytics }: { analytics: CompilationAnalytics | null | undefined }) {
  if (!analytics) return <p className="text-xs text-muted-foreground">Spending and timing were not recorded for this compilation. Existing policy results are unchanged.</p>;
  return <dl className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label="Compilation execution metrics">
    <Stat label="New provider spend" value={cost(analytics)} hint={analytics.unpriced_calls ? `${analytics.unpriced_calls} calls have no reported cost; total is a lower bound` : "Provider-reported USD; replay adds no spend"} />
    <Stat label="Compilation latency" value={duration(analytics.duration_ms)} hint="Document loading, readings and candidate construction" />
    <Stat label="Provider latency" value={duration(analytics.provider_latency_ms)} hint={`${analytics.live_calls} live calls · ${analytics.failed_calls} failed calls`} />
    <Stat label="Tokens · input / output" value={`${formatInteger(analytics.input_tokens)} / ${formatInteger(analytics.output_tokens)}`} hint={`${analytics.reused_documents} documents reused without a provider call`} />
  </dl>;
}

/** Scope is the latest 100 completed compilations, explicitly labelled; signed results are never recomputed. */
export function PolicyAnalytics() {
  const extractions = useExtractions({ limit: 100, offset: 0 });
  const [mode, setMode] = useState("");
  const [offset, setOffset] = useState(0);
  if (extractions.isPending) return <LoadingRows rows={4} label="Loading policy analytics" />;
  if (extractions.isError) return <ErrorState error={extractions.error} onRetry={() => void extractions.refetch()} />;
  const items = extractions.data.items.filter((item) => !mode || item.provenance.execution_mode === mode);
  const measured = items.flatMap((item) => item.analytics ? [item.analytics] : []);
  const missing = items.length - measured.length;
  const sum = (key: "duration_ms" | "provider_latency_ms" | "live_calls" | "failed_calls" | "reused_documents" | "unpriced_calls") => measured.reduce((total, item) => total + item[key], 0);
  const tokenTotal = (key: "input_tokens" | "output_tokens") => measured.length && measured.every((item) => item[key] != null) ? measured.reduce((total, item) => total + item[key]!, 0) : null;
  const times = measured.map((item) => item.duration_ms).sort((a, b) => a - b);
  const p95 = times.length ? times[Math.ceil(times.length * 0.95) - 1]! : null;
  const unknownCosts = sum("unpriced_calls");
  const totalCost = measured.length ? `${unknownCosts ? "At least " : ""}${formatUsd(sumDecimals(measured.map((item) => item.new_spend_usd)))}` : "Not recorded";

  return <div className="space-y-5">
    <Section id="policy-analytics" title="Compilation spending & performance" description={`Scope: newest ${extractions.data.items.length} of ${extractions.data.total} stored compilations. Approval, publication and receipt evaluation costs are not included.`}>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <label htmlFor="compilation-mode" className="text-xs font-medium">Execution mode</label>
        <select id="compilation-mode" value={mode} onChange={(e) => { setMode(e.target.value); setOffset(0); }} className="h-9 rounded-md border border-input bg-card px-3 text-sm">
          <option value="">All modes</option><option value="live">Live</option><option value="replay">Replay</option>
        </select>
        <span className="text-xs text-muted-foreground">{measured.length} of {items.length} compilations have recorded execution metrics</span>
      </div>
      {missing ? <p className="mb-4 rounded-md border border-border bg-muted/40 p-3 text-xs text-muted-foreground">{missing} older compilations have no execution metrics and are excluded from the totals below. Missing data is not zero.</p> : null}
      {unknownCosts ? <p className="mb-4 text-xs text-status-review-text">{unknownCosts} provider calls have no reported cost. Spending is a lower bound, not a complete bill.</p> : null}
      <dl className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" aria-label="Policy analytics totals">
        <Stat label="Total new spend" value={totalCost} hint="Exact sum of recorded provider costs in USD" />
        <Stat label="Total compilation latency" value={duration(measured.length ? sum("duration_ms") : null)} hint="Sum of elapsed compilation times, excluding queue wait" />
        <Stat label="Mean compilation latency" value={duration(measured.length ? Math.round(sum("duration_ms") / measured.length) : null)} hint={`P95: ${duration(p95)} · ${measured.length} measured compilations`} />
        <Stat label="Total provider latency" value={duration(measured.length ? sum("provider_latency_ms") : null)} hint="Time spent in actual provider requests, including failures" />
        <Stat label="Live provider calls" value={measured.length ? formatInteger(sum("live_calls")) : "Not recorded"} hint={`${sum("failed_calls")} failed calls · ${sum("reused_documents")} documents reused without calls`} />
        <Stat label="Tokens · input / output" value={`${formatInteger(tokenTotal("input_tokens"))} / ${formatInteger(tokenTotal("output_tokens"))}`} hint="Provider-reported counts; — means usage is missing" />
      </dl>
    </Section>
    <Section id="compilation-performance" title="Per-compilation metrics" description="Open a compilation to inspect its policy readings and review decisions. Replay and cache hits make no new provider calls.">
      {!items.length ? <EmptyState title="No recorded compilation metrics in this view" description="Published versions may reuse existing compiler evidence without a new extraction or provider call. View their source documents and approval in Rules; spending and latency are shown only when recorded." /> : <>
        <Table className="min-w-[850px]">
          <caption className="sr-only">Compilation spending and latency</caption>
          <TableHeader><TableRow><TableHead>Compilation</TableHead><TableHead>Mode · model</TableHead><TableHead>Documents</TableHead><TableHead>New spend</TableHead><TableHead>Total latency</TableHead><TableHead>Provider latency</TableHead><TableHead>Live calls</TableHead><TableHead>Tokens in / out</TableHead></TableRow></TableHeader>
          <TableBody>{items.slice(offset, offset + 10).map((item) => <TableRow key={item.extraction_id}>
            <TableCell><Link to={`/documents/extractions/${encodeURIComponent(item.extraction_id)}?tab=provenance`} className="font-mono text-xs text-primary hover:underline">{item.extraction_id}</Link><span className="block text-xs text-muted-foreground">{formatDateTime(item.created_at)}</span></TableCell>
            <TableCell><span className="capitalize">{item.provenance.execution_mode}</span><span className="block text-xs text-muted-foreground">{modelOf(item)}</span></TableCell>
            <TableCell>{item.documents.length}</TableCell><TableCell className="tabular">{cost(item.analytics)}</TableCell>
            <TableCell className="tabular">{duration(item.analytics?.duration_ms ?? null)}</TableCell><TableCell className="tabular">{duration(item.analytics?.provider_latency_ms ?? null)}</TableCell>
            <TableCell>{item.analytics ? `${item.analytics.live_calls} (${item.analytics.failed_calls} failed)` : "Not recorded"}</TableCell>
            <TableCell className="tabular">{item.analytics ? `${formatInteger(item.analytics.input_tokens)} / ${formatInteger(item.analytics.output_tokens)}` : "Not recorded"}</TableCell>
          </TableRow>)}</TableBody>
        </Table>
        <Pager offset={offset} limit={10} total={items.length} onChange={setOffset} />
      </>}
    </Section>
  </div>;
}
