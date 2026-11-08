import { Loader2, Quote } from "lucide-react";
import { useState, type ReactNode } from "react";
import type { ComparisonStatus, FieldComparison, ReviewAction, ReviewDecision } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/misc";
import { shortHash } from "@/lib/format";
import { parseJson, type JsonValue } from "@/lib/losslessJson";
import { SELECT } from "@/pages/evaluations/display";
import { ValueText } from "./actor";

export const ACTIONS: { action: ReviewAction; label: string }[] = [
  { action: "accept_regex", label: "Use the regex reading" },
  { action: "accept_llm", label: "Use the model reading" },
  { action: "confirm", label: "Confirm the agreed value" },
  { action: "override", label: "Set another value" },
  { action: "reject", label: "Reject the field" },
];

export function ComparisonStatusBadge({ status }: { status: ComparisonStatus }) {
  const variant = status === "agreed" ? "pass" : status === "disagree" ? "flag" : "review";
  return <Badge variant={variant}>{status.replace("_", " ")}</Badge>;
}

/** One field as the compiler compared it: where the clause is, what it says, and each reading. Nothing is filled in. */
export function ComparisonCard({ comparison, children }: { comparison: FieldComparison; children?: ReactNode }) {
  const location = comparison.location;
  return (
    <li className="space-y-2 rounded-md border border-border p-3" aria-label={`${comparison.rule_id} ${comparison.field}`}>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-mono text-[13px] font-semibold">{comparison.rule_id}</span>
        <span className="text-[13px] text-muted-foreground">{comparison.field}</span>
        <ComparisonStatusBadge status={comparison.status} />
        {comparison.review_required ? <Badge variant="review">Needs a decision</Badge> : null}
        <span className="ml-auto font-mono text-[11px] text-subtle-foreground">{comparison.comparison_id}</span>
      </div>
      <p className="text-xs text-subtle-foreground">
        {comparison.policy_doc}
        {location?.section ? ` · ${location.section}` : ""}
        {location?.line != null ? ` · line ${location.line}` : ""}
        {comparison.clause_hash ? <span className="font-mono"> · clause {shortHash(comparison.clause_hash)}</span> : null}
      </p>
      {comparison.evidence ? (
        <blockquote className="flex gap-2 whitespace-pre-wrap break-words border-l-2 border-border pl-3 text-xs text-foreground/85">
          <Quote className="mt-0.5 size-3 shrink-0 text-subtle-foreground" aria-hidden />
          {comparison.evidence}
        </blockquote>
      ) : (
        <p className="text-xs text-status-review-text">No source evidence was located for this field.</p>
      )}
      <dl className="grid gap-2 text-[13px] sm:grid-cols-3">
        <div>
          <dt className="text-xs text-muted-foreground">Regex reading</dt>
          <dd>
            <ValueText value={comparison.regex_value} />
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Model reading</dt>
          <dd>
            <ValueText value={comparison.llm_value} />
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Agreed value</dt>
          <dd>{comparison.agreed_value === null ? <span className="text-subtle-foreground">none</span> : <ValueText value={comparison.agreed_value} />}</dd>
        </div>
      </dl>
      {children}
    </li>
  );
}

interface Draft {
  action: ReviewAction | "";
  value: string;
  rationale: string;
}

/** The decisions as the core's ReviewDecision records, or why they cannot be sent. */
export function buildDecisions(comparisons: readonly FieldComparison[], drafts: Record<string, Draft>, reviewer: string): { decisions: ReviewDecision[]; issues: string[] } {
  const issues: string[] = [];
  if (!reviewer.trim()) issues.push("Say who is reviewing (Acting as).");
  const decisions: ReviewDecision[] = [];
  for (const comparison of comparisons) {
    const draft = drafts[comparison.comparison_id];
    if (!draft?.action) {
      issues.push(`Decide ${comparison.rule_id} ${comparison.field}.`);
      continue;
    }
    const decision: ReviewDecision = { comparison_id: comparison.comparison_id, extraction_sha256: comparison.extraction_sha256, action: draft.action, reviewer: reviewer.trim() };
    if (draft.rationale.trim()) decision.rationale = draft.rationale.trim();
    if (draft.action === "override") {
      try {
        // Parsed without rounding: a number typed here reaches the compiler exactly as written.
        decision.value = parseJson(draft.value) as JsonValue;
      } catch {
        issues.push(`${comparison.rule_id} ${comparison.field}: the value must be JSON, such as "text" in quotes, a number, or a list.`);
        continue;
      }
    }
    decisions.push(decision);
  }
  return { decisions, issues };
}

