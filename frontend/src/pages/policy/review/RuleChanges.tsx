/**
 * Changing the draft rules from a review: correcting a rule an issue shows is wrong, or adding the rule a new clause
 * needs. Either is a rule override, compiled again with the same document revisions as a new compilation to review:
 * the compiler builds and checks it like hand-written logic (a rule's limits and severity must be what its clause
 * states), and nothing published changes.
 */
import { Loader2, Plus, Wrench } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useCreateExtraction } from "@/api/queries";
import type { ExtractionSummary, RuleDiscrepancy, RuleLogic, RuleOverride } from "@/api/types";
import { ErrorState } from "@/components/states/states";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/misc";
import { SELECT } from "@/pages/evaluations/display";
import { useActor } from "../actor";
import { problemsOf, RuleEditor } from "../RuleEditor";
import type { ArtifactRule } from "../rules";

type Limit = { value: string; unit: string };

/** Compile the extraction's document revisions again with this rule change, then open the new compilation's review. */
export function useRecompile(extraction: ExtractionSummary) {
  const create = useCreateExtraction();
  const navigate = useNavigate();
  const [actor] = useActor();
  const recompile = (change: RuleOverride) => {
    const earlier = (extraction.rule_overrides ?? []).filter((item) => item.rule_id !== change.rule_id).map(({ rule_id, set, rationale, add }) => ({ rule_id, set, rationale, ...(add ? { add } : {}) }));
    const provenance = extraction.provenance;
    create.mutate(
      {
        documents: extraction.documents.map((document) => ({ document_id: document.document_id, revision: document.revision })),
        recording_set: provenance.recording_set,
        ...(provenance.recorded_with ? { recorded_with: provenance.recorded_with } : {}),
        actor: actor.trim(),
        rule_overrides: [...earlier, change],
      },
      { onSuccess: (next) => navigate(`/documents/extractions/${encodeURIComponent(next.extraction_id)}?tab=review`) },
    );
  };
  return { recompile, create, actor };
}

