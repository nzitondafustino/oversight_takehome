/** A ruleset version's executable rules in plain English, one line each, details on demand. */
import { ChevronDown, ChevronRight, Loader2, Pencil, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useEditRules } from "@/api/queries";
import type { RulesetDetail } from "@/api/types";
import { ErrorState } from "@/components/states/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Drawer } from "@/components/ui/drawer";
import { ApiError } from "@/api/client";
import { ActorField, useActor } from "./actor";
import { EDITABLE_TEXT, problemsOf, RuleEditor } from "./RuleEditor";
import { highlightJson } from "@/components/results/JsonViewer";
import { EmptyState } from "@/components/states/states";
import { Input } from "@/components/ui/misc";
import { formatDateTime, humanize, shortHash } from "@/lib/format";
import { cn } from "@/lib/utils";
import { SELECT } from "@/pages/evaluations/display";
import { displayTitle } from "./library";
import { conditionText, paramText, plainRule, type ArtifactRule } from "./rules";

const SEVERITY_VARIANT: Record<string, "flag" | "review" | "neutral"> = { critical: "flag", high: "flag", medium: "review", low: "neutral" };

/** A rule's severity in one word, or what it does when it has none. */
export function severityText(rule: ArtifactRule): string {
  if (rule.kind === "process") return "Process";
  if (rule.kind === "carve_out") return "Exemption";
  if (rule.outcome === "review_signal") return "Review";
  return rule.severity ? rule.severity.charAt(0).toUpperCase() + rule.severity.slice(1) : rule.severity_cases?.length ? "Varies" : "—";
}

/** A changed value in words, for the before-and-after list. */
export function valueText(field: string, value: unknown, rule: ArtifactRule): string {
  if (value === null || value === undefined || value === "") return "none";
  if (field === "status") return value === "active" ? "Active" : "Inactive (removed from evaluation)";
  if (field === "applies_when" || field === "select") return conditionText(value as never, (rule.params ?? {}) as never) || "always";
  if (field.startsWith("params.")) return paramText(value as never);
  if (field === "outcome") return value === "review_signal" ? "send to review" : "raise an exception";
  return String(value);
}

/** Each field a set of edits changes, flattened, params one by one. */
export function changedFields(rule: ArtifactRule, set: Record<string, unknown>): { field: string; before: unknown; after: unknown }[] {
  return Object.entries(set).flatMap(([field, value]) =>
    field === "params"
      ? Object.entries(value as Record<string, unknown>).map(([name, param]) => ({ field: `params.${name}`, before: rule.params?.[name], after: param }))
      : [{ field, before: rule[field], after: value }],
  );
}

export function fieldName(field: string): string {
  return field.startsWith("params.") ? field.slice(7).replace(/_/g, " ") : EDITABLE_TEXT[field] ?? field;
}

export function rulesOf(version: RulesetDetail): ArtifactRule[] {
  const rules = version.artifact.rules;
  return Array.isArray(rules) ? (rules as ArtifactRule[]) : [];
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-1 py-1.5 sm:grid-cols-[10rem_1fr] sm:gap-3">
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd className="text-[13px]">{children}</dd>
    </div>
  );
}

