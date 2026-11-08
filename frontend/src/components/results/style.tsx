import { AlertOctagon, AlertTriangle, CircleCheck, CircleDashed, CircleHelp, Info, type LucideIcon } from "lucide-react";
import type { ReceiptResult } from "@/api/types";
import { Badge, type BadgeProps } from "@/components/ui/badge";

type Variant = NonNullable<BadgeProps["variant"]>;

/**
 * How an exception's severity is shown. This is presentation only: the disposition is the API's, decided by
 * EXC-01 from the ruleset's own severity mapping, and is never recomputed here.
 */
export function severityStyle(severity: string | null): { variant: Variant; icon: LucideIcon; label: string } {
  const value = severity?.toLowerCase() ?? null;
  if (value === "critical" || value === "high" || value === "medium") return { variant: "flag", icon: AlertOctagon, label: value[0]!.toUpperCase() + value.slice(1) };
  if (value === "low") return { variant: "review", icon: AlertTriangle, label: "Low" };
  return { variant: "neutral", icon: Info, label: severity ? severity : "No severity" };
}

/** `tone`, when known from the receipt's disposition reasons, overrides the colour the severity text suggests. */
export function SeverityBadge({ severity, tone }: { severity: string | null; tone?: "flag" | "review" | "neutral" }) {
  const style = severityStyle(severity);
  const variant = tone ?? style.variant;
  const Icon = tone === "flag" ? AlertOctagon : tone === "review" ? AlertTriangle : style.icon;
  const label = style.label;
  return (
    <Badge variant={variant}>
      <Icon aria-hidden />
      <span>
        <span className="sr-only">Severity: </span>
        {label}
      </span>
    </Badge>
  );
}

/** Whether every rule was decided: kept apart from the disposition, as a flagged receipt can still be undecided. */
export function DecidedBadge({ result }: { result: Pick<ReceiptResult, "decided" | "open_questions"> }) {
  if (result.decided)
    return (
      <Badge variant="pass">
        <CircleCheck aria-hidden />
        Fully decided
      </Badge>
    );
  const count = result.open_questions.length;
  return (
    <Badge variant="info" title="Not every rule could be decided. Open questions are not violations.">
      <CircleDashed aria-hidden />
      Undecided{count ? ` · ${count} open` : ""}
    </Badge>
  );
}

export const OPEN_QUESTION_ICON = CircleHelp;
