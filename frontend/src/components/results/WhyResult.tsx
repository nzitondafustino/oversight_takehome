import { CircleCheck, CircleDashed, CircleHelp, Flag, Search } from "lucide-react";
import type { ReactNode } from "react";
import type { ReceiptResult } from "@/api/types";
import { isProvisionalPass } from "@/components/status/badges";
import { cn } from "@/lib/utils";
import { exceptionsOf, questionsOf } from "./lineStatus";

export interface FindingSelection {
  key: string;
  lineIds: string[];
}

/** The reason codes the engine gives (engine/evaluation.py), in words, for codes with no finding to list. */
const PLAIN: Record<string, string> = {
  inevitable_violation: "Every reading of an unresolved field would flag this receipt.",
  lines_do_not_account_for_total: "The line items do not add up to the receipt total, so the receipt cannot be fully checked.",
  unverified_amount: "An amount on the receipt could not be verified, so it cannot pass.",
  nothing_found: "No active findings.",
};

function reasonText(code: string, ruleIds: string[]): string {
  if (code === "incomplete_evidence") return `Not every rule could be decided${ruleIds.length ? `: ${ruleIds.join(", ")}` : ""}.`;
  if (code === "unresolved_exception") return `A breach was found, but whether another rule replaces or excuses it is not known${ruleIds.length ? `: ${ruleIds.join(", ")}` : ""}.`;
  return PLAIN[code] ?? code.replace(/_/g, " ");
}

function Row({
  selected,
  onSelect,
  tone,
  rule,
  children,
  scope,
}: {
  selected: boolean;
  onSelect?: () => void;
  tone: "flag" | "review" | "info";
  rule: string;
  children: ReactNode;
  scope: string;
}) {
  const body = (
    <>
      <span
        className={cn(
          "mt-0.5 shrink-0 rounded px-1.5 py-0.5 font-mono text-[11px] font-semibold",
          tone === "flag" && "bg-status-flag-soft text-status-flag-text",
          tone === "review" && "bg-status-review-soft text-status-review-text",
          tone === "info" && "bg-status-info-soft text-primary",
        )}
      >
        {rule}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block break-words text-[13px] leading-5 text-foreground">{children}</span>
        <span className="block text-xs text-subtle-foreground">{scope}</span>
      </span>
    </>
  );
  return (
    <li>
      {onSelect ? (
        <button
          type="button"
          onClick={onSelect}
          aria-pressed={selected}
          className={cn("flex w-full gap-2.5 rounded-md px-2 py-1.5 text-left hover:bg-muted/60", selected && "bg-primary-soft hover:bg-primary-soft")}
        >
          {body}
        </button>
      ) : (
        <div className="flex gap-2.5 px-2 py-1.5">{body}</div>
      )}
    </li>
  );
}

function Group({ icon, title, children }: { icon: ReactNode; title: string; children: ReactNode }) {
  return (
    <div>
      <p className="flex items-center gap-1.5 px-2 text-xs font-semibold text-foreground">
        {icon}
        {title}
      </p>
      <ul className="mt-1 space-y-0.5">{children}</ul>
    </div>
  );
}

const scopeOf = (lineIds: string[]) => (lineIds.length ? `Items ${lineIds.join(", ")}` : "Whole receipt");

/**
 * Why the receipt has its disposition, from its own disposition reasons: what flags it, then what sends it to
 * review, then what is still undecided. Nothing here is inferred; every row is a finding the engine returned.
 */