/**
 * A decision for each field the review must decide. The compiler judges the decisions: a review it does not accept
 * comes back with what remains and why, and is never treated as ready here.
 */
export function DecisionForm({
  comparisons,
  reviewer,
  pending,
  submitLabel,
  onSubmit,
  extra,
}: {
  comparisons: readonly FieldComparison[];
  reviewer: string;
  pending: boolean;
  submitLabel: string;
  onSubmit: (decisions: ReviewDecision[]) => void;
  extra?: ReactNode;
}) {
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const { decisions, issues } = buildDecisions(comparisons, drafts, reviewer);
  const set = (id: string, change: Partial<Draft>) => setDrafts((current) => ({ ...current, [id]: { action: "", value: "", rationale: "", ...current[id], ...change } }));

  return (
    <div className="space-y-3">
      <ul className="space-y-3">
        {comparisons.map((comparison) => {
          const draft = drafts[comparison.comparison_id] ?? { action: "", value: "", rationale: "" };
          const id = comparison.comparison_id;
          return (
            <ComparisonCard key={id} comparison={comparison}>
              <div className="grid gap-2 border-t border-border pt-2 sm:grid-cols-2">
                <div className="space-y-1">
                  <label htmlFor={`${id}-action`} className="block text-xs font-medium">
                    Decision
                  </label>
                  <select id={`${id}-action`} className={SELECT} value={draft.action} onChange={(event) => set(id, { action: event.target.value as ReviewAction })}>
                    <option value="">Choose a decision</option>
                    {ACTIONS.map((item) => (
                      <option key={item.action} value={item.action}>
                        {item.label}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="space-y-1">
                  <label htmlFor={`${id}-rationale`} className="block text-xs font-medium">
                    Rationale
                  </label>
                  <Input id={`${id}-rationale`} className="h-9 text-[13px]" value={draft.rationale} onChange={(event) => set(id, { rationale: event.target.value })} placeholder="Why (recorded with the decision)" />
                </div>
                {draft.action === "override" ? (
                  <div className="space-y-1 sm:col-span-2">
                    <label htmlFor={`${id}-value`} className="block text-xs font-medium">
                      Value (JSON)
                    </label>
                    <textarea id={`${id}-value`} rows={2} value={draft.value} onChange={(event) => set(id, { value: event.target.value })} className="w-full rounded-md border border-input bg-card p-2 font-mono text-xs" />
                    <div className="flex flex-wrap gap-1.5">
                      {(["regex_value", "llm_value"] as const).map((key) =>
                        comparison[key] !== null && comparison[key] !== undefined ? (
                          <button key={key} type="button" onClick={() => set(id, { value: JSON.stringify(comparison[key]) })} className="rounded-md border border-border px-2 py-0.5 text-xs hover:bg-muted">
                            Start from the {key === "regex_value" ? "regex" : "model"} reading
                          </button>
                        ) : null,
                      )}
                    </div>
                  </div>
                ) : null}
              </div>
            </ComparisonCard>
          );
        })}
      </ul>
      {extra}
      {issues.length ? (
        <ul className="space-y-0.5 text-xs text-status-review-text" aria-label="Before sending the review">
          {issues.map((issue) => (
            <li key={issue}>{issue}</li>
          ))}
        </ul>
      ) : null}
      <Button type="button" disabled={issues.length > 0 || pending} onClick={() => onSubmit(decisions)}>
        {pending ? <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden /> : null}
        {submitLabel}
      </Button>
    </div>
  );
}
