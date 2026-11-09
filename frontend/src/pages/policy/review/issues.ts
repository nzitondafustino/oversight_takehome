/**
 * The review's logic, in business terms: what each issue asks of the reviewer, why it was flagged, whether a decision
 * on it changes how receipts are checked, and where it stands. The compiler's own names stay in Technical details.
 */
import type { Baseline, ExtractionReviewDetail, FieldComparison, PreviousDecision, ReviewAction, ReviewDecision, ReviewIssue, RuleDiscrepancy, RuleLogic } from "@/api/types";

export type IssueKind = "difference" | "unconfirmed" | "missing" | "agreed";
export type Filter = "open" | "decided" | "all";

/** A genuine disagreement, a number neither reading took as a limit, a reading absent on one side, or agreement. */
export function kindOf(comparison: Pick<FieldComparison, "status">): IssueKind {
  if (comparison.status === "disagree") return "difference";
  if (comparison.status === "unconfirmed") return "unconfirmed";
  if (comparison.status === "only_regex" || comparison.status === "only_llm") return "missing";
  return "agreed";
}

export const KIND_TEXT: Record<IssueKind, string> = {
  difference: "The readings differ",
  unconfirmed: "Needs confirmation",
  missing: "Found by one reading only",
  agreed: "The readings agree",
};

export const FILTERS: { id: Filter; label: string }[] = [
  { id: "open", label: "To decide" },
  { id: "decided", label: "Decided" },
  { id: "all", label: "Every field" },
];

/** Each extracted field in business terms; the compiler's name is kept for Technical details. */
export const FIELD_TEXT: Record<string, string> = {
  title: "Rule name",
  section: "Policy section",
  severities: "Severity",
  review_signal: "Review signal only",
  thresholds: "Limits",
  references: "Rules it refers to",
  quote: "Quoted policy text",
  rule: "Rule exists",
};

export const READING = {
  regex: { name: "Text-match reading", inSentence: "text-match reading", technical: "regex" },
  llm: { name: "AI reading", inSentence: "AI reading", technical: "LLM" },
} as const;

/**
 * Whether the executable rules read this field, so a decision on it can change how receipts are checked. Limits,
 * severities and review signals are checked against the compiled rule; a rule's existence and name must match the
 * text; the section, references and quote document the rule and are checked against the source text only.
 */
export function affectsExecution(field: string): boolean {
  return field === "thresholds" || field === "severities" || field === "review_signal" || field === "rule";
}

const UNITS: Record<string, string> = { usd: "USD", eur: "EUR", count: "(count)", nights: "nights", hour: "hours", ratio: "ratio" };

/** A reading as the items a person compares: "50 USD", "High", "Yes". null when the reading has no value at all. */
export function readingItems(field: string, value: unknown): string[] | null {
  if (value === null || value === undefined) return null;
  if (field === "thresholds" && Array.isArray(value))
    return value.map((item) => {
      const threshold = item as { value?: unknown; unit?: string };
      const unit = UNITS[threshold.unit ?? ""] ?? threshold.unit ?? "";
      return `${String(threshold.value)}${unit ? ` ${unit}` : ""}`;
    });
  if (field === "rule") return [value === "present" ? "In the policy" : value === "absent" ? "Not in the policy" : String(value)];
  if (typeof value === "boolean") return [value ? "Yes" : "No"];
  if (Array.isArray(value)) return value.map((item) => (field === "severities" && typeof item === "string" ? item.charAt(0).toUpperCase() + item.slice(1) : typeof item === "string" ? item : JSON.stringify(item)));
  if (typeof value === "string") return [value];
  return [JSON.stringify(value)];
}

/** Items as one phrase: "50 USD, 75 USD", or what an empty or missing reading means for this field. */
export function itemsText(field: string, items: string[] | null): string {
  if (items === null) return "nothing found";
  if (items.length) return items.join(", ");
  return field === "thresholds" ? "no limit" : field === "references" ? "no other rule" : field === "severities" ? "no severity" : "none";
}

/** Items only one reading has: these are what differs. */
export function itemDiff(left: string[] | null, right: string[] | null): { onlyLeft: Set<string>; onlyRight: Set<string> } {
  const a = new Set(left ?? []);
  const b = new Set(right ?? []);
  return { onlyLeft: new Set([...a].filter((item) => !b.has(item))), onlyRight: new Set([...b].filter((item) => !a.has(item))) };
}

