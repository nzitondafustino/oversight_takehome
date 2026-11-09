/**
 * The evaluation report of the runs being compared, side by side: classification, rule-level detection, dispositions,
 * the keyword baseline, evidence and diagnosed failures. Every figure is a run's stored metrics, read through the same
 * endpoint as its detail page; nothing is scored here. A block a run has no score for says why, and is never shown
 * as zero.
 */
import { useState, type ReactNode } from "react";
import { useEvaluationsMetrics } from "@/api/queries";
import type { BaselineMetrics, BaselineLine, ClassificationMetrics, DispositionMetrics, ErrorAnalysis, EvaluationComparisonRow, EvaluationMetrics, EvidenceMetrics, FindingsMetrics, Prf, Rate, RuleDetection, Unavailable } from "@/api/types";
import { ErrorState, LoadingRows } from "@/components/states/states";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { formatInteger, formatScore } from "@/lib/format";
import { cn } from "@/lib/utils";
import { SELECT } from "./display";
import { rateDetail, rateText } from "./display";
import { DispositionMatrix, Matrix } from "./Metrics";
import { Details, STAGE_TEXT } from "./QualitySections";

type Run = { row: EvaluationComparisonRow; metrics: EvaluationMetrics };
type Block<T> = { ok: true; value: T } | { ok: false; text: string; reason: string };

/** A metric block of one run, or why it has none: not scored, or not in the scoring version the run was scored with. */
function block<T>(metrics: EvaluationMetrics, value: T | Unavailable | undefined): Block<T> {
  if (value === undefined) return { ok: false, text: "Not in this scoring", reason: `Not in ${metrics.schema_version}: rescore the run with the current version to see it` };
  if ((value as Unavailable).available === false) return { ok: false, text: `Not scored: ${(value as Unavailable).reason}`, reason: (value as Unavailable).reason };
  return { ok: true, value: value as T };
}

/** A missing value, worded so it is never read as zero. */
function Missing({ text = "—", reason }: { text?: string; reason: string }) {
  return (
    <span className="text-[12px] italic text-subtle-foreground" title={reason}>
      {text}
      <span className="sr-only">: {reason}</span>
    </span>
  );
}

function name(run: Run) {
  return run.row.label ?? run.row.evaluation_id;
}

/** A difference from the first run, as the comparison table above shows it. */
function delta(value: number | null | undefined, base: number | null | undefined, scale = 1, digits = 3) {
  if (value == null || base == null || Math.abs(value - base) < 1e-9) return null;
  const difference = (value - base) * scale;
  return <span className={cn("block text-[11px]", difference > 0 ? "text-status-pass" : "text-status-flag-text")}>{`${difference > 0 ? "+" : "−"}${Math.abs(difference).toFixed(digits)} vs first`}</span>;
}

type Line = { label: string; hint?: string; cell: (run: Run, first: Run) => ReactNode };

