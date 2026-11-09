/**
 * Edit one rule's supported logic with controls fitted to each field. What it produces is only the fields that
 * changed, as a rule override the API compiles into a new draft version; nothing here edits a stored ruleset, and
 * raw JSON is never accepted. Limits and severities the policy text states are checked against it by the compiler.
 */
import { Plus, X } from "lucide-react";
import { useFieldCatalog } from "@/api/queries";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/misc";
import { SELECT } from "@/pages/evaluations/display";
import { conditionText, fieldText, type ArtifactRule } from "./rules";

type Condition = { op: string; field?: string; value?: unknown; values?: unknown; conditions?: Condition[]; condition?: Condition };
type Param = { type: "number"; value: string; unit: string; source?: string } | { type: "set"; values: string[] };

export const EDITABLE_TEXT: Record<string, string> = {
  title: "Rule name",
  status: "Active status",
  outcome: "Outcome",
  severity: "Severity",
  applies_when: "When it applies",
  select: "What it checks",
  waived_by_ref: "Waived by",
  params: "Limits and lists",
};

const OPERATORS = [
  ["eq", "is"],
  ["ne", "is not"],
  ["in", "is one of"],
  ["gt", "is over"],
  ["gte", "is at least"],
  ["lt", "is under"],
  ["lte", "is at most"],
  ["missing", "is not given"],
] as const;
const REFS = [
  ["", "Nothing: it is never waived"],
  ["pre_approval", "A pre-approval reference"],
  ["vp_approval", "A VP approval reference"],
  ["manager_approval", "A manager approval reference"],
  ["baggage_delay", "A baggage-delay reference"],
] as const;
const DECIMAL = /^\d+(\.\d+)?$/;

/** The rows of a flat condition (one comparison, or all/any of comparisons), or null when it is nested. */
export function rowsOf(condition: Condition | null | undefined): { join: "all" | "any"; rows: Condition[] } | null {
  if (!condition) return { join: "all", rows: [] };
  if (condition.op === "all" || condition.op === "any") {
    const rows = condition.conditions ?? [];
    return rows.every((row) => !["all", "any", "not"].includes(row.op)) ? { join: condition.op, rows } : null;
  }
  return condition.op === "not" ? null : { join: "all", rows: [condition] };
}

export function conditionOf(join: "all" | "any", rows: Condition[]): Condition | null {
  if (!rows.length) return null;
  return rows.length === 1 ? rows[0]! : { op: join, conditions: rows };
}

/** Why an edited value would be refused before it is sent, field by field. */
export function problemsOf(rule: ArtifactRule, set: Record<string, unknown>): Record<string, string> {
  const problems: Record<string, string> = {};
  if ("title" in set && !String(set.title ?? "").trim()) problems.title = "A rule needs a name.";
  for (const key of ["applies_when", "select"] as const) {
    const condition = set[key] as Condition | null | undefined;
    for (const row of rowsOf(condition)?.rows ?? []) {
      if (!row.field) problems[key] = "Choose a field for each condition.";
      else if (row.op === "in" && !(Array.isArray(row.values) && row.values.length)) problems[key] = "List at least one value for “is one of”.";
      else if (!["in", "missing"].includes(row.op) && (row.value === "" || row.value === undefined)) problems[key] = "Give each condition a value.";
    }
  }
  const params = (set.params ?? {}) as Record<string, Param>;
  for (const [name, param] of Object.entries(params)) {
    if (param.type === "number" && !DECIMAL.test(param.value)) problems[`params.${name}`] = "Enter an amount, such as 30 or 30.50.";
    if (param.type === "set" && !param.values.length) problems[`params.${name}`] = "A list needs at least one value.";
  }
  void rule;
  return problems;
}

function Label({ htmlFor, children, hint }: { htmlFor?: string; children: React.ReactNode; hint?: string }) {
  return (
    <div className="space-y-0.5">
      <label htmlFor={htmlFor} className="block text-xs font-medium">
        {children}
      </label>
      {hint ? <p className="text-[11px] leading-4 text-subtle-foreground">{hint}</p> : null}
    </div>
  );
}