const ASK: Record<string, string> = {
  thresholds: "Check the limits this rule applies",
  severities: "Check how serious a breach of this rule is",
  review_signal: "Check whether this rule only sends receipts to review",
  title: "Check the rule's name",
  section: "Check which part of the policy this rule is in",
  references: "Check which other rules this one refers to",
  quote: "Check the policy text quoted for this rule",
};

/** What needs attention, in one line. */
export function headline(comparison: Pick<FieldComparison, "status" | "field" | "rule_id">): string {
  switch (comparison.status) {
    case "unconfirmed":
      return "Confirm that this rule sets no limit";
    case "only_regex":
      return `Confirm that ${comparison.rule_id} is a rule of this policy`;
    case "only_llm":
      return `Discard ${comparison.rule_id}: the AI reading found a rule the policy does not contain`;
    case "agreed":
      return "Both readings agree: nothing to decide";
    default:
      return ASK[comparison.field] ?? `Check the ${(FIELD_TEXT[comparison.field] ?? comparison.field).toLowerCase()}`;
  }
}

/** Why it was flagged: the disagreement, uncertainty or missing reading, with the values involved. */
export function whyFlagged(comparison: FieldComparison, possibleLimits: readonly string[] = []): string {
  const label = (FIELD_TEXT[comparison.field] ?? comparison.field).toLowerCase();
  switch (comparison.status) {
    case "disagree": {
      if (comparison.field === "quote") return "The AI reading quotes text that is not in this clause or its heading, so it cannot be shown as the rule's source.";
      const left = readingItems(comparison.field, comparison.regex_value);
      const right = readingItems(comparison.field, comparison.llm_value);
      const { onlyLeft, onlyRight } = itemDiff(left, right);
      const parts = [];
      if (onlyLeft.size) parts.push(`only the ${READING.regex.inSentence} found ${[...onlyLeft].join(", ")}`);
      if (onlyRight.size) parts.push(`only the ${READING.llm.inSentence} found ${[...onlyRight].join(", ")}`);
      const detail = parts.length ? `: ${parts.join("; ")}` : `: ${itemsText(comparison.field, left)} against ${itemsText(comparison.field, right)}`;
      return `The policy was read twice, by matching its text and by AI, and the two readings give different ${label}${detail}.`;
    }
    case "unconfirmed":
      return `Neither reading found a limit, but the clause states ${possibleLimits.length ? possibleLimits.map((item) => `“${item}”`).join(", ") : "a number"}. Confirm that it is not an amount, count or time the rule checks.`;
    case "only_regex":
      return "The policy has a heading for this rule, but the AI reading did not report it.";
    case "only_llm":
      return "The AI reading reported this rule, but the policy has no heading for it.";
    default:
      return "Both readings give the same value.";
  }
}

export type Resolution = "open" | "resolved" | "has_issue" | "not_required";

/** Where a field stands after the latest saved review: decided and accepted by the compiler, decided with a problem, or open. */
export function resolutionOf(comparison: FieldComparison, latest: Pick<ExtractionReviewDetail, "decisions" | "remaining" | "issues"> | undefined): Resolution {
  if (!comparison.review_required) return "not_required";
  if (!latest) return "open";
  if (latest.issues.some((issue) => issue.level === "error" && issue.comparison_id === comparison.comparison_id)) return "has_issue";
  const decided = latest.decisions.some((decision) => decision.comparison_id === comparison.comparison_id);
  return decided && !latest.remaining.includes(comparison.comparison_id) ? "resolved" : "open";
}

/**
 * The review's issues, each tied to the field it is about. A review stored before the compiler named the field of a
 * conflict with the executable rules carries no comparison_id: its message names the rule ("faithful: TIME-03: ..."),
 * so it is tied to that rule's decided limit, severity or review-signal field, never left looking resolved. A review
 * stored before the compiler told the two apart calls every conflict executable_conflict: one whose decision departs
 * from what the text states is an interpretation mismatch, as the compiler now reports it.
 */