/** Rows of figures, one column per run: the same layout as the comparison table above. */
function SideBySide({ caption, runs, lines, label = "Metric" }: { caption: string; runs: Run[]; lines: Line[]; label?: string }) {
  return (
    <div className="overflow-x-auto rounded-md border border-border">
      <table className="w-full text-[13px]">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr className="border-b border-border">
            <th scope="col" className="px-3 py-2 text-left text-xs font-medium text-muted-foreground">
              {label}
            </th>
            {runs.map((run) => (
              <th key={run.row.evaluation_id} scope="col" className="min-w-40 px-3 py-2 text-left text-xs font-medium">
                {name(run)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {lines.map((line) => (
            <tr key={line.label} className="border-t border-border">
              <th scope="row" className="px-3 py-2 text-left align-top text-xs font-medium text-muted-foreground">
                {line.label}
                {line.hint ? <span className="block font-normal text-subtle-foreground">{line.hint}</span> : null}
              </th>
              {runs.map((run) => (
                <td key={run.row.evaluation_id} className="tabular px-3 py-2 align-top">
                  {line.cell(run, runs[0]!)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** One figure from a block, or why the run has none. */
function from<T>(pick: (metrics: EvaluationMetrics) => T | Unavailable | undefined, show: (value: T, first: T | null) => ReactNode) {
  return (run: Run, first: Run) => {
    const own = block(run.metrics, pick(run.metrics));
    if (!own.ok) return <Missing text={own.text} reason={own.reason} />;
    const base = block(first.metrics, pick(first.metrics));
    return show(own.value, run === first || !base.ok ? null : base.value);
  };
}

function RateFigure({ rate, base }: { rate: Rate; base?: Rate | null }) {
  if (rate.denominator === 0) return <Missing text="Nothing to score" reason="no case of this kind in the run" />;
  return (
    <>
      <span className="font-medium">{rateText(rate)}</span>
      <span className="block text-[11px] text-subtle-foreground">{rateDetail(rate)}</span>
      {delta(rate.value, base?.value, 100, 1)}
    </>
  );
}

function PrfFigure({ prf, base, counts = true }: { prf: Prf; base?: Prf | null; counts?: boolean }) {
  return (
    <>
      <span className="font-medium">{prf.f1 == null ? <Missing reason="no positive case and none predicted" /> : formatScore(prf.f1)}</span>
      <span className="block text-[11px] text-subtle-foreground">
        P {formatScore(prf.precision)} · R {formatScore(prf.recall)}
        {counts ? ` · ${prf.tp}·${prf.fp}·${prf.fn}` : ""}
      </span>
      {delta(prf.f1, base?.f1)}
    </>
  );
}

// --- Classification ---

function ClassificationTab({ runs }: { runs: Run[] }) {
  const pick = (metrics: EvaluationMetrics) => metrics.classification as ClassificationMetrics | Unavailable;
  const categories = [...new Set(runs.flatMap((run) => (run.metrics.classification?.available ? Object.keys((run.metrics.classification as ClassificationMetrics).per_category) : [])))].sort();
  return (
    <div className="space-y-4">
      <SideBySide
        caption="Classification quality by run"
        runs={runs}
        lines={[
          { label: "Category accuracy", hint: "per labelled line", cell: from(pick, (value: ClassificationMetrics, base) => <RateFigure rate={value.accuracy} base={base?.accuracy} />) },
          { label: "Coverage", hint: "lines given a category", cell: from(pick, (value: ClassificationMetrics, base) => <RateFigure rate={value.coverage} base={base?.coverage} />) },
          { label: "Macro F1", hint: "over expected categories", cell: from(pick, (value: ClassificationMetrics, base) => <PrfFigure prf={{ tp: 0, fp: 0, fn: 0, ...value.macro }} base={base ? { tp: 0, fp: 0, fn: 0, ...base.macro } : null} counts={false} />) },
          { label: "Restricted F1", hint: "restricted = true · TP·FP·FN", cell: from(pick, (value: ClassificationMetrics, base) => <PrfFigure prf={value.restricted} base={base?.restricted} />) },
          {
            label: "Restricted TN",
            hint: "and lines with no value",
            cell: from(pick, (value: ClassificationMetrics) =>
              value.restricted_matrix ? `${value.restricted_matrix.tn} · ${value.restricted_matrix.rows_gold_columns_predicted.true.none + value.restricted_matrix.rows_gold_columns_predicted.false.none} none` : <Missing text="Not in this scoring" reason="the restricted matrix is in scoring version 2 onwards" />,
            ),
          },
        ]}
      />
      <section aria-label="Per category" className="space-y-1.5">
        <h4 className="text-xs font-semibold uppercase tracking-wider text-subtle-foreground">Per category · F1, precision, recall, support</h4>
        <SideBySide
          caption="Scores per expected category by run"
          label="Category"
          runs={runs}
          lines={categories.map((category) => ({
            label: category,
            cell: from(pick, (value: ClassificationMetrics, base) => {
              const row = value.per_category[category];
              if (!row) return <Missing text="No such line" reason="this run's answers have no line of this category" />;
              return (
                <>
                  <PrfFigure prf={row} base={base?.per_category[category]} />
                  <span className="block text-[11px] text-subtle-foreground">support {row.support}</span>
                </>
              );
            }),
          }))}
        />
      </section>
      <Details summary="Category confusion matrices">
        <div className="grid gap-4 xl:grid-cols-2">
          {runs.map((run) => {
            const own = block(run.metrics, pick(run.metrics));
            return (
              <div key={run.row.evaluation_id} className="min-w-0 space-y-1">
                <p className="text-xs font-medium">{name(run)}</p>
                {own.ok ? <Matrix caption={`Expected categories by predicted ones: ${name(run)}`} {...own.value.confusion_matrix} /> : <Missing text={own.text} reason={own.reason} />}
              </div>
            );
          })}
        </div>
      </Details>
    </div>
  );
}

// --- Rules ---

type RuleFilter = "all" | "differ" | "errors";

function RuleCell({ row, base }: { row: (Prf & Partial<RuleDetection>) | undefined; base: (Prf & Partial<RuleDetection>) | undefined }) {
  if (!row) return <Missing text="Not in this ruleset" reason="the run's ruleset has no such rule" />;
  if (row.status === "not_tested") return <Missing text="Not tested" reason="no expected finding and none raised: nothing to score, not a perfect score" />;
  return (
    <>
      <PrfFigure prf={row} base={base && base.status !== "not_tested" ? base : null} />
      {row.status === "raised_without_answers" ? <Badge variant="review">raised, never expected</Badge> : null}
      {row.duplicates ? <span className="block text-[11px] text-status-review-text">{row.duplicates} duplicate</span> : null}
    </>
  );
}

function RulesTab({ runs }: { runs: Run[] }) {
  const [filter, setFilter] = useState<RuleFilter>("errors");
  const pick = (metrics: EvaluationMetrics) => metrics.findings as FindingsMetrics | Unavailable;
  const perRule = (run: Run) => (run.metrics.findings?.available ? (run.metrics.findings as FindingsMetrics).per_rule : {});
  const rules = [...new Set(runs.flatMap((run) => Object.keys(perRule(run))))].sort();
  const key = (row: Prf | undefined) => (row ? `${row.tp}·${row.fp}·${row.fn}` : "");
  const shown = rules.filter((rule) => {
    const rows = runs.map((run) => perRule(run)[rule]);
    if (filter === "differ") return new Set(rows.map(key)).size > 1;
    if (filter === "errors") return rows.some((row) => row && (row.fp > 0 || row.fn > 0));
    return true;
  });
  return (
    <div className="space-y-4">
      <SideBySide
        caption="Exception detection by run"
        runs={runs}
        lines={[
          { label: "Micro F1", hint: "every expected finding · TP·FP·FN", cell: from(pick, (value: FindingsMetrics, base) => <PrfFigure prf={value.micro} base={base?.micro} />) },
          { label: "Micro F1 without EXC-03", hint: "uncertainty signals left out", cell: from(pick, (value: FindingsMetrics, base) => <PrfFigure prf={value.micro_without_uncertainty_signals} base={base?.micro_without_uncertainty_signals} />) },
          {
            label: "Macro F1",
            hint: "over tested rules only",
            cell: from(pick, (value: FindingsMetrics, base) =>
              value.macro ? (
                <>
                  <PrfFigure prf={{ tp: 0, fp: 0, fn: 0, ...value.macro }} base={base?.macro ? { tp: 0, fp: 0, fn: 0, ...base.macro } : null} counts={false} />
                  <span className="block text-[11px] text-subtle-foreground">{value.macro.rules} tested rules</span>
                </>
              ) : (
                <Missing text="Not in this scoring" reason="macro detection is in scoring version 2 onwards" />
              ),
            ),
          },
          {
            label: "Rules",
            hint: "tested · raised without answers · not tested",
            cell: from(pick, (value: FindingsMetrics) =>
              value.rules_by_status ? `${value.rules_by_status.tested.length} · ${value.rules_by_status.raised_without_answers.length} · ${value.rules_by_status.not_tested.length}` : <Missing text="Not in this scoring" reason="rule status is in scoring version 2 onwards" />,
            ),
          },
        ]}
      />
      <section aria-label="Per rule" className="space-y-1.5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h4 className="text-xs font-semibold uppercase tracking-wider text-subtle-foreground">
            Per rule · F1, precision, recall, TP·FP·FN · {shown.length} of {rules.length}
          </h4>
          <select aria-label="Rules shown" className={cn(SELECT, "w-full sm:w-72")} value={filter} onChange={(event) => setFilter(event.target.value as RuleFilter)}>
            <option value="errors">Rules with a miss or a false finding</option>
            <option value="differ">Rules scored differently</option>
            <option value="all">Every rule</option>
          </select>
        </div>
        {shown.length ? (
          <SideBySide caption="Scores per rule by run" label="Rule" runs={runs} lines={shown.map((rule) => ({ label: rule, cell: (run, first) => (run.metrics.findings?.available ? <RuleCell row={perRule(run)[rule]} base={run === first ? undefined : perRule(first)[rule]} /> : <Missing text={`Not scored: ${(run.metrics.findings as Unavailable | undefined)?.reason ?? "no detection metrics"}`} reason={(run.metrics.findings as Unavailable | undefined)?.reason ?? "no detection metrics"} />) }))} />
        ) : (
          <p className="text-[13px] text-muted-foreground">{filter === "errors" ? "No rule has a miss or a false finding in these runs." : "Every rule is scored the same in these runs."}</p>
        )}
      </section>
      <Details summary="Missed and unexpected findings">
        <div className="grid gap-4 xl:grid-cols-2">
          {runs.map((run) => {
            const own = block(run.metrics, pick(run.metrics));
            return (
              <div key={run.row.evaluation_id} className="min-w-0 space-y-1 text-[13px]">
                <p className="text-xs font-medium">{name(run)}</p>
                {!own.ok ? (
                  <Missing text={own.text} reason={own.reason} />
                ) : own.value.missing === undefined ? (
                  <Missing text="Not in this scoring" reason="missed and unexpected findings are in scoring version 2 onwards" />
                ) : (
                  <ul className="space-y-0.5" aria-label={`Missed and unexpected findings: ${name(run)}`}>
                    {own.value.missing.map((item) => (
                      <li key={`m-${item.receipt_id}-${item.rule_id}`}>
                        <span className="font-mono text-xs">{item.receipt_id}</span> missed <span className="font-mono text-xs">{item.rule_id}</span> <span className="text-xs text-muted-foreground">({item.reason.replace(/_/g, " ")})</span>
                      </li>
                    ))}
                    {(own.value.unexpected ?? []).map((item, index) => (
                      <li key={`u-${item.receipt_id}-${item.rule_id}-${index}`}>
                        <span className="font-mono text-xs">{item.receipt_id}</span> raised <span className="font-mono text-xs">{item.rule_id}</span> <span className="text-xs text-muted-foreground">(not expected{item.duplicate ? ", duplicate" : ""})</span>
                      </li>
                    ))}
                    {!own.value.missing.length && !(own.value.unexpected ?? []).length ? <li className="text-muted-foreground">None: every expected finding raised, nothing else.</li> : null}
                  </ul>
                )}
              </div>
            );
          })}
        </div>
      </Details>
    </div>
  );
}

// --- Dispositions ---

function DispositionsTab({ runs }: { runs: Run[] }) {
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      {runs.map((run) => {
        const own = block(run.metrics, run.metrics.disposition as DispositionMetrics | Unavailable);
        return (
          <section key={run.row.evaluation_id} aria-label={`Dispositions: ${name(run)}`} className="min-w-0 space-y-2 rounded-md border border-border p-3">
            <p className="flex flex-wrap items-baseline justify-between gap-2 text-[13px]">
              <span className="font-medium">{name(run)}</span>
              {own.ok ? <span className="text-xs text-muted-foreground">accuracy {rateText(own.value.accuracy)} · {rateDetail(own.value.accuracy)}</span> : null}
            </p>
            {own.ok ? <DispositionMatrix disposition={own.value} /> : <Missing text={own.text} reason={own.reason} />}
          </section>
        );
      })}
    </div>
  );
}

// --- Baseline ---

function LineList({ lines, empty }: { lines: BaselineLine[]; empty: string }) {
  if (!lines.length) return <p className="text-xs text-muted-foreground">{empty}</p>;
  return (
    <ul className="space-y-1 text-[13px]">
      {lines.map((line) => (
        <li key={line.line_id}>
          <span className="font-mono text-xs">{line.line_id}</span> “{line.description}” <span className="text-xs text-muted-foreground">is {line.gold_category}{line.gold_restricted ? " (restricted)" : ""}; model {line.main.category ?? "no category"}; keywords {line.baseline.matched ? `matched “${line.baseline.matched}”` : "matched nothing"}</span>
        </li>
      ))}
    </ul>
  );
}

function BaselineTab({ runs }: { runs: Run[] }) {
  const pick = (metrics: EvaluationMetrics) => metrics.baseline as BaselineMetrics | Unavailable | undefined;
  return (
    <div className="space-y-4">
      <SideBySide
        caption="The model against the keyword baseline by run"
        runs={runs}
        lines={[
          { label: "Model: restricted F1", hint: "TP·FP·FN", cell: from(pick, (value: BaselineMetrics, base) => <PrfFigure prf={value.restricted.main} base={base?.restricted.main} />) },
          { label: "Keywords: restricted F1", hint: "the same lines", cell: from(pick, (value: BaselineMetrics, base) => <PrfFigure prf={value.restricted.baseline} base={base?.restricted.baseline} />) },
          { label: "Model − keywords", hint: "F1 difference", cell: from(pick, (value: BaselineMetrics) => (value.difference.f1 == null ? <Missing reason="one side has no F1" /> : <span className={cn("font-medium", value.difference.f1 >= 0 ? "text-status-pass" : "text-status-flag-text")}>{`${value.difference.f1 >= 0 ? "+" : "−"}${Math.abs(value.difference.f1).toFixed(3)}`}</span>)) },
          { label: "Model wins", hint: "right where keywords are wrong", cell: from(pick, (value: BaselineMetrics) => formatInteger(value.lines_by_outcome.main_only)) },
          { label: "Model losses", hint: "wrong where keywords are right", cell: from(pick, (value: BaselineMetrics) => formatInteger(value.lines_by_outcome.baseline_only)) },
          { label: "Both right · both wrong", cell: from(pick, (value: BaselineMetrics) => `${value.lines_by_outcome.both_right} · ${value.lines_by_outcome.both_wrong}`) },
        ]}
      />
      {runs.map((run) => {
        const own = block(run.metrics, pick(run.metrics));
        if (!own.ok) return null;
        return (
          <Details key={run.row.evaluation_id} summary={`${name(run)}: ${own.value.lines_by_outcome.main_only} ${own.value.lines_by_outcome.main_only === 1 ? "win" : "wins"}, ${own.value.lines_by_outcome.baseline_only} ${own.value.lines_by_outcome.baseline_only === 1 ? "loss" : "losses"}`}>
            <div className="grid gap-3 lg:grid-cols-2">
              <div className="min-w-0 space-y-1">
                <p className="text-xs font-medium text-status-pass">Model wins</p>
                <LineList lines={own.value.outcomes.main_only} empty="No line the keywords got wrong and the model right." />
              </div>
              <div className="min-w-0 space-y-1">
                <p className="text-xs font-medium text-status-flag-text">Model losses</p>
                <LineList lines={own.value.outcomes.baseline_only} empty="No line the keywords got right and the model wrong." />
              </div>
            </div>
          </Details>
        );
      })}
    </div>
  );
}

// --- Evidence ---

function EvidenceTab({ runs }: { runs: Run[] }) {
  const pick = (metrics: EvaluationMetrics) => metrics.evidence as EvidenceMetrics | Unavailable | undefined;
  return (
    <div className="space-y-4">
      <SideBySide
        caption="Evidence correctness by run"
        runs={runs}
        lines={[
          { label: "Exact line items", hint: "cited lines = expected lines", cell: from(pick, (value: EvidenceMetrics, base) => <RateFigure rate={value.exact} base={base?.exact} />) },
          { label: "Line precision", hint: "cited lines that are expected", cell: from(pick, (value: EvidenceMetrics, base) => <RateFigure rate={value.line_precision} base={base?.line_precision} />) },
          { label: "Line recall", hint: "expected lines that are cited", cell: from(pick, (value: EvidenceMetrics, base) => <RateFigure rate={value.line_recall} base={base?.line_recall} />) },
          { label: "Severity", hint: "as expected", cell: from(pick, (value: EvidenceMetrics, base) => <RateFigure rate={value.severity_exact} base={base?.severity_exact} />) },
          { label: "Matched exceptions", hint: "scorable · not scorable", cell: from(pick, (value: EvidenceMetrics) => `${value.matched_exceptions} · ${value.scorable} · ${value.not_scorable}`) },
        ]}
      />
      <Details summary="Line-item mismatches">
        <div className="grid gap-4 xl:grid-cols-2">
          {runs.map((run) => {
            const own = block(run.metrics, pick(run.metrics));
            return (
              <div key={run.row.evaluation_id} className="min-w-0 space-y-1 text-[13px]">
                <p className="text-xs font-medium">{name(run)}</p>
                {!own.ok ? (
                  <Missing text={own.text} reason={own.reason} />
                ) : own.value.mismatches.length ? (
                  <ul className="space-y-0.5">
                    {own.value.mismatches.map((item) => (
                      <li key={`${item.receipt_id}-${item.rule_id}`}>
                        <span className="font-mono text-xs">
                          {item.receipt_id} · {item.rule_id}
                        </span>{" "}
                        <span className="text-xs text-muted-foreground">
                          {item.missing_line_ids.length ? `missing ${item.missing_line_ids.join(", ")}` : ""}
                          {item.missing_line_ids.length && item.extra_line_ids.length ? "; " : ""}
                          {item.extra_line_ids.length ? `extra ${item.extra_line_ids.join(", ")}` : ""}
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-muted-foreground">Every matched exception cites exactly the expected lines.</p>
                )}
              </div>
            );
          })}
        </div>
      </Details>
    </div>
  );
}

// --- Errors ---

function ErrorsTab({ runs }: { runs: Run[] }) {
  const pick = (metrics: EvaluationMetrics) => metrics.error_analysis as ErrorAnalysis | Unavailable | undefined;
  const stages = ["translation", "classification", "retrieval", "rule_logic"] as const;
  const failures = runs.flatMap((run) => {
    const own = block(run.metrics, pick(run.metrics));
    return own.ok ? own.value.failures.map((failure) => ({ run, failure })) : [];
  });
  return (
    <div className="space-y-4">
      <SideBySide
        caption="Diagnosed failures by run"
        runs={runs}
        lines={[
          { label: "Observed failures", hint: "root causes", cell: from(pick, (value: ErrorAnalysis) => <span className="font-medium">{formatInteger(value.observed)}</span>) },
          ...stages.map((stage) => ({
            label: STAGE_TEXT[stage] ?? stage,
            cell: from(pick, (value: ErrorAnalysis) => (stage === "retrieval" && value.by_stage.retrieval === 0 ? <Missing text="Not attributed" reason={value.retrieval} /> : formatInteger(value.by_stage[stage]))),
          })),
        ]}
      />
      {failures.length ? (
        <ol className="space-y-2" aria-label="Diagnosed failures">
          {failures.map(({ run, failure }) => (
            <li key={`${run.row.evaluation_id}-${failure.id}`} className="rounded-md border border-border px-3 py-2 text-[13px]">
              <p className="flex flex-wrap items-center gap-2">
                <span className="text-xs font-medium">{name(run)}</span>
                <span className="font-mono text-xs">{failure.id}</span>
                <Badge variant="info">{STAGE_TEXT[failure.stage] ?? failure.stage}</Badge>
                <span className="font-mono text-xs">
                  {failure.receipt_id}
                  {failure.line_ids.length ? ` · ${failure.line_ids.join(", ")}` : ""}
                  {failure.rule_id ? ` · ${failure.rule_id}` : ""}
                </span>
              </p>
              <p className="mt-1">{failure.description}</p>
              {failure.consequences.length ? <p className="text-xs text-muted-foreground">Led to: {failure.consequences.join("; ")}.</p> : null}
              <p className="text-xs">
                <span className="font-medium">Improve:</span> {failure.recommendation}
              </p>
              <p className="text-[11px] text-subtle-foreground">Placed by {failure.placed_by}.</p>
            </li>
          ))}
        </ol>
      ) : (
        <p className="text-[13px] text-muted-foreground">No diagnosed failure in these runs{runs.some((run) => !block(run.metrics, pick(run.metrics)).ok) ? ", among those with an error analysis" : ""}.</p>
      )}
    </div>
  );
}

const SECTIONS = [
  ["classification", "Classification", ClassificationTab],
  ["rules", "Rules", RulesTab],
  ["dispositions", "Dispositions", DispositionsTab],
  ["baseline", "Keyword baseline", BaselineTab],
  ["evidence", "Evidence", EvidenceTab],
  ["errors", "Error analysis", ErrorsTab],
] as const;

/** The quality metrics behind every run compared, read from each run's stored metrics. */
export function CompareReport({ rows }: { rows: EvaluationComparisonRow[] }) {
  const results = useEvaluationsMetrics(rows.map((row) => row.evaluation_id));
  const failed = results.find((result) => result.isError);
  let body: ReactNode;
  if (results.some((result) => result.isPending)) body = <LoadingRows rows={4} label="Loading each run's metrics" />;
  else if (failed) body = <ErrorState error={failed.error} onRetry={() => results.forEach((result) => result.isError && void result.refetch())} />;
  else {
    const runs: Run[] = rows.map((row, index) => ({ row, metrics: results[index]!.data! }));
    const versions = [...new Set(runs.map((run) => run.metrics.schema_version))];
    const provisional = runs.filter((run) => run.metrics.provisional);
    body = (
      <div className="space-y-6">
        {provisional.length || versions.length > 1 ? (
          <div className="space-y-1">
            {provisional.length ? <p role="status" className="text-xs text-status-review-text">Provisional metrics, from while {provisional.map(name).join(", ")} ran: reading the final ones.</p> : null}
            {versions.length > 1 ? <p className="text-xs text-status-review-text">These runs were scored with different versions ({versions.join(", ")}): a block one version lacks says so, and is never zero.</p> : null}
          </div>
        ) : null}
        {/* Every section in sequence, each under its own heading: nothing is hidden behind a tab, so the page and its PDF hold the whole report. */}
        {SECTIONS.map(([value, label, Section], index) => (
          <section key={value} id={`report-${value}`} aria-label={label} className={cn("space-y-3", index > 0 && "border-t border-border pt-5")}>
            <h3 className="text-sm font-semibold text-foreground">
              {index + 1} · {label}
            </h3>
            <Section runs={runs} />
          </section>
        ))}
      </div>
    );
  }
  return (
    <Card>
      <CardContent className="space-y-1 p-4">
        <h2 className="text-sm font-semibold">Evaluation report</h2>
        <p className="pb-2 text-xs text-subtle-foreground">Each run's stored metrics, side by side, against the first run. “Not scored” (with its reason) and “Not tested” mean there was nothing to score, never zero.</p>
        {body}
      </CardContent>
    </Card>
  );
}
