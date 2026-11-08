import { CircleDashed, Lock } from "lucide-react";
import type { ReactNode } from "react";
import type { Disposition, EvaluationMetrics, Prf, Unavailable } from "@/api/types";
import { DISPOSITION_ORDER, DISPOSITIONS } from "@/components/status/badges";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatInteger, formatRate, formatScore, humanize } from "@/lib/format";
import { cn } from "@/lib/utils";
import { RateStat, Stat } from "./display";

function Block({ title, children, note }: { title: string; children: ReactNode; note?: ReactNode }) {
  return (
    <section aria-label={title} className="space-y-3 border-t border-border pt-4 first:border-t-0 first:pt-0">
      <div>
        <h3 className="text-sm font-semibold text-foreground">{title}</h3>
        {note ? <p className="mt-0.5 text-xs leading-4 text-subtle-foreground">{note}</p> : null}
      </div>
      {children}
    </section>
  );
}

function NotScored({ block }: { block: Unavailable }) {
  return <p className="text-[13px] text-muted-foreground">Not scored: {block.reason}.</p>;
}

function PrfTable({ caption, label, rows }: { caption: string; label: string; rows: [string, Prf & { support?: number }][] }) {
  return (
    <div className="max-h-80 overflow-auto rounded-md border border-border">
      <Table>
        <caption className="sr-only">{caption}</caption>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead>{label}</TableHead>
            <TableHead className="text-right">Precision</TableHead>
            <TableHead className="text-right">Recall</TableHead>
            <TableHead className="text-right">F1</TableHead>
            <TableHead className="text-right">TP · FP · FN</TableHead>
            {rows.some(([, row]) => row.support != null) ? <TableHead className="text-right">Support</TableHead> : null}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map(([name, row]) => (
            <TableRow key={name}>
              <TableCell className="font-mono text-xs">{name}</TableCell>
              <TableCell className="tabular text-right">{formatScore(row.precision)}</TableCell>
              <TableCell className="tabular text-right">{formatScore(row.recall)}</TableCell>
              <TableCell className="tabular text-right">{formatScore(row.f1)}</TableCell>
              <TableCell className="tabular text-right text-xs text-muted-foreground">
                {row.tp} · {row.fp} · {row.fn}
              </TableCell>
              {row.support != null ? <TableCell className="tabular text-right">{row.support}</TableCell> : null}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function Matrix({ caption, rows, columns, counts }: { caption: string; rows: string[]; columns: string[]; counts: Record<string, Record<string, number>> }) {
  return (
    <div className="max-h-96 overflow-auto rounded-md border border-border">
      <table className="w-full text-xs">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            <th scope="col" className="sticky left-0 bg-card px-2 py-1.5 text-left font-medium text-muted-foreground">
              Expected ↓ · predicted →
            </th>
            {columns.map((column) => (
              <th key={column} scope="col" className="px-2 py-1.5 text-right font-mono font-medium text-muted-foreground">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row} className="border-t border-border">
              <th scope="row" className="sticky left-0 bg-card px-2 py-1 text-left font-mono font-medium">
                {row}
              </th>
              {columns.map((column) => {
                const value = counts[row]?.[column] ?? 0;
                return (
                  <td key={column} className={cn("tabular px-2 py-1 text-right", value === 0 ? "text-subtle-foreground" : row === column ? "font-semibold text-status-pass" : "font-semibold text-status-flag-text")}>
                    {value}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Banner first: provisional metrics are over the receipts stored so far, each evaluated alone, and never stored;
 * final metrics were written once when the job finished and are never recomputed.
 */
function Provenance({ metrics }: { metrics: EvaluationMetrics }) {
  if (metrics.provisional)
    return (
      <div role="status" className="flex gap-2.5 rounded-md border border-status-review-border bg-status-review-soft px-3 py-2 text-[13px]">
        <CircleDashed className="mt-0.5 size-4 shrink-0 text-status-review-text" aria-hidden />
        <p>
          <span className="font-semibold text-status-review-text">Provisional metrics.</span> Worked out from the {formatInteger(metrics.operational.run)} receipts stored so far, each evaluated
          alone, so rules spanning receipts are left undecided. They are not stored, and change as the job runs. Final metrics replace them when it finishes.
        </p>
      </div>
    );
  return (
    <div className="flex gap-2.5 rounded-md border border-border bg-muted/50 px-3 py-2 text-[13px]">
      <Lock className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
      <p>
        <span className="font-semibold">Final metrics</span>, stored once when the job finished ({metrics.schema_version}) and never recomputed.{" "}
        {metrics.result_context === "isolated_receipts"
          ? "The run did not finish its dataset pass, so each receipt was evaluated alone and rules spanning receipts were left undecided."
          : `Every receipt was evaluated together as one ${metrics.context_complete === false ? "incomplete" : "complete"} context.`}{" "}
        Point estimates from one dataset, with Wilson 95% intervals.
      </p>
    </div>
  );
}

const RATES: { key: "false_pass" | "missed_flag_passed" | "missed_flag_any" | "false_flag" | "review" | "abstention" | "undetermined" | "flagged_undecided"; label: string; hint: string }[] = [
  { key: "false_pass", label: "False pass", hint: "Expected flag or review, got pass" },
  { key: "missed_flag_passed", label: "Missed flag → pass", hint: "Expected flag, got pass" },
  { key: "missed_flag_any", label: "Missed flag", hint: "Expected flag, got anything else" },
  { key: "false_flag", label: "False flag", hint: "Flagged without an expected flag" },
  { key: "review", label: "Sent to review", hint: "Of evaluated receipts" },
  { key: "abstention", label: "Abstention", hint: "Review for uncertainty or open evidence" },
  { key: "undetermined", label: "Undecided", hint: "Not every rule decided" },
  { key: "flagged_undecided", label: "Flagged, undecided", hint: "Of flagged receipts" },
];

export function MetricsView({ metrics }: { metrics: EvaluationMetrics }) {
  const { classification, disposition, findings, operational } = metrics;
  return (
    <div className="space-y-5">
      <Provenance metrics={metrics} />

      <Block title="Classification" note="Over lines with an expected category. A line with no category, or one whose receipt failed, is not correct.">
        {!classification.available ? (
          <NotScored block={classification} />
        ) : (
          <>
            <dl className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <RateStat label="Category accuracy" rate={classification.accuracy} />
              <RateStat label="Accuracy when predicted" rate={classification.accuracy_when_predicted} />
              <RateStat label="Coverage" rate={classification.coverage} hint="Lines given a category" />
              <Stat label="Macro F1" value={formatScore(classification.macro.f1)} hint={`P ${formatScore(classification.macro.precision)} · R ${formatScore(classification.macro.recall)}`} />
            </dl>
            <p className="text-xs text-muted-foreground">
              {formatInteger(classification.lines)} labelled lines:{" "}
              {Object.entries(classification.outcomes)
                .map(([outcome, count]) => `${count} ${humanize(outcome).toLowerCase()}`)
                .join(" · ")}
              . Restricted flag over {formatInteger(classification.restricted.lines)} labelled lines: F1 {formatScore(classification.restricted.f1)}.
            </p>
            <details>
              <summary className="cursor-pointer text-[13px] font-medium text-primary">Per category</summary>
              <div className="mt-2">
                <PrfTable caption="Scores per expected category" label="Category" rows={Object.entries(classification.per_category)} />
              </div>
            </details>
            <details>
              <summary className="cursor-pointer text-[13px] font-medium text-primary">Confusion matrix</summary>
              <div className="mt-2">
                <Matrix caption="Expected categories by predicted ones" {...classification.confusion_matrix} />
                <p className="mt-1 text-xs text-subtle-foreground">(none): no category, for whatever reason.</p>
              </div>
            </details>
          </>
        )}
      </Block>

      <Block title="Disposition" note="Over receipts with an expected disposition. A receipt that could not be evaluated counts as not correct. Dispositions only are compared: a provisional pass counts as matching an expected pass, so read it with Undecided below.">
        {!disposition.available ? (
          <NotScored block={disposition} />
        ) : (
          <>
            <dl className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <RateStat label="Disposition accuracy" rate={disposition.accuracy} />
              <RateStat label="Accuracy when evaluated" rate={disposition.accuracy_when_evaluated} />
              {RATES.slice(0, 2).map((item) => (
                <RateStat key={item.key} label={item.label} rate={disposition[item.key]} hint={item.hint} />
              ))}
            </dl>
            <dl className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
              {RATES.slice(2).map((item) => (
                <RateStat key={item.key} label={item.label} rate={disposition[item.key]} hint={item.hint} />
              ))}
            </dl>
            <details>
              <summary className="cursor-pointer text-[13px] font-medium text-primary">Per disposition and matrix</summary>
              <div className="mt-2 grid gap-3 lg:grid-cols-2">
                <PrfTable caption="Scores per disposition" label="Disposition" rows={DISPOSITION_ORDER.map((name) => [name, disposition.per_disposition[name]] as [string, Prf & { support: number }])} />
                <Matrix caption="Expected dispositions by predicted ones" rows={[...DISPOSITION_ORDER]} columns={[...DISPOSITION_ORDER]} counts={disposition.matrix_gold_by_predicted} />
              </div>
              {disposition.execution_errors ? (
                <p className="mt-1 text-xs text-status-flag-text">{disposition.execution_errors} labelled receipts could not be evaluated and count as not correct.</p>
              ) : null}
            </details>
          </>
        )}
      </Block>

      <Block title="Findings" note="Exceptions and review signals by rule. Strict counts every EXC-03 uncertainty signal as a false positive, as the answers never hold one.">
        {!findings.available ? (
          <NotScored block={findings} />
        ) : (
          <>
            <dl className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <Stat label="Micro F1 (strict)" value={formatScore(findings.micro.f1)} hint={`P ${formatScore(findings.micro.precision)} · R ${formatScore(findings.micro.recall)}`} />
              <Stat
                label="Micro F1 without EXC-03"
                value={formatScore(findings.micro_without_uncertainty_signals.f1)}
                hint={`P ${formatScore(findings.micro_without_uncertainty_signals.precision)} · R ${formatScore(findings.micro_without_uncertainty_signals.recall)}`}
              />
              <RateStat label="Exact evidence lines" rate={findings.evidence_exact} />
              <Stat label="Receipts scored" value={`${formatInteger(findings.evaluated)}/${formatInteger(findings.receipts)}`} />
            </dl>
            <details>
              <summary className="cursor-pointer text-[13px] font-medium text-primary">Per rule</summary>
              <div className="mt-2">
                <PrfTable caption="Scores per rule" label="Rule" rows={Object.entries(findings.per_rule)} />
              </div>
            </details>
          </>
        )}
      </Block>

      <Block title="Operational" note="Over the receipts run. Latency is wall-clock time per receipt in the first pass: replay is fast, live includes each provider call.">
        <dl className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Stat label="Receipts" value={`${formatInteger(operational.evaluated)}/${formatInteger(operational.receipts)}`} hint={`${formatInteger(operational.run)} run · ${formatInteger(operational.not_run)} not run`} />
          <RateStat label="Execution errors" rate={operational.execution_errors} hint="Receipts that could not be evaluated" />
          <RateStat label="Uncertain receipts" rate={operational.uncertainty.receipts} hint="With an EXC-03 signal" />
          <RateStat label="Undecided receipts" rate={operational.undetermined_receipts} hint="Some rule still open" />
          <RateStat label="Coverage" rate={operational.coverage} hint="Lines with a category" />
          <RateStat label="Classification errors" rate={operational.classification_errors} hint="Lines the classifier failed on" />
          <Stat label="Mean latency" value={operational.latency.mean_per_receipt_ms == null ? "—" : `${formatInteger(operational.latency.mean_per_receipt_ms)} ms`} hint={`per receipt, ${formatInteger(operational.latency.receipts_timed)} timed`} />
          <Stat label="Total latency" value={operational.latency.total_ms == null ? "—" : `${formatInteger(operational.latency.total_ms)} ms`} />
        </dl>
        {/* min-w-0 and break-all: a reason such as recipient_type:secondary_field_uncertain must wrap on a phone. */}
        <div className="grid gap-3 text-xs md:grid-cols-3 [&>*]:min-w-0">
          <div>
            <p className="font-medium text-foreground">Dispositions given</p>
            <ul className="mt-1 space-y-0.5 text-muted-foreground">
              {DISPOSITION_ORDER.map((name: Disposition) => (
                <li key={name}>
                  {DISPOSITIONS[name].label}: <span className="tabular text-foreground">{formatInteger(operational.dispositions[name])}</span>
                  {name === "pass" ? " (decided and provisional together: see undecided receipts)" : ""}
                </li>
              ))}
            </ul>
          </div>
          <div>
            <p className="font-medium text-foreground">Uncertainty by reason</p>
            <ul className="mt-1 space-y-0.5 break-all font-mono text-muted-foreground">
              {Object.entries(operational.uncertainty.by_reason).length ? (
                Object.entries(operational.uncertainty.by_reason).map(([reason, count]) => (
                  <li key={reason}>
                    {reason}: <span className="tabular text-foreground">{count}</span>
                  </li>
                ))
              ) : (
                <li className="font-sans">None</li>
              )}
            </ul>
            <p className="mt-1 text-subtle-foreground">Lines with uncertainty: {formatRate(operational.uncertainty.lines.value)}</p>
          </div>
          <div>
            <p className="font-medium text-foreground">Execution error codes</p>
            <ul className="mt-1 space-y-0.5 break-all font-mono text-muted-foreground">
              {Object.entries(operational.execution_error_codes).length ? (
                Object.entries(operational.execution_error_codes).map(([code, count]) => (
                  <li key={code}>
                    {code}: <span className="tabular text-foreground">{count}</span>
                  </li>
                ))
              ) : (
                <li className="font-sans">None</li>
              )}
            </ul>
          </div>
        </div>
      </Block>
    </div>
  );
}
