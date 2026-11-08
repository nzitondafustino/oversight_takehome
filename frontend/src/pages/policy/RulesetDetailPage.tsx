import { ArrowLeft } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useRuleset, useRulesetComparisons, useRulesets } from "@/api/queries";
import type { FieldComparison, RulesetDetail } from "@/api/types";
import { PageHeader } from "@/components/layout/AppLayout";
import { highlightJson } from "@/components/results/JsonViewer";
import { EmptyState, ErrorState, LoadingRows } from "@/components/states/states";
import { RulesetStatusBadge } from "@/components/status/badges";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/misc";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { formatDateTime, humanize, shortHash } from "@/lib/format";
import { Section } from "@/pages/dashboard/Section";
import { Facts } from "@/pages/evaluations/display";
import { ValueText } from "./actor";
import { ComparisonCard } from "./DecisionForm";
import { LifecycleActions } from "./Lifecycle";
import { ReviewState, sourceText } from "./PoliciesPage";

/** The fields every rule in the core's RuleSet schema carries; the rest is shown as stored. */
interface ArtifactRule {
  rule_id: string;
  title?: string;
  policy_doc?: string;
  status?: string;
  kind?: string;
  severity?: string | null;
  source_quote?: string | null;
  params?: Record<string, unknown>;
  [key: string]: unknown;
}

function rulesOf(version: RulesetDetail): ArtifactRule[] {
  const rules = version.artifact.rules;
  return Array.isArray(rules) ? (rules as ArtifactRule[]) : [];
}

