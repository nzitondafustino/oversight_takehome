import { AlertTriangle, ChevronRight, CircleHelp, FileText, Languages } from "lucide-react";
import type { ReactNode } from "react";
import type { ReceiptResult } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { InputLine } from "@/pages/validate/receipts";
import { lineFindings, lineStatus, type LineStatusKind } from "./lineStatus";
import { OrchestrationHistory } from "./OrchestrationHistory";
import { SeverityBadge } from "./style";

export function lineAnchor(receiptId: string, lineId: string) {
  return `line-${receiptId}-${lineId}`.replace(/[^A-Za-z0-9_-]/g, "_");
}

const STATUS_STYLE: Record<LineStatusKind, string> = {
  flag: "border-status-flag-border bg-status-flag-soft text-status-flag-text",
  review: "border-status-review-border bg-status-review-soft text-status-review-text",
  error: "border-status-flag-border bg-status-flag-soft text-status-flag-text",
  uncertain: "border-status-review-border bg-status-review-soft text-status-review-text",
  open: "border-status-info-border bg-status-info-soft text-primary",
  none: "border-border bg-card text-subtle-foreground",
};

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline gap-1.5">
      <dt className="text-subtle-foreground">{label}</dt>
      <dd className="font-medium text-foreground">{children}</dd>
    </div>
  );
}

/**
 * The receipt's items: original description, amount when the receipt was submitted here, and a status taken from
 * the findings that name the item. Expanding one shows its classification and every finding that cites it.
 */
