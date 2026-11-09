/**
 * One issue at a time, read top to bottom: what needs attention, why it was flagged, where it comes from, where it
 * applies, what it affects, and the decisions that fit this kind of issue. A decision is the core's ReviewDecision,
 * saved as the next stored review, which the compiler judges; the compiled rule is never rewritten by one, so each
 * choice says beforehand whether it matches the rule. A published or approved version's decisions are read-only.
 */
import { ArrowRight, CheckCircle2, ChevronLeft, ChevronRight, CircleAlert, Loader2, Quote } from "lucide-react";
import { useEffect, useMemo, useState, type KeyboardEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useComparisonSource, useExtractionBaseline, useReviewExtraction } from "@/api/queries";
import type { ChangeRequest, ExtractionReviewDetail, ExtractionSummary, FieldComparison, ReviewDecision, RuleLogic } from "@/api/types";
import { EmptyState, ErrorState } from "@/components/states/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Progress } from "@/components/ui/misc";
import { cn } from "@/lib/utils";
import { SELECT } from "@/pages/evaluations/display";
import { ActorField, useActor } from "../actor";
import { ChangeRequests } from "./ChangeRequests";
import { editorValue, FieldEditor, initialValue } from "./FieldEditor";
import {
  affectsExecution,
  attachIssues,
  changeTexts,
  decidedValue,
  decisionEffect,
  decisionText,
  FIELD_TEXT,
  FILTERS,
  headline,
  itemDiff,
  itemsText,
  KIND_TEXT,
  kindOf,
  matchesFilter,
  problemText,
  READING,
  readingItems,
  resolutionOf,
  issueState,
  STATE_TEXT,
  whyFlagged,
  type Effect,
  type Filter,
} from "./issues";
import { LimitUses } from "./LimitUses";
import { RuleCorrection } from "./RuleChanges";
import { RuleEffect } from "./RuleEffect";
import { SourcePanel } from "./SourcePanel";
import { BaselineSummary, PolicyChange, PreviousDecisionNote } from "./VersionPanels";

type Owner = { extractionId: string } | { rulesetId: string };
type Saved = Pick<ExtractionReviewDetail, "decisions" | "remaining" | "issues">;

/** One thing a reviewer can do about an issue. A choice saves a reading as it is; correct and reject need words. */
type Choice = { id: string; label: string; detail?: string; action: "accept_regex" | "accept_llm" | "override" | "reject" | "request"; rationale?: string };

interface Draft {
  choice: string | null;
  value: unknown;
  rationale: string;
}

/** The decisions that fit this kind of issue: never an action that cannot apply to it. */
export function choicesFor(comparison: FieldComparison, possibleLimits: readonly string[]): Choice[] {
  const kind = kindOf(comparison);
  const field = comparison.field;
  const show = (value: unknown) => itemsText(field, readingItems(field, value));
  const request: Choice = { id: "request", label: "Ask for clarification", detail: "Blocks approval until someone answers.", action: "request" };
  const reject: Choice = { id: "reject", label: "Reject the interpretation", detail: "Blocks approval until the policy or its rule is corrected and compiled again.", action: "reject" };
  if (kind === "unconfirmed") {
    const numbers = possibleLimits.join(", ") || "the number";
    return [
      { id: "accept_regex", label: "No limit", detail: `${possibleLimits.length ? numbers : "The number"} is not an amount, count or time the rule checks.`, action: "accept_regex", rationale: `Not a limit: ${numbers} in the clause is not an amount or count the rule checks.` },
      { id: "override", label: "It is a limit: enter it", action: "override" },
      request,
    ];
  }
  if (comparison.status === "only_regex") return [{ id: "accept_regex", label: "Keep the rule", detail: "The policy states it under its own heading.", action: "accept_regex" }, request, reject];
  if (comparison.status === "only_llm") return [{ id: "accept_regex", label: "Discard it", detail: "The policy has no such rule.", action: "accept_regex" }, request];
  const readings: Choice[] = (["accept_regex", "accept_llm"] as const)
    .filter((action) => (action === "accept_regex" ? comparison.regex_value : comparison.llm_value) !== null && (action === "accept_regex" ? comparison.regex_value : comparison.llm_value) !== undefined)
    .map((action) => ({ id: action, label: `Use: ${show(action === "accept_regex" ? comparison.regex_value : comparison.llm_value)}`, detail: `As the ${(action === "accept_regex" ? READING.regex : READING.llm).inSentence} has it.`, action }));
  return [...readings, { id: "override", label: "Neither: enter the correct value", action: "override" }, request, reject];
}

