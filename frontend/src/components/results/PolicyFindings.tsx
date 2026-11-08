import { ChevronRight, FileText, Search } from "lucide-react";
import type { ReactNode } from "react";
import type { ReceiptResult } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { OPEN_QUESTION_ICON, SeverityBadge, severityStyle } from "./style";

export function findingAnchor(receiptId: string, key: string) {
  return `finding-${receiptId}-${key}`.replace(/[^A-Za-z0-9_-]/g, "_");
}

function LineRefs({ lineIds, onSelectLine, wholeReceipt = "Whole receipt" }: { lineIds: string[]; onSelectLine: (lineId: string) => void; wholeReceipt?: string }) {
  if (!lineIds.length) return <span className="text-xs text-subtle-foreground">{wholeReceipt}</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {lineIds.map((lineId) => (
        <button
          key={lineId}
          type="button"
          onClick={() => onSelectLine(lineId)}
          className="rounded border border-border bg-card px-1.5 py-0.5 font-mono text-[11px] text-primary hover:border-primary hover:bg-primary-soft focus-visible:outline-2 focus-visible:outline-ring"
          aria-label={`Show line ${lineId}`}
        >
          {lineId}
        </button>
      ))}
    </span>
  );
}

function Section({ title, count, tone, note, children }: { title: string; count: number; tone: string; note?: ReactNode; children: ReactNode }) {
  return (
    <details open={count > 0} className="group rounded-lg border border-border bg-card">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-2.5 text-[13px] font-semibold text-foreground [&::-webkit-details-marker]:hidden">
        <ChevronRight className="size-4 text-subtle-foreground transition-transform group-open:rotate-90" aria-hidden />
        <span aria-hidden className={cn("size-2 rounded-full", tone)} />
        {title}
        <span className="tabular ml-auto rounded-full bg-muted px-2 text-xs font-medium text-muted-foreground">{count}</span>
      </summary>
      <div className="border-t border-border px-4 py-3">
        {note ? <p className="mb-2.5 text-xs leading-4 text-subtle-foreground">{note}</p> : null}
        {count === 0 ? <p className="text-xs text-subtle-foreground">None.</p> : children}
      </div>
    </details>
  );
}

/** The core's ReasonCode values (engine/evaluation.py), in words. */
const REASON_COPY: Record<string, string> = {
  exception_flags: "Exceptions of a severity the ruleset flags",
  inevitable_violation: "Every reading of an unresolved field would flag it",
  exception_reviews: "Exceptions of a severity the ruleset sends to review",
  review_signal: "Review signals",
  uncertainty: "Classification uncertainty",
  incomplete_evidence: "Not every rule could be decided",
  lines_do_not_account_for_total: "The line items do not add up to the receipt total",
  unresolved_exception: "A breach was found, but whether another rule replaces or excuses it is not known",
  unverified_amount: "An amount could not be verified",
  nothing_found: "Nothing found against it",
};

/**
 * The exception's tone, from the receipt's own disposition reasons: the ruleset decides which severities flag
 * and which send to review (EXC-01), so that is read, not assumed. Severity text is a fallback only.
 */
export function exceptionTone(result: ReceiptResult, ruleId: string, severity: string | null): "flag" | "review" | "neutral" {
  const inReason = (code: string) => result.disposition_reasons.some((reason) => reason.code === code && reason.rule_ids.includes(ruleId));
  if (inReason("exception_flags")) return "flag";
  if (inReason("exception_reviews")) return "review";
  const fallback = severityStyle(severity).variant;
  return fallback === "flag" || fallback === "review" ? fallback : "neutral";
}