export function ExpenseItems({
  result,
  inputs,
  currency,
  expanded,
  onToggle,
  highlighted,
}: {
  result: ReceiptResult;
  /** The submitted lines by ID, when this run was just submitted here: the result holds no amounts. */
  inputs: Map<string, InputLine> | null;
  currency: string | null;
  expanded: ReadonlySet<string>;
  onToggle: (lineId: string) => void;
  highlighted: ReadonlySet<string>;
}) {
  return (
    <ol className="divide-y divide-border rounded-lg border border-border bg-card" aria-label="Expense items">
      {result.line_items.map((line) => {
        const input = inputs?.get(line.line_id);
        const findings = lineFindings(result, line.line_id);
        const status = lineStatus(findings);
        const open = expanded.has(line.line_id);
        const isHighlighted = highlighted.has(line.line_id);
        const panelId = `${lineAnchor(result.receipt_id, line.line_id)}-panel`;
        return (
          <li
            key={line.line_id}
            id={lineAnchor(result.receipt_id, line.line_id)}
            tabIndex={-1}
            data-highlighted={isHighlighted || undefined}
            className={cn("scroll-mt-24 outline-none first:rounded-t-lg last:rounded-b-lg", isHighlighted && "bg-primary-soft ring-2 ring-inset ring-primary/40")}
          >
            <button
              type="button"
              onClick={() => onToggle(line.line_id)}
              aria-expanded={open}
              aria-controls={panelId}
              className="flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-muted/40"
            >
              <ChevronRight className={cn("mt-0.5 size-4 shrink-0 text-subtle-foreground transition-transform", open && "rotate-90")} aria-hidden />
              <span className="min-w-0 flex-1">
                <span className="block break-words text-[13px] font-medium leading-5 text-foreground">{line.description_raw}</span>
                <span className="font-mono text-[11px] text-subtle-foreground">{line.line_id}</span>
              </span>
              <span className="shrink-0 text-right">
                <span className="tabular block text-[13px] font-semibold text-foreground">
                  {input?.amount ?? <span className="text-xs font-normal italic text-subtle-foreground">{input ? "Unavailable" : "Not stored"}</span>}
                  {input?.amount && currency ? <span className="ml-1 text-xs font-normal text-subtle-foreground">{currency}</span> : null}
                </span>
                <span className={cn("mt-1 inline-block rounded-md border px-1.5 py-0.5 text-[11px] font-medium", STATUS_STYLE[status.kind])}>{status.label}</span>
              </span>
            </button>

            {open ? (
              <div id={panelId} className="space-y-3 border-t border-border bg-background/50 px-4 py-3 pl-11 text-xs">
                <dl className="flex flex-wrap gap-x-5 gap-y-1">
                  <Fact label="Category">
                    {line.category ? <span className="font-mono">{line.category}</span> : <span className="font-normal italic text-subtle-foreground">None predicted</span>}
                    {line.restricted ? (
                      <Badge variant="outline" className="ml-1.5" title="The taxonomy marks this category restricted. A restricted category is not a finding by itself.">
                        Restricted category
                      </Badge>
                    ) : null}
                  </Fact>
                  <Fact label="Confidence">{line.confidence != null ? line.confidence.toFixed(2) : <span className="font-normal italic text-subtle-foreground">Unavailable</span>}</Fact>
                  {input ? (
                    <Fact label="Quantity × unit price">
                      {input.quantity ?? "?"} × {input.unitPrice ?? "?"}
                    </Fact>
                  ) : null}
                  {line.attributes ? Object.entries(line.attributes).map(([key, value]) => <Fact key={key} label={key.replace(/_/g, " ")}>{String(value)}</Fact>) : null}
                </dl>
                {line.english_translation ? (
                  <p className="flex items-start gap-1 text-muted-foreground">
                    <Languages className="mt-px size-3.5 shrink-0" aria-label="Translation" />
                    {line.english_translation}
                    {input?.language ? <span className="text-subtle-foreground"> (from {input.language})</span> : null}
                  </p>
                ) : null}

                {findings.exceptions.map((item) => (
                  <div key={item.key} className={cn("rounded-md border-l-4 bg-card px-3 py-2", item.tone === "flag" ? "border-l-status-flag" : item.tone === "review" ? "border-l-status-review" : "border-l-status-neutral")}>
                    <p className="flex flex-wrap items-center gap-1.5">
                      <span className="font-mono text-[13px] font-semibold text-foreground">{item.exception.rule_id}</span>
                      <SeverityBadge severity={item.exception.severity} tone={item.tone} />
                      <span className="flex items-center gap-1 text-subtle-foreground">
                        <FileText className="size-3" aria-hidden />
                        {item.exception.policy_doc}
                      </span>
                    </p>
                    <p className="mt-1 break-words text-[13px] leading-5 text-foreground">{item.exception.explanation}</p>
                    <blockquote className="mt-1.5 whitespace-pre-wrap break-words border-l-2 border-border pl-3 leading-5 text-foreground/85">{item.exception.evidence}</blockquote>
                    <p className="mt-1 text-subtle-foreground">
                      Signed quote: <span className="whitespace-pre-wrap break-words font-mono text-foreground/85">{item.exception.source_quote}</span>
                    </p>
                  </div>
                ))}
                {findings.questions.map((item) => (
                  <div key={item.key} className="rounded-md border-l-4 border-l-status-info bg-card px-3 py-2">
                    <p className="flex items-center gap-1.5">
                      <CircleHelp className="size-3.5 text-primary" aria-hidden />
                      <span className="font-mono text-[13px] font-semibold text-foreground">{item.question.rule_id}</span>
                      <span className="text-subtle-foreground">not yet decided, not a violation</span>
                    </p>
                    <ul className="mt-1 list-inside list-disc text-[13px] leading-5 text-foreground/90">
                      {item.question.reasons.map((reason, index) => (
                        <li key={index}>{reason}</li>
                      ))}
                    </ul>
                  </div>
                ))}
                {findings.uncertainty.map((item, index) => (
                  <p key={index} className="rounded-md bg-status-review-soft px-3 py-1.5 leading-5 text-foreground/85">
                    <span className="font-medium text-status-review-text">Uncertain {item.field}</span> ({item.state}): {item.reason}
                    {item.confidence != null ? <span className="tabular text-subtle-foreground"> · confidence {item.confidence.toFixed(2)}</span> : null}
                  </p>
                ))}
                {findings.classificationError ? (
                  <p className="flex items-start gap-1.5 rounded-md bg-status-flag-soft px-3 py-1.5 text-status-flag-text">
                    <AlertTriangle className="mt-px size-3.5 shrink-0" aria-hidden />
                    <span>
                      <span className="font-medium">Classification error:</span> {findings.classificationError}
                    </span>
                  </p>
                ) : null}
                <OrchestrationHistory details={line.classification_details} />
                {status.kind === "none" ? <p className="text-subtle-foreground">No finding names this item. Receipt-level findings, if any, are listed above.</p> : null}
              </div>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