export function attachIssues<T extends Pick<ReviewIssue, "code" | "message" | "comparison_id">>(issues: readonly T[], comparisons: readonly FieldComparison[], decisions: readonly Pick<ReviewDecision, "comparison_id" | "action" | "value">[]): T[] {
  const decided = new Map(decisions.map((item) => [item.comparison_id, item]));
  const items = (field: string, value: unknown) => JSON.stringify((readingItems(field, value) ?? []).map(normalize).sort());
  return issues.map((issue) => {
    if (issue.code !== "executable_conflict") return issue;
    const ruleId = /faithful: ([A-Z][A-Z0-9]*-\d+):/.exec(issue.message)?.[1];
    const field = /: severity/.test(issue.message) ? "severities" : /: review signal/.test(issue.message) ? "review_signal" : "thresholds";
    const match = issue.comparison_id
      ? comparisons.find((item) => item.comparison_id === issue.comparison_id)
      : comparisons.find((item) => item.rule_id === ruleId && item.field === field && decided.has(item.comparison_id));
    if (!match || !decided.has(match.comparison_id)) return issue;
    // As the compiler now says it: a decision that departs from what the clause states is the reviewer's to revisit.
    const departs = items(field, decidedValue(match, decided.get(match.comparison_id))) !== items(field, match.regex_value);
    return { ...issue, comparison_id: match.comparison_id, code: departs ? "interpretation_mismatch" : issue.code };
  });
}

/** Open issues include decisions with a problem: both still need the reviewer. */
export function matchesFilter(filter: Filter, resolution: Resolution): boolean {
  if (filter === "all") return true;
  if (filter === "decided") return resolution === "resolved";
  return resolution === "open" || resolution === "has_issue";
}

/** The value a saved decision gives its field: the reading it accepted, or its correction. */
export function decidedValue(comparison: FieldComparison, decision: Pick<ReviewDecision, "action" | "value"> | undefined): unknown {
  if (!decision) return undefined;
  if (decision.action === "override") return decision.value;
  if (decision.action === "accept_llm") return comparison.llm_value;
  if (decision.action === "confirm") return comparison.agreed_value;
  return comparison.regex_value;
}

/** A recorded decision in plain words, for history and read-only views. */
export function decisionText(action: string, field: string, value?: unknown): string {
  const texts: Record<ReviewAction, string> = {
    accept_regex: `Used the ${READING.regex.inSentence}`,
    accept_llm: `Used the ${READING.llm.inSentence}`,
    confirm: "Confirmed the agreed value",
    override: `Corrected to ${typeof value === "string" ? `“${value}”` : itemsText(field, readingItems(field, value))}`,
    reject: "Rejected the interpretation",
  };
  return texts[action as ReviewAction] ?? action;
}

const PREFIX = /^\S+\.md [A-Z][A-Z0-9]*-\d+ \w+: /;  // "regional-addendum.md REG-01 thresholds: ", which the issue's place already says

type Param = { type: string; value?: string; unit?: string; source?: string };

const normalize = (item: string) => item.replace(/^-?\d+(?:\.\d+)?/, (number) => String(Number(number)));

function list(items: string[]): string {
  return items.length <= 1 ? items.join("") : `${items.slice(0, -1).join(", ")} and ${items.at(-1)}`;
}

/** The limits a compiled rule takes from the policy, each as a reader sees it with what it is: "2 (min receipts)". */
function heldLimits(logic: RuleLogic): { item: string; name: string }[] {
  return Object.entries((logic.params ?? {}) as Record<string, Param>)
    .filter(([, param]) => param.type === "number" && param.source !== "project_decision")
    .map(([name, param]) => ({ item: normalize(readingItems("thresholds", [param])![0]!), name: name.replace(/_(usd|eur)$/, "").replace(/_/g, " ") }));
}

/** "2 (min receipts)": a count's own unit is left out where the parameter's name already says what it counts. */
function limitLabel(limit: { item: string; name: string }): string {
  return `${limit.item.replace(" (count)", "")} (${limit.name})`;
}

export interface Effect {
  /** Whether the decision matches the compiled rule: false blocks approval; null when nothing executable reads the field. */
  fits: boolean | null;
  text: string;
}

const BLOCKS = "Saving it records the decision, but approval stays blocked until the rule is changed in a draft version.";

/**
 * What a decision does to the executable rules: the compiled candidate is built from the hand-written rule logic, so a
 * decision never rewrites it. It either matches what the rule already does, or disagrees and blocks approval.
 */