function ConditionEditor({ id, label, condition, params, onChange, problem }: { id: string; label: string; condition: Condition | null | undefined; params: Record<string, Param>; onChange: (next: Condition | null) => void; problem?: string }) {
  const catalog = useFieldCatalog();
  const flat = rowsOf(condition);
  if (!flat)
    return (
      <div className="space-y-1">
        <Label>{label}</Label>
        <p className="rounded-md bg-muted/50 px-3 py-2 text-[13px]">{conditionText(condition, params as never) || "Always"}</p>
        <p className="text-[11px] text-subtle-foreground">This condition is nested, so it is not edited here: it stays as it is.</p>
      </div>
    );
  const fields = Object.keys(catalog.data?.fields ?? {}).sort();
  const limits = Object.entries(params).filter(([, param]) => param.type === "number").map(([name]) => name);  // a condition may compare with one
  const vocab = catalog.data?.vocabularies ?? {};
  const set = (index: number, change: Partial<Condition>) => onChange(conditionOf(flat.join, flat.rows.map((row, at) => (at === index ? cleaned({ ...row, ...change }) : row))));
  return (
    <fieldset className="space-y-2">
      <legend className="text-xs font-medium">{label}</legend>
      <p className="text-[11px] text-subtle-foreground">{flat.rows.length ? `Now: ${conditionText(condition, params as never)}.` : "Always: no condition."}</p>
      {flat.rows.length > 1 ? (
        <select aria-label={`${label}: how conditions combine`} className={`${SELECT} w-56`} value={flat.join} onChange={(event) => onChange(conditionOf(event.target.value as "all" | "any", flat.rows))}>
          <option value="all">All of these are true</option>
          <option value="any">Any of these is true</option>
        </select>
      ) : null}
      {flat.rows.map((row, index) => {
        const options = row.field ? vocab[row.field] : undefined;
        const isParam = row.value && typeof row.value === "object" && "param" in (row.value as object);
        return (
          <div key={index} className="flex flex-wrap items-center gap-2 rounded-md border border-border p-2">
            <select aria-label={`${label} ${index + 1}: field`} className={`${SELECT} w-56`} value={row.field ?? ""} onChange={(event) => set(index, { field: event.target.value })}>
              <option value="">Choose a field</option>
              {[...new Set([...(row.field ? [row.field] : []), ...fields])].map((field) => (
                <option key={field} value={field}>
                  {fieldText(field).replace(/^the /, "")}
                </option>
              ))}
            </select>
            <select aria-label={`${label} ${index + 1}: operator`} className={`${SELECT} w-36`} value={row.op} onChange={(event) => set(index, { op: event.target.value })}>
              {OPERATORS.map(([op, text]) => (
                <option key={op} value={op}>
                  {text}
                </option>
              ))}
            </select>
            {row.op !== "missing" && row.op !== "in" && limits.length ? (
              <select aria-label={`${label} ${index + 1}: compared with`} className={`${SELECT} w-44`} value={isParam ? String((row.value as { param: string }).param) : ""} onChange={(event) => set(index, { value: event.target.value ? { param: event.target.value } : "" })}>
                <option value="">A value typed here</option>
                {limits.map((name) => (
                  <option key={name} value={name}>
                    The {name.replace(/_/g, " ")} limit
                  </option>
                ))}
              </select>
            ) : null}
            {row.op === "missing" ? null : isParam ? (
              <span className="text-[13px]" title="A limit: change it under Limits and lists">
                the {String((row.value as { param: string }).param).replace(/_/g, " ")} limit
              </span>
            ) : row.op === "in" ? (
              <Input
                aria-label={`${label} ${index + 1}: values`}
                className="h-9 w-64 text-[13px]"
                placeholder="Values, separated by commas"
                value={Array.isArray(row.values) ? row.values.join(", ") : ""}
                onChange={(event) => set(index, { values: event.target.value.split(",").map((item) => item.trim()).filter(Boolean) })}
              />
            ) : catalog.data?.fields[row.field ?? ""]?.type === "boolean" ? (
              <select aria-label={`${label} ${index + 1}: value`} className={`${SELECT} w-28`} value={String(row.value)} onChange={(event) => set(index, { value: event.target.value === "true" })}>
                <option value="true">yes</option>
                <option value="false">no</option>
              </select>
            ) : options ? (
              <select aria-label={`${label} ${index + 1}: value`} className={`${SELECT} w-56`} value={String(row.value ?? "")} onChange={(event) => set(index, { value: event.target.value })}>
                <option value="">Choose a value</option>
                {options.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            ) : (
              <Input aria-label={`${label} ${index + 1}: value`} className="h-9 w-56 text-[13px]" value={String(row.value ?? "")} onChange={(event) => set(index, { value: event.target.value })} />
            )}
            <Button type="button" size="sm" variant="ghost" aria-label={`Remove ${label.toLowerCase()} condition ${index + 1}`} onClick={() => onChange(conditionOf(flat.join, flat.rows.filter((_, at) => at !== index)))}>
              <X aria-hidden />
            </Button>
          </div>
        );
      })}
      <Button type="button" size="sm" variant="outline" onClick={() => onChange(conditionOf(flat.join, [...flat.rows, { op: "eq", field: "", value: "" }]))}>
        <Plus aria-hidden />
        Add a condition
      </Button>
      {problem ? (
        <p id={`${id}-error`} role="alert" className="text-xs text-destructive">
          {problem}
        </p>
      ) : null}
    </fieldset>
  );
}

/** A comparison in the shape the schema takes: in has values, missing has neither, the others a value. */
function cleaned(row: Condition): Condition {
  if (row.op === "in") return { op: "in", field: row.field, values: Array.isArray(row.values) ? row.values : row.value !== undefined && row.value !== "" ? [String(row.value)] : [] };
  if (row.op === "missing") return { op: "missing", field: row.field };
  return { op: row.op, field: row.field, value: row.value ?? (Array.isArray(row.values) ? row.values[0] : "") };
}

/**
 * The editor for one rule. `set` holds only what changed; onChange gives it back with a field removed again when it
 * returns to the rule's own value, so an unchanged field is never sent.
 */
export function RuleEditor({ rule, set, onChange }: { rule: ArtifactRule; set: Record<string, unknown>; onChange: (set: Record<string, unknown>) => void }) {
  const id = `edit-${rule.rule_id}`;
  const value = <T,>(field: string, original: T): T => (field in set ? (set[field] as T) : original);
  const change = (field: string, next: unknown, original: unknown) => {
    const copy = { ...set };
    if (JSON.stringify(next ?? null) === JSON.stringify(original ?? null)) delete copy[field];
    else copy[field] = next;
    onChange(copy);
  };
  const params = (rule.params ?? {}) as Record<string, Param>;
  const editedParams = { ...params, ...((set.params ?? {}) as Record<string, Param>) };
  const setParam = (name: string, next: Param) => {
    const changed = { ...((set.params ?? {}) as Record<string, Param>), [name]: next };
    if (JSON.stringify(next) === JSON.stringify(params[name])) delete changed[name];
    change("params", Object.keys(changed).length ? changed : undefined, undefined);
  };
  const problems = problemsOf(rule, set);
  const check = rule.kind === "check";
  return (
    <div className="space-y-4" aria-label={`Edit ${rule.rule_id}`}>
      <div className="space-y-1">
        <Label htmlFor={`${id}-title`}>Rule name</Label>
        <Input id={`${id}-title`} className="h-9 text-[13px]" value={value("title", rule.title ?? "")} onChange={(event) => change("title", event.target.value, rule.title)} aria-invalid={Boolean(problems.title)} />
        {problems.title ? <p role="alert" className="text-xs text-destructive">{problems.title}</p> : null}
      </div>
      <div className="space-y-1">
        <Label hint="The policy's own wording: it is fixed, as each rule must quote its clause. Change the document to change it.">Policy statement</Label>
        <p className="rounded-md bg-muted/50 px-3 py-2 text-xs text-muted-foreground">“{String(rule.source_quote ?? "").replace(/\*\*|__/g, "")}”</p>
      </div>
      {check ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1">
            <Label htmlFor={`${id}-outcome`}>Outcome</Label>
            <select id={`${id}-outcome`} className={SELECT} value={value("outcome", rule.outcome ?? "exception")} onChange={(event) => change("outcome", event.target.value, rule.outcome)}>
              <option value="exception">Raise an exception</option>
              <option value="review_signal">Send to review</option>
            </select>
          </div>
          <div className="space-y-1">
            <Label htmlFor={`${id}-severity`} hint="Must be what the policy states: the compiler checks it against the document.">
              Severity
            </Label>
            <select id={`${id}-severity`} className={SELECT} value={value("severity", rule.severity ?? "") ?? ""} onChange={(event) => change("severity", event.target.value || null, rule.severity ?? null)}>
              <option value="">None</option>
              {["critical", "high", "medium", "low"].map((severity) => (
                <option key={severity} value={severity}>
                  {severity.charAt(0).toUpperCase() + severity.slice(1)}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1 sm:col-span-2">
            <Label htmlFor={`${id}-waived`} hint="Required evidence: the reference on file that excuses a finding of this rule.">
              Waived by
            </Label>
            <select id={`${id}-waived`} className={SELECT} value={value("waived_by_ref", rule.waived_by_ref ?? "") ?? ""} onChange={(event) => change("waived_by_ref", event.target.value || null, rule.waived_by_ref ?? null)}>
              {REFS.map(([ref, text]) => (
                <option key={ref} value={ref}>
                  {text}
                </option>
              ))}
            </select>
          </div>
        </div>
      ) : null}
      {check ? (
        <>
          <ConditionEditor id={`${id}-applies`} label="When it applies" condition={value("applies_when", rule.applies_when as Condition | null)} params={editedParams} onChange={(next) => change("applies_when", next, rule.applies_when ?? null)} problem={problems.applies_when} />
          {rule.select !== undefined || "select" in set ? (
            <ConditionEditor id={`${id}-select`} label="What it checks" condition={value("select", rule.select as Condition | null)} params={editedParams} onChange={(next) => change("select", next, rule.select ?? null)} problem={problems.select} />
          ) : rule.helper ? (
            <p className="text-[11px] text-subtle-foreground">What it checks is named logic ({rule.helper.replace(/_/g, " ")}): its limits are edited below.</p>
          ) : null}
        </>
      ) : null}
      {Object.keys(params).length ? (
        <fieldset className="space-y-2">
          <legend className="text-xs font-medium">{rule.kind === "process" ? "Dispositions and lists" : "Limits and lists"}</legend>
          {Object.entries(editedParams).map(([name, param]) => {
            const label = name.replace(/_/g, " ");
            const problem = problems[`params.${name}`];
            if (param.type === "number")
              return (
                <div key={name} className="space-y-1">
                  <div className="flex items-center gap-2">
                    <label htmlFor={`${id}-${name}`} className="w-56 text-[13px]">
                      {label}
                    </label>
                    <Input id={`${id}-${name}`} inputMode="decimal" className="h-9 w-32 text-[13px]" value={param.value} onChange={(event) => setParam(name, { ...param, value: event.target.value.trim() })} aria-invalid={Boolean(problem)} />
                    <span className="text-xs text-muted-foreground">{param.unit === "usd" ? "USD" : param.unit === "eur" ? "EUR" : param.unit}</span>
                  </div>
                  <p className="text-[11px] text-subtle-foreground">{param.source === "project_decision" ? "A project decision, not a policy limit." : "A policy limit: it must match the document, which the compiler checks."}</p>
                  {problem ? <p role="alert" className="text-xs text-destructive">{problem}</p> : null}
                </div>
              );
            const severities = name.endsWith("severities");
            return (
              <div key={name} className="space-y-1">
                <span className="block text-[13px]">{label}</span>
                {severities ? (
                  <div className="flex flex-wrap gap-3" role="group" aria-label={label}>
                    {["critical", "high", "medium", "low"].map((severity) => (
                      <label key={severity} className="flex items-center gap-1.5 text-[13px] capitalize">
                        <input
                          type="checkbox"
                          className="size-3.5 accent-primary"
                          checked={param.values.includes(severity)}
                          onChange={(event) => setParam(name, { ...param, values: ["critical", "high", "medium", "low"].filter((item) => (item === severity ? event.target.checked : param.values.includes(item))) })}
                        />
                        {severity}
                      </label>
                    ))}
                  </div>
                ) : (
                  <Input aria-label={label} className="h-9 text-[13px]" value={param.values.join(", ")} onChange={(event) => setParam(name, { ...param, values: event.target.value.split(",").map((item) => item.trim()).filter(Boolean) })} />
                )}
                {problem ? <p role="alert" className="text-xs text-destructive">{problem}</p> : null}
              </div>
            );
          })}
        </fieldset>
      ) : null}
    </div>
  );
}
