/**
 * A compiled ruleset's executable rules in plain English, from the core's RuleSet schema: when a rule applies, what
 * it checks, its limits, and what it does. The words are generated from the rule itself; nothing is invented, and a
 * rule part this does not recognise is shown in its stored form.
 */

export type Condition = { op: string; field?: string; value?: unknown; values?: unknown; conditions?: Condition[]; condition?: Condition };
export type Param = { type: "number"; value: string; unit: string; source?: string } | { type: "set"; values: string[] };

export interface ArtifactRule {
  rule_id: string;
  title?: string;
  policy_doc?: string;
  status?: string;
  kind?: string;
  outcome?: string;
  scope?: string;
  severity?: string | null;
  severity_cases?: { when: Condition; severity: string }[];
  applies_when?: Condition | null;
  select?: Condition | null;
  helper?: string | null;
  waived_by_ref?: string | null;
  replaces?: { rule_id: string; level: string }[];
  modifies?: string[];
  requires_ref?: string;
  directive?: string;
  params?: Record<string, Param>;
  source_quote?: string;
  [key: string]: unknown;
}

const FIELDS: Record<string, string> = {
  "receipt.mcc": "the merchant category code",
  "receipt.submitted_expense_type": "the expense type",
  "receipt.attendee_count": "the number of attendees",
  "receipt.currency": "the currency",
  "receipt.fx_rate_to_usd": "the exchange rate to USD",
  "receipt.entity": "the entity",
  "receipt.employee_country": "the employee's country",
  "item.category": "the item's category",
  "item.is_purchase": "the item is a purchase",
};

const UNITS: Record<string, string> = { usd: "USD", eur: "EUR", count: "", nights: "nights", hour: "hours", ratio: "" };
const OPS: Record<string, string> = { gt: "is over", gte: "is at least", lt: "is under", lte: "is at most", eq: "is", ne: "is not" };
/** Each named check in the core's vocabulary (policy/vocabulary.py Helper), in words that follow its rules' titles. */
const HELPERS: Record<string, string> = {
  meal_cap: "the employee's meals for the day stay within the meal cap",
  split_transactions: "no purchase was split across receipts to stay under a limit",
  gift_cap: "gifts stay within the gift cap, with a named recipient where required",
  government_official: "the requirements for government officials as recipients are met",
  otc_trip_cap: "over-the-counter medication stays within the per-trip cap",
  baggage_allowance: "baggage-delay purchases stay within the allowance",
  expense_type_mismatch: "the submitted expense type matches what was bought",
  alcohol_per_attendee: "alcohol spending per attendee stays within the cap",
  vat_threshold: "VAT documentation is present where the amount requires it",
};
const REFS: Record<string, string> = { pre_approval: "a pre-approval", vp_approval: "a VP approval", manager_approval: "a manager approval", baggage_delay: "a baggage-delay" };
const DIRECTIVES: Record<string, string> = {
  disposition: "Decides each receipt's outcome (flag, review or pass) from the severities of what was found.",
  reference_kinds: "Says which approval references count, and for what.",
  uncertainty: "Says when an uncertain classification sends a receipt to review.",
  evidence: "Requires each finding to quote the policy it rests on.",
  fx_rate: "Sets how foreign-currency amounts are converted.",
  guidance: "Guidance for reviewers; it raises no finding itself.",
};

function words(text: string): string {
  return text.replace(/_/g, " ").replace(/\busd\b/gi, "USD").replace(/\bmcc\b/gi, "MCC").replace(/\bfx\b/gi, "FX");
}

export function fieldText(field: string | undefined): string {
  if (!field) return "a field";
  return FIELDS[field] ?? `the ${words(field.split(".").pop() ?? field)}`;
}

export function paramText(param: Param | undefined): string {
  if (!param) return "a configured value";
  if (param.type === "number") return [param.value, UNITS[param.unit] ?? param.unit].filter(Boolean).join(" ");
  return param.values.join(", ");
}

function valueText(value: unknown, params: Record<string, Param>): string {
  if (value && typeof value === "object" && "param" in (value as object)) return paramText(params[(value as { param: string }).param]);
  if (Array.isArray(value)) return value.map(String).join(", ");
  return typeof value === "string" ? `“${value}”` : String(value);
}