export function decisionEffect(comparison: Pick<FieldComparison, "field" | "rule_id" | "regex_value">, logic: RuleLogic | undefined, value: unknown): Effect {
  const id = comparison.rule_id;
  if (!affectsExecution(comparison.field) && comparison.field !== "title")
    return { fits: null, text: "This field documents the rule. No check reads it, so how receipts are checked does not change." };
  if (comparison.field === "rule") {
    const present = value === "present";
    if (present === Boolean(logic)) return { fits: true, text: present ? `The compiled rules include ${id}: nothing changes.` : `The compiled rules have no ${id}: nothing changes.` };
    return { fits: false, text: `${present ? `The compiled rules have no ${id}` : `The compiled rules still include ${id}`}. ${BLOCKS}` };
  }
  if (!logic) return { fits: false, text: `The compiled rules have no ${id}, so nothing executable can take this value. ${BLOCKS}` };
  if (comparison.field === "title")
    return value === comparison.regex_value ? { fits: true, text: "The rule keeps the name its heading states: nothing changes." } : { fits: false, text: `The compiled rule keeps the name its heading states. ${BLOCKS}` };
  if (comparison.field === "thresholds") {
    const held = heldLimits(logic);
    const chosen = (readingItems("thresholds", value) ?? []).map(normalize);
    const dropped = held.filter((limit) => !chosen.includes(limit.item));
    const added = chosen.filter((item) => !held.some((limit) => limit.item === item));
    if (!dropped.length && !added.length)
      return { fits: true, text: held.length ? `The compiled rule ${id} already uses ${list(held.map(limitLabel))}: no rule change is needed.` : `The compiled rule ${id} has no limit either: no rule change is needed.` };
    const stated = new Set((readingItems("thresholds", comparison.regex_value) ?? []).map(normalize));
    if (dropped.length && dropped.every((limit) => stated.has(limit.item)) && !added.length)
      return { fits: false, text: `The policy text states ${list(dropped.map(limitLabel))}, and ${id} checks it as the policy says. This leaves it out, so it would be an interpretation mismatch, and approval stays blocked.` };
    const parts = [];
    if (dropped.length) parts.push(`${id} checks ${list(dropped.map(limitLabel))}, which this leaves out`);
    if (added.length) parts.push(`no part of ${id} uses ${list(added)}`);
    return { fits: false, text: `${parts.join("; ")}. ${BLOCKS}`.replace(/^./, (first) => first.toUpperCase()) };
  }
  if (comparison.field === "severities") {
    const held = [...new Set([logic.severity, ...(logic.severity_cases ?? []).map((item) => item.severity)].filter(Boolean) as string[])].sort();
    const chosen = [...new Set(Array.isArray(value) ? (value as string[]) : [])].sort();
    if (held.join() === chosen.join()) return { fits: true, text: `The compiled rule ${id} raises ${held.length ? `a ${list(held)} exception` : "no exception"}: no rule change is needed.` };
    return { fits: false, text: `The compiled rule ${id} raises ${held.length ? `a ${list(held)} exception` : "no exception"}, not ${chosen.length ? list(chosen) : "none"}. ${BLOCKS}` };
  }
  const signal = logic.outcome === "review_signal";
  if (value === signal) return { fits: true, text: `The compiled rule ${id} ${signal ? "sends the receipt to review" : "raises an exception"}: no rule change is needed.` };
  return { fits: false, text: `The compiled rule ${id} ${signal ? "only sends the receipt to review" : "raises an exception"}. ${BLOCKS}` };
}

/**
 * A saved decision's problem in plain words, for the issue it is about. A conflict with the compiled rule says what
 * the rule holds that the decision does not; the compiler's own message stays available as technical detail.
 */