export function WhyResult({ result, selected, onSelect }: { result: ReceiptResult; selected: string | null; onSelect: (selection: FindingSelection) => void }) {
  const exceptions = exceptionsOf(result);
  const questions = questionsOf(result);
  const codes = new Set(result.disposition_reasons.map((reason) => reason.code));
  const flagging = exceptions.filter((item) => item.tone === "flag");
  const reviewing = exceptions.filter((item) => item.tone !== "flag");
  // incomplete_evidence is explained by the open questions listed below; it is shown on its own only without them,
  // and never as a reason for review on a pass, as open questions alone send nothing to review (EXC-01).
  const provisional = isProvisionalPass(result);
  const otherReasons = result.disposition_reasons.filter(
    (reason) =>
      (PLAIN[reason.code] && reason.code !== "nothing_found") ||
      reason.code === "unresolved_exception" ||
      (reason.code === "incomplete_evidence" && !questions.length && !provisional),
  );
  const clean = result.disposition === "pass" && !result.exceptions.length && !result.review_signals.length && result.decided;

  return (
    <section aria-label="Why this result" className="rounded-lg border border-border bg-card px-3 py-3">
      <h3 className="px-2 text-[13px] font-semibold text-foreground">Why this result?</h3>
      <div className="mt-2 space-y-3">
        {clean ? (
          <p className="flex items-center gap-2 px-2 text-[13px] text-foreground">
            <CircleCheck className="size-4 text-status-pass" aria-hidden />
            No active findings, and every rule was decided.
          </p>
        ) : null}

        {provisional ? (
          <p className="flex items-center gap-2 px-2 text-[13px] text-foreground">
            <CircleDashed className="size-4 text-primary" aria-hidden />
            A provisional pass: no exception or review signal was found, but not every rule could be decided, so this is not a clearance.
          </p>
        ) : null}

        {flagging.length ? (
          <Group icon={<Flag className="size-3.5 text-status-flag-text" aria-hidden />} title="Policy exceptions that flag this receipt">
            {flagging.map((item) => (
              <Row
                key={item.key}
                tone="flag"
                rule={item.exception.rule_id}
                scope={scopeOf(item.exception.line_ids)}
                selected={selected === item.key}
                onSelect={() => onSelect({ key: item.key, lineIds: item.exception.line_ids })}
              >
                {item.exception.explanation}
              </Row>
            ))}
          </Group>
        ) : null}

        {reviewing.length || result.review_signals.length || otherReasons.length ? (
          <Group icon={<Search className="size-3.5 text-status-review-text" aria-hidden />} title={result.disposition === "flag" ? "Also needs review" : "Needs review"}>
            {reviewing.map((item) => (
              <Row
                key={item.key}
                tone="review"
                rule={item.exception.rule_id}
                scope={`${scopeOf(item.exception.line_ids)} · policy exception`}
                selected={selected === item.key}
                onSelect={() => onSelect({ key: item.key, lineIds: item.exception.line_ids })}
              >
                {item.exception.explanation}
              </Row>
            ))}
            {result.review_signals.map((signal, index) => (
              <Row key={`signal-${index}`} tone="review" rule={signal.rule_id} scope="Whole receipt · review signal, not an exception" selected={false}>
                {signal.reason}
              </Row>
            ))}
            {otherReasons.map((reason) => (
              <Row key={reason.code} tone="review" rule={reason.basis} scope={reason.basis === "project_decision" ? "A project decision, not a policy rule" : "Disposition reason"} selected={false}>
                {reasonText(reason.code, reason.rule_ids)}
              </Row>
            ))}
          </Group>
        ) : null}

        {questions.length ? (
          <Group icon={<CircleHelp className="size-3.5 text-primary" aria-hidden />} title="Not yet decided (not violations)">
            {questions.map((item) => (
              <Row
                key={item.key}
                tone="info"
                rule={item.question.rule_id}
                scope={scopeOf(item.question.line_ids)}
                selected={selected === item.key}
                onSelect={() => onSelect({ key: item.key, lineIds: item.question.line_ids })}
              >
                {item.question.reasons.join(" ")}
              </Row>
            ))}
          </Group>
        ) : null}

        {!clean && !provisional && !flagging.length && !reviewing.length && !result.review_signals.length && !questions.length && !otherReasons.length ? (
          <p className="px-2 text-[13px] text-muted-foreground">
            {codes.has("nothing_found") ? "No active findings." : "The result lists no findings for this receipt."}
          </p>
        ) : null}
      </div>
    </section>
  );
}
