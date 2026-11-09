/**
 * Part 3's quality sections of one evaluation, from its stored metrics: exception detection per rule, evidence
 * correctness, the keyword baseline, rule coverage and error analysis. Each shows what the API stored, with counts
 * beside every rate, and says plainly when a block was not scored or is not in this run's scoring version.
 */
import type { ReactNode } from "react";
import type { BaselineMetrics, CoverageMetrics, ErrorAnalysis, EvidenceMetrics, FindingsMetrics, Prf, RuleDetection, Unavailable } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatInteger, formatScore, humanize } from "@/lib/format";
import { cn } from "@/lib/utils";
import { RateStat, Stat } from "./display";

export function Details({ summary, children, open }: { summary: ReactNode; children: ReactNode; open?: boolean }) {
  return (
    <details open={open}>
      <summary className="cursor-pointer text-[13px] font-medium text-primary">{summary}</summary>
      <div className="mt-2">{children}</div>
    </details>
  );
}

export function NotInVersion({ what, version }: { what: string; version: string }) {
  return <p className="text-[13px] text-muted-foreground">{what} is not in this run's scoring ({version}). Rescore it with the current version to see it.</p>;
}

export function Unscored({ block }: { block: Unavailable }) {
  return <p className="text-[13px] text-muted-foreground">Not scored: {block.reason}.</p>;
}

