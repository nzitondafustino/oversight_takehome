import { DuplicatePublication } from "./DuplicatePublication";
import { RecordActions } from "@/components/management/RecordActions";
import { ArrowLeft } from "lucide-react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useReadiness, useRevisionApproval, useRuleset, useRulesetComparisons, useRulesets } from "@/api/queries";
import type { FieldComparison, RulesetDetail } from "@/api/types";
import { PageHeader } from "@/components/layout/AppLayout";
import { highlightJson } from "@/components/results/JsonViewer";
import { ErrorState, LoadingRows } from "@/components/states/states";
import { RulesetStatusBadge } from "@/components/status/badges";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { formatDateTime, humanize, shortHash } from "@/lib/format";
import { Section } from "@/pages/dashboard/Section";
import { Facts } from "@/pages/evaluations/display";
import { VersionChangesPanel } from "./review/VersionPanels";
import { ValueText } from "./actor";
import { EditsSummary, PublicationWorkflow } from "./PublicationWorkflow";
import { ApprovalBadge } from "./DocumentPanels";
import { displayTitle } from "./library";
import { VersionReview } from "./review/ReviewViews";
import { rulesOf, RulesView } from "./RulesView";
import { LifecycleActions } from "./Lifecycle";
import { sourceText } from "./PoliciesPage";

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

/**
 * The compiler's field comparisons grouped by rule, beside the rule as this version holds it. A rule in the
 * comparisons but not in the artifact (or the reverse) is called out, never filled in.
 */
function OtherVersions({ version }: { version: RulesetDetail }) {
  const versions = useRulesets({ name: version.name, limit: 50, offset: 0, include_superseded: true });
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
          {item.superseded_by ? <span>Superseded by {item.superseded_by.name} v{item.superseded_by.version}</span> : <RulesetStatusBadge status={item.status} />}
          {item.publication_mode ? <Badge variant="outline">{item.publication_mode}</Badge> : null}
          <span className="text-xs text-subtle-foreground">{formatDateTime(item.created_at)}</span>
        </li>
      ))}
    </ul>
  );
}

/** Each document revision a version was compiled from, with that exact revision's own approval. */
function SourceDocuments({ version }: { version: RulesetDetail }) {
  if (!version.compilation) return <p className="text-[13px] text-muted-foreground">This version was uploaded as an artifact, not compiled from stored documents.</p>;
  return (
    <ul className="divide-y divide-border rounded-md border border-border" aria-label="Source documents">
      {version.compilation.documents.map((document) => (
        <SourceDocumentRow key={`${document.document_id}-${document.revision ?? "inline"}`} document={document} />
      ))}
    </ul>
  );
}

function SourceDocumentRow({ document }: { document: RulesetDetail["compilation"] extends infer C ? (C extends { documents: (infer D)[] } ? D : never) : never }) {
  const approval = useRevisionApproval(document.revision != null ? document.document_id : undefined, document.revision ?? undefined);
  return (
    <li className="flex flex-wrap items-center gap-2 px-3 py-2 text-[13px]">
      <span className="min-w-0 flex-1">
        {document.revision != null ? (
          <Link to={`/documents/${encodeURIComponent(document.document_id)}?revision=${document.revision}`} className="font-medium text-primary hover:underline">
            {displayTitle({ title: null, filename: document.filename })}
          </Link>
        ) : (
          <span className="font-medium">{displayTitle({ title: null, filename: document.filename })}</span>
        )}
        <span className="block font-mono text-[11px] text-subtle-foreground">{document.filename}</span>
      </span>
      <span className="text-xs text-muted-foreground">{document.revision != null ? `Revision ${document.revision}` : "Sent inline"}</span>
      {document.revision != null ? (approval.data ? <ApprovalBadge status={approval.data.status} /> : null) : <span className="text-xs text-subtle-foreground">No document approval</span>}
    </li>
  );
}

