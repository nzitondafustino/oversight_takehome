import {
  Ban,
  CheckCircle2,
  CircleDashed,
  CircleSlash,
  Clock,
  Flag,
  Loader2,
  OctagonX,
  Search,
  Wallet,
  type LucideIcon,
} from "lucide-react";
import type { Disposition, EvaluationStatus, RulesetStatus } from "@/api/types";
import { Badge, type BadgeProps } from "@/components/ui/badge";

type Variant = NonNullable<BadgeProps["variant"]>;

/** Pass, review and flag: the core's dispositions, each with its own icon so color never carries meaning alone. */
export const DISPOSITIONS: Record<Disposition, { label: string; icon: LucideIcon; variant: Variant; fill: string }> = {
  flag: { label: "Flag", icon: Flag, variant: "flag", fill: "var(--color-status-flag)" },
  review: { label: "Review", icon: Search, variant: "review", fill: "var(--color-status-review)" },
  pass: { label: "Pass", icon: CheckCircle2, variant: "pass", fill: "var(--color-status-pass)" },
};

export const DISPOSITION_ORDER: readonly Disposition[] = ["flag", "review", "pass"];

/**
 * A pass the engine could not fully decide: nothing was found against it, but some rules are still open, so it
 * is provisional and never a clearance. Only a pass with decided set is a fully decided one.
 */
export function isProvisionalPass(result: { disposition: Disposition; decided: boolean }): boolean {
  return result.disposition === "pass" && !result.decided;
}

/** How one receipt's disposition is shown, telling a provisional pass apart from a fully decided one. */
export function dispositionDisplay(disposition: Disposition, decided = true): { label: string; icon: LucideIcon; variant: Variant } {
  if (isProvisionalPass({ disposition, decided })) return { label: "Provisional pass", icon: CircleDashed, variant: "pass" };
  return DISPOSITIONS[disposition];
}

export function DispositionBadge({ disposition, decided, count }: { disposition: Disposition; decided?: boolean; count?: number }) {
  const { label, icon: Icon, variant } = dispositionDisplay(disposition, decided);
  return (
    <Badge variant={variant}>
      <Icon aria-hidden />
      {label}
      {count != null ? <span className="tabular font-semibold">{count}</span> : null}
    </Badge>
  );
}

const EVALUATION_STATUSES: Record<EvaluationStatus, { label: string; icon: LucideIcon; variant: Variant; spin?: boolean }> = {
  queued: { label: "Queued", icon: Clock, variant: "neutral" },
  running: { label: "Running", icon: Loader2, variant: "info", spin: true },
  cancel_requested: { label: "Cancelling", icon: CircleDashed, variant: "review" },
  completed: { label: "Completed", icon: CheckCircle2, variant: "pass" },
  failed: { label: "Failed", icon: OctagonX, variant: "flag" },
  cancelled: { label: "Cancelled", icon: Ban, variant: "neutral" },
  budget_exhausted: { label: "Budget exhausted", icon: Wallet, variant: "review" },
};

export function EvaluationStatusBadge({ status }: { status: EvaluationStatus }) {
  const config = EVALUATION_STATUSES[status] ?? { label: status, icon: CircleSlash, variant: "neutral" as Variant };
  const Icon = config.icon;
  return (
    <Badge variant={config.variant}>
      <Icon aria-hidden className={config.spin ? "animate-spin motion-reduce:animate-none" : undefined} />
      {config.label}
    </Badge>
  );
}

const RULESET_STATUSES: Record<RulesetStatus, { label: string; variant: Variant }> = {
  draft: { label: "Draft", variant: "neutral" },
  in_review: { label: "In review", variant: "review" },
  approved: { label: "Approved", variant: "info" },
  published: { label: "Published", variant: "pass" },
};

export function RulesetStatusBadge({ status }: { status: RulesetStatus }) {
  const config = RULESET_STATUSES[status] ?? { label: status, variant: "neutral" as Variant };
  return <Badge variant={config.variant}>{config.label}</Badge>;
}

export function ModeBadge({ mode, preview }: { mode: string; preview?: boolean }) {
  return (
    <span className="inline-flex items-center gap-1">
      <Badge variant={mode === "live" ? "accent" : "outline"} className="capitalize">
        {mode}
      </Badge>
      {preview ? (
        <Badge variant="review" title="Validated against a version not published for production">
          Preview
        </Badge>
      ) : null}
    </span>
  );
}
