import { Check, ChevronDown, ChevronRight, CircleDashed, X } from "lucide-react";
import { Fragment, useState } from "react";
import { useEvaluationResults } from "@/api/queries";
import type { Disposition, EvaluationResultFilters, EvaluationResultItem } from "@/api/types";
import { OrchestrationHistory } from "@/components/results/OrchestrationHistory";
import { DecidedBadge } from "@/components/results/style";
import { EmptyState, ErrorState, LoadingRows } from "@/components/states/states";
import { DispositionBadge, isProvisionalPass } from "@/components/status/badges";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatInteger, humanize } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Pager } from "@/pages/dashboard/Section";
import { newSpendText } from "./display";

const PAGE_SIZE = 20;

type Choice = "" | "true" | "false";

function Verdict({ correct, provisional = false }: { correct: boolean | null; provisional?: boolean }) {
  if (correct === null) return <span className="text-xs text-subtle-foreground">not labelled</span>;
  // Scoring compares dispositions only: a provisional pass equals an expected pass there, but it is no clearance.
  if (correct && provisional)
    return (
      <span className="inline-flex items-center gap-0.5 text-xs font-medium text-status-review-text" title="The disposition equals the expected pass, but rules are still open: this is a provisional pass, not a clearance.">
        <CircleDashed className="size-3.5" aria-hidden />
        disposition matches · rules still open
      </span>
    );
  return correct ? (
    <span className="inline-flex items-center gap-0.5 text-xs font-medium text-status-pass">
      <Check className="size-3.5" aria-hidden />
      matches
    </span>
  ) : (
    <span className="inline-flex items-center gap-0.5 text-xs font-medium text-status-flag-text">
      <X className="size-3.5" aria-hidden />
      differs
    </span>
  );
}

function FilterSelect({ id, label, value, onChange, options }: { id: string; label: string; value: string; onChange: (value: string) => void; options: [string, string][] }) {
  return (
    <div className="flex items-center gap-1.5">
      <label htmlFor={id} className="text-xs font-medium text-muted-foreground">
        {label}
      </label>
      <select id={id} value={value} onChange={(event) => onChange(event.target.value)} className="h-8 rounded-md border border-input bg-card px-2 text-[13px]">
        {options.map(([optionValue, optionLabel]) => (
          <option key={optionValue} value={optionValue}>
            {optionLabel}
          </option>
        ))}
      </select>
    </div>
  );
}

