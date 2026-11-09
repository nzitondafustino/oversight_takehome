import { CircleDashed, Lock } from "lucide-react";
import type { ReactNode } from "react";
import { useRescoreEvaluation } from "@/api/queries";
import type { ClassificationMetrics, Disposition, DispositionMetrics, EvaluationMetrics, EvidenceMetrics, FindingsMetrics, Prf, Unavailable } from "@/api/types";
import { ErrorState } from "@/components/states/states";
import { Button } from "@/components/ui/button";
import { DISPOSITION_ORDER, DISPOSITIONS } from "@/components/status/badges";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDateTime, formatInteger, formatRate, formatScore, humanize } from "@/lib/format";
import { cn } from "@/lib/utils";
import { RateStat, Stat } from "./display";
import { BaselineSection, CoverageSection, DetectionSection, Details, ErrorAnalysisSection, EvidenceSection, NotInVersion, Unscored } from "./QualitySections";

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

export function Matrix({ caption, rows, columns, counts }: { caption: string; rows: string[]; columns: string[]; counts: Record<string, Record<string, number>> }) {
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
        <span className="font-semibold">Final metrics</span>, from the results stored when the job finished; they are never recomputed on the fly, and the scoring version that made them is shown below.{" "}
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

/** Which scoring made these numbers, and how to see them with the current one. Old scores are never shown as new. */
function ScoringBanner({ metrics, scoring, onScoring, evaluationId }: { metrics: EvaluationMetrics; scoring: "latest" | "original"; onScoring?: (scoring: "latest" | "original") => void; evaluationId?: string }) {
  const rescore = useRescoreEvaluation();
  const scored = metrics.scored;
  if (!scored || scored.kind === "provisional") return null;
  const current = scored.current_schema_version;
  const outdated = metrics.schema_version !== current;
  return (
    <div className={cn("flex flex-wrap items-center gap-2 rounded-md border px-3 py-2 text-[13px]", outdated ? "border-status-review-border bg-status-review-soft" : "border-border")} role="status">
      <p className="flex-1">
        {scored.kind === "rescored"
          ? `Rescored with ${metrics.schema_version} on ${formatDateTime(scored.rescored_at ?? null)}, from the run's stored results. It finished scored with ${scored.original_schema_version}, which is kept.`
          : outdated
            ? `Scored with ${metrics.schema_version} when the run finished. The current scoring, ${current}, adds exception detection per rule, evidence, the keyword baseline, coverage and error analysis.`
            : `Scored with ${metrics.schema_version} when the run finished.`}
      </p>
      {outdated && evaluationId && !scored.rescorings?.some((item) => item.schema_version === current) ? (
        <Button size="sm" onClick={() => rescore.mutate(evaluationId, { onSuccess: () => onScoring?.("latest") })} disabled={rescore.isPending}>
          Rescore with {current}
        </Button>
      ) : null}
      {scored.rescorings?.length && onScoring ? (
        <Button size="sm" variant="outline" onClick={() => onScoring(scoring === "latest" ? "original" : "latest")}>
          {scoring === "latest" ? `Show as first scored (${scored.original_schema_version})` : "Show the latest scoring"}
        </Button>
      ) : null}
      {rescore.isError ? <ErrorState error={rescore.error} /> : null}
    </div>
  );
}

function Headline({ metrics }: { metrics: EvaluationMetrics }) {
  const { classification, disposition, findings, evidence } = metrics;
  const value = (block: { available: boolean } | undefined, read: () => string) => (block?.available ? read() : "—");
  return (
    <dl className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6" aria-label="Headline">
      <Stat label="Category accuracy" value={value(classification, () => formatRate((classification as ClassificationMetrics).accuracy.value))} hint="Q1 · per line" />
      <Stat label="Macro F1" value={value(classification, () => formatScore((classification as ClassificationMetrics).macro.f1))} hint="Q1 · over expected categories" />
      <Stat label="Restricted F1" value={value(classification, () => formatScore((classification as ClassificationMetrics).restricted.f1))} hint="Q2 · restricted = true" />
      <Stat label="Disposition accuracy" value={value(disposition, () => formatRate((disposition as DispositionMetrics).accuracy.value))} hint="Q3 · per receipt" />
      <Stat label="Finding F1" value={value(findings, () => formatScore((findings as FindingsMetrics).micro.f1))} hint="Q3 · per rule, micro" />
      <Stat label="Exact evidence" value={evidence?.available ? formatRate((evidence as EvidenceMetrics).exact.value) : findings.available && (findings as FindingsMetrics).evidence_exact ? formatRate((findings as FindingsMetrics).evidence_exact!.value) : "—"} hint="Q3 · matched exceptions" />
    </dl>
  );
}

export function DispositionMatrix({ disposition }: { disposition: DispositionMetrics }) {
  const full = disposition.matrix_with_not_evaluated;
  if (!full) return <Matrix caption="Expected dispositions by predicted ones" rows={[...DISPOSITION_ORDER]} columns={[...DISPOSITION_ORDER]} counts={disposition.matrix_gold_by_predicted} />;
  return (
    <div className="space-y-1">
      <div className="overflow-auto rounded-md border border-border">
        <table className="w-full text-xs">
          <caption className="sr-only">Expected dispositions by predicted ones, with receipts not evaluated</caption>
          <thead>
            <tr>
              <th scope="col" className="px-2 py-1.5 text-left font-medium text-muted-foreground">Expected ↓ · predicted →</th>
              {full.columns.map((column) => (
                <th key={column} scope="col" className="px-2 py-1.5 text-right font-medium text-muted-foreground">{column === "not_evaluated" ? "not evaluated" : column}</th>
              ))}
              <th scope="col" className="px-2 py-1.5 text-right font-medium text-muted-foreground">Total</th>
            </tr>
          </thead>
          <tbody>
            {DISPOSITION_ORDER.map((row) => (
              <tr key={row} className="border-t border-border">
                <th scope="row" className="px-2 py-1 text-left font-medium">{row}</th>
                {full.columns.map((column) => {
                  const count = full.rows[row]?.[column] ?? 0;
                  const undecided = column !== "not_evaluated" ? disposition.undecided_by_cell?.[row]?.[column as Disposition] ?? 0 : 0;
                  return (
                    <td key={column} className={cn("tabular px-2 py-1 text-right", count === 0 ? "text-subtle-foreground" : row === column ? "font-semibold text-status-pass" : "font-semibold text-status-flag-text")}>
                      {count}
                      {undecided ? <span className="ml-1 font-normal text-status-review-text" title="not fully decided">({undecided} undecided)</span> : null}
                    </td>
                  );
                })}
                <td className="tabular px-2 py-1 text-right">{full.row_totals[row]}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-subtle-foreground">
        {full.correct} correct, {full.incorrect} incorrect. A receipt not evaluated is never correct. A pass the engine did not fully decide stays a pass, with its undecided count beside it: a provisional pass, not a cleared one.
      </p>
    </div>
  );
}

export function MetricsView({ metrics, scoring = "latest", onScoring, evaluationId }: { metrics: EvaluationMetrics; scoring?: "latest" | "original"; onScoring?: (scoring: "latest" | "original") => void; evaluationId?: string }) {
  const { classification, disposition, findings, operational } = metrics;
  const version = metrics.schema_version;
  return (
    <div className="space-y-5">
      <Provenance metrics={metrics} />
      <ScoringBanner metrics={metrics} scoring={scoring} onScoring={onScoring} evaluationId={evaluationId} />
      <Headline metrics={metrics} />

      <Block title="1 · Classification quality" note="How accurately each line is classified, and how accurately restricted lines are identified. Per labelled line; a line with no category, or whose receipt failed, is wrong.">
        {!classification.available ? (
          <NotScored block={classification} />
        ) : (
          <>
            <dl className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <RateStat label="Category accuracy" rate={classification.accuracy} />
              <RateStat label="Accuracy when predicted" rate={classification.accuracy_when_predicted} />
              <RateStat label="Coverage" rate={classification.coverage} hint="Lines given a category" />
              <Stat label="Macro F1" value={formatScore(classification.macro.f1)} hint={`P ${formatScore(classification.macro.precision)} · R ${formatScore(classification.macro.recall)} · over ${Object.keys(classification.per_category).length} expected categories`} />
            </dl>
            <dl className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <Stat label="Restricted precision" value={formatScore(classification.restricted.precision)} hint={`${classification.restricted.tp} of ${classification.restricted.tp + classification.restricted.fp} flagged lines`} />
              <Stat label="Restricted recall" value={formatScore(classification.restricted.recall)} hint={`${classification.restricted.tp} of ${classification.restricted.tp + classification.restricted.fn} restricted lines`} />
              <Stat label="Restricted F1" value={formatScore(classification.restricted.f1)} hint={`independent of macro F1 · ${classification.restricted.lines} lines`} />
              {classification.restricted_matrix ? (
                <Stat
                  label="Restricted matrix"
                  value={`${classification.restricted.tp} · ${classification.restricted.fp} · ${classification.restricted.fn} · ${classification.restricted_matrix.tn}`}
                  hint={`TP · FP · FN · TN${classification.restricted_matrix.rows_gold_columns_predicted.true.none + classification.restricted_matrix.rows_gold_columns_predicted.false.none ? ` · ${classification.restricted_matrix.rows_gold_columns_predicted.true.none + classification.restricted_matrix.rows_gold_columns_predicted.false.none} with no value` : ""}`}
                />
              ) : null}
            </dl>
            <p className="text-xs text-muted-foreground">
              {formatInteger(classification.lines)} labelled lines:{" "}
              {Object.entries(classification.outcomes)
                .map(([outcome, count]) => `${count} ${humanize(outcome).toLowerCase()}`)
                .join(" · ")}
              .{classification.extra_predicted && Object.keys(classification.extra_predicted).length ? ` Predicted categories no answer has: ${Object.entries(classification.extra_predicted).map(([name, count]) => `${name} (${count})`).join(", ")}.` : ""}
            </p>
            <Details summary="Per category">
              <PrfTable caption="Scores per expected category" label="Category" rows={Object.entries(classification.per_category)} />
            </Details>
            <Details summary="Category confusion matrix">
              <Matrix caption="Expected categories by predicted ones" {...classification.confusion_matrix} />
              <p className="mt-1 text-xs text-subtle-foreground">(none): no category, for whatever reason.</p>
            </Details>
          </>
        )}
      </Block>

      <Block title="2 · Policy decision quality" note="The receipt's disposition: flag, review or pass, as the engine decided it. A receipt that could not be evaluated counts as not correct.">
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
            <DispositionMatrix disposition={disposition} />
            <Details summary="Missed flags, false flags, abstention and undecided outcomes">
              <dl className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
                {RATES.slice(2).map((item) => (
                  <RateStat key={item.key} label={item.label} rate={disposition[item.key]} hint={item.hint} />
                ))}
              </dl>
              <div className="mt-2">
                <PrfTable caption="Scores per disposition" label="Disposition" rows={DISPOSITION_ORDER.map((name) => [name, disposition.per_disposition[name]] as [string, Prf & { support: number }])} />
              </div>
            </Details>
          </>
        )}
      </Block>

      <Block title="3 · Exception detection" note="Does it raise the right rule on the right receipt? Exceptions and review signals, rule by rule. EXC-03 uncertainty signals are never expected, so each counts against the strict score.">
        {!findings.available ? (
          <NotScored block={findings} />
        ) : findings.macro ? (
          <DetectionSection findings={findings} />
        ) : (
          <>
            <NotInVersion what="Per-rule detection with missed and unexpected findings" version={version} />
            <PrfTable caption="Scores per rule" label="Rule" rows={Object.entries(findings.per_rule)} />
          </>
        )}
      </Block>

      <Block title="4 · Evidence correctness" note="For the exceptions that match an expected one: does it cite exactly the right line items? Separate from detection and disposition.">
        {!metrics.evidence ? <NotInVersion what="Evidence correctness" version={version} /> : !metrics.evidence.available ? <Unscored block={metrics.evidence} /> : <EvidenceSection evidence={metrics.evidence} />}
      </Block>

      <Block title="5 · Keyword baseline" note="How much better or worse than matching restricted terms: the simpler system this replaces, on the same lines.">
        {!metrics.baseline ? <NotInVersion what="The keyword baseline" version={version} /> : !metrics.baseline.available ? <Unscored block={metrics.baseline} /> : <BaselineSection baseline={metrics.baseline} />}
      </Block>

      <Block title="6 · Coverage and error analysis" note="Which rules this dataset exercised and tests, and where the run failed and which stage was responsible.">
        {!metrics.coverage ? <NotInVersion what="Rule coverage" version={version} /> : !metrics.coverage.available ? <Unscored block={metrics.coverage} /> : <CoverageSection coverage={metrics.coverage} />}
        {!metrics.error_analysis ? null : !metrics.error_analysis.available ? <Unscored block={metrics.error_analysis} /> : <ErrorAnalysisSection analysis={metrics.error_analysis} />}
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
