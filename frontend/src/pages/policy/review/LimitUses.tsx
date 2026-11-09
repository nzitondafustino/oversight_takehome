/**
 * Each value a reading gives a limit field, tied to the rule parameter that holds it: what the engine measures, how
 * it compares, what it applies to, which rule's threshold it is, and where the clause states it. A value no
 * parameter of the rule holds is said so: the rule would not use it.
 */
import type { RuleLogic } from "@/api/types";
import { conditionText, type Condition, type Param } from "../rules";
import { whereInClause } from "./issues";

const UNITS: Record<string, string> = { usd: "USD", eur: "EUR", count: "", nights: "nights", hour: "hours", ratio: "" };

type Threshold = { value: unknown; unit?: string };

function asThresholds(value: unknown): Threshold[] {
  return Array.isArray(value) ? (value.filter((item) => item && typeof item === "object" && "value" in item) as Threshold[]) : [];
}

function label(threshold: Threshold): string {
  const unit = UNITS[threshold.unit ?? ""] ?? threshold.unit ?? "";
  return `${String(threshold.value)}${unit ? ` ${unit}` : ""}`;
}

function paramName(name: string): string {
  const words = name.replace(/_(usd|eur)$/, "").replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function referenced(condition: unknown): Set<string> {
  const found = new Set<string>();
  const walk = (node: unknown) => {
    if (!node || typeof node !== "object") return;
    const item = node as Condition;
    const value = item.value as { param?: unknown } | undefined;
    if (value && typeof value === "object" && typeof value.param === "string") found.add(value.param);
    item.conditions?.forEach(walk);
    walk(item.condition);
  };
  walk(condition);
  return found;
}

/** A stored rule's logic, for a version whose artifact holds it: where its conditions compare each param. */
export function logicOf(rule: { applies_when?: unknown; select?: unknown; params?: Record<string, Param> } & Record<string, unknown>): RuleLogic {
  const where = { applies_when: referenced(rule.applies_when), select: referenced(rule.select) };
  const uses = Object.fromEntries(Object.keys(rule.params ?? {}).map((name) => [name, { in_conditions: (["applies_when", "select"] as const).filter((key) => where[key].has(name)), helper: null }]));
  return { ...(rule as unknown as RuleLogic), param_uses: uses };
}

export function LimitUses({ ruleId, logic, readings, clause }: { ruleId: string; logic: RuleLogic | undefined; readings: unknown[]; clause: string | null }) {
  const seen = new Map<string, Threshold>();
  for (const reading of readings) for (const threshold of asThresholds(reading)) seen.set(`${Number(threshold.value)}|${threshold.unit ?? ""}`, threshold);
  if (!seen.size) return null;
  const params = (logic?.params ?? {}) as Record<string, Param>;
  const holding = (threshold: Threshold) =>
    Object.entries(params).filter(([, param]) => param.type === "number" && param.unit === (threshold.unit ?? "") && Number(param.value) === Number(threshold.value)).map(([name]) => name);
  return (
    <div className="rounded-md border border-border px-3 py-2 sm:col-span-2" aria-label="How each limit is applied">
      <p className="text-xs font-medium text-muted-foreground">How each value is applied by {ruleId}</p>
      <ul className="mt-1 divide-y divide-border text-[13px]">
        {[...seen.values()].map((threshold) => {
          const value = label(threshold);
          const names = holding(threshold);
          const stated = whereInClause(value, clause);
          return (
            <li key={value} className="space-y-0.5 py-1.5">
              <p>
                <span className="font-semibold tabular">{value}</span>
                {names.length ? (
                  names.map((name) => {
                    const use = logic?.param_uses[name];
                    return (
                      <span key={name}>
                        {" "}→ {paramName(name)} <span className="font-mono text-[11px] text-subtle-foreground">{name}</span>
                        {use?.helper?.threshold_of ? <span className="ml-1 rounded bg-muted px-1 text-xs">threshold of {use.helper.threshold_of}</span> : null}
                      </span>
                    );
                  })
                ) : logic ? (
                  <span className="text-status-review-text"> → no parameter of {ruleId} holds this value: the rule would not use it</span>
                ) : null}
              </p>
              {names.map((name) => {
                const use = logic?.param_uses[name];
                const helper = use?.helper;
                return (
                  <dl key={name} className="grid gap-x-2 text-xs sm:grid-cols-[6.5rem_1fr]">
                    {helper ? (
                      <>
                        <dt className="text-muted-foreground">Compared</dt>
                        <dd>
                          {helper.measure}: <span className="font-medium">{helper.operator} {value}</span>
                        </dd>
                        <dt className="text-muted-foreground">Applies to</dt>
                        <dd>{helper.applies_to}</dd>
                      </>
                    ) : null}
                    {use?.in_conditions.includes("applies_when") ? (
                      <>
                        <dt className="text-muted-foreground">Applies when</dt>
                        <dd>{conditionText(logic!.applies_when as Condition, params)}</dd>
                      </>
                    ) : null}
                    {use?.in_conditions.includes("select") ? (
                      <>
                        <dt className="text-muted-foreground">Checks</dt>
                        <dd>{conditionText(logic!.select as Condition, params)}</dd>
                      </>
                    ) : null}
                  </dl>
                );
              })}
              <p className="text-xs text-muted-foreground">{stated ? <>Stated: “{stated}”</> : <span className="italic text-status-review-text">Not stated in this clause</span>}</p>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