export function RulesetDetailPage() {
  const navigate = useNavigate();
  const { rulesetId = "" } = useParams();
  const version = useRuleset(rulesetId);
  const readiness = useReadiness(rulesetId, version.data?.status, version.data?.review?.review_sha256);
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? (version.data?.status === "in_review" && version.data.compilation ? "review" : "rules");
  const openRule = (ruleId: string) => setParams({ tab: "rules", rule: ruleId }, { replace: true });
  const comparisons = useRulesetComparisons(version.data?.compilation ? rulesetId : undefined, false);
  const back = (
    <Button asChild variant="outline" size="sm">
      <Link to="/policies?tab=rules">
        <ArrowLeft aria-hidden />
        Policy Management
      </Link>
    </Button>
  );
  if (version.isPending) return <><PageHeader title="Ruleset version" actions={back} /><LoadingRows rows={6} label="Loading ruleset version" /></>;
  if (version.isError) return <><PageHeader title="Ruleset version" actions={back} /><ErrorState error={version.error} onRetry={() => void version.refetch()} /></>;
  const data = version.data;

  return (
    <>
      <PageHeader title={`${data.name} v${data.version}`} description={`Created ${formatDateTime(data.created_at)}. Its rules never change; only its approval and publication move.`} actions={<div className="flex gap-2">{back}{data.status !== "published" && data.compilation ? <Button asChild size="sm"><a href="#publication-actions">{data.status === "approved" ? "Publish rules" : "Review and approve rules"}</a></Button> : null}</div>} />
      {!data.superseded_by && !data.compilation && data.status !== "published" ? <div role="region" aria-label="How to approve and publish" className="mb-4 space-y-3 rounded-lg border border-status-review-border bg-status-review-soft p-4">
        <h2 className="font-semibold">Create a compiled version to approve and publish</h2>
        <p className="text-sm">This uploaded ruleset has no compiler record linking its rules to reviewed policy documents. It can be previewed, but cannot be approved or published.</p>
        <p className="text-sm">Select the policy documents, compile their rules, resolve the review, then submit, accept, approve and publish the new version. Approval covers that exact version.</p>
        <div className="flex flex-wrap gap-2"><Button asChild size="sm"><Link to="/policies?tab=documents">Select policy documents</Link></Button><Button asChild size="sm" variant="outline"><Link to="/policies?tab=rules">Find a compiled version</Link></Button></div>
      </div> : null}
      <div className="mb-4 flex flex-wrap items-center gap-2 text-[13px]">
        {data.superseded_by ? <Badge variant="neutral">Superseded</Badge> : <RulesetStatusBadge status={data.status} />}
        {data.status === "draft" || data.status === "in_review" ? <RecordActions path={`/v1/rulesets/${encodeURIComponent(data.ruleset_id)}`} title={data.status === "draft" ? "draft version" : "review version"} onDeleted={() => navigate("/policies?tab=rules")} /> : null}
        {data.publication_mode ? <span className="text-muted-foreground">for {data.publication_mode}</span> : null}
      </div>
      {!data.superseded_by && (data.status === "draft" || data.status === "in_review") ? <div className="mb-4"><DuplicatePublication version={data} /></div> : null}
      {data.superseded_by ? <div role="status" className="mb-4 rounded-lg border border-primary bg-primary-soft p-4">This version was superseded by <Link className="font-medium text-primary hover:underline" to={`/policies/${encodeURIComponent(data.superseded_by.ruleset_id)}`}>{data.superseded_by.name} v{data.superseded_by.version}</Link>. Its original content is retained for history.</div> : null}
      <div className="grid items-start gap-4 xl:grid-cols-3">
        <div className="min-w-0 xl:col-span-2">
          <Tabs value={tab} onValueChange={(value) => setParams({ tab: value }, { replace: true })}>
            <TabsList>
              <TabsTrigger value="documents">Documents</TabsTrigger>
              <TabsTrigger value="rules">Rules</TabsTrigger>
              <TabsTrigger value="review">Review</TabsTrigger>
              <TabsTrigger value="history">History</TabsTrigger>
            </TabsList>
            <TabsContent value="documents">
              <Section id="documents" title="Source documents" description="The exact document revisions this version was compiled from, each with its own approval.">
                <SourceDocuments version={data} />
              </Section>
            </TabsContent>
            <TabsContent value="rules">
              <Section id="rules" title="Rules" description={data.status === "published" ? "What this version checks. Editing makes a new draft version: this one never changes." : "What this version checks, in plain words. Edit a rule to make the next draft version."}>
                <div className="space-y-3">
                  <VersionChangesPanel rulesetId={data.ruleset_id} titles={Object.fromEntries(rulesOf(data).map((rule) => [rule.rule_id, rule.title ?? rule.rule_id]))} />
                  <EditsSummary version={data} onOpenRule={openRule} />
                  <RulesView key={data.ruleset_id} version={data} focus={params.get("rule")} />
                </div>
              </Section>
            </TabsContent>
            <TabsContent value="review">
              <Section id="review" title="Review" description="How each interpretation of the policy was decided.">
                <VersionReview version={data} />
              </Section>
            </TabsContent>
            <TabsContent value="history" className="space-y-4">
              <Section id="audit" title="Review, approval and history">
                <Audit version={data} comparisons={comparisons.data?.comparisons ?? []} />
              </Section>
              <Section id="versions" title={`Versions of ${data.name}`}>
                <OtherVersions version={data} />
              </Section>
              <details className="rounded-lg border border-border bg-card px-4 py-3 text-xs">
                <summary className="cursor-pointer text-[13px] font-medium">Provenance and artifact</summary>
                <div className="mt-3 space-y-3">
                  <Provenance version={data} />
                  <pre className="max-h-[32rem] overflow-auto rounded-md bg-muted/40 p-3 font-mono text-[11px] leading-4" aria-label="Artifact JSON">
                    {highlightJson(JSON.stringify(data.artifact, null, 2))}
                  </pre>
                </div>
              </details>
            </TabsContent>
          </Tabs>
        </div>
        <div id="publication-actions" className="scroll-mt-6 space-y-4">
          <Section id="lifecycle" title="Next step">
            <div className="space-y-4">
              {data.superseded_by ? <Button asChild size="sm"><Link to={`/policies/${encodeURIComponent(data.superseded_by.ruleset_id)}`}>View published successor</Link></Button> : <><PublicationWorkflow version={data} onOpenRule={openRule} /><LifecycleActions version={data} canPublish={readiness.data?.can_publish ?? false} /></>}
            </div>
          </Section>
        </div>
      </div>
    </>
  );
}