function SimpleTable({ caption, head, rows }: { caption: string; head: string[]; rows: ReactNode[][] }) {
  return (
    <div className="max-h-96 overflow-auto rounded-md border border-border">
      <Table>
        <caption className="sr-only">{caption}</caption>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            {head.map((name, index) => (
              <TableHead key={name} className={index ? "text-right" : undefined}>
                {name}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row, index) => (
            <TableRow key={index}>
              {row.map((cell, column) => (
                <TableCell key={column} className={cn("text-[13px]", column ? "tabular text-right" : "font-mono text-xs")}>
                  {cell}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

const lines = (ids: string[] | null | undefined) => (ids == null ? "not stated" : ids.length ? ids.join(", ") : "receipt-level");
const prfText = (scores: Pick<Prf, "precision" | "recall" | "f1">) => `P ${formatScore(scores.precision)} · R ${formatScore(scores.recall)}`;

const STATUS: Record<RuleDetection["status"], { label: string; variant: "pass" | "review" | "neutral" }> = {
  tested: { label: "Tested", variant: "pass" },
  raised_without_answers: { label: "No positive case, raised", variant: "review" },
  not_tested: { label: "No positive case", variant: "neutral" },
};

export function DetectionSection({ findings }: { findings: FindingsMetrics }) {
  if (!findings.macro) return null;  // scored before version 2: the caller says so
  const rows = Object.entries(findings.per_rule).filter(([, row]) => row.status !== "not_tested");
  const untested = findings.rules_by_status?.not_tested ?? [];
  return (
    <div className="space-y-3">
      <dl className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Finding F1 (micro)" value={formatScore(findings.micro.f1)} hint={`${prfText(findings.micro)} · ${findings.micro.tp} matched, ${findings.micro.fp} unexpected, ${findings.micro.fn} missed`} />
        <Stat label="Without uncertainty signals" value={formatScore(findings.micro_without_uncertainty_signals.f1)} hint={`${prfText(findings.micro_without_uncertainty_signals)} · EXC-03 left out`} />
        <Stat label={`Macro F1 over ${findings.macro.rules} tested rules`} value={formatScore(findings.macro.f1)} hint={prfText(findings.macro)} />
        <Stat label="Findings" value={`${formatInteger(findings.predicted ?? 0)} raised / ${formatInteger(findings.expected ?? 0)} expected`} hint={`${findings.evaluated}/${findings.receipts} receipts evaluated`} />
      </dl>
      <p className="text-xs text-muted-foreground">
        A finding matches an expected one on receipt and rule. A right flag never stands in for a missed rule. A rule with no expected finding is not tested: its scores are undefined, never perfect.
      </p>
      <Details summary={`Per rule (${rows.length} raised or expected)`} open>
        <SimpleTable
          caption="Exception detection per rule"
          head={["Rule", "Status", "Expected", "Raised", "TP · FP · FN", "Precision", "Recall", "F1", "Exact evidence"]}
          rows={rows.map(([rule, row]) => [
            rule,
            <Badge key="status" variant={STATUS[row.status ?? "tested"].variant}>{STATUS[row.status ?? "tested"].label}</Badge>,
            row.expected,
            row.predicted,
            `${row.tp} · ${row.fp} · ${row.fn}${row.duplicates ? ` (${row.duplicates} dup.)` : ""}`,
            formatScore(row.precision),
            formatScore(row.recall),
            formatScore(row.f1),
            row.evidence_exact?.denominator ? `${row.evidence_exact.numerator}/${row.evidence_exact.denominator}` : "—",
          ])}
        />
        {untested.length ? <p className="mt-1 text-xs text-muted-foreground">No positive case in the answers, and never raised ({untested.length}): {untested.join(", ")}.</p> : null}
      </Details>
      <Details summary={`Missed findings (${findings.missing?.length ?? 0}) and unexpected ones (${findings.unexpected?.length ?? 0})`}>
        <div className="grid gap-3 lg:grid-cols-2">
          <SimpleTable caption="Missed findings" head={["Receipt", "Rule", "Expected lines", "Why"]} rows={(findings.missing ?? []).map((item) => [item.receipt_id, item.rule_id, lines(item.expected_line_ids), humanize(item.reason)])} />
          <SimpleTable caption="Unexpected findings" head={["Receipt", "Rule", "Lines", "Duplicate"]} rows={(findings.unexpected ?? []).map((item) => [item.receipt_id, item.rule_id, lines(item.line_ids), item.duplicate ? "yes" : "no"])} />
        </div>
        {Object.keys(findings.attribution_counts ?? {}).length ? (
          <p className="mt-1 text-xs text-muted-foreground">
            The right lines under the wrong rule: {Object.entries(findings.attribution_counts ?? {}).map(([pair, count]) => `${pair} (${count})`).join("; ")}.
          </p>
        ) : null}
      </Details>
    </div>
  );
}

export function EvidenceSection({ evidence }: { evidence: EvidenceMetrics }) {
  return (
    <div className="space-y-3">
      <dl className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <RateStat label="Exact evidence" rate={evidence.exact} hint="Cited lines equal the expected ones, as sets" />
        <RateStat label="Line precision" rate={evidence.line_precision} hint="Cited lines that were expected" />
        <RateStat label="Line recall" rate={evidence.line_recall} hint="Expected lines that were cited" />
        <Stat label="Matched exceptions" value={formatInteger(evidence.matched_exceptions)} hint={`${evidence.scorable} scorable · ${evidence.not_scorable} without expected lines, not scored`} />
      </dl>
      <p className="text-xs text-muted-foreground">Only exceptions that match an expected one are scored here, apart from detection and disposition. Review signals cite no lines.</p>
      {evidence.mismatches.length ? (
        <SimpleTable
          caption="Evidence mismatches"
          head={["Receipt · rule", "Expected", "Cited", "Missing", "Extra"]}
          rows={evidence.mismatches.map((item) => [`${item.receipt_id} · ${item.rule_id}`, lines(item.expected_line_ids), lines(item.cited_line_ids), item.missing_line_ids.join(", ") || "—", item.extra_line_ids.join(", ") || "—"])}
        />
      ) : (
        <p className="text-[13px] text-muted-foreground">Every scorable matched exception cites exactly the expected lines.</p>
      )}
    </div>
  );
}

const OUTCOMES: { key: keyof BaselineMetrics["outcomes"]; label: string }[] = [
  { key: "main_only", label: "The engine is right, the baseline wrong" },
  { key: "baseline_only", label: "The baseline is right, the engine wrong" },
  { key: "both_wrong", label: "Both wrong" },
];

export function BaselineSection({ baseline }: { baseline: BaselineMetrics }) {
  const { main, baseline: keyword } = baseline.restricted;
  const signed = (value: number | null) => (value == null ? "—" : `${value > 0 ? "+" : ""}${formatScore(value)}`);
  const categories = baseline.restricted_categories;
  return (
    <div className="space-y-3">
      <SimpleTable
        caption="The engine against the keyword baseline on the restricted decision"
        head={["Restricted decision", "Engine", "Keyword baseline", "Difference"]}
        rows={[
          ["Precision", formatScore(main.precision), formatScore(keyword.precision), signed(baseline.difference.precision)],
          ["Recall", formatScore(main.recall), formatScore(keyword.recall), signed(baseline.difference.recall)],
          ["F1", formatScore(main.f1), formatScore(keyword.f1), signed(baseline.difference.f1)],
          ["True positives", main.tp, keyword.tp, main.tp - keyword.tp],
          ["False positives", main.fp, keyword.fp, main.fp - keyword.fp],
          ["False negatives", main.fn, keyword.fn, main.fn - keyword.fn],
          ...(categories.main && categories.baseline
            ? [[`Category accuracy on ${categories.main.lines} restricted-category lines`, formatScore(categories.main.accuracy.value), formatScore(categories.baseline.accuracy.value), signed(categories.main.accuracy.value != null && categories.baseline.accuracy.value != null ? Math.round((categories.main.accuracy.value - categories.baseline.accuracy.value) * 1e6) / 1e6 : null)]]
            : []),
        ]}
      />
      <p className="text-xs text-muted-foreground">
        {baseline.version}: whole-word terms from the taxonomy and policy text, over each line's raw description, with no translation or model. Same {formatInteger(baseline.lines)} lines and the same scoring as the engine.{" "}
        {Object.values(baseline.not_comparable).map((reason) => `Not comparable: ${reason}.`).join(" ")}
      </p>
      <p className="text-[13px]">
        Of {formatInteger(baseline.lines)} lines: both right on {baseline.lines_by_outcome.both_right}; the engine alone on {baseline.lines_by_outcome.main_only}; the baseline alone on {baseline.lines_by_outcome.baseline_only}; both wrong on {baseline.lines_by_outcome.both_wrong}.
      </p>
      {OUTCOMES.map(({ key, label }) => (
        <Details key={key} summary={`${label} (${baseline.outcomes[key].length})`}>
          {baseline.outcomes[key].length ? (
            <SimpleTable
              caption={label}
              head={["Line", "Description", "Expected", "Engine", "Baseline"]}
              rows={baseline.outcomes[key].map((item) => [
                item.line_id,
                <span key="text" className="font-sans">{item.description}</span>,
                `${item.gold_category}${item.gold_restricted ? " · restricted" : ""}`,
                `${item.main.category ?? "none"}${item.main.restricted ? " · restricted" : ""}`,
                item.baseline.matched ? `“${item.baseline.matched}” → ${item.baseline.category}` : "no term",
              ])}
            />
          ) : (
            <p className="text-[13px] text-muted-foreground">None.</p>
          )}
        </Details>
      ))}
    </div>
  );
}

const EXECUTION: Record<string, string> = {
  evaluated: "Met its subject",
  gate_only: "Gate held, no subject",
  not_evaluated: "Gate never held",
  never_runs: "Never runs",
  every_receipt: "Every receipt",
};

export function CoverageSection({ coverage }: { coverage: CoverageMetrics }) {
  const gaps = coverage.without_positive_cases;
  return (
    <div className="space-y-3">
      <dl className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Executable rules" value={formatInteger(coverage.executable_rules)} hint={`of ${coverage.rules} in the ruleset`} />
        <Stat label="Execution coverage" value={`${coverage.exercised.length}/${coverage.executable_rules}`} hint="Met a receipt holding what they examine" />
        <Stat label="Positive-case coverage" value={`${coverage.with_positive_cases.length}/${coverage.executable_rules}`} hint="The answers expect them at least once" />
        <Stat label="Rules left undecided" value={formatInteger(coverage.with_undecided.length)} hint="On some receipt, an open question" />
      </dl>
      <p className="text-xs text-muted-foreground">
        Measured on the {coverage.receipts} evaluated receipts with the engine's own facts, never read from the ruleset: running on a receipt is not the same as meeting a case the rule should raise.
        {gaps.length ? ` No positive case in the dataset for: ${gaps.join(", ")}.` : ""}
      </p>
      <Details summary="Per rule">
        <SimpleTable
          caption="Rule coverage"
          head={["Rule", "Execution", "Gate held on", "Subject on", "Expected", "Raised", "Undecided on"]}
          rows={Object.entries(coverage.per_rule).map(([rule, row]) => [
            rule,
            <span key="execution" title={row.note ?? undefined} className={cn("font-sans", row.execution === "evaluated" || row.execution === "every_receipt" ? "" : "text-status-review-text")}>
              {EXECUTION[row.execution] ?? row.execution}
            </span>,
            row.gate_receipts,
            row.subject_receipts,
            row.expected,
            row.predicted,
            row.undecided_receipts,
          ])}
        />
      </Details>
    </div>
  );
}

export const STAGE_TEXT: Record<string, string> = { translation: "Translation", classification: "Classification", retrieval: "Retrieval", rule_logic: "Rule logic" };

export function ErrorAnalysisSection({ analysis }: { analysis: ErrorAnalysis }) {
  return (
    <div className="space-y-3">
      <dl className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label="Observed failures" value={formatInteger(analysis.observed)} hint="Root causes; their effects are listed under them" />
        {Object.entries(analysis.by_stage).map(([stage, count]) => (
          <Stat key={stage} label={STAGE_TEXT[stage] ?? stage} value={formatInteger(count)} />
        ))}
      </dl>
      <p className="text-xs text-muted-foreground">
        Measured on this run only: no failure is invented. Each is placed by a fixed rule, which it names, to confirm by hand. Retrieval: {analysis.retrieval}.
      </p>
      {analysis.failures.length ? (
        <ol className="space-y-2" aria-label="Diagnosed failures">
          {analysis.failures.map((failure) => (
            <li key={failure.id} className="rounded-md border border-border px-3 py-2 text-[13px]">
              <p className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-xs">{failure.id}</span>
                <Badge variant="info">{STAGE_TEXT[failure.stage] ?? failure.stage}</Badge>
                <span className="font-mono text-xs">{failure.receipt_id}{failure.line_ids.length ? ` · ${failure.line_ids.join(", ")}` : ""}{failure.rule_id ? ` · ${failure.rule_id}` : ""}</span>
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
        <p className="text-[13px] text-muted-foreground">No observed failure in this run.</p>
      )}
    </div>
  );
}
