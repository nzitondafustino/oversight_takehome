/**
 * A corrected value, entered with controls fitting the field rather than raw JSON: limits as amount and unit rows
 * (amounts kept as exact decimal text), severities as checkboxes, yes or no, a rule's presence, or text. A field
 * this does not know falls back to JSON.
 */
import { Plus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/misc";
import { parseJson } from "@/lib/losslessJson";
import { SELECT } from "@/pages/evaluations/display";

export const SEVERITIES = ["critical", "high", "medium", "low"] as const;
export const UNIT_OPTIONS = [
  ["usd", "USD"],
  ["eur", "EUR"],
  ["count", "Count"],
  ["nights", "Nights"],
  ["hour", "Hours"],
  ["ratio", "Ratio"],
] as const;
const DECIMAL = /^\d+(\.\d+)?$/;

export type Threshold = { value: string; unit: string };

/** The editor's starting value, from a reading, in the field's own shape. */
export function initialValue(field: string, reading: unknown): unknown {
  if (reading !== null && reading !== undefined) return reading;
  return field === "thresholds" || field === "severities" || field === "references" ? [] : field === "review_signal" ? false : field === "rule" ? "present" : "";
}

/** The value to send, or why it cannot be sent yet. */
export function editorValue(field: string, draft: unknown): { value?: unknown; problem?: string } {
  if (field === "thresholds") {
    const rows = draft as Threshold[];
    const bad = rows.find((row) => !DECIMAL.test(row.value.trim()));
    if (bad) return { problem: "Each limit needs an amount, such as 75 or 75.50." };
    return { value: rows.map((row) => ({ unit: row.unit, value: row.value.trim() })) };
  }
  if (["title", "section", "quote"].includes(field)) return String(draft).trim() ? { value: String(draft).trim() } : { problem: "Enter the text." };
  if (field === "references") return { value: String(Array.isArray(draft) ? draft.join(",") : draft).split(",").map((item) => item.trim()).filter(Boolean) };
  if (field === "severities" || field === "review_signal") return { value: draft };
  if (field === "rule") return { value: draft === "present" ? "present" : null };
  try {
    return { value: parseJson(String(draft)) };
  } catch {
    return { problem: "Enter valid JSON." };
  }
}

export function FieldEditor({ id, field, value, onChange }: { id: string; field: string; value: unknown; onChange: (value: unknown) => void }) {
  if (field === "thresholds") {
    const rows = (Array.isArray(value) ? value : []) as Threshold[];
    const set = (index: number, change: Partial<Threshold>) => onChange(rows.map((row, at) => (at === index ? { ...row, ...change } : row)));
    return (
      <fieldset className="space-y-2">
        <legend className="text-xs font-medium">Limits</legend>
        {rows.map((row, index) => (
          <div key={index} className="flex items-center gap-2">
            <Input aria-label={`Amount ${index + 1}`} inputMode="decimal" className="h-9 w-32 text-[13px]" value={row.value} onChange={(event) => set(index, { value: event.target.value })} />
            <select aria-label={`Unit ${index + 1}`} className={`${SELECT} w-32`} value={row.unit} onChange={(event) => set(index, { unit: event.target.value })}>
              {UNIT_OPTIONS.map(([unit, label]) => (
                <option key={unit} value={unit}>
                  {label}
                </option>
              ))}
            </select>
            <Button type="button" size="sm" variant="ghost" aria-label={`Remove limit ${index + 1}`} onClick={() => onChange(rows.filter((_, at) => at !== index))}>
              <X aria-hidden />
            </Button>
          </div>
        ))}
        <Button type="button" size="sm" variant="outline" onClick={() => onChange([...rows, { value: "", unit: "usd" }])}>
          <Plus aria-hidden />
          Add a limit
        </Button>
        {!rows.length ? <p className="text-xs text-muted-foreground">No limits: the rule sets none.</p> : null}
      </fieldset>
    );
  }
  if (field === "severities") {
    const chosen = new Set(Array.isArray(value) ? (value as string[]) : []);
    return (
      <fieldset className="flex flex-wrap gap-3">
        <legend className="mb-1 w-full text-xs font-medium">Severity levels</legend>
        {SEVERITIES.map((severity) => (
          <label key={severity} className="flex items-center gap-1.5 text-[13px] capitalize">
            <input
              type="checkbox"
              className="size-3.5 accent-primary"
              checked={chosen.has(severity)}
              onChange={(event) => onChange(SEVERITIES.filter((item) => (item === severity ? event.target.checked : chosen.has(item))))}
            />
            {severity}
          </label>
        ))}
      </fieldset>
    );
  }
  if (field === "review_signal" || field === "rule") {
    const options = field === "rule" ? ([["present", "The rule exists"], ["absent", "There is no such rule"]] as const) : ([["yes", "Yes"], ["no", "No"]] as const);
    const current = field === "rule" ? (value === "present" ? "present" : "absent") : value ? "yes" : "no";
    return (
      <fieldset className="flex flex-wrap gap-3">
        <legend className="mb-1 w-full text-xs font-medium">{field === "rule" ? "Rule" : "Raised as a review signal only"}</legend>
        {options.map(([key, label]) => (
          <label key={key} className="flex items-center gap-1.5 text-[13px]">
            <input type="radio" name={`${id}-choice`} className="size-3.5 accent-primary" checked={current === key} onChange={() => onChange(field === "rule" ? key : key === "yes")} />
            {label}
          </label>
        ))}
      </fieldset>
    );
  }
  if (field === "references") {
    return (
      <div className="space-y-1">
        <label htmlFor={`${id}-references`} className="block text-xs font-medium">
          Required references, separated by commas
        </label>
        <Input id={`${id}-references`} className="h-9 text-[13px]" value={Array.isArray(value) ? value.join(", ") : String(value)} onChange={(event) => onChange(event.target.value)} />
      </div>
    );
  }
  if (["title", "section", "quote"].includes(field)) {
    return (
      <div className="space-y-1">
        <label htmlFor={`${id}-text`} className="block text-xs font-medium">
          Correct text
        </label>
        <textarea id={`${id}-text`} rows={field === "quote" ? 3 : 1} value={String(value ?? "")} onChange={(event) => onChange(event.target.value)} className="w-full rounded-md border border-input bg-card p-2 text-[13px]" />
      </div>
    );
  }
  return (
    <div className="space-y-1">
      <label htmlFor={`${id}-json`} className="block text-xs font-medium">
        Correct value (JSON)
      </label>
      <textarea id={`${id}-json`} rows={2} value={typeof value === "string" ? value : JSON.stringify(value)} onChange={(event) => onChange(event.target.value)} className="w-full rounded-md border border-input bg-card p-2 font-mono text-xs" />
    </div>
  );
}