export function problemText(issue: Pick<ReviewIssue, "code" | "message">, comparison: FieldComparison, logic: RuleLogic | undefined, decided: unknown): string {
  if (issue.code === "interpretation_mismatch") {
    const stated = itemsText(comparison.field, readingItems(comparison.field, comparison.regex_value));
    const id = comparison.rule_id;
    if (comparison.field === "thresholds") {
      // Say exactly which stated limits the decision drops or adds, each with the part of the rule that checks it.
      const statedItems = (readingItems("thresholds", comparison.regex_value) ?? []).map(normalize);
      const chosen = (readingItems("thresholds", decided) ?? []).map(normalize);
      const held = logic ? heldLimits(logic) : [];
      const named = (item: string) => {
        const limit = held.find((entry) => entry.item === item);
        return limit ? limitLabel(limit) : item;
      };
      const dropped = statedItems.filter((item) => !chosen.includes(item));
      const added = chosen.filter((item) => !statedItems.includes(item));
      const parts = [];
      if (dropped.length) parts.push(`leaves out ${list(dropped.map(named))}, which the policy states and ${id} checks`);
      if (added.length) parts.push(`adds ${list(added)}, which the policy does not state`);
      if (parts.length) return `This decision ${parts.join(", and ")}. The policy states ${stated}; ${id} follows it. Use the reading that matches the policy, or enter the correct value. If the policy itself is wrong, correct the document in a new revision.`;
    }
    return `The policy text states ${stated}, and the compiled rule ${id} follows it, but this decision gives ${itemsText(comparison.field, readingItems(comparison.field, decided))}. Use the reading that matches the policy, or enter the correct value. If the policy itself is wrong, correct the document in a new revision.`;
  }
  if (issue.code === "executable_conflict") {
    const effect = decisionEffect(comparison, logic, decided);
    const text = effect.fits === false ? effect.text.replace(BLOCKS, "") : "";
    return `${text ? `${text.trim()} ` : ""}This decision disagrees with the compiled rule ${comparison.rule_id}. Choose the value the rule holds, or change the rule in a draft version.`;
  }
  if (issue.code === "unsupported_value") return `The policy text does not support this value: ${issue.message.replace(PREFIX, "")}.`;
  if (issue.code === "rejected") return "You rejected this interpretation. It blocks approval until the policy or its compiled rule is corrected and compiled again.";
  const message = issue.message.replace(PREFIX, "");
  return message.charAt(0).toUpperCase() + message.slice(1) + (message.endsWith(".") ? "" : ".");
}

const RULE_ID = /\b[A-Z]+-\d{2}\b/g;  // as the compiler strips it: a rule reference is never a number in the clause
const WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"];
const NUMBER = /\$?\d[\d,]*(?:\.\d+)?|\b(?:two|three|four|five|six|seven|eight|nine|ten)\b/gi;