export function PolicyFindings({
  result,
  focused,
  onSelectLine,
}: {
  result: ReceiptResult;
  focused: string | null;
  onSelectLine: (lineId: string) => void;
}) {
  const id = (key: string) => findingAnchor(result.receipt_id, key);
  const ring = (key: string) => (focused === key ? "ring-2 ring-primary/40" : "");

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-border bg-muted/40 px-4 py-3">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-subtle-foreground">Why this disposition</p>
        {result.disposition_reasons.length ? (
          <ul className="mt-1.5 space-y-1 text-[13px] leading-5">
            {result.disposition_reasons.map((reason, index) => (
              <li key={index} className="flex flex-wrap items-center gap-1.5">
                <span className="text-foreground">{REASON_COPY[reason.code] ?? reason.code.replace(/_/g, " ")}</span>
                {reason.rule_ids.map((rule) => (
                  <Badge key={rule} variant="outline" className="font-mono">
                    {rule}
                  </Badge>
                ))}
                <span className="text-xs text-subtle-foreground">
                  · basis {reason.basis}
                  {reason.basis === "project_decision" ? " (a project decision, not EXC-01)" : ""}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-[13px] text-muted-foreground">No reasons recorded: nothing active stands against this receipt.</p>
        )}
      </div>

      <Section
        title="Active exceptions"
        count={result.exceptions.length}
        tone="bg-status-flag"
        note="Only active exceptions are returned. Replaced, waived and excused findings are not part of this result."
      >
        <ul className="space-y-2.5">
          {result.exceptions.map((exception, index) => {
            const key = `exception-${index}`;
            const tone = exceptionTone(result, exception.rule_id, exception.severity);
            return (
              <li
                key={key}
                id={id(key)}
                tabIndex={-1}
                className={cn(
                  "scroll-mt-24 rounded-md border-l-4 bg-background/60 px-3 py-2.5 outline-none",
                  tone === "flag" ? "border-l-status-flag" : tone === "review" ? "border-l-status-review" : "border-l-status-neutral",
                  ring(key),
                )}
              >
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="font-mono text-[13px] font-semibold text-foreground">{exception.rule_id}</span>
                  <SeverityBadge severity={exception.severity} tone={tone} />
                  <span className="text-xs text-subtle-foreground">{tone === "flag" ? "· flags the receipt" : tone === "review" ? "· sends to review" : ""}</span>
                  <span className="flex items-center gap-1 text-xs text-subtle-foreground">
                    <FileText className="size-3" aria-hidden />
                    {exception.policy_doc}
                  </span>
                </div>
                <p className="mt-1.5 break-words text-[13px] leading-5 text-foreground">{exception.explanation}</p>
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs">
                  <span className="text-subtle-foreground">Lines</span>
                  <LineRefs lineIds={exception.line_ids} onSelectLine={onSelectLine} />
                </div>
                <details className="mt-2 text-xs">
                  <summary className="cursor-pointer font-medium text-primary">Policy evidence</summary>
                  <blockquote className="mt-1.5 whitespace-pre-wrap break-words border-l-2 border-border pl-3 leading-5 text-foreground/85">{exception.evidence}</blockquote>
                  <p className="mt-1.5 text-subtle-foreground">
                    Signed quote: <span className="whitespace-pre-wrap break-words font-mono text-foreground/85">{exception.source_quote}</span>
                  </p>
                </details>
              </li>
            );
          })}
        </ul>
      </Section>

      <Section title="Review signals" count={result.review_signals.length} tone="bg-status-review" note="Signals ask for a reviewer's look. They are not exceptions.">
        <ul className="space-y-2">
          {result.review_signals.map((signal, index) => {
            const key = `signal-${index}`;
            return (
              <li key={key} id={id(key)} tabIndex={-1} className={cn("scroll-mt-24 rounded-md border-l-4 border-l-status-review bg-background/60 px-3 py-2 outline-none", ring(key))}>
                <div className="flex items-center gap-1.5">
                  <Search className="size-3.5 text-status-review-text" aria-hidden />
                  <span className="font-mono text-[13px] font-semibold text-foreground">{signal.rule_id}</span>
                  <Badge variant="review">Review signal</Badge>
                </div>
                <p className="mt-1 break-words text-[13px] leading-5 text-foreground">{signal.reason}</p>
              </li>
            );
          })}
        </ul>
      </Section>

      <Section
        title="Open questions"
        count={result.open_questions.length}
        tone="bg-status-info"
        note="Rules that could not be decided from the information given. Open questions are not policy violations."
      >
        <ul className="space-y-2">
          {result.open_questions.map((question, index) => {
            const key = `question-${index}`;
            const Icon = OPEN_QUESTION_ICON;
            return (
              <li key={key} id={id(key)} tabIndex={-1} className={cn("scroll-mt-24 rounded-md border-l-4 border-l-status-info bg-background/60 px-3 py-2 outline-none", ring(key))}>
                <div className="flex flex-wrap items-center gap-1.5">
                  <Icon className="size-3.5 text-primary" aria-hidden />
                  <span className="font-mono text-[13px] font-semibold text-foreground">{question.rule_id}</span>
                  <Badge variant="info">Undecided</Badge>
                  <LineRefs lineIds={question.line_ids} onSelectLine={onSelectLine} wholeReceipt="" />
                </div>
                <ul className="mt-1 list-inside list-disc text-[13px] leading-5 text-foreground/90">
                  {question.reasons.map((reason, reasonIndex) => (
                    <li key={reasonIndex} className="break-words">
                      {reason}
                    </li>
                  ))}
                </ul>
              </li>
            );
          })}
        </ul>
      </Section>

      <Section
        title="Uncertainty records"
        count={result.uncertainty.length}
        tone="bg-status-neutral"
        note="EXC-03 detail, one per line and field. How uncertainty is handled is a project decision, not a source-policy requirement."
      >
        <ul className="space-y-1.5">
          {result.uncertainty.map((item, index) => (
            <li key={index} className="flex flex-wrap items-center gap-1.5 text-[13px] leading-5">
              <LineRefs lineIds={[item.line_id]} onSelectLine={onSelectLine} />
              <span className="font-medium text-foreground">{item.field}</span>
              <Badge variant="neutral">{item.state}</Badge>
              <span className="text-foreground/85">{item.reason}</span>
              {item.confidence != null ? <span className="tabular text-xs text-subtle-foreground">confidence {item.confidence.toFixed(2)}</span> : null}
            </li>
          ))}
        </ul>
      </Section>
    </div>
  );
}