export function RulesView({ version, focus }: { version: RulesetDetail; focus?: string | null }) {
  const rules = rulesOf(version);
  const navigate = useNavigate();
  const save = useEditRules();
  const [actor, setActor] = useActor();
  const [edits, setEdits] = useState<Record<string, Record<string, unknown>>>({});
  const [editing, setEditing] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const [rationale, setRationale] = useState("");
  const editable = version.compilation !== null;
  const pending = Object.entries(edits).filter(([, set]) => Object.keys(set).length);
  const problems = pending.flatMap(([ruleId, set]) => Object.values(problemsOf(rules.find((rule) => rule.rule_id === ruleId)!, set)).map((text) => `${ruleId}: ${text}`));
  const refusal = save.error instanceof ApiError ? (save.error.details as { blockers?: { message: string; rule_id: string | null }[] } | undefined)?.blockers : undefined;
  const [search, setSearch] = useState("");
  const [document, setDocument] = useState("");
  const [open, setOpen] = useState<Set<string>>(new Set(focus ? [focus] : []));
  useEffect(() => {
    // A link to a rule (a publication blocker, a change) opens it and brings it into view, keeping unsaved edits.
    if (!focus) return;
    setOpen((current) => new Set([...current, focus]));
    setSearch("");
    setDocument("");
    requestAnimationFrame(() => globalThis.document.getElementById(`rule-${focus}`)?.scrollIntoView?.({ block: "center" }));
  }, [focus]);
  const documents = useMemo(() => [...new Set(rules.map((rule) => rule.policy_doc).filter(Boolean) as string[])].sort(), [rules]);
  const shown = rules.filter((rule) => (!document || rule.policy_doc === document) && `${rule.rule_id} ${rule.title ?? ""}`.toLowerCase().includes(search.trim().toLowerCase()));
  const toggle = (id: string) =>
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  return (
    <div className="space-y-3">
      <Drawer open={removing !== null} onClose={() => setRemoving(null)} title={`Remove ${removing ?? "rule"}?`}>
        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">Are you sure you want to remove rule <strong>{removing}</strong>? It will be inactive in the new draft. The published version stays unchanged until the draft is approved and published.</p>
          <div className="flex gap-2">
            <Button type="button" variant="outline" onClick={() => setRemoving(null)}>Cancel</Button>
            <Button type="button" onClick={() => {
              if (removing) setEdits((all) => ({ ...all, [removing]: { ...all[removing], status: "not_evaluable" } }));
              setRemoving(null);
            }}>Confirm removal</Button>
          </div>
        </div>
      </Drawer>
      <div className="flex flex-wrap gap-2">
        <div className="relative min-w-52 flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 size-4 text-subtle-foreground" aria-hidden />
          <Input aria-label="Find a rule" placeholder="Find a rule by name" className="h-9 pl-8 text-[13px]" value={search} onChange={(event) => setSearch(event.target.value)} />
        </div>
        <select aria-label="Source document" className={cn(SELECT, "w-64")} value={document} onChange={(event) => setDocument(event.target.value)}>
          <option value="">All source documents</option>
          {documents.map((item) => (
            <option key={item} value={item}>
              {displayTitle({ title: null, filename: item })}
            </option>
          ))}
        </select>
      </div>
      <p className="text-xs text-muted-foreground">
        {shown.length} of {rules.length} rules · {version.active_rule_count} active
      </p>
      {!shown.length ? <EmptyState title="No rule matches" description="Try another name or document." className="py-6" /> : null}
      <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-card" aria-label="Rules">
        {shown.map((rule) => {
          const plain = plainRule(rule);
          const expanded = open.has(rule.rule_id);
          return (
            <li key={rule.rule_id}>
              <button type="button" id={`rule-${rule.rule_id}`} aria-expanded={expanded} onClick={() => toggle(rule.rule_id)} className="grid w-full grid-cols-[1rem_5.5rem_minmax(0,1fr)_auto] items-center gap-2 px-4 py-2 text-left hover:bg-muted/40">
                {expanded ? <ChevronDown className="size-4" aria-hidden /> : <ChevronRight className="size-4" aria-hidden />}
                <span className="font-mono text-xs font-medium text-muted-foreground">{plain.id}</span>
                <span className="truncate text-[13px] font-medium">
                  {plain.name}
                  {edits[rule.rule_id] && Object.keys(edits[rule.rule_id]!).length ? <span className="ml-2 text-xs font-normal text-primary">edited</span> : null}
                </span>
                <Badge variant={SEVERITY_VARIANT[rule.severity ?? ""] ?? "outline"}>{severityText(rule)}</Badge>
              </button>
              {expanded && editing === rule.rule_id ? (
                <div className="space-y-3 border-t border-border bg-background/50 px-4 py-3 pl-10">
                  <RuleEditor rule={rule} set={edits[rule.rule_id] ?? {}} onChange={(set) => setEdits((all) => ({ ...all, [rule.rule_id]: set }))} />
                  <div className="flex gap-2">
                    <Button type="button" size="sm" variant="outline" onClick={() => setEditing(null)}>
                      Done
                    </Button>
                    <Button type="button" size="sm" variant="ghost" onClick={() => setEdits((all) => ({ ...all, [rule.rule_id]: {} }))}>
                      Discard changes to {rule.rule_id}
                    </Button>
                  </div>
                </div>
              ) : expanded ? (
                <div className="border-t border-border bg-background/50 px-4 py-2 pl-10">
                  {plain.inactive ? <p className="mb-1 text-xs text-status-review-text">{plain.inactive}</p> : null}
                  {editable && rule.status !== "retired" ? (
                    <Button type="button" size="sm" variant="outline" className="float-right" onClick={() => setEditing(rule.rule_id)}>
                      <Pencil aria-hidden />
                      Edit
                    </Button>
                  ) : null}
                  {editable && rule.kind !== "process" ? (
                    <Button type="button" size="sm" variant="ghost" className="text-status-flag-text" onClick={() => {
                      if (rule.status === "active" && edits[rule.rule_id]?.status === undefined) {
                        setRemoving(rule.rule_id);
                        return;
                      }
                      setEdits((all) => {
                        if (all[rule.rule_id]?.status !== undefined) {
                          const { status: _status, ...rest } = all[rule.rule_id]!;
                          return { ...all, [rule.rule_id]: rest };
                        }
                        return { ...all, [rule.rule_id]: { ...all[rule.rule_id], status: rule.status === "active" ? "not_evaluable" : "active" } };
                      });
                    }}>{edits[rule.rule_id]?.status !== undefined ? "Undo status change" : rule.status === "active" ? "Remove rule in new draft" : "Restore rule in new draft"}</Button>
                  ) : null}
                  <dl className="divide-y divide-border">
                    <Row label="When it applies">{plain.applies}</Row>
                    <Row label="What it checks">{plain.checks}</Row>
                    <Row label="Limits">{plain.limits.length ? <ul>{plain.limits.map((limit) => <li key={limit}>{limit}</li>)}</ul> : <span className="text-muted-foreground">None</span>}</Row>
                    <Row label="Outcome">{plain.outcome}</Row>
                    <Row label="Source document">{plain.source ? displayTitle({ title: null, filename: plain.source }) : "—"}</Row>
                  </dl>
                  {rule.source_quote ? <blockquote className="mt-2 border-l-2 border-border pl-3 text-xs text-muted-foreground">“{rule.source_quote}”</blockquote> : null}
                  <details className="mt-2 text-xs">
                    <summary className="cursor-pointer text-muted-foreground">Technical details</summary>
                    <pre className="mt-1 max-h-64 overflow-auto rounded-md bg-muted p-2 font-mono text-[11px]" dangerouslySetInnerHTML={{ __html: highlightJson(JSON.stringify(rule, null, 2)) }} />
                  </details>
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>
      {pending.length ? (
        <section className="sticky bottom-0 z-10 space-y-3 rounded-lg border border-primary/30 bg-card p-4 shadow-sm" aria-label="Changes to save">
          <h3 className="text-sm font-semibold">
            {pending.length} {pending.length === 1 ? "rule" : "rules"} changed: before and after
          </h3>
          <ul className="space-y-1 text-[13px]">
            {pending.flatMap(([ruleId, set]) => {
              const rule = rules.find((item) => item.rule_id === ruleId)!;
              return changedFields(rule, set).map((change) => (
                <li key={`${ruleId}-${change.field}`} className="grid gap-1 sm:grid-cols-[5.5rem_9rem_1fr]">
                  <span className="font-mono text-xs">{ruleId}</span>
                  <span className="text-muted-foreground">{fieldName(change.field)}</span>
                  <span>
                    <del className="text-status-flag-text">{valueText(change.field, change.before, rule)}</del> → <ins className="font-medium text-status-pass no-underline">{valueText(change.field, change.after, rule)}</ins>
                  </span>
                </li>
              ));
            })}
          </ul>
          <p className="text-xs text-muted-foreground">
            {version.status === "published" ? "This published version never changes. " : "This version never changes either. "}
            Saving makes the next draft version of {version.name}: the compiler builds it again from the same documents with these changes and checks them, and it then needs review and approval before it can be published.
          </p>
          <div className="grid gap-2 sm:grid-cols-2">
            <ActorField actor={actor} setActor={setActor} id="edit-actor" />
            <div className="space-y-1">
              <label htmlFor="edit-rationale" className="block text-xs font-medium">
                Why these rules change (required)
              </label>
              <Input id="edit-rationale" className="h-8 text-[13px]" value={rationale} onChange={(event) => setRationale(event.target.value)} />
            </div>
          </div>
          {problems.length ? (
            <ul role="alert" className="text-xs text-destructive">
              {problems.map((problem) => (
                <li key={problem}>{problem}</li>
              ))}
            </ul>
          ) : null}
          {save.isError ? (
            <>
              <ErrorState error={save.error} />
              {refusal?.length ? (
                <ul className="text-xs" aria-label="What the compiler refused">
                  {refusal.map((item, index) => (
                    <li key={index}>
                      {item.rule_id ? (
                        <button type="button" className="mr-1 font-mono text-primary hover:underline" onClick={() => { setOpen((current) => new Set([...current, item.rule_id!])); setEditing(item.rule_id); }}>
                          {item.rule_id}
                        </button>
                      ) : null}
                      {item.message}
                    </li>
                  ))}
                </ul>
              ) : null}
            </>
          ) : null}
          <div className="flex gap-2">
            <Button
              type="button"
              disabled={!actor.trim() || !rationale.trim() || problems.length > 0 || save.isPending}
              onClick={() =>
                save.mutate(
                  { rulesetId: version.ruleset_id, body: { actor: actor.trim(), rationale: rationale.trim(), changes: pending.map(([rule_id, set]) => ({ rule_id, set })) } },
                  { onSuccess: (draft) => navigate(`/policies/${encodeURIComponent(draft.ruleset_id)}`) },
                )
              }
            >
              {save.isPending ? <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden /> : null}
              Save as a new draft version
            </Button>
            <Button type="button" variant="ghost" onClick={() => { setEdits({}); setEditing(null); }}>
              Discard all
            </Button>
          </div>
        </section>
      ) : null}
      <details className="text-xs">
        <summary className="cursor-pointer text-muted-foreground">Version technical details</summary>
        <dl className="mt-2 grid gap-1 sm:grid-cols-2">
          <div>
            <dt className="text-muted-foreground">Ruleset hash</dt>
            <dd className="font-mono" title={version.artifact_sha256}>{shortHash(version.artifact_sha256)}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Schema · label</dt>
            <dd>
              {version.schema_version} · {version.ruleset_version}
            </dd>
          </div>
          {version.compilation ? (
            <>
              <div>
                <dt className="text-muted-foreground">Compiled from</dt>
                <dd>
                  {humanize(version.compilation.reading_kind)} readings · {version.compilation.recording_set}
                </dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Extraction hash</dt>
                <dd className="font-mono" title={version.compilation.extraction_sha256}>{shortHash(version.compilation.extraction_sha256)}</dd>
              </div>
            </>
          ) : null}
          <div>
            <dt className="text-muted-foreground">Created</dt>
            <dd>{formatDateTime(version.created_at)}</dd>
          </div>
        </dl>
      </details>
    </div>
  );
}