/** The clause's sentences, as plain text: a rule quotes one of them. */
export function sentencesOf(text: string): string[] {
  const plain = text.replace(/\*\*|__/g, "").replace(/\s+/g, " ").trim();
  return plain.split(/(?<=[.;:])\s+(?=[A-Z(])/).map((sentence) => sentence.replace(/[.;:]$/, "").trim()).filter((sentence) => sentence.length > 8);
}

/** A param name for each limit the clause states: "limit_usd", "min_count", "limit_2_usd" when two share a unit. */
export function paramsFor(limits: Limit[]): Record<string, { type: "number"; value: string; unit: string }> {
  const params: Record<string, { type: "number"; value: string; unit: string }> = {};
  for (const limit of limits) {
    const base = limit.unit === "count" ? "min_count" : `limit_${limit.unit}`;
    let name = base;
    for (let n = 2; name in params; n += 1) name = base.replace(/^(min|limit)_/, `$1_${n}_`);
    params[name] = { type: "number", value: limit.value, unit: limit.unit };
  }
  return params;
}

function Rationale({ value, onChange, label }: { value: string; onChange: (value: string) => void; label: string }) {
  return <Input aria-label={label} placeholder={`${label} (required)`} className="h-9 text-[13px]" value={value} onChange={(event) => onChange(event.target.value)} />;
}

function Submit({ disabled, pending, actor, children, onClick }: { disabled: boolean; pending: boolean; actor: string; children: React.ReactNode; onClick: () => void }) {
  return (
    <>
      <Button type="button" size="sm" onClick={onClick} disabled={disabled || !actor.trim() || pending} title={actor.trim() ? undefined : "Enter your name under Acting as"}>
        {pending ? <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden /> : null}
        {children}
      </Button>
      {!actor.trim() ? <p className="text-xs text-status-review-text">Enter your name under “Acting as” to change a rule.</p> : null}
    </>
  );
}

/** A clause the documents state under its own rule ID that no rule implements: write its rule here. */
export function AddRule({ item, extraction }: { item: RuleDiscrepancy; extraction: ExtractionSummary }) {
  const stated = item.stated as { title: string; text: string; severities: string[]; review_signal: boolean; thresholds: Limit[] };
  const sentences = sentencesOf(stated.text);
  const [quote, setQuote] = useState(sentences[0] ?? "");
  const [scope, setScope] = useState<"receipt" | "item">("receipt");
  const [set, setSet] = useState<Record<string, unknown>>({});
  const [rationale, setRationale] = useState("");
  const { recompile, create, actor } = useRecompile(extraction);
  const base: ArtifactRule = {
    rule_id: item.rule_id,
    kind: "check",
    title: stated.title,
    outcome: stated.review_signal ? "review_signal" : "exception",
    severity: stated.review_signal ? null : stated.severities[0] ?? null,
    scope,
    source_quote: quote,
    params: paramsFor(stated.thresholds),
    applies_when: null,
    ...(scope === "item" ? { select: null } : {}),
  } as ArtifactRule;
  const rule = { ...base, ...set } as ArtifactRule & Record<string, unknown>;
  const problems = Object.values(problemsOf(base, set));
  if (scope === "receipt" && !rule.applies_when) problems.push("Say when it applies: a whole-receipt rule needs at least one condition.");
  if (scope === "item" && !rule.select) problems.push("Say which lines it catches: a line rule needs at least one condition under “What it checks”.");
  if (rule.outcome === "exception" && !rule.severity) problems.push("An exception needs a severity.");
  const used = JSON.stringify([rule.applies_when, rule.select]);
  const unused = Object.keys((rule.params ?? {}) as object).filter((name) => !used.includes(`"param":"${name}"`));
  if (unused.length) problems.push(`Use each limit the policy states in a condition (compare a field with it): ${unused.join(", ")}.`);
  const add = () => {
    const whole = Object.fromEntries(Object.entries({ ...rule }).filter(([key, value]) => !["rule_id", "kind", ...(value === null || value === undefined ? [key] : [])].includes(key)));
    recompile({ rule_id: item.rule_id, add: true, set: whole, rationale: rationale.trim() });
  };
  return (
    <div className="space-y-3 rounded-md border border-border px-3 py-2 text-[13px]" role="region" aria-label={`${item.rule_id} needs a rule`}>
      <p>
        <span className="font-mono text-xs">{item.rule_id}</span> · {stated.title} · {item.policy_doc}
      </p>
      <p>The policy states this clause under its own rule ID, and no rule implements it, so no draft ruleset could be built:</p>
      <blockquote className="whitespace-pre-wrap border-l-2 border-border pl-3 text-foreground/90">{stated.text.replace(/\*\*/g, "")}</blockquote>
      <p className="text-xs text-muted-foreground">
        Add its rule below: the limits{stated.severities.length ? " and severity" : ""} the clause states are filled in, and the compiler refuses any it does not state. Or correct the document if the clause should not be there.
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="space-y-1">
          <span className="block text-xs font-medium">The policy's words it quotes</span>
          <select aria-label="Quoted words" className={SELECT} value={quote} onChange={(event) => setQuote(event.target.value)}>
            {sentences.map((sentence) => (
              <option key={sentence} value={sentence}>
                {sentence}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1">
          <span className="block text-xs font-medium">What it checks</span>
          <select aria-label="Rule scope" className={SELECT} value={scope} onChange={(event) => { setScope(event.target.value as "receipt" | "item"); setSet(({ select: _dropped, ...rest }) => rest); }}>
            <option value="receipt">The whole receipt</option>
            <option value="item">Each line</option>
          </select>
        </label>
      </div>
      <RuleEditor key={scope} rule={base} set={set} onChange={setSet} />
      {problems.length ? (
        <ul className="list-disc pl-5 text-xs text-status-review-text" aria-label="Still needed">
          {problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      ) : null}
      <Rationale label="Why this rule is added" value={rationale} onChange={setRationale} />
      <Submit disabled={Boolean(problems.length) || !rationale.trim()} pending={create.isPending} actor={actor} onClick={add}>
        <Plus aria-hidden /> Add {item.rule_id} and compile again
      </Submit>
      {create.isError ? <ErrorState error={create.error} /> : null}
    </div>
  );
}

/** Correct one draft rule from the issue that shows it wrong: only the fields edited are sent. */
export function RuleCorrection({ ruleId, logic, extraction, onCancel }: { ruleId: string; logic: RuleLogic; extraction: ExtractionSummary; onCancel: () => void }) {
  const own = (extraction.rule_overrides ?? []).find((item) => item.rule_id === ruleId);
  const [set, setSet] = useState<Record<string, unknown>>({});
  const [rationale, setRationale] = useState("");
  const { recompile, create, actor } = useRecompile(extraction);
  const rule = { ...(logic as unknown as ArtifactRule), rule_id: ruleId };
  const problems = Object.values(problemsOf(rule, set));
  const save = () => {
    const merged = own ? { ...own.set, ...set, ...(set.params ? { params: { ...((own.set.params as object) ?? {}), ...(set.params as object) } } : {}) } : set;
    recompile({ rule_id: ruleId, set: merged, rationale: rationale.trim(), ...(own?.add ? { add: true } : {}) });
  };
  return (
    <div className="space-y-3 rounded-md border border-border bg-background px-3 py-2 text-[13px]" role="region" aria-label={`Correct ${ruleId}`}>
      <p className="flex items-center gap-2 font-medium">
        <Wrench className="size-4 text-muted-foreground" aria-hidden /> Correct the draft rule {ruleId}
      </p>
      <p className="text-xs text-muted-foreground">Compiles the same document revisions again with this correction, as a new compilation to review. The compiler accepts it only if the rule then does what the policy states. Nothing published changes.</p>
      <RuleEditor rule={rule} set={set} onChange={setSet} />
      {problems.length ? <p className="text-xs text-status-review-text">{problems.join(" ")}</p> : null}
      <Rationale label="Why the rule changes" value={rationale} onChange={setRationale} />
      <div className="flex flex-wrap items-center gap-2">
        <Submit disabled={!Object.keys(set).length || Boolean(problems.length) || !rationale.trim()} pending={create.isPending} actor={actor} onClick={save}>
          Correct {ruleId} and compile again
        </Submit>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
      {create.isError ? <ErrorState error={create.error} /> : null}
    </div>
  );
}