export function conditionText(condition: Condition | null | undefined, params: Record<string, Param> = {}, nested = false): string {
  if (!condition) return "";
  switch (condition.op) {
    case "all":
    case "any": {
      const parts = (condition.conditions ?? []).map((item) => conditionText(item, params, true));
      const joined = parts.join(condition.op === "all" ? " and " : " or ");
      return nested && parts.length > 1 ? `(${joined})` : joined;
    }
    case "not":
      return `not ${conditionText(condition.condition, params, true)}`;
    case "missing":
      return `${fieldText(condition.field)} is not given`;
    case "in":
      return `${fieldText(condition.field)} is one of ${valueText(condition.values, params)}`;
    default: {
      if (typeof condition.value === "boolean" && (condition.op === "eq" || condition.op === "ne")) {
        const positive = (condition.op === "eq") === condition.value;
        const subject = fieldText(condition.field);
        return subject.includes(" is ") ? (positive ? subject : subject.replace(" is ", " is not ")) : `${subject} is ${positive ? "yes" : "no"}`;
      }
      return `${fieldText(condition.field)} ${OPS[condition.op] ?? condition.op} ${valueText(condition.value, params)}`;
    }
  }
}

function sentence(text: string): string {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) + "." : "";
}

export interface PlainRule {
  id: string;
  name: string;
  applies: string;
  checks: string;
  limits: string[];
  outcome: string;
  source: string | null;
  inactive: string | null;
}

/** One rule, in plain words. */
export function plainRule(rule: ArtifactRule): PlainRule {
  const params = rule.params ?? {};
  const limits = Object.entries(params).map(([name, param]) => `${words(name.replace(/_(usd|eur)$/, "")).replace(/^\w/, (c) => c.toUpperCase())}: ${paramText(param)}`);
  let applies = "Every receipt.";
  let checks = "";
  let outcome = "";
  if (rule.kind === "check") {
    applies = rule.applies_when ? sentence(`when ${conditionText(rule.applies_when, params)}`) : rule.scope === "item" ? "Every item on every receipt." : "Every receipt.";
    if (rule.select) checks = sentence(`${rule.scope === "item" ? "items where" : "receipts where"} ${conditionText(rule.select, params)}`);
    else if (rule.helper) checks = sentence(`that ${HELPERS[rule.helper] ?? words(rule.helper)}`);
    else checks = rule.scope === "item" ? "Each item." : "The whole receipt.";
    const severity = rule.severity_cases?.length
      ? `a severity that depends on the receipt (${rule.severity_cases.map((item) => `${item.severity} when ${conditionText(item.when, params)}`).join("; ")}${rule.severity ? `; otherwise ${rule.severity}` : ""})`
      : rule.severity ?? "";
    outcome = rule.outcome === "review_signal" ? "Sends the receipt to review." : `Raises a ${severity ? `${severity} ` : ""}exception.`;
    if (rule.waived_by_ref) outcome += ` Waived when ${REFS[rule.waived_by_ref] ?? words(rule.waived_by_ref)} reference is on file.`;
    if (rule.replaces?.length) outcome += ` Takes the place of ${rule.replaces.map((item) => item.rule_id).join(", ")}.`;
  } else if (rule.kind === "carve_out") {
    applies = rule.select ? sentence(`items where ${conditionText(rule.select, params)}`) : "Findings of the rules it modifies.";
    checks = `Whether ${REFS[rule.requires_ref ?? ""] ?? words(rule.requires_ref ?? "a")} reference is on file${rule.helper ? `, and that ${HELPERS[rule.helper] ?? words(rule.helper)}` : ""}.`;
    outcome = `Excuses findings of ${(rule.modifies ?? []).join(", ")}.`;
  } else if (rule.kind === "process") {
    applies = "How every receipt is decided.";
    checks = DIRECTIVES[rule.directive ?? ""] ?? sentence(words(rule.directive ?? "a process step"));
    outcome = "Shapes the outcome; raises no finding of its own.";
  }
  const inactive = rule.status === "retired" ? "Retired: not applied." : rule.status === "not_evaluable" ? "Not evaluable: kept for the record, never applied." : null;
  return { id: rule.rule_id, name: rule.title ?? rule.rule_id, applies, checks, limits, outcome, source: rule.policy_doc ?? null, inactive };
}