function Reading({ label, technical, items, field, differing }: { label: string; technical?: string; items: string[] | null; field: string; differing: Set<string> }) {
  return (
    <div className="min-w-0 rounded-md border border-border px-3 py-2">
      <p className="text-xs font-medium text-muted-foreground">
        {label} {technical ? <span className="font-normal text-subtle-foreground">· {technical}</span> : null}
      </p>
      {items === null || items.length === 0 ? (
        <p className="mt-0.5 text-[13px] italic text-muted-foreground">{itemsText(field, items)}</p>
      ) : (
        <ul className="mt-1 flex flex-wrap gap-1.5 text-[13px]">
          {items.map((item) => (
            <li key={item}>
              {differing.has(item) ? (
                <mark className="rounded bg-status-review-soft px-1 font-medium text-status-review-text ring-1 ring-status-review-border" data-differs>
                  {item}
                </mark>
              ) : (
                <span className="rounded bg-muted px-1">{item}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Step({ title, children, className }: { title: string; children: ReactNode; className?: string }) {
  return (
    <section aria-label={title} className={cn("space-y-1.5", className)}>
      <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{title}</h4>
      {children}
    </section>
  );
}

function EffectNote({ effect }: { effect: Effect }) {
  return (
    <p className={cn("text-xs", effect.fits === false ? "text-status-flag-text" : "text-muted-foreground")} data-fits={String(effect.fits)}>
      {effect.text}
    </p>
  );
}

export interface ReviewStage {
  /** Where the latest review stands for approval, and the link that takes it further. */
  approvalHref?: string;
  /** The ruleset versions made from this compilation. */
  versions?: { ruleset_id: string; name: string; version: number; status: string }[];
  /** Why no draft ruleset could be built, whatever the review decides: then nothing here can be approved. */
  blocked?: string;
}

export function IssueReview({ owner, extraction, comparisons, ruleTitles, ruleLogic = {}, saved, readOnly, readOnlyReason, requests = [], extractionSha, initialId, initialDocument, stage }: {
  owner: Owner;
  /** The compilation under review, so a draft rule can be corrected from an issue and compiled again. */
  extraction?: ExtractionSummary;
  comparisons: readonly FieldComparison[];
  ruleTitles: Record<string, string>;
  /** What each rule under review does, and each rule it replaces or is replaced by. */
  ruleLogic?: Record<string, RuleLogic>;
  /** The latest stored review: its decisions, what remains, and the compiler's issues with it. */
  saved: Saved | undefined;
  readOnly: boolean;
  readOnlyReason?: string;
  requests?: readonly ChangeRequest[];
  extractionSha?: string;
  /** The issue to open first, such as one an approval blocker links to. */
  initialId?: string | null;
  /** The policy document to show the issues of, such as one a document page links from. */
  initialDocument?: string | null;
  stage?: ReviewStage;
}) {
  const review = useReviewExtraction();
  const [actor, setActor] = useActor();
  const opening = initialId ? comparisons.find((item) => item.comparison_id === initialId) : undefined;
  const [filter, setFilter] = useState<Filter>(() => (opening && !opening.review_required ? "all" : readOnly ? "decided" : "open"));
  const [documentName, setDocumentName] = useState(initialDocument ?? "");
  const [currentId, setCurrentId] = useState<string | null>(opening?.comparison_id ?? null);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [changing, setChanging] = useState<string | null>(null);
  const [correcting, setCorrecting] = useState<string | null>(null);  // the rule whose correction is open
  const [feedback, setFeedback] = useState<{ comparisonId: string; ok: boolean; text: string[]; effect?: Effect } | null>(null);
  const stored: Saved | undefined = review.data ?? saved;
  const latest: Saved | undefined = stored ? { ...stored, issues: attachIssues(stored.issues, comparisons, stored.decisions) } : undefined;
  const who = actor.trim();

  const baseline = useExtractionBaseline("extractionId" in owner && !readOnly ? owner.extractionId : undefined).data;
  const previousOf = new Map((baseline?.decisions ?? []).map((item) => [item.comparison_id, item]));
  const ruleChanged = (ruleId: string) => changeTexts(ruleId, baseline).length > 0 || (baseline?.changed_clauses ?? []).some((item) => item.rule_id === ruleId);
  const resolutions = useMemo(() => new Map(comparisons.map((item) => [item.comparison_id, resolutionOf(item, latest)])), [comparisons, latest]);
  const inDocument = comparisons.filter((item) => !documentName || item.policy_doc === documentName);
  const counts = Object.fromEntries(FILTERS.map((item) => [item.id, inDocument.filter((comparison) => matchesFilter(item.id, resolutions.get(comparison.comparison_id)!)).length])) as Record<Filter, number>;
  const shown = (filter === "all" ? inDocument : inDocument.filter((item) => item.review_required)).filter((item) => matchesFilter(filter, resolutions.get(item.comparison_id)!));
  const required = comparisons.filter((item) => item.review_required);
  const resolved = required.filter((item) => resolutions.get(item.comparison_id) === "resolved").length;
  const problems = required.filter((item) => resolutions.get(item.comparison_id) === "has_issue").length;
  const general = (latest?.issues ?? []).filter((item) => item.level === "error" && !item.comparison_id);
  const documents = [...new Set(required.map((item) => item.policy_doc))].sort();
  // The issue just decided stays in view, with its outcome, even when the filter no longer lists it.
  const current = (currentId ? comparisons.find((item) => item.comparison_id === currentId && (!documentName || item.policy_doc === documentName)) : undefined) ?? shown[0];
  const index = current ? shown.indexOf(current) : -1;
  useEffect(() => {
    if (current && current.comparison_id !== currentId) setCurrentId(current.comparison_id);
  }, [current, currentId]);

  const source = useComparisonSource(current ? owner : undefined, current?.comparison_id);
  const possibleLimits = current?.status === "unconfirmed" ? [...new Set((source.data?.highlights ?? []).filter((item) => item.side === "clause").map((item) => item.text))] : [];

  const open = (id: string) => {
    setCurrentId(id);
    setFeedback(null);
    setChanging(null);
    setCorrecting(null);
  };
  const go = (step: number) => {
    const next = index >= 0 ? shown[index + step] : shown[0];
    if (next) open(next.comparison_id);
  };
  const onKey = (event: KeyboardEvent) => {
    const tag = (event.target as HTMLElement).tagName;
    if (["INPUT", "TEXTAREA", "SELECT"].includes(tag)) return;
    if (event.key === "ArrowRight") go(1);
    if (event.key === "ArrowLeft") go(-1);
  };

  const savedDecision = current ? latest?.decisions.find((item) => item.comparison_id === current.comparison_id) : undefined;
  const resolution = current ? resolutions.get(current.comparison_id)! : "not_required";
  const choices = current ? choicesFor(current, possibleLimits) : [];
  const draft: Draft | null = current ? drafts[current.comparison_id] ?? { choice: null, value: initialValue(current.field, current.llm_value ?? current.regex_value), rationale: "" } : null;
  const setDraft = (change: Partial<Draft>) => current && setDrafts((all) => ({ ...all, [current.comparison_id]: { ...draft!, ...change } }));
  const chosen = choices.find((item) => item.id === draft?.choice);
  const logic = current ? ruleLogic[current.rule_id] : undefined;
  const mentioned = current ? (() => {
    const references = comparisons.find((item) => item.rule_id === current.rule_id && item.policy_doc === current.policy_doc && item.field === "references");
    const value = references?.agreed_value ?? references?.regex_value;
    return Array.isArray(value) ? (value as string[]) : [];
  })() : [];

  /** The value a choice gives the field, or why it cannot be saved yet. */
  function valueOf(comparison: FieldComparison, choice: Choice, value: Draft): { value?: unknown; problem?: string } {
    if (choice.action === "accept_regex") return { value: comparison.regex_value };
    if (choice.action === "accept_llm") return { value: comparison.llm_value };
    if (choice.action === "override") return editorValue(comparison.field, value.value);
    return {};
  }

  function decisionFor(comparison: FieldComparison, choice: Choice, value: Draft): { decision?: ReviewDecision; problem?: string } {
    const rationale = value.rationale.trim() || choice.rationale || "";
    const base = { comparison_id: comparison.comparison_id, extraction_sha256: comparison.extraction_sha256, reviewer: who };
    if (choice.action === "accept_regex" || choice.action === "accept_llm") return { decision: { ...base, action: choice.action, ...(rationale ? { rationale } : {}) } };
    if (!rationale) return { problem: choice.action === "reject" ? "Say what is wrong: a rejection needs a reason." : "Say why this is the correct value: a correction needs a reason." };
    if (choice.action === "reject") return { decision: { ...base, action: "reject", rationale } };
    const corrected = editorValue(comparison.field, value.value);
    if (corrected.problem) return { problem: corrected.problem };
    return { decision: { ...base, action: "override", value: corrected.value as ReviewDecision["value"], rationale } };
  }

  const nextOpen = (after: FieldComparison, result: Saved): FieldComparison | undefined => {
    const order = [...inDocument.filter((item) => item.review_required)];
    const start = order.indexOf(after);
    return [...order.slice(start + 1), ...order.slice(0, start)].find((item) => item !== after && ["open", "has_issue"].includes(resolutionOf(item, result)));
  };

  /** Save these decisions as the next review, in place of any earlier decision on the same fields. */
  function submit(decisions: ReviewDecision[], onDone: (result: Saved) => void) {
    if (!("extractionId" in owner) || !extractionSha) return;
    const replaced = new Set(decisions.map((item) => item.comparison_id));
    const others = (latest?.decisions ?? []).filter((item) => !replaced.has(item.comparison_id)) as unknown as ReviewDecision[];
    review.mutate({ extractionId: owner.extractionId, body: { actor: who, extraction_sha256: extractionSha, decisions: [...others, ...decisions] } }, { onSuccess: onDone });
  }

  /** An earlier decision whose question is unchanged, saved again for this compilation under this reviewer's name. */
  const reused = (comparison: FieldComparison): ReviewDecision | undefined => {
    const previous = previousOf.get(comparison.comparison_id);
    if (previous?.state !== "unchanged" || !baseline?.published) return undefined;
    const { action, value, rationale } = previous.decision;
    const why = `Reused from ${baseline.published.name} v${baseline.published.version}: same clause and readings${rationale ? `. ${rationale}` : ""}`;
    return { comparison_id: comparison.comparison_id, extraction_sha256: comparison.extraction_sha256, reviewer: who, action, ...(action === "override" ? { value } : {}), rationale: why };
  };
  const reusable = required.filter((item) => !latest?.decisions.some((decision) => decision.comparison_id === item.comparison_id) && reused(item));

  /**
   * The ways out of a decision the compiler refuses, in one click where the fix is certain: the reading that states
   * what the policy does, the decision an earlier version made on the same question, or a correction of the value;
   * and, where the compiled rule is what disagrees, a correction of that rule.
   */
  function IssueFixes() {
    if (!current || readOnly) return <p className="mt-1 text-xs text-muted-foreground">Choose another decision below to fix it.</p>;
    const codes = new Set((latest?.issues ?? []).filter((item) => item.level === "error" && item.comparison_id === current.comparison_id).map((item) => item.code));
    const policyChoice = choices.find((choice) => choice.action === "accept_regex");
    const matchesPolicy = codes.has("interpretation_mismatch") && policyChoice && savedDecision?.action !== "accept_regex" && current.regex_value !== null && current.regex_value !== undefined;
    const previous = previousOf.get(current.comparison_id);
    const reusePrevious = previous?.state === "unchanged" && savedDecision && !sameDecision(savedDecision, previous.decision) ? reused(current) : undefined;
    const busy = !who || review.isPending;
    const canCorrectRule = Boolean(extraction && logic && affectsExecution(current.field) && current.field !== "rule");
    return (
      <div className="mt-2 flex flex-wrap items-center gap-2" aria-label="Ways to fix it">
        {matchesPolicy ? (
          <Button type="button" size="sm" onClick={() => saveChoice(policyChoice, { choice: policyChoice.id, value: null, rationale: "As the policy text states" })} disabled={busy} title={who ? undefined : "Say who is reviewing"}>
            Use {itemsText(current.field, readingItems(current.field, current.regex_value))}: what the policy states
          </Button>
        ) : null}
        {reusePrevious && baseline?.published ? (
          <Button type="button" size="sm" variant="outline" onClick={() => reuse([current])} disabled={busy}>
            Reuse the decision from {baseline.published.name} v{baseline.published.version}
          </Button>
        ) : null}
        {choices.some((choice) => choice.action === "override") ? (
          <Button type="button" size="sm" variant="outline" onClick={() => { setChanging(current.comparison_id); setDraft({ choice: "override", value: initialValue(current.field, current.regex_value ?? current.llm_value) }); setFeedback(null); }}>
            Enter the correct value
          </Button>
        ) : null}
        {canCorrectRule && codes.has("executable_conflict") ? (
          <Button type="button" size="sm" variant="outline" onClick={() => setCorrecting(current.rule_id)}>
            Correct the draft rule {current.rule_id}
          </Button>
        ) : null}
        {!who ? <span className="text-xs text-status-review-text">Enter your name under “Acting as” to save a fix.</span> : null}
      </div>
    );
  }

  function reuse(items: FieldComparison[]) {
    const decisions = items.map(reused).filter(Boolean) as ReviewDecision[];
    if (!decisions.length || !current) return;
    const at = current;
    submit(decisions, (result) => {
      const left = required.filter((item) => ["open", "has_issue"].includes(resolutionOf(item, { ...result, issues: attachIssues(result.issues, comparisons, result.decisions) }))).length;
      setFeedback({ comparisonId: at.comparison_id, ok: true, text: [`${decisions.length === 1 ? "The earlier decision was" : `${decisions.length} earlier decisions were`} saved again for this compilation, under your name.`, left ? `${left} ${left === 1 ? "issue still needs" : "issues still need"} a decision.` : "Every issue is decided: the review can now be approved."] });
    });
  }

  function save() {
    if (current && draft && chosen) saveChoice(chosen, draft);
  }

  /** Save one choice for the current issue: from the form, or in one click from the issue's own fixes. */
  function saveChoice(chosen: Choice, draft: Draft) {
    if (!current) return;
    const { decision, problem } = decisionFor(current, chosen, draft);
    if (!decision) {
      setFeedback({ comparisonId: current.comparison_id, ok: false, text: [problem!] });
      return;
    }
    const effect = chosen.action === "reject" ? undefined : decisionEffect(current, logic, valueOf(current, chosen, draft).value);
    const decided = current;
    submit([decision], (result) => {
          const attached = { ...result, issues: attachIssues(result.issues, comparisons, result.decisions) };
          const state = resolutionOf(decided, attached);
          const left = required.filter((item) => ["open", "has_issue"].includes(resolutionOf(item, attached))).length;
          setChanging(null);
          setDrafts((all) => ({ ...all, [decided.comparison_id]: { ...(all[decided.comparison_id] ?? { value: initialValue(decided.field, decided.llm_value ?? decided.regex_value), rationale: "" }), choice: null } }));  // a one-click fix has no draft yet
          if (state === "resolved")
            setFeedback({ comparisonId: decided.comparison_id, ok: true, effect, text: [`Decision recorded: ${decisionText(decision.action, decided.field, decision.value)}.`, left ? `${left} ${left === 1 ? "issue still needs" : "issues still need"} a decision.` : "Every issue is decided: the review can now be approved."] });
          else
            setFeedback({ comparisonId: decided.comparison_id, ok: false, text: [state === "has_issue" ? "Decision recorded, but it has a problem: see above." : "Saved, but the compiler still counts this field as undecided."] });
    });
  }

  const nextAfterSave = current && latest ? nextOpen(current, latest) : undefined;

  return (
    <div className="space-y-4 outline-none" onKeyDown={onKey} tabIndex={-1}>
      <section aria-label="Review status" className="space-y-3 rounded-lg border border-border bg-card p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <ol className="grid flex-1 gap-2 text-[13px] sm:grid-cols-4">
            <li>
              <p className="text-xs text-muted-foreground">1 · Decisions recorded</p>
              <p className="font-semibold" aria-live="polite">
                {resolved} of {required.length}
              </p>
            </li>
            <li>
              <p className="text-xs text-muted-foreground">2 · Problems with decisions</p>
              <p className={cn("font-semibold", problems + general.length ? "text-status-flag-text" : "")}>{problems + general.length || "None"}</p>
            </li>
            <li>
              <p className="text-xs text-muted-foreground">3 · Ready for approval</p>
              <p className="font-semibold">
                {readOnly ? "—" : stage?.blocked ? <span className="text-status-flag-text">No: {stage.blocked}</span> : resolved === required.length && !problems && !general.length ? (stage?.approvalHref ? <Link className="text-primary hover:underline" to={stage.approvalHref}>Yes: make a draft version</Link> : "Yes") : "Not yet"}
              </p>
            </li>
            <li>
              <p className="text-xs text-muted-foreground">4 · Ruleset version</p>
              <p className="font-semibold">
                {stage?.versions?.length
                  ? stage.versions.map((version) => (
                      <Link key={version.ruleset_id} className="mr-2 text-primary hover:underline" to={`/policies/${encodeURIComponent(version.ruleset_id)}`}>
                        {version.name} v{version.version} · {version.status.replace("_", " ")}
                      </Link>
                    ))
                  : readOnly ? "This version" : "None yet"}
              </p>
            </li>
          </ol>
          {!readOnly ? (
            <div className="w-52">
              <ActorField actor={actor} setActor={setActor} id="issue-actor" />
            </div>
          ) : null}
        </div>
        <Progress value={required.length ? (resolved / required.length) * 100 : 100} label="Review progress" />
        <p className="text-xs text-muted-foreground">
          Recording a decision does not change a compiled rule or publish anything. A version made from a ready review goes through approval, and only publishing it changes how receipts are checked.
        </p>
        {readOnly && readOnlyReason ? (
          <p className="text-xs text-muted-foreground">
            <Badge variant="outline">Read only</Badge> {readOnlyReason}
          </p>
        ) : null}
        {baseline && !readOnly ? <BaselineSummary baseline={baseline} titles={ruleTitles} reusable={reusable.length} onReuse={() => reuse(reusable)} busy={!who || review.isPending} /> : null}
        {general.length ? (
          <div role="alert" className="rounded-md border border-status-flag-border bg-status-flag-soft px-3 py-2 text-[13px]">
            <p className="font-semibold text-status-flag-text">Problems not tied to one issue</p>
            <ul className="list-disc pl-5">
              {general.map((item) => (
                <li key={item.message}>{item.message}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </section>

      <div className="flex flex-wrap items-center gap-2">
        <div role="radiogroup" aria-label="Show" className="flex flex-wrap gap-1.5">
          {FILTERS.map((item) => (
            <button
              key={item.id}
              type="button"
              role="radio"
              aria-checked={filter === item.id}
              onClick={() => {
                setFilter(item.id);
                setCurrentId(null);
                setFeedback(null);
              }}
              className={cn("rounded-full border px-3 py-1 text-xs font-medium", filter === item.id ? "border-primary bg-primary-soft text-primary" : "border-border text-muted-foreground hover:bg-muted")}
            >
              {item.label} · {counts[item.id]}
            </button>
          ))}
        </div>
        {documents.length > 1 ? (
          <select aria-label="Document" className={cn(SELECT, "ml-auto w-full sm:w-72")} value={documentName} onChange={(event) => { setDocumentName(event.target.value); setCurrentId(null); setFeedback(null); }}>
            <option value="">All documents</option>
            {documents.map((name) => (
              <option key={name} value={name}>
                {name} · {required.filter((item) => item.policy_doc === name && ["open", "has_issue"].includes(resolutions.get(item.comparison_id)!)).length} to decide
              </option>
            ))}
          </select>
        ) : null}
      </div>

      {filter !== "all" && shown.length > 1 && shown.length <= 40 ? (
        <nav aria-label="Issues" className="flex flex-wrap gap-1.5">
          {shown.map((item) => {
            const state = resolutions.get(item.comparison_id)!;
            return (
              <button key={item.comparison_id} type="button" onClick={() => open(item.comparison_id)} aria-current={item === current ? "true" : undefined}
                className={cn("rounded-md border px-2 py-1 text-xs", item === current ? "border-primary bg-primary-soft" : "border-border hover:bg-muted", state === "has_issue" && "text-status-flag-text")}>
                <span className="font-mono">{item.rule_id}</span> · {FIELD_TEXT[item.field] ?? item.field}
              </button>
            );
          })}
        </nav>
      ) : null}

      {!current ? (
        <EmptyState
          title={filter === "open" ? "Nothing left to decide" : filter === "decided" ? "No decisions yet" : "No fields"}
          description={filter === "open" ? (required.length ? "Every issue has a decision without a problem. Review them under Decided, or continue to approval." : "Both readings agree on every field: nothing needs a decision.") : "Choose another filter to see more."}
          action={filter === "open" && stage?.approvalHref && required.length ? <Button asChild size="sm"><Link to={stage.approvalHref}>Continue to approval</Link></Button> : undefined}
          className="py-8"
        />
      ) : (
        <article className="space-y-4 rounded-lg border border-border bg-card p-4" aria-label={`${current.rule_id} ${FIELD_TEXT[current.field] ?? current.field}`}>
          <header className="flex flex-wrap items-start gap-3">
            <div className="min-w-0 flex-1">
              <p className="text-xs text-muted-foreground">
                <span className="font-mono">{current.rule_id}</span> · {current.policy_doc} · {FIELD_TEXT[current.field] ?? current.field}
              </p>
              <h3 className="text-base font-semibold leading-6">{ruleTitles[current.rule_id] ?? current.rule_id}</h3>
            </div>
            <div className="flex flex-col items-end gap-1">
              {(() => {
                const state = STATE_TEXT[issueState(current, resolution, latest?.issues ?? [], previousOf.get(current.comparison_id), ruleChanged(current.rule_id))];
                return <Badge variant={state.variant}>{state.label}</Badge>;
              })()}
              <span className="text-xs text-muted-foreground">{KIND_TEXT[kindOf(current)]}</span>
            </div>
          </header>

          <Step title="What needs attention">
            <p className="text-[15px] font-medium">{headline(current)}</p>
          </Step>
          <Step title="Why it was flagged">
            <p className="text-[13px]">{whyFlagged(current, possibleLimits)}</p>
            {(() => {
              if (current.field === "rule" || kindOf(current) === "unconfirmed") return null;
              const left = readingItems(current.field, current.regex_value);
              const right = readingItems(current.field, current.llm_value);
              const diff = kindOf(current) === "agreed" ? { onlyLeft: new Set<string>(), onlyRight: new Set<string>() } : itemDiff(left, right);
              return (
                <div className="grid gap-2 sm:grid-cols-2" role="region" aria-label="Interpretations">
                  <Reading label={READING.regex.name} items={left} field={current.field} differing={diff.onlyLeft} />
                  <Reading label={READING.llm.name} items={right} field={current.field} differing={diff.onlyRight} />
                </div>
              );
            })()}
          </Step>

          {baseline?.published && ruleChanged(current.rule_id) ? <PolicyChange ruleId={current.rule_id} baseline={baseline} changes={changeTexts(current.rule_id, baseline)} /> : null}
          {baseline && previousOf.get(current.comparison_id) && resolution !== "resolved" ? (
            <PreviousDecisionNote previous={previousOf.get(current.comparison_id)!} field={current.field} baseline={baseline} canReuse={!readOnly && !savedDecision} busy={!who || review.isPending} onReuse={() => reuse([current])} />
          ) : null}
          {latest?.issues.some((item) => item.level === "error" && item.comparison_id === current.comparison_id) ? (
            <div role="alert" className="rounded-md border border-status-flag-border bg-status-flag-soft px-3 py-2 text-[13px]">
              <p className="font-semibold text-status-flag-text">{STATE_TEXT[issueState(current, resolution, latest.issues, undefined, false)].label}</p>
              <ul className="list-disc pl-5">
                {latest.issues
                  .filter((item) => item.level === "error" && item.comparison_id === current.comparison_id)
                  .map((item) => (
                    <li key={item.message}>{problemText(item, current, logic, decidedValue(current, savedDecision))}</li>
                  ))}
              </ul>
              <IssueFixes />
            </div>
          ) : null}
          {correcting === current.rule_id && extraction && logic ? <RuleCorrection ruleId={current.rule_id} logic={logic} extraction={extraction} onCancel={() => setCorrecting(null)} /> : null}

          <div className="grid gap-4 lg:grid-cols-2">
            <Step title="Where it comes from" className="order-2 min-w-0 lg:order-1">
              <SourcePanel owner={owner} comparisonId={current.comparison_id} evidence={current.evidence} />
            </Step>
            <div className="order-1 min-w-0 space-y-4 lg:order-2">
              <RuleEffect ruleId={current.rule_id} section={current.location?.section} logic={ruleLogic} titles={ruleTitles} mentioned={mentioned} />
              {current.field === "thresholds" ? (
                <LimitUses ruleId={current.rule_id} logic={logic} readings={[current.regex_value, current.llm_value, savedDecision?.action === "override" ? savedDecision.value : null]} clause={current.evidence} />
              ) : null}

              <Step title="Your decision">
                {savedDecision ? (
                  <div className="rounded-md border border-border px-3 py-2 text-[13px]" aria-label="Saved decision">
                    <p className="font-medium">{decisionText(String(savedDecision.action), current.field, savedDecision.value)}</p>
                    <p className="text-xs text-muted-foreground">
                      by {String(savedDecision.reviewer)}
                      {savedDecision.rationale ? ` · “${String(savedDecision.rationale)}”` : ""}
                    </p>
                    {!readOnly && current.review_required && changing !== current.comparison_id ? (
                      <Button type="button" variant="link" size="sm" className="h-auto px-0" onClick={() => { setChanging(current.comparison_id); setFeedback(null); }}>
                        Change this decision
                      </Button>
                    ) : null}
                  </div>
                ) : readOnly || !current.review_required ? (
                  <p className="text-[13px] text-muted-foreground">{current.review_required ? "No decision was recorded for this field." : "No decision needed: both readings agree."}</p>
                ) : null}

                {!readOnly && current.review_required && (!savedDecision || changing === current.comparison_id || resolution === "has_issue") ? (
                  <div className="space-y-2">
                    <div role="radiogroup" aria-label="Decision" className="space-y-1.5">
                      {choices.map((choice) => {
                        const value = valueOf(current, choice, draft!).value;
                        const effect = choice.action === "accept_regex" || choice.action === "accept_llm" ? decisionEffect(current, logic, value) : null;
                        return (
                          <label key={choice.id} className={cn("flex cursor-pointer gap-2 rounded-md border px-2.5 py-1.5 text-[13px]", draft!.choice === choice.id ? "border-primary bg-primary-soft/40" : "border-border hover:bg-muted/50")}>
                            <input type="radio" name={`${current.comparison_id}-choice`} className="mt-1 size-3.5 accent-primary" checked={draft!.choice === choice.id} onChange={() => { setDraft({ choice: choice.id }); setFeedback(null); }} />
                            <span className="min-w-0">
                              <span className="font-medium">{choice.label}</span>
                              {choice.detail ? <span className="block text-xs text-muted-foreground">{choice.detail}</span> : null}
                              {effect && effect.fits === false ? <span className="block text-xs text-status-flag-text">{effect.text}</span> : null}
                            </span>
                          </label>
                        );
                      })}
                    </div>
                    {extraction && logic && affectsExecution(current.field) && current.field !== "rule" && correcting !== current.rule_id ? (
                      <p className="text-xs text-muted-foreground">
                        The compiled rule itself is wrong?{" "}
                        <Button type="button" variant="link" size="sm" className="h-auto px-0 text-xs" onClick={() => setCorrecting(current.rule_id)}>
                          Correct the draft rule {current.rule_id}
                        </Button>
                      </p>
                    ) : null}
                    {chosen?.action === "override" ? <FieldEditor id={current.comparison_id} field={current.field} value={draft!.value} onChange={(value) => setDraft({ value })} /> : null}
                    {chosen?.action === "request" && "extractionId" in owner ? <ChangeRequests extractionId={owner.extractionId} comparison={current} requests={requests.filter((item) => item.comparison_id === current.comparison_id)} actor={who} startOpen /> : null}
                    {chosen && chosen.action !== "request" ? (
                      <div className="space-y-1">
                        <label htmlFor={`${current.comparison_id}-rationale`} className="block text-xs font-medium">
                          Reason{chosen.action === "override" || chosen.action === "reject" ? " (required)" : " (optional)"}
                        </label>
                        <Input id={`${current.comparison_id}-rationale`} className="h-9 text-[13px]" value={draft!.rationale} placeholder={chosen.rationale} onChange={(event) => setDraft({ rationale: event.target.value })} />
                      </div>
                    ) : null}
                    {chosen && chosen.action !== "request" && chosen.action !== "reject" ? (() => {
                      const { value, problem } = valueOf(current, chosen, draft!);
                      if (problem) return null;
                      return (
                        <div className="rounded-md border border-border bg-background px-3 py-2 text-[13px]" aria-label="What will change">
                          <p>
                            <span className="font-medium">Will record for {current.rule_id} · {FIELD_TEXT[current.field] ?? current.field}:</span> {savedDecision ? <><del className="text-status-flag-text">{decisionText(String(savedDecision.action), current.field, savedDecision.value)}</del> →</> : null} <ins className="font-medium text-status-pass no-underline">{itemsText(current.field, readingItems(current.field, value))}</ins>
                          </p>
                          <EffectNote effect={decisionEffect(current, logic, value)} />
                        </div>
                      );
                    })() : null}
                    {chosen && chosen.action !== "request" ? (
                      <Button type="button" onClick={save} disabled={!who || review.isPending} title={who ? undefined : "Say who is reviewing"}>
                        {review.isPending ? <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden /> : null}
                        Save decision
                      </Button>
                    ) : null}
                    {!who ? <p className="text-xs text-status-review-text">Enter your name under “Acting as” above to save decisions.</p> : null}
                    {requests.some((item) => item.comparison_id === current.comparison_id && item.status === "open") && chosen?.action !== "request" ? (
                      <p className="text-xs text-status-flag-text">A clarification request on this field is open: it blocks approval until it is answered.</p>
                    ) : null}
                  </div>
                ) : null}
              </Step>

              {feedback && feedback.comparisonId === current.comparison_id ? (
                <div role={feedback.ok ? "status" : "alert"} className={cn("space-y-1.5 rounded-md border px-3 py-2 text-[13px]", feedback.ok ? "border-status-pass-border bg-status-pass-soft" : "border-status-flag-border bg-status-flag-soft")}>
                  <div className="flex gap-2">
                    {feedback.ok ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-status-pass" aria-hidden /> : <CircleAlert className="mt-0.5 size-4 shrink-0 text-status-flag-text" aria-hidden />}
                    <ul>
                      {feedback.text.map((line) => (
                        <li key={line}>{line}</li>
                      ))}
                    </ul>
                  </div>
                  {feedback.effect ? <EffectNote effect={feedback.effect} /> : null}
                  {feedback.ok && nextAfterSave ? (
                    <Button type="button" size="sm" onClick={() => open(nextAfterSave.comparison_id)}>
                      Next issue: {nextAfterSave.rule_id} · {FIELD_TEXT[nextAfterSave.field] ?? nextAfterSave.field}
                      <ArrowRight aria-hidden />
                    </Button>
                  ) : feedback.ok && stage?.approvalHref ? (
                    <Button asChild size="sm">
                      <Link to={stage.approvalHref}>
                        Continue to approval
                        <ArrowRight aria-hidden />
                      </Link>
                    </Button>
                  ) : null}
                </div>
              ) : null}
              {review.isError ? <ErrorState error={review.error} /> : null}
            </div>
          </div>

          {current.evidence ? (
            <details className="text-[13px]">
              <summary className="cursor-pointer text-xs font-medium text-muted-foreground">Original policy statement</summary>
              <blockquote className="mt-1 flex gap-2 whitespace-pre-wrap border-l-2 border-border pl-3 text-foreground/90">
                <Quote className="mt-0.5 size-3 shrink-0 text-subtle-foreground" aria-hidden />
                {current.evidence}
              </blockquote>
            </details>
          ) : null}
          <details className="text-xs text-muted-foreground">
            <summary className="cursor-pointer font-medium">Technical details</summary>
            <dl className="mt-1 grid gap-x-3 gap-y-0.5 sm:grid-cols-[10rem_1fr]">
              <dt>Field</dt>
              <dd className="font-mono">{current.field}</dd>
              <dt>Comparison status</dt>
              <dd className="font-mono">{current.status}</dd>
              <dt>{READING.regex.name} ({READING.regex.technical})</dt>
              <dd className="break-all font-mono">{JSON.stringify(current.regex_value)}</dd>
              <dt>{READING.llm.name} ({READING.llm.technical})</dt>
              <dd className="break-all font-mono">{JSON.stringify(current.llm_value)}</dd>
              <dt>Comparison</dt>
              <dd className="break-all font-mono">{current.comparison_id}</dd>
              {(latest?.issues ?? []).filter((item) => item.comparison_id === current.comparison_id).map((item) => (
                <div key={item.message} className="contents">
                  <dt>Compiler {item.level}</dt>
                  <dd className="font-mono">{item.code}: {item.message}</dd>
                </div>
              ))}
            </dl>
          </details>

          <footer className="flex items-center gap-2 border-t border-border pt-3">
            <Button type="button" variant="outline" size="sm" onClick={() => go(-1)} disabled={index <= 0}>
              <ChevronLeft aria-hidden />
              Previous
            </Button>
            <span className="flex-1 text-center text-xs text-muted-foreground">
              {index >= 0 ? `Issue ${index + 1} of ${shown.length}` : `${shown.length} ${FILTERS.find((item) => item.id === filter)!.label.toLowerCase()}`} <span className="hidden sm:inline">· use ← → to move</span>
            </span>
            <Button type="button" variant="outline" size="sm" onClick={() => go(1)} disabled={index >= shown.length - 1 && index >= 0}>
              Next
              <ChevronRight aria-hidden />
            </Button>
          </footer>
        </article>
      )}
    </div>
  );
}


/** Whether two decisions give the same answer: the same action, and for a correction the same value. */
function sameDecision(a: { action: unknown; value?: unknown }, b: { action: unknown; value?: unknown }): boolean {
  return a.action === b.action && (a.action !== "override" || JSON.stringify(a.value ?? null) === JSON.stringify(b.value ?? null));
}
