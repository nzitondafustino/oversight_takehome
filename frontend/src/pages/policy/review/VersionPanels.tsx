/**
 * The draft beside the version it would replace. A policy change is shown as a change to confirm, never as an error;
 * an earlier decision is offered again only where its question is unchanged, and must be made again where it is not;
 * a compiled rule that does not do what the current policy states is corrected in the draft, with who and why.
 */
import { ArrowRight, Loader2, Wrench } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useCreateExtraction, useRulesetChanges } from "@/api/queries";
import type { Baseline, ExtractionSummary, PreviousDecision, RuleDiscrepancy, RuleOverride } from "@/api/types";
import { ErrorState } from "@/components/states/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/misc";
import { useActor } from "../actor";
import { AddRule } from "./RuleChanges";
import { changeReason, changeTexts, decisionText, itemsText, proposedParams, readingItems, READING, type RuleChangeText } from "./issues";

function versionName(baseline: Baseline): string {
  return baseline.published ? `${baseline.published.name} v${baseline.published.version}` : "";
}

/** The draft beside the published version: what it changes, and which earlier decisions can be made again. */
export function BaselineSummary({ baseline, titles, reusable, onReuse, busy }: { baseline: Baseline; titles: Record<string, string>; reusable: number; onReuse: () => void; busy: boolean }) {
  if (!baseline.published) return <p className="text-xs text-muted-foreground">Nothing is published yet: every rule in this draft is new.</p>;
  const rules = [...new Set((baseline.rule_changes ?? []).map((change) => change.rule_id))];
  const revalidate = (baseline.decisions ?? []).filter((item) => item.state === "changed").length;
  return (
    <div className="space-y-2 rounded-md border border-border px-3 py-2 text-[13px]" role="region" aria-label="Compared with the published version">
      <p className="font-medium">Compared with the published version, {versionName(baseline)}</p>
      {rules.length ? (
        <div>
          <p>
            <Badge variant="info">Policy change detected</Badge> This draft changes {rules.length} {rules.length === 1 ? "rule" : "rules"}. Approving it authorizes these changes; {versionName(baseline)} stays as it is.
          </p>
          <ul className="mt-1 space-y-0.5" aria-label="Policy changes">
            {rules.map((ruleId) => (
              <li key={ruleId}>
                <span className="font-mono text-xs">{ruleId}</span>{titles[ruleId] ? ` ${titles[ruleId]}` : ""}: {changeTexts(ruleId, baseline).map((change) => `${change.label} ${change.before} → ${change.after}`).join("; ")}
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="text-muted-foreground">No rule does anything different from {versionName(baseline)}.{(baseline.changed_clauses ?? []).length ? ` The text of ${(baseline.changed_clauses ?? []).length} ${(baseline.changed_clauses ?? []).length === 1 ? "clause" : "clauses"} changed without changing a rule.` : ""}</p>
      )}
      {reusable || revalidate ? (
        <div className="flex flex-wrap items-center gap-2">
          {reusable ? (
            <Button type="button" size="sm" variant="outline" onClick={onReuse} disabled={busy}>
              {busy ? <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden /> : null}
              Reuse {reusable} unchanged {reusable === 1 ? "decision" : "decisions"} from {versionName(baseline)}
            </Button>
          ) : null}
          <span className="text-xs text-muted-foreground">
            {reusable ? `Same clause text and same readings as when ${versionName(baseline)} was approved: each is saved again under your name, citing it. ` : ""}
            {revalidate ? `${revalidate} earlier ${revalidate === 1 ? "decision needs" : "decisions need"} revalidation: the clause or its readings changed.` : ""}
          </span>
        </div>
      ) : null}
    </div>
  );
}

/** A version beside the version published before it: each rule it changes, before and after, in words. */
export function VersionChangesPanel({ rulesetId, titles }: { rulesetId: string; titles: Record<string, string> }) {
  const changes = useRulesetChanges(rulesetId).data;
  if (!changes?.against) return null;
  const name = `${changes.against.name} v${changes.against.version}`;
  const baseline: Baseline = { published: changes.against, rule_changes: changes.rule_changes };
  const rules = [...new Set(changes.rule_changes.map((change) => change.rule_id))];
  return (
    <div className="rounded-md border border-border px-3 py-2 text-[13px]" role="region" aria-label="Compared with the previously published version">
      <p className="font-medium">Compared with {name}, published before this version</p>
      {rules.length ? (
        <ul className="mt-1 space-y-0.5">
          {rules.map((ruleId) => (
            <li key={ruleId}>
              <span className="font-mono text-xs">{ruleId}</span>
              {titles[ruleId] ? ` ${titles[ruleId]}` : ""}: {changeTexts(ruleId, baseline).map((change) => `${change.label} ${change.before} → ${change.after}`).join("; ")}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-muted-foreground">Every rule does what it did in {name}.</p>
      )}
    </div>
  );
}

/** One rule's change from the published version, with why it is under review and what to do. */
export function PolicyChange({ ruleId, baseline, changes }: { ruleId: string; baseline: Baseline; changes: RuleChangeText[] }) {
  const clauseChanged = (baseline.changed_clauses ?? []).some((item) => item.rule_id === ruleId);
  const { reason, todo } = changeReason(ruleId, changes, clauseChanged);
  return (
    <section aria-label="Policy change detected" className="rounded-md border border-status-info-border bg-status-info-soft/40 px-3 py-2 text-[13px]">
      <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Policy change detected</h4>
      {changes.length ? (
        <dl className="grid grid-cols-[1fr_auto_1fr] items-baseline gap-x-3 gap-y-0.5">
          <dt className="text-xs text-muted-foreground">Previous published version, {versionName(baseline)}</dt>
          <span />
          <dt className="text-xs text-muted-foreground">Current draft</dt>
          {changes.map((change) => (
            <div key={change.label} className="contents">
              <dd>
                {change.label}: <span className="font-medium">{change.before}</span>
              </dd>
              <ArrowRight className="size-3.5 text-muted-foreground" aria-hidden />
              <dd>
                {change.label}: <span className="font-medium">{change.after}</span>
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
      <p className="mt-1.5">
        <span className="font-medium">Reason for review:</span> {reason}
      </p>
      <p>
        <span className="font-medium">What to do:</span> {todo}
      </p>
    </section>
  );
}

/** The decision the published version was approved with for this field: reusable when unchanged, else to revalidate. */
export function PreviousDecisionNote({ previous, field, baseline, onReuse, busy, canReuse }: { previous: PreviousDecision; field: string; baseline: Baseline; onReuse: () => void; busy: boolean; canReuse: boolean }) {
  const decided = decisionText(previous.decision.action, field, previous.decision.value);
  if (previous.state === "unchanged")
    return (
      <div className="rounded-md border border-border px-3 py-2 text-[13px]" role="region" aria-label="Previous decision">
        <p>
          <span className="font-medium">Decided before, in {versionName(baseline)}:</span> {decided}
          {previous.decision.rationale ? ` · “${previous.decision.rationale}”` : ""}
        </p>
        <p className="text-xs text-muted-foreground">The clause text and both readings are what was decided then, so the decision answers the same question.</p>
        {canReuse ? (
          <Button type="button" size="sm" variant="outline" className="mt-1" onClick={onReuse} disabled={busy}>
            Reuse this decision
          </Button>
        ) : null}
      </div>
    );
  return (
    <div className="rounded-md border border-status-review-border bg-status-review-soft px-3 py-2 text-[13px]" role="region" aria-label="Previous decision">
      <p className="font-medium text-status-review-text">Previous decision needs revalidation</p>
      <p>
        {versionName(baseline)} was approved with: {decided}
        {previous.decision.rationale ? ` · “${previous.decision.rationale}”` : ""}.
      </p>
      <p className="text-xs text-muted-foreground">
        Since then {previous.previous.clause_hash ? "the clause or its readings changed" : "the readings changed"}: then the {READING.regex.inSentence} found {itemsText(field, readingItems(field, previous.previous.regex_value))} and the {READING.llm.inSentence} {itemsText(field, readingItems(field, previous.previous.llm_value))}. Decide again for the current text: the earlier decision is not carried over.
      </p>
    </div>
  );
}

/**
 * The draft's rules that do not do what the current policy states. The reviewer can correct a limit or severity in
 * the draft: a new compilation of the same revisions, with the correction as a rule override the compiler validates
 * against the text. The published version never changes.
 */
export function RuleDiscrepancies({ extraction }: { extraction: ExtractionSummary }) {
  const discrepancies = extraction.discrepancies ?? [];
  if (!discrepancies.length) return null;
  const onlyNew = discrepancies.every((item) => item.field === "rule");
  return (
    <section aria-label="Compiled rules that need correction" className="space-y-2 rounded-lg border border-status-flag-border bg-card p-4">
      <h3 className="flex items-center gap-2 text-sm font-semibold">
        <Wrench className="size-4 text-status-flag-text" aria-hidden />
        {onlyNew ? (discrepancies.length === 1 ? "A new clause needs a rule" : "New clauses need rules") : "Compiled rule needs correction"}
      </h3>
      <p className="text-[13px] text-muted-foreground">
        {onlyNew
          ? "These revisions state a clause no executable rule implements, so no draft ruleset was built. Add its rule, or correct the document if the clause should not be there."
          : "The policy text these revisions state and the draft's executable rules disagree, so no draft ruleset was built. This is not a reviewer's error: the rule logic was written for an earlier text. Correct the draft rule to what the policy now states, add a rule a new clause needs, or correct the document if the text is wrong."}
      </p>
      {discrepancies.map((item) => (
        <Discrepancy key={`${item.rule_id}-${item.field}`} item={item} extraction={extraction} />
      ))}
    </section>
  );
}

function stated(item: RuleDiscrepancy): string {
  if (item.field === "thresholds") return itemsText("thresholds", readingItems("thresholds", item.stated));
  if (item.field === "severities") return (item.stated as string[]).join(", ") || "no severity";
  return item.stated ? "a review signal only" : "an exception";
}

/** A rule that cites words its clause no longer has: only the rule logic itself can be corrected, never by a reviewer. */
function StaleQuote({ item }: { item: RuleDiscrepancy }) {
  return (
    <div className="space-y-1.5 rounded-md border border-border px-3 py-2 text-[13px]" role="region" aria-label={`${item.rule_id} needs correction`}>
      <p>
        <span className="font-mono text-xs">{item.rule_id}</span> · {item.policy_doc}
      </p>
      <p>
        The compiled rule cites the policy as saying <span className="font-medium">“{String(item.compiled)}”</span>, which the current clause no longer contains:
      </p>
      <blockquote className="whitespace-pre-wrap border-l-2 border-border pl-3 text-foreground/90">{String(item.stated).replace(/\*\*/g, "")}</blockquote>
      <p className="text-xs text-status-flag-text">
        No review decision can resolve this. A developer updates {item.rule_id}'s source quote in the rule logic (rules/rule_logic.yaml) to words the clause states, then the documents are compiled again. Until then no draft ruleset can be built, so nothing here can be approved.
      </p>
    </div>
  );
}

function Discrepancy({ item, extraction }: { item: RuleDiscrepancy; extraction: ExtractionSummary }) {
  if (item.field === "source_quote") return <StaleQuote item={item} />;
  if (item.field === "rule") return <AddRule item={item} extraction={extraction} />;
  return <Correction item={item} extraction={extraction} />;
}

function Correction({ item, extraction }: { item: RuleDiscrepancy; extraction: ExtractionSummary }) {
  const create = useCreateExtraction();
  const navigate = useNavigate();
  const [actor] = useActor();
  const proposals = proposedParams(item);
  const [values, setValues] = useState<Record<string, string>>(() => Object.fromEntries(proposals.map((proposal) => [proposal.name, proposal.after?.value ?? ""])));
  const [severity, setSeverity] = useState(item.field === "severities" ? (item.stated as string[])[0] ?? "" : "");
  const [rationale, setRationale] = useState("");
  const compiled =
    item.field === "thresholds"
      ? Object.entries(item.compiled as Record<string, { value: string; unit: string }>).map(([name, limit]) => `${name.replace(/_(usd|eur)$/, "").replace(/_/g, " ")} ${itemsText("thresholds", readingItems("thresholds", [limit]))}`).join(", ")
      : item.field === "severities"
        ? (item.compiled as string[]).join(", ")
        : "a review signal only";
  const set: Record<string, unknown> | null =
    item.field === "thresholds"
      ? proposals.length && proposals.every((proposal) => /^\d+(\.\d+)?$/.test(values[proposal.name] ?? ""))
        ? { params: Object.fromEntries(proposals.map((proposal) => [proposal.name, { type: "number", value: values[proposal.name], unit: proposal.before.unit }])) }
        : null
      : item.field === "severities" && severity
        ? { severity }
        : null;
  const recompile = () => {
    if (!set || !rationale.trim() || !actor.trim()) return;
    const earlier = (extraction.rule_overrides ?? []).filter((override) => override.rule_id !== item.rule_id).map(({ rule_id, set: changes, rationale: why }) => ({ rule_id, set: changes, rationale: why }));
    const own = (extraction.rule_overrides ?? []).find((override) => override.rule_id === item.rule_id);
    const correction: RuleOverride = { rule_id: item.rule_id, set: own ? { ...own.set, ...set, ...(set.params ? { params: { ...((own.set.params as object) ?? {}), ...(set.params as object) } } : {}) } : set, rationale: rationale.trim() };
    const provenance = extraction.provenance;
    create.mutate(
      {
        documents: extraction.documents.map((document) => ({ document_id: document.document_id, revision: document.revision })),
        recording_set: provenance.recording_set,
        ...(provenance.recorded_with ? { recorded_with: provenance.recorded_with } : {}),
        actor: actor.trim(),
        rule_overrides: [...earlier, correction],
      },
      { onSuccess: (next) => navigate(`/documents/extractions/${encodeURIComponent(next.extraction_id)}?tab=review`) },
    );
  };
  return (
    <div className="space-y-2 rounded-md border border-border px-3 py-2 text-[13px]" role="region" aria-label={`${item.rule_id} needs correction`}>
      <p>
        <span className="font-mono text-xs">{item.rule_id}</span> · {item.policy_doc}
      </p>
      <dl className="grid gap-x-3 sm:grid-cols-[9rem_1fr]">
        <dt className="text-muted-foreground">The policy states</dt>
        <dd className="font-medium">{stated(item)}</dd>
        <dt className="text-muted-foreground">The draft rule checks</dt>
        <dd className="font-medium">{compiled}</dd>
      </dl>
      {item.field === "thresholds" && proposals.length ? (
        <fieldset className="space-y-1">
          <legend className="text-xs font-medium">Correct the draft rule</legend>
          {proposals.map((proposal) => (
            <label key={proposal.name} className="flex items-center gap-2 text-[13px]">
              <span className="w-40">{proposal.name.replace(/_(usd|eur)$/, "").replace(/_/g, " ")}: {proposal.before.value} →</span>
              <Input aria-label={`New ${proposal.name}`} className="h-8 w-28 text-[13px]" inputMode="decimal" value={values[proposal.name] ?? ""} onChange={(event) => setValues((all) => ({ ...all, [proposal.name]: event.target.value }))} />
              <span className="text-xs text-muted-foreground">{proposal.after ? "as the policy now states" : "the policy states no matching value: enter one it states"}</span>
            </label>
          ))}
        </fieldset>
      ) : item.field === "severities" ? (
        <label className="flex items-center gap-2">
          <span className="text-xs font-medium">Correct the severity to</span>
          <Input aria-label="New severity" className="h-8 w-32 text-[13px]" value={severity} onChange={(event) => setSeverity(event.target.value)} />
        </label>
      ) : (
        <p className="text-xs text-muted-foreground">This difference cannot be corrected here: the rule's outcome is changed by editing the rule logic.</p>
      )}
      {set ? (
        <>
          <Input aria-label="Why the draft rule changes" placeholder="Why the draft rule changes (required), such as the revision that changed the policy" className="h-9 text-[13px]" value={rationale} onChange={(event) => setRationale(event.target.value)} />
          <p className="text-xs text-muted-foreground">
            Compiles these same document revisions again, with this correction to the draft rule, as a new compilation to review. The compiler accepts it only if the rule then does what the policy states. Nothing published changes.
          </p>
          <Button type="button" size="sm" onClick={recompile} disabled={!rationale.trim() || !actor.trim() || create.isPending} title={actor.trim() ? undefined : "Enter your name under Acting as"}>
            {create.isPending ? <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden /> : null}
            Correct the draft rule and compile again
          </Button>
          {!actor.trim() ? <p className="text-xs text-status-review-text">Enter your name under “Acting as” to correct a rule.</p> : null}
        </>
      ) : null}
      {create.isError ? <ErrorState error={create.error} /> : null}
    </div>
  );
}