function ReceiptLines({ item }: { item: EvaluationResultItem }) {
  const predicted = new Map((item.result?.line_items ?? []).map((line) => [line.line_id, line]));
  const lineIds = [...new Set([...Object.keys(item.scoring.lines), ...predicted.keys()])];
  const expectedRules = item.expected.findings?.exceptions.map((exception) => exception.rule_id) ?? null;
  const actualRules = item.result?.exceptions.map((exception) => exception.rule_id) ?? [];
  return (
    <div className="space-y-3 bg-muted/30 px-5 py-3 text-xs">
      {item.error ? (
        <p className="text-status-flag-text">
          <span className="font-mono font-medium">{item.error.code}</span>: {item.error.message}
        </p>
      ) : null}
      {lineIds.length ? (
        <table className="w-full">
          <caption className="sr-only">Lines of {item.receipt_id}</caption>
          <thead className="text-left text-muted-foreground">
            <tr>
              <th className="py-1 font-medium">Line</th>
              <th className="py-1 font-medium">Expected category</th>
              <th className="py-1 font-medium">Predicted</th>
              <th className="py-1 font-medium">Outcome</th>
              <th className="py-1 text-right font-medium">Confidence</th>
            </tr>
          </thead>
          <tbody>
            {lineIds.map((lineId) => {
              const scored = item.scoring.lines[lineId];
              const line = predicted.get(lineId);
              return (
                <tr key={lineId} className="border-t border-border">
                  <td className="py-1 pr-2">
                    <span className="font-mono">{lineId}</span>
                    {line ? <span className="block text-subtle-foreground">{line.description_raw}</span> : null}
                  </td>
                  <td className="py-1 pr-2 font-mono">{scored?.expected ?? "—"}</td>
                  <td className="py-1 pr-2 font-mono">{line?.category ?? scored?.predicted ?? "—"}</td>
                  <td className={cn("py-1 pr-2", scored?.outcome === "correct" ? "text-status-pass" : scored ? "text-status-flag-text" : "text-subtle-foreground")}>
                    {scored ? humanize(scored.outcome) : "not labelled"}
                    {line?.classification_error ? <span className="block text-status-flag-text">{line.classification_error}</span> : null}
                    {line?.classification_details?.orchestration ? <div className="mt-1"><OrchestrationHistory details={line.classification_details} /></div> : null}
                  </td>
                  <td className="tabular py-1 text-right">{line?.confidence ?? "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : null}
      {item.result ? (
        <dl className="grid gap-2 sm:grid-cols-3">
          <div>
            <dt className="text-muted-foreground">Exceptions</dt>
            <dd className="font-mono">
              {actualRules.join(", ") || "none"}
              {expectedRules ? <span className="block font-sans text-subtle-foreground">expected: {expectedRules.join(", ") || "none"}</span> : null}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Uncertainty</dt>
            <dd>
              {item.result.uncertainty.length
                ? item.result.uncertainty.map((entry, index) => (
                    <span key={index} className="block">
                      <span className="font-mono">{entry.line_id}</span> {entry.field}: {humanize(entry.reason)}
                    </span>
                  ))
                : "none"}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Open questions</dt>
            <dd className="font-mono">{item.result.open_questions.map((question) => question.rule_id).join(", ") || <span className="font-sans">none</span>}</dd>
          </div>
        </dl>
      ) : null}
    </div>
  );
}

function ResultRow({ item, mode }: { item: EvaluationResultItem; mode: string }) {
  const [open, setOpen] = useState(false);
  const { result, scoring } = item;
  const labelled = Object.values(scoring.lines);
  const correctLines = labelled.filter((line) => line.outcome === "correct").length;
  return (
    <Fragment>
      <TableRow>
        <TableCell className="pl-5">
          <button type="button" onClick={() => setOpen((value) => !value)} aria-expanded={open} className="inline-flex items-center gap-1 font-mono text-[13px] font-medium text-foreground hover:text-primary">
            {open ? <ChevronDown className="size-3.5" aria-hidden /> : <ChevronRight className="size-3.5" aria-hidden />}
            {item.receipt_id}
          </button>
          {item.context === "isolated_receipts" ? <p className="text-[11px] text-subtle-foreground">evaluated alone</p> : null}
        </TableCell>
        <TableCell>{scoring.expected_disposition ? <DispositionBadge disposition={scoring.expected_disposition} /> : <span className="text-xs text-subtle-foreground">not labelled</span>}</TableCell>
        <TableCell>
          {result ? (
            <div className="flex flex-wrap items-center gap-1">
              <DispositionBadge disposition={result.disposition} decided={result.decided} />
              <DecidedBadge result={result} />
            </div>
          ) : (
            <Badge variant="flag">Not evaluated</Badge>
          )}
          <div className="mt-0.5">
            <Verdict correct={scoring.disposition_correct} provisional={result ? isProvisionalPass(result) : false} />
          </div>
        </TableCell>
        <TableCell className="text-xs">
          {labelled.length ? (
            <>
              <span className="tabular">
                {correctLines}/{labelled.length} lines
              </span>
              <div>
                <Verdict correct={scoring.classification_correct} />
              </div>
            </>
          ) : (
            <span className="text-subtle-foreground">not labelled</span>
          )}
        </TableCell>
        <TableCell className="text-xs">
          {item.error ? <span className="font-mono text-status-flag-text">{item.error.code}</span> : null}
          {result?.uncertainty.length ? <Badge variant="review">{result.uncertainty.length} uncertain</Badge> : null}
          {!item.error && !result?.uncertainty.length ? <span className="text-subtle-foreground">—</span> : null}
        </TableCell>
        <TableCell className="tabular text-right text-xs">{item.latency_ms == null ? "—" : `${formatInteger(item.latency_ms)} ms`}</TableCell>
        <TableCell className="pr-5 text-right text-xs">{newSpendText(mode, item.spend)}</TableCell>
      </TableRow>
      {open ? (
        <TableRow className="hover:bg-transparent">
          <TableCell colSpan={7} className="p-0">
            <ReceiptLines item={item} />
          </TableCell>
        </TableRow>
      ) : null}
    </Fragment>
  );
}

/** GET /v1/evaluations/{id}/results: each receipt's core result against its answers, filtered by the API, in dataset order. */
export function EvaluationResults({ evaluationId, mode }: { evaluationId: string; mode: string }) {
  const [offset, setOffset] = useState(0);
  const [classification, setClassification] = useState<Choice>("");
  const [disposition, setDisposition] = useState<Disposition | "">("");
  const [error, setError] = useState<Choice>("");
  const [uncertain, setUncertain] = useState<Choice>("");
  const filters: EvaluationResultFilters = {
    ...(classification ? { classification_correct: classification === "true" } : {}),
    ...(disposition ? { disposition: [disposition] } : {}),
    ...(error ? { error: error === "true" } : {}),
    ...(uncertain ? { uncertain: uncertain === "true" } : {}),
  };
  const results = useEvaluationResults(evaluationId, { limit: PAGE_SIZE, offset, ...filters });
  const filtered = Boolean(classification || disposition || error || uncertain);
  const set = <T,>(setter: (value: T) => void) => (value: string) => {
    setter(value as T);
    setOffset(0);
  };

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-3" role="group" aria-label="Result filters">
        <FilterSelect id="filter-classification" label="Classification" value={classification} onChange={set(setClassification)} options={[["", "Any"], ["true", "All lines correct"], ["false", "Some line wrong"]]} />
        <FilterSelect id="filter-disposition" label="Predicted" value={disposition} onChange={set(setDisposition)} options={[["", "Any"], ["flag", "Flag"], ["review", "Review"], ["pass", "Pass"]]} />
        <FilterSelect id="filter-error" label="Errors" value={error} onChange={set(setError)} options={[["", "Any"], ["true", "Failed"], ["false", "No error"]]} />
        <FilterSelect id="filter-uncertain" label="Uncertain" value={uncertain} onChange={set(setUncertain)} options={[["", "Any"], ["true", "Yes"], ["false", "No"]]} />
        {results.data ? <span className="ml-auto text-xs text-subtle-foreground">{results.data.total} receipts</span> : null}
      </div>
      {results.isPending ? (
        <LoadingRows rows={5} label="Loading results" />
      ) : results.isError ? (
        <ErrorState error={results.error} onRetry={() => void results.refetch()} />
      ) : results.data.items.length === 0 ? (
        <EmptyState title={filtered ? "No receipts match these filters" : "No results stored yet"} description={filtered ? "Clear a filter to see more." : "Results appear as the job runs."} className="py-6" />
      ) : (
        <>
          <div className="-mx-5 overflow-x-auto border-y border-border">
            <Table>
              <caption className="sr-only">Receipt results</caption>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="pl-5">Receipt</TableHead>
                  <TableHead>Expected</TableHead>
                  <TableHead>Predicted</TableHead>
                  <TableHead>Categories</TableHead>
                  <TableHead>Errors · uncertainty</TableHead>
                  <TableHead className="text-right">Latency</TableHead>
                  <TableHead className="pr-5 text-right">New spend</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {results.data.items.map((item) => (
                  <ResultRow key={item.receipt_id} item={item} mode={mode} />
                ))}
              </TableBody>
            </Table>
          </div>
          <Pager offset={offset} limit={PAGE_SIZE} total={results.data.total} onChange={setOffset} busy={results.isFetching} />
        </>
      )}
    </div>
  );
}
