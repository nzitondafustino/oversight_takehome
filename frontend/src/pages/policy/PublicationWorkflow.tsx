/**
 * One workflow for a ruleset version, Edit → Resolve → Approve → Publish, as the API judges it now: what changed,
 * what is left to resolve, and exactly what blocks the next step, each linked to its rule or field.
 */
import { Check, CircleAlert } from "lucide-react";
import { Link } from "react-router-dom";
import { useReadiness } from "@/api/queries";
import type { Readiness, RulesetDetail } from "@/api/types";
import { LoadingRows } from "@/components/states/states";
import { cn } from "@/lib/utils";
import { Stat } from "@/pages/evaluations/display";

const STEPS = ["Edit", "Resolve", "Approve", "Publish"] as const;

function stepOf(readiness: Readiness): number {
  if (readiness.status === "published") return 4;
  if (readiness.status === "approved") return 3;
  return readiness.next_step === "approve" ? 2 : 1;
}

export function PublicationWorkflow({ version, onOpenRule }: { version: RulesetDetail; onOpenRule: (ruleId: string) => void }) {
  const readiness = useReadiness(version.ruleset_id, version.status, version.review?.review_sha256);
  if (readiness.isPending) return <LoadingRows rows={2} label="Checking where this version stands" />;
  if (readiness.isError || !readiness.data) return null;
  const data = readiness.data;
  const at = stepOf(data);
  const extraction = version.compilation?.extraction_id;
  return (
    <div className="space-y-3">
      <ol className="flex flex-wrap items-center gap-1 text-xs" aria-label="Publication steps">
        {STEPS.map((step, index) => (
          <li key={step} className="flex items-center gap-1" aria-current={index === at ? "step" : undefined}>
            <span className={cn("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-medium", index < at ? "border-status-pass-border bg-status-pass-soft text-status-pass" : index === at ? "border-primary bg-primary-soft text-primary" : "border-border text-subtle-foreground")}>
              {index < at ? <Check className="size-3" aria-hidden /> : null}
              {step}
            </span>
            {index < STEPS.length - 1 ? <span aria-hidden className="text-subtle-foreground">→</span> : null}
          </li>
        ))}
      </ol>
      <dl className="grid grid-cols-2 gap-2" aria-label="Publication summary">
        <Stat label="Rules changed" value={data.rules_changed} />
        <Stat label="Review issues left" value={data.review_remaining} />
        <Stat label="Validation errors" value={data.validation_errors} hint="the compiler validated it" />
        <Stat label="Approval" value={data.approval === "published" ? "Published" : data.approval === "approved" ? "Approved" : "Not approved"} hint={data.approval_mode ? `for ${data.approval_mode}` : undefined} />
      </dl>
      {data.blockers.length ? (
        <div role="alert" className="space-y-1 rounded-md border border-status-review-border bg-status-review-soft px-3 py-2 text-[13px]">
          <p className="flex items-center gap-1.5 font-semibold text-status-review-text">
            <CircleAlert className="size-4" aria-hidden />
            Not ready to publish
          </p>
          <ul className="space-y-0.5" aria-label="What blocks publication">
            {data.blockers.map((blocker, index) => (
              <li key={index}>
                {blocker.message}{" "}
                {blocker.comparison_id && extraction ? (
                  <Link className="text-xs font-medium text-primary hover:underline" to={`/documents/extractions/${encodeURIComponent(extraction)}?tab=review&item=${encodeURIComponent(blocker.comparison_id)}`}>
                    Resolve it
                  </Link>
                ) : blocker.rule_id ? (
                  <button type="button" className="text-xs font-medium text-primary hover:underline" onClick={() => onOpenRule(blocker.rule_id!)}>
                    Open {blocker.rule_id}
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : data.status !== "published" ? (
        <p className="text-[13px] text-muted-foreground">
          Next: {{ submit: "submit it for review", review: "accept its review", approve: "approve it", publish: "publish it", none: "nothing" }[data.next_step]}.
        </p>
      ) : null}
    </div>
  );
}

/** A version made by editing another: each field it changes, before and after, with who and why. */
export function EditsSummary({ version, onOpenRule }: { version: RulesetDetail; onOpenRule: (ruleId: string) => void }) {
  const edits = version.edits;
  if (!edits) return null;
  const show = (value: unknown) => (value === null || value === undefined ? "none" : typeof value === "string" ? value : JSON.stringify(value));
  return (
    <div className="space-y-2 rounded-md border border-border bg-card px-4 py-3" aria-label="Changes in this version">
      <p className="text-[13px]">
        <span className="font-semibold">Changed from version {edits.based_on_version}</span> by {edits.actor}: {edits.rationale}
      </p>
      <ul className="space-y-1 text-[13px]">
        {edits.changes.map((change) => (
          <li key={`${change.rule_id}-${change.field}`} className="grid gap-1 sm:grid-cols-[5.5rem_9rem_1fr]">
            <button type="button" className="text-left font-mono text-xs text-primary hover:underline" onClick={() => onOpenRule(change.rule_id)}>
              {change.rule_id}
            </button>
            <span className="text-muted-foreground">{change.field.replace(/^params\./, "").replace(/_/g, " ")}</span>
            <span className="break-words">
              <del className="text-status-flag-text">{show(change.before)}</del> → <ins className="font-medium text-status-pass no-underline">{show(change.after)}</ins>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
