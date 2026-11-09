/** The five steps from documents to a published ruleset, and one status label for where an extraction stands. */
import { Check } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { STEPS, WORKFLOW_LABELS, type WorkflowStatus } from "./workflow";

export function WorkflowSteps({ current, done = current }: { current: number; done?: number }) {
  return (
    <ol className="flex flex-wrap items-center gap-x-1 gap-y-1.5 text-xs" aria-label="Workflow">
      {STEPS.map((label, index) => (
        <li key={label} className="flex items-center gap-1" aria-current={index === current ? "step" : undefined}>
          <span
            className={cn(
              "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-medium",
              index < done && index !== current ? "border-status-pass-border bg-status-pass-soft text-status-pass" : index === current ? "border-primary bg-primary-soft text-primary" : "border-border text-subtle-foreground",
            )}
          >
            {index < done && index !== current ? <Check className="size-3" aria-hidden /> : <span className="tabular">{index + 1}</span>}
            {label}
          </span>
          {index < STEPS.length - 1 ? <span aria-hidden className="text-subtle-foreground">›</span> : null}
        </li>
      ))}
    </ol>
  );
}

const VARIANT: Record<WorkflowStatus, "neutral" | "pass" | "review" | "flag" | "info"> = {
  ready_for_review: "info",
  changes_required: "flag",
  ready_for_approval: "review",
  approved: "pass",
  published: "pass",
};

export function WorkflowStatusBadge({ status }: { status: WorkflowStatus }) {
  return <Badge variant={VARIANT[status]}>{WORKFLOW_LABELS[status]}</Badge>;
}