/** The pieces of a clause a reader takes in at once: split at semicolons, colons and sentence ends. */
function fragments(text: string): string[] {
  return text
    .replace(/\*\*|__/g, "")
    .replace(/\s+/g, " ")
    .split(/(?<=[;:])\s+|(?<=\.)\s+(?=[A-Z(])/)
    .map((piece) => piece.trim())
    .filter(Boolean);
}

function shorten(text: string, at: number, size = 170): string {
  if (text.length <= size) return text;
  const start = Math.max(0, Math.min(at - size / 2, text.length - size));
  return `${start > 0 ? "…" : ""}${text.slice(start, start + size).trim()}${start + size < text.length ? "…" : ""}`;
}

/**
 * Where a limit comes from: the part of the clause that states its number, such as "50 USD" in "the $50 gift cap
 * for one recipient (GIFT-01)". null when the clause does not state it, which is worth knowing too.
 */
export function whereInClause(item: string, clause: string | null): string | null {
  const value = /^-?\d+(?:\.\d+)?/.exec(item.replace(/,/g, ""))?.[0];
  if (!value || !clause) return null;
  const number = Number(value);
  for (const piece of fragments(clause)) {
    const plain = piece.replace(RULE_ID, (id) => " ".repeat(id.length));
    for (const match of plain.matchAll(NUMBER)) {
      const text = match[0].replace(/[$,]/g, "").toLowerCase();
      const read = WORDS.includes(text) ? WORDS.indexOf(text) : Number(text);
      if (read === number) return shorten(piece, match.index ?? 0);
    }
  }
  return null;
}

// --- Versions: the draft beside the published version it would replace ---

export type IssueState = "needs_confirmation" | "interpretation_mismatch" | "rule_correction" | "revalidate" | "policy_change" | "decided" | "not_required";

export const STATE_TEXT: Record<IssueState, { label: string; variant: "neutral" | "pass" | "review" | "flag" | "info" }> = {
  needs_confirmation: { label: "Needs confirmation", variant: "review" },
  interpretation_mismatch: { label: "Interpretation mismatch", variant: "flag" },
  rule_correction: { label: "Compiled rule needs correction", variant: "flag" },
  revalidate: { label: "Previous decision needs revalidation", variant: "review" },
  policy_change: { label: "Policy change detected", variant: "info" },
  decided: { label: "Decided", variant: "pass" },
  not_required: { label: "No decision needed", variant: "neutral" },
};

/**
 * One state for an issue, the most pressing first: a saved decision the policy text does not support, one the
 * compiled rule does not follow, an earlier decision whose question changed, a rule this draft changes, then plain
 * confirmation. A policy change is never an error: it is what the reviewer is asked to confirm.
 */
export function issueState(comparison: FieldComparison, resolution: Resolution, issues: readonly Pick<ReviewIssue, "code" | "comparison_id" | "level">[], previous: PreviousDecision | undefined, ruleChanged: boolean): IssueState {
  if (resolution === "not_required") return "not_required";
  const mine = issues.filter((issue) => issue.level === "error" && issue.comparison_id === comparison.comparison_id).map((issue) => issue.code);
  if (mine.includes("executable_conflict")) return "rule_correction";
  if (mine.length) return "interpretation_mismatch";
  if (resolution === "resolved") return "decided";
  if (previous?.state === "changed") return "revalidate";
  if (ruleChanged) return "policy_change";
  return "needs_confirmation";
}

const PARAM_UNITS: Record<string, string> = { usd: "USD", eur: "EUR", count: "", nights: "nights", hour: "hours", ratio: "" };

function words(name: string): string {
  const text = name.replace(/_(usd|eur)$/, "").replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function valueWords(field: string, value: unknown): string {
  if (value === null || value === undefined) return field === "rule" ? "not in this version" : "none";
  if (field === "rule") return "in this version";
  if (typeof value === "object" && value && "value" in value) {
    const param = value as { value: string; unit?: string };
    return [param.value, PARAM_UNITS[param.unit ?? ""] ?? param.unit].filter(Boolean).join(" ");
  }
  if (typeof value === "object" && value && "values" in value) return ((value as { values: string[] }).values ?? []).join(", ");
  if (Array.isArray(value)) return value.length ? value.map((item) => (typeof item === "object" ? (item as { rule_id?: string }).rule_id ?? JSON.stringify(item) : String(item))).join(", ") : "none";
  if (typeof value === "object") return "changed conditions";
  return String(value);
}

/** One change a draft makes to a published rule, in words: "Min receipts: 2 → 3". */
export interface RuleChangeText {
  label: string;
  before: string;
  after: string;
}

export function changeTexts(ruleId: string, baseline: Baseline | undefined): RuleChangeText[] {
  return (baseline?.rule_changes ?? [])
    .filter((change) => change.rule_id === ruleId)
    .map((change) => ({
      label: change.field === "rule" ? "Rule" : change.field.startsWith("params.") ? words(change.field.slice(7)) : words(change.field),
      before: valueWords(change.field, change.before),
      after: valueWords(change.field, change.after),
    }));
}

/** What a reviewer should do about a policy change, and why it is in front of them. */
export function changeReason(ruleId: string, changes: RuleChangeText[], clauseChanged: boolean): { reason: string; todo: string } {
  const what = changes.map((change) => change.label.toLowerCase()).join(" and ");
  return {
    reason: changes.length
      ? `The current draft changes the ${what} of ${ruleId}${clauseChanged ? ", and the policy text of its clause changed too" : ", although its clause text did not change"}.`
      : `The policy text of ${ruleId}'s clause changed, but the compiled rule does the same as before.`,
    todo: changes.length
      ? "Verify that the current policy document supports the new value, then confirm or correct it. Approval of this version authorizes the change; the published version stays as it is."
      : "Check that the new wording means the same as before. Nothing about how receipts are checked changes.",
  };
}

/** For a compiled-rule discrepancy: each compiled limit the clause does not state, paired with a stated limit no param holds. */
export function proposedParams(discrepancy: RuleDiscrepancy): { name: string; before: { value: string; unit: string }; after: { value: string; unit: string } | null }[] {
  if (discrepancy.field !== "thresholds") return [];
  const stated = (discrepancy.stated as { value: string; unit: string }[]) ?? [];
  const compiled = Object.entries((discrepancy.compiled as Record<string, { value: string; unit: string }>) ?? {});
  const key = (limit: { value: string; unit: string }) => `${Number(limit.value)}|${limit.unit}`;
  const held = new Set(compiled.map(([, limit]) => key(limit)));
  const unheld = stated.filter((limit) => !held.has(key(limit)));
  return compiled
    .filter(([, limit]) => !stated.some((item) => key(item) === key(limit)))
    .map(([name, limit]) => {
      const sameUnit = unheld.filter((item) => item.unit === limit.unit);
      return { name, before: limit, after: sameUnit.length === 1 ? sameUnit[0]! : null };
    });
}