function Provenance({ version }: { version: RulesetDetail }) {
  const compilation = version.compilation;
  return (
    <div className="space-y-4">
      <Facts
        items={[
          ["Source", sourceText(version)],
          ["Recorded source", `${version.source.kind}${version.source.reference ? ` · ${version.source.reference}` : ""}${version.source.description ? ` · ${version.source.description}` : ""}`],
          ["Artifact SHA-256", <span className="break-all font-mono text-xs">{version.artifact_sha256}</span>],
          ["Schema · ruleset label", `${version.schema_version} · ${version.ruleset_version}`],
          ["Rules", `${version.active_rule_count} active of ${version.rule_count}`],
          ["Based on", version.based_on_version ? `${version.name} v${version.based_on_version}` : "—"],
        ]}
      />
      {compilation ? (
        <div className="space-y-2 border-t border-border pt-3">
          <h3 className="text-sm font-semibold">Compilation</h3>
          <Facts
            items={[
              ["Readings", `${compilation.reading_kind} · recording set ${compilation.recording_set}`],
              ["Extraction", compilation.extraction_id ? <Link className="font-mono text-xs text-primary hover:underline" to={`/documents/extractions/${encodeURIComponent(compilation.extraction_id)}`}>{compilation.extraction_id}</Link> : "Documents sent inline"],
              ["Extraction SHA-256", <span className="break-all font-mono text-xs">{compilation.extraction_sha256}</span>],
              ["Prepared review", compilation.prepared_review ? `${compilation.prepared_review.review_id} · ${humanize(compilation.prepared_review.policy)}` : "—"],
              ["Fields needing review", String(compilation.comparisons_needing_review)],
              ["Ready without review", compilation.ready_for_approval ? "Yes" : "No"],
            ]}
          />
          {compilation.blockers.length ? (
            <ul className="space-y-0.5 text-xs text-status-review-text" aria-label="Unresolved at compilation">
              {compilation.blockers.map((blocker) => (
                <li key={blocker}>{blocker}</li>
              ))}
            </ul>
          ) : null}
          <ul className="space-y-0.5 text-xs" aria-label="Compiled documents">
            {compilation.documents.map((document) => (
              <li key={document.document_id}>
                <Link to={`/documents/${encodeURIComponent(document.document_id)}`} className="text-primary hover:underline">
                  {document.filename}
                </Link>
                {document.revision != null ? ` r${document.revision}` : ""} <span className="font-mono text-subtle-foreground">{shortHash(document.sha256)}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function Audit({ version, comparisons }: { version: RulesetDetail; comparisons: readonly FieldComparison[] }) {
  const named = new Map(comparisons.map((item) => [item.comparison_id, `${item.rule_id} ${item.field}`]));
  const review = version.review;
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-sm font-semibold">Review</h3>
        {review ? (
          <div className="mt-1 space-y-1 text-[13px]">
            <p>
              <Badge variant={review.decision === "accepted" ? "pass" : "flag"}>{humanize(review.decision)}</Badge> by {review.reviewer.claimed} <span className="text-xs text-subtle-foreground">(claimed, not verified)</span> ·{" "}
              {formatDateTime(review.decided_at)}
            </p>
            {review.reason ? <p className="text-muted-foreground">Reason: {review.reason}</p> : null}
            {review.review_sha256 ? <p className="break-all font-mono text-xs text-subtle-foreground">review {review.review_sha256}</p> : null}
            {review.decisions?.length ? (
              <ul className="space-y-1 text-xs" aria-label="Review decisions">
                {review.decisions.map((decision, index) => (
                  <li key={index} className="rounded-md border border-border px-2 py-1">
                    <span className="font-medium">{named.get(String(decision.comparison_id)) ?? String(decision.comparison_id)}</span>: {humanize(String(decision.action))}
                    {"value" in decision ? <> → <ValueText value={decision.value} /></> : null}
                    <span className="block text-subtle-foreground">
                      by {String(decision.reviewer)}
                      {decision.rationale ? ` · ${String(decision.rationale)}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : (
          <p className="mt-1 text-[13px] text-muted-foreground">No review decision for the current submission.</p>
        )}
      </div>
      <div>
        <h3 className="text-sm font-semibold">Approval and publication</h3>
        <Facts
          className="mt-1"
          items={[
            ["Approval reference", <span className="break-all font-mono text-xs">{version.approval_reference ?? "—"}</span>],
            ["Approved for", version.approval_mode ? `${version.approval_mode} · ${formatDateTime(version.approved_at)}` : "—"],
            ["Published", version.publication_mode ? `${version.publication_mode} · ${formatDateTime(version.published_at)}` : "—"],
            ["Submitted", formatDateTime(version.submitted_at)],
          ]}
        />
      </div>
      <div>
        <h3 className="text-sm font-semibold">Lifecycle events</h3>
        <ol className="mt-1 space-y-1.5 border-l border-border pl-3 text-xs" aria-label="Lifecycle events">
          {version.transitions.map((event, index) => (
            <li key={index}>
              <span className="font-medium">
                {event.from_status ? `${humanize(event.from_status)} → ` : "Created as "}
                {humanize(event.to_status)}
              </span>{" "}
              · {formatDateTime(event.at)} · {event.actor ? `${event.actor.claimed} (claimed)` : "no actor given"}
              {event.reason ? <span className="block text-muted-foreground">{event.reason}</span> : null}
              <span className="block font-mono text-[11px] text-subtle-foreground">
                content {shortHash(event.content_hash)}
                {event.approval_reference ? ` · approval ${shortHash(event.approval_reference)}` : ""}
              </span>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}

function Rules({ version }: { version: RulesetDetail }) {
  const [query, setQuery] = useState("");
  const rules = rulesOf(version).filter((rule) => !query || `${rule.rule_id} ${rule.title ?? ""} ${rule.policy_doc ?? ""}`.toLowerCase().includes(query.toLowerCase()));
  return (
    <div className="space-y-2">
      <Input aria-label="Find a rule" placeholder="Find a rule by ID, title or document" className="h-8 max-w-sm text-[13px]" value={query} onChange={(event) => setQuery(event.target.value)} />
      <ul className="space-y-1.5">
        {rules.map((rule) => (
          <li key={rule.rule_id} className="rounded-md border border-border">
            <details>
              <summary className="flex cursor-pointer flex-wrap items-center gap-1.5 px-3 py-2 text-[13px]">
                <span className="font-mono font-semibold">{rule.rule_id}</span>
                <span>{rule.title}</span>
                {rule.status && rule.status !== "active" ? <Badge variant="neutral">{rule.status}</Badge> : null}
                {rule.severity ? <Badge variant="outline">{rule.severity}</Badge> : null}
                <span className="ml-auto text-xs text-subtle-foreground">
                  {rule.kind} · {rule.policy_doc}
                </span>
              </summary>
              <div className="border-t border-border px-3 py-2">
                {rule.source_quote ? <blockquote className="mb-2 border-l-2 border-border pl-3 text-xs">“{rule.source_quote}”</blockquote> : null}
                <pre className="max-h-72 overflow-auto rounded-md bg-muted/40 p-2 font-mono text-[11px] leading-4">{highlightJson(JSON.stringify(rule, null, 2))}</pre>
              </div>
            </details>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * The compiler's field comparisons grouped by rule, beside the rule as this version holds it. A rule in the
 * comparisons but not in the artifact (or the reverse) is called out, never filled in.
 */
function Comparison({ version }: { version: RulesetDetail }) {
  const [everything, setEverything] = useState(false);
  const comparisons = useRulesetComparisons(version.compilation ? version.ruleset_id : undefined, false);
  const rules = useMemo(() => new Map(rulesOf(version).map((rule) => [rule.rule_id, rule])), [version]);
  if (!version.compilation) return <EmptyState title="No comparisons" description="This version is an uploaded artifact: the compiler never read policy text for it." className="py-6" />;
  if (comparisons.isPending) return <LoadingRows rows={4} label="Loading comparisons" />;
  if (comparisons.isError) return <ErrorState error={comparisons.error} onRetry={() => void comparisons.refetch()} />;

  const byRule = new Map<string, FieldComparison[]>();
  for (const item of comparisons.data.comparisons) byRule.set(item.rule_id, [...(byRule.get(item.rule_id) ?? []), item]);
  const unsettled = (items: FieldComparison[]) => items.some((item) => item.status !== "agreed" || item.review_required);
  const shown = [...byRule.entries()].filter(([, items]) => everything || unsettled(items));
  const notCompared = [...rules.keys()].filter((ruleId) => !byRule.has(ruleId));

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3 text-[13px]">
        <span>
          {byRule.size} rules compared · {[...byRule.values()].filter(unsettled).length} with a field not agreed
        </span>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={everything} onChange={(event) => setEverything(event.target.checked)} className="size-3.5 accent-primary" />
          Show agreed rules too
        </label>
      </div>
      {notCompared.length ? <p className="text-xs text-status-review-text">In this version but not in the comparisons: {notCompared.join(", ")}</p> : null}
      {shown.length === 0 ? <p className="text-[13px] text-muted-foreground">Every compared field agrees.</p> : null}
      {shown.map(([ruleId, items]) => {
        const rule = rules.get(ruleId);
        return (
          <section key={ruleId} aria-label={`Rule ${ruleId}`} className="space-y-2 rounded-md border border-border p-3">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="font-mono text-[13px] font-semibold">{ruleId}</span>
              {rule ? <span className="text-[13px]">{rule.title}</span> : <Badge variant="flag">Not in this version</Badge>}
            </div>
            {rule ? (
              <div className="rounded-md bg-muted/40 p-2 text-xs">
                <p className="font-medium">In this version (executable)</p>
                <p>Quote: {rule.source_quote ? `“${rule.source_quote}”` : <span className="text-status-review-text">missing</span>}</p>
                <p>Severity: {rule.severity ?? "—"}</p>
                <pre className="mt-1 max-h-40 overflow-auto font-mono text-[11px]">{JSON.stringify(rule.params ?? {}, null, 2)}</pre>
              </div>
            ) : null}
            <ul className="space-y-2">
              {items.filter((item) => everything || item.status !== "agreed" || item.review_required).map((item) => (
                <ComparisonCard key={item.comparison_id} comparison={item} />
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

function OtherVersions({ version }: { version: RulesetDetail }) {
  const versions = useRulesets({ name: version.name, limit: 50, offset: 0 });
  if (versions.isPending) return <LoadingRows rows={2} label="Loading versions" />;
  if (versions.isError) return <ErrorState error={versions.error} />;
  return (
    <ul className="space-y-1 text-[13px]" aria-label="Version history">
      {versions.data.items.map((item) => (
        <li key={item.ruleset_id} className="flex flex-wrap items-center gap-1.5">
          {item.ruleset_id === version.ruleset_id ? (
            <span className="font-medium">v{item.version} (this version)</span>
          ) : (
            <Link to={`/policies/${encodeURIComponent(item.ruleset_id)}`} className="text-primary hover:underline">
              v{item.version}
            </Link>
          )}
          <RulesetStatusBadge status={item.status} />
          {item.publication_mode ? <Badge variant="outline">{item.publication_mode}</Badge> : null}
          <span className="text-xs text-subtle-foreground">{formatDateTime(item.created_at)}</span>
        </li>
      ))}
    </ul>
  );
}

export function RulesetDetailPage() {
  const { rulesetId = "" } = useParams();
  const version = useRuleset(rulesetId);
  const comparisons = useRulesetComparisons(version.data?.compilation ? rulesetId : undefined, false);
  const back = (
    <Button asChild variant="outline" size="sm">
      <Link to="/policies">
        <ArrowLeft aria-hidden />
        All versions
      </Link>
    </Button>
  );
  if (version.isPending) return <><PageHeader title="Ruleset version" actions={back} /><LoadingRows rows={6} label="Loading ruleset version" /></>;
  if (version.isError) return <><PageHeader title="Ruleset version" actions={back} /><ErrorState error={version.error} onRetry={() => void version.refetch()} /></>;
  const data = version.data;

  return (
    <>
      <PageHeader title={`${data.name} v${data.version}`} description={`${data.ruleset_id} · created ${formatDateTime(data.created_at)} · its content never changes`} actions={back} />
      <div className="mb-4 flex flex-wrap items-center gap-1.5">
        <RulesetStatusBadge status={data.status} />
        <ReviewState version={data} />
        {data.publication_mode ? <Badge variant={data.publication_mode === "production" ? "pass" : "review"}>Published for {data.publication_mode}</Badge> : null}
      </div>
      <div className="grid items-start gap-4 xl:grid-cols-3">
        <div className="min-w-0 xl:col-span-2">
          <Tabs defaultValue="overview">
            <TabsList>
              <TabsTrigger value="overview">Provenance and audit</TabsTrigger>
              <TabsTrigger value="comparison">Comparison</TabsTrigger>
              <TabsTrigger value="rules">Rules</TabsTrigger>
              <TabsTrigger value="artifact">Artifact</TabsTrigger>
            </TabsList>
            <TabsContent value="overview" className="space-y-4">
              <Section id="provenance" title="Provenance">
                <Provenance version={data} />
              </Section>
              <Section id="audit" title="Review, approval and history">
                <Audit version={data} comparisons={comparisons.data?.comparisons ?? []} />
              </Section>
            </TabsContent>
            <TabsContent value="comparison">
              <Section id="comparison" title="Extracted against executable" description="The compiler's regex and model readings of each field, beside the rule this version runs.">
                <Comparison version={data} />
              </Section>
            </TabsContent>
            <TabsContent value="rules">
              <Section id="rules" title="Rules" description={`${data.rule_count} rules, as stored in the artifact.`}>
                <Rules version={data} />
              </Section>
            </TabsContent>
            <TabsContent value="artifact">
              <Section id="artifact" title="Artifact" description={`SHA-256 ${data.artifact_sha256}. Numbers are exact decimal strings, as stored.`}>
                <pre className="max-h-[40rem] overflow-auto rounded-md bg-muted/40 p-3 font-mono text-[11px] leading-4" aria-label="Artifact JSON">
                  {highlightJson(JSON.stringify(data.artifact, null, 2))}
                </pre>
              </Section>
            </TabsContent>
          </Tabs>
        </div>
        <div className="space-y-4">
          <Section id="lifecycle" title="Lifecycle">
            <LifecycleActions version={data} />
          </Section>
          <Section id="versions" title={`Versions of ${data.name}`}>
            <OtherVersions version={data} />
          </Section>
        </div>
      </div>
    </>
  );
}
