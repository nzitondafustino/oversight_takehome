/**
 * The compilation workflow's state, from what the API reports: one status label for an extraction, its summary, and
 * exactly what stands between it and approval. Nothing here decides readiness: the compiler does, and the API checks
 * again at every step.
 */
import type { ChangeRequest, ExtractionListItem, ExtractionReviewDetail, ExtractionSummary } from "@/api/types";
import { attachIssues, decidedValue, FIELD_TEXT, problemText } from "./review/issues";

export type WorkflowStatus = "ready_for_review" | "changes_required" | "ready_for_approval" | "approved" | "published";

export const WORKFLOW_LABELS: Record<WorkflowStatus, string> = {
  ready_for_review: "Ready for review",
  changes_required: "Changes required",
  ready_for_approval: "Ready for approval",
  approved: "Approved",
  published: "Published",
};

export const STEPS = ["Select documents", "Compile policy rules", "Review differences", "Approve decisions", "Publish ruleset"] as const;

/**
 * One label for where an extraction stands, the furthest it has reached: published or approved (a version made from
 * it), changes required (open change requests, or no candidate at all), ready for approval (the compiler needs no
 * review, or accepted one), else ready for review.
 */
export function workflowStatus(extraction: ExtractionListItem): WorkflowStatus {
  const versions = extraction.versions ?? [];
  if (versions.some((version) => version.status === "published")) return "published";
  if (versions.some((version) => version.status === "approved")) return "approved";
  if ((extraction.open_change_requests ?? 0) > 0 || extraction.status === "no_candidate") return "changes_required";
  if (extraction.ready_for_approval || extraction.reviews.some((review) => review.ready_for_approval)) return "ready_for_approval";
  return "ready_for_review";
}

export interface Summary {
  documents: number;
  rules: number | null;
  rulesAgreed: number | null;
  rulesNeedingReview: number | null;
  fields: number;
  conflicting: number;
  missing: number;
  blocking: number;
}

/** What the compiler produced, from its agreement summary: counts it did not report stay unknown, never zero. */
export function summarize(extraction: ExtractionSummary): Summary {
  const summary = extraction.agreement_summary as { rules?: number; rules_agreed?: number; rules_needing_review?: number; fields?: Record<string, number> };
  const fields = summary.fields ?? {};
  return {
    documents: extraction.documents.length,
    rules: summary.rules ?? null,
    rulesAgreed: summary.rules_agreed ?? null,
    rulesNeedingReview: summary.rules_needing_review ?? null,
    fields: extraction.comparison_count,
    conflicting: fields.disagree ?? 0,
    missing: (fields.unconfirmed ?? 0) + (fields.only_regex ?? 0) + (fields.only_llm ?? 0),
    blocking: extraction.blockers.length,
  };
}

export interface Blocker {
  /** What stops approval, in a sentence. */
  text: string;
  /** The comparison to open to resolve it, when it is about one field. */
  comparisonId?: string;
}

/**
 * Everything that stands between this extraction and a version that can be approved, each linked to its field where
 * it has one: no candidate, fields still undecided by the latest review, the compiler's issues with it, and open
 * change requests. Empty means the compiler accepts it; the API still checks again.
 */
export function approvalBlockers(extraction: ExtractionSummary, latest: ExtractionReviewDetail | undefined, requests: readonly ChangeRequest[]): Blocker[] {
  const byId = new Map(extraction.review_required.map((item) => [item.comparison_id, item]));
  const named = new Map(extraction.review_required.map((item) => [item.comparison_id, `${item.rule_id} · ${FIELD_TEXT[item.field] ?? item.field}`]));
  const blockers: Blocker[] = [];
  if (extraction.status === "no_candidate") blockers.push({ text: "The compiler built no candidate ruleset from these documents: compile them again after correcting them." });
  if (!extraction.ready_for_approval) {
    if (!latest) {
      for (const item of extraction.review_required) blockers.push({ text: `${named.get(item.comparison_id)}: needs a decision.`, comparisonId: item.comparison_id });
    } else if (!latest.ready_for_approval) {
      for (const id of latest.remaining) blockers.push({ text: `${named.get(id) ?? id}: still needs a decision.`, comparisonId: id });
      for (const issue of attachIssues(latest.issues, extraction.review_required, latest.decisions).filter((item) => item.level === "error")) {
        const comparison = issue.comparison_id ? byId.get(issue.comparison_id) : undefined;
        const decision = latest.decisions.find((item) => item.comparison_id === issue.comparison_id);
        const text = comparison ? problemText(issue, comparison, extraction.rule_logic?.[comparison.rule_id], decidedValue(comparison, decision)) : issue.message;
        blockers.push({ text: `${issue.comparison_id ? `${named.get(issue.comparison_id) ?? issue.comparison_id}: ` : ""}${text}`, comparisonId: issue.comparison_id ?? undefined });
      }
    }
  }
  for (const request of requests.filter((item) => item.status === "open"))
    blockers.push({ text: `${request.rule_id} · ${FIELD_TEXT[request.field] ?? request.field}: clarification requested by ${request.requested_by.claimed}, not yet answered.`, comparisonId: request.comparison_id });
  return blockers;
}

/**
 * Compilations still waiting for a reviewer: not yet approved or published, and newer than the latest published version.
 * An older one was overtaken by what was published since, so it stays in the history, not in the review queue.
 */
export function pendingCompilations(extractions: readonly ExtractionListItem[], published: readonly { status: string; created_at: string }[]): ExtractionListItem[] {
  const latest = published.filter((item) => item.status === "published").map((item) => item.created_at).sort().at(-1);
  return extractions.filter((item) => ["ready_for_review", "changes_required"].includes(workflowStatus(item)) && (!latest || item.created_at > latest));
}

/** The latest review, which a person continues from, and the latest the compiler accepted, which a version is made from. */
export function latestReviews(extraction: ExtractionListItem) {
  const reviews = [...extraction.reviews].sort((a, b) => a.created_at.localeCompare(b.created_at));
  return { latest: reviews.at(-1), latestReady: [...reviews].reverse().find((review) => review.ready_for_approval) };
}
