/**
 * Where an issue's rule applies and what it does when it runs, from the compiled candidate rule itself: its conditions,
 * what it checks, its outcome, and the rules it replaces or is replaced by. Nothing is inferred from the policy's
 * wording; a rule the candidate does not hold is said so.
 */
import type { ReactNode } from "react";
import type { RuleLogic } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { conditionText, plainRule, type ArtifactRule, type Condition, type Param } from "../rules";

const LEVELS: Record<string, string> = {
  receipt: "on every receipt it applies to",
  line: "on each line it catches",
};

function named(ruleId: string, titles: Record<string, string>): string {
  return titles[ruleId] ? `${ruleId} (${titles[ruleId]})` : ruleId;
}

function list(items: string[]): string {
  return items.length <= 1 ? items.join("") : `${items.slice(0, -1).join(", ")} and ${items.at(-1)}`;
}

/** Whether the rule runs: an active check or carve-out does; guidance, retired and not-evaluable rules raise nothing. */
export function executableText(logic: RuleLogic): { label: string; variant: "pass" | "neutral" | "review" } {
  if (logic.status === "retired") return { label: "Retired: not applied", variant: "neutral" };
  if (logic.status === "not_evaluable") return { label: "Not executable: kept for the record", variant: "review" };
  if (logic.kind === "process") return { label: "Guidance: raises no finding of its own", variant: "neutral" };
  return { label: "Executable check", variant: "pass" };
}

/** Who replaces whom, in sentences, with the condition under which a replacing rule applies. */
export function relationships(ruleId: string, logic: Record<string, RuleLogic>, titles: Record<string, string>, mentioned: readonly string[] = []): string[] {
  const own = logic[ruleId];
  const sentences: string[] = [];
  const replaces = own?.replaces ?? [];
  for (const level of [...new Set(replaces.map((item) => item.level))]) {
    const ids = replaces.filter((item) => item.level === level).map((item) => named(item.rule_id, titles));
    if (level === "finding") sentences.push(`Replaces the finding of ${list(ids)} that it names as split around: that finding is not reported, and ${ruleId} is reported instead.`);
    else sentences.push(`Replaces ${list(ids)} ${LEVELS[level] ?? `at the ${level} level`}: ${ids.length === 1 ? "that rule is" : "those rules are"} not reported there, and ${ruleId} is reported instead.`);
  }
  for (const [otherId, other] of Object.entries(logic)) {
    const replacement = (other.replaces ?? []).find((item) => item.rule_id === ruleId);
    if (!replacement || otherId === ruleId) continue;
    const when = other.applies_when ? ` when ${conditionText(other.applies_when as Condition, (other.params ?? {}) as Record<string, Param>)}` : "";
    if (replacement.level === "finding") sentences.push(`Replaced by ${named(otherId, titles)} where a split was made around ${ruleId}'s limit: the split is reported instead.`);
    else sentences.push(`Replaced by ${named(otherId, titles)}${when} ${LEVELS[replacement.level] ?? ""}: there ${ruleId} is not reported.`.replace(/\s+:/, ":"));
  }
  const replacedIds = new Set(replaces.map((item) => item.rule_id));
  const untouched = mentioned.filter((id) => id !== ruleId && !replacedIds.has(id));
  if (untouched.length && replaces.length) sentences.push(`Mentions ${list(untouched.map((id) => named(id, titles)))} without replacing ${untouched.length === 1 ? "it" : "them"}: ${untouched.length === 1 ? "it still applies" : "they still apply"}.`);
  return sentences;
}

export function RuleEffect({ ruleId, section, logic, titles, mentioned }: { ruleId: string; section: string | null | undefined; logic: Record<string, RuleLogic>; titles: Record<string, string>; mentioned: readonly string[] }) {
  const own = logic[ruleId];
  if (!own) {
    return (
      <div className="space-y-2 text-[13px]">
        <Facet title="Where it applies">{section ? <p>Policy section: {section}</p> : <p className="text-muted-foreground">The policy states no section for this rule.</p>}</Facet>
        <Facet title="What it affects">
          <p className="text-muted-foreground">The compiled rules have no rule {ruleId}, so nothing runs for it.</p>
        </Facet>
      </div>
    );
  }
  const plain = plainRule({ rule_id: ruleId, ...own } as unknown as ArtifactRule);
  const executable = executableText(own);
  const related = relationships(ruleId, logic, titles, mentioned);
  return (
    <div className="space-y-2 text-[13px]">
      <Facet title="Where it applies">
        {section ? <p>Policy section: {section}</p> : null}
        <p>{plain.applies}</p>
      </Facet>
      <Facet title={`What ${ruleId} does`} badge={<Badge variant={executable.variant}>{executable.label}</Badge>}>
        <dl className="grid gap-x-2 gap-y-0.5 sm:grid-cols-[5.5rem_1fr]">
          {plain.checks ? (
            <>
              <dt className="text-muted-foreground">Checks</dt>
              <dd>{plain.checks}</dd>
            </>
          ) : null}
          <dt className="text-muted-foreground">Outcome</dt>
          <dd>{related.length ? plain.outcome.replace(/ Takes the place of [^.]*\./, "") : plain.outcome}</dd>
          {plain.limits.length ? (
            <>
              <dt className="text-muted-foreground">Limits</dt>
              <dd>{plain.limits.join(" · ")}</dd>
            </>
          ) : null}
        </dl>
        {related.length ? (
          <ul aria-label="Related rules" className="mt-1.5 space-y-1 border-t border-border pt-1.5">
            {related.map((sentence) => (
              <li key={sentence}>{sentence}</li>
            ))}
          </ul>
        ) : null}
      </Facet>
    </div>
  );
}

function Facet({ title, badge, children }: { title: string; badge?: ReactNode; children: ReactNode }) {
  return (
    <section aria-label={title} className="rounded-md border border-border px-3 py-2">
      <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{title}</h4>
        {badge}
      </div>
      {children}
    </section>
  );
}
