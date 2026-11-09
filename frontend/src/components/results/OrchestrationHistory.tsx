/**
 * A LangGraph line's attempt history, read from classification_details.orchestration as the core records it: each
 * classification and revision with its node, model, outcome and confidence, each review with its verdict and feedback,
 * and why the line stopped. Lines classified directly carry no orchestration audit, so nothing is shown for them.
 */
import { cn } from "@/lib/utils";

interface Attempt {
  name: string;
  model: string;
  outcome: string;
  confidence: number | null;
  error: string | null;
  revision?: number;
  max_retries?: number;
  retried_validation_error?: boolean;
}

interface Review {
  of: string;
  revision: number;
  model: string;
  outcome: string;
  verdict: string | null;
  reason: string | null;
  suggested_category: string | null;
  feedback: string | null;
}

interface Orchestration {
  answered_by: string | null;
  result: string;
  attempts: Attempt[];
  reflection?: { calls: number; revisions: number; reviews: Review[]; unresolved: boolean; withheld_confidence?: number | null; stop_reason?: string; max_retries?: number; max_calls?: number; feedback_context?: string };
}

/** Why a line stopped, by the core's result codes. An unknown code is shown as it is. */
export const STOP_REASONS: Record<string, string> = {
  accepted: "Accepted",
  routing_off: "Kept: routing sends it no further",
  exhausted: "Every node tried; none was confident",
  call_budget: "Call limit reached",
  retries_exhausted: "Retry limit reached",
  repeated_objection: "The reviewer repeated its objection",
  revision_unchanged: "The revision repeated the same answer",
  revision_failed: "The revision failed validation",
  revision_missing_recording: "No recording for the revision",
  review_missing_recording: "No recording for the review",
  reviewer_failed: "The reviewer failed",
  reviewer_uncertain: "The reviewer could not tell from the line",
  no_reviser: "No node can revise",
};

const OUTCOMES: Record<string, string> = {
  accepted: "accepted",
  low_confidence: "low confidence",
  no_category: "no category",
  error: "failed validation",
  missing_recording: "no recording",
};

function confidence(value: number | null) {
  return value == null ? null : <span className="tabular"> · confidence {value.toFixed(2)}</span>;
}

export function OrchestrationHistory({ details }: { details: Record<string, unknown> | null | undefined }) {
  const audit = details?.orchestration as Orchestration | undefined;
  if (!audit || !Array.isArray(audit.attempts)) return null;
  const reflection = audit.reflection;
  const stop = reflection?.stop_reason ?? audit.result;
  return (
    <details className="rounded-md border border-border bg-card px-3 py-2 text-xs" aria-label="Classification attempts">
      <summary className="cursor-pointer text-foreground">
        <span className="font-medium">Stopped: {STOP_REASONS[stop] ?? stop}</span>
        <span className="text-subtle-foreground">
          {" "}
          · {audit.attempts.length} {audit.attempts.length === 1 ? "attempt" : "attempts"}
          {reflection ? ` · ${reflection.calls} model calls${reflection.max_calls ? ` of ${reflection.max_calls}` : ""} · ${reflection.revisions} revisions${reflection.max_retries != null ? ` of ${reflection.max_retries}` : ""}` : ""}
        </span>
        {reflection?.unresolved ? <span className="ml-1.5 rounded border border-status-review-border bg-status-review-soft px-1 text-status-review-text">Unresolved: confidence withheld</span> : null}
      </summary>
      <ol className="mt-2 space-y-1" aria-label="Attempts">
        {audit.attempts.map((attempt, index) => (
          <li key={index} className={cn(attempt.outcome === "accepted" ? "text-foreground" : "text-muted-foreground")}>
            <span className="font-medium">{attempt.revision ? `Revision ${attempt.revision}` : "Classification"}</span> by {attempt.name} ·{" "}
            <span className="font-mono">{attempt.model}</span> · {OUTCOMES[attempt.outcome] ?? attempt.outcome}
            {confidence(attempt.confidence)}
            {attempt.retried_validation_error ? <span className="text-subtle-foreground"> · retried after a failed revision</span> : null}
            {attempt.error && attempt.outcome === "error" ? <span className="block text-status-flag-text">{attempt.error}</span> : null}
          </li>
        ))}
      </ol>
      {reflection?.reviews.length ? (
        <ol className="mt-2 space-y-1 border-t border-border pt-2" aria-label="Reviews">
          {reflection.reviews.map((review, index) => (
            <li key={index}>
              <span className="font-medium">Review</span> of {review.of}
              {review.revision ? ` revision ${review.revision}` : ""} · {review.verdict ?? OUTCOMES[review.outcome] ?? review.outcome}
              {review.outcome === "reused" ? <span className="text-subtle-foreground"> (earlier verdict, no call)</span> : null}
              {review.reason ? <span className="block text-muted-foreground">{review.reason}</span> : null}
              {review.feedback ? <span className="block text-muted-foreground">Feedback: {review.feedback}</span> : null}
              {review.suggested_category ? (
                <span className="block text-muted-foreground">
                  Suggested: <span className="font-mono">{review.suggested_category}</span>
                </span>
              ) : null}
            </li>
          ))}
        </ol>
      ) : null}
    </details>
  );
}
