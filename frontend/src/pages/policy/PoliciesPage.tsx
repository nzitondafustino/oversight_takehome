import { RecordActions } from "@/components/management/RecordActions";
import { Upload } from "lucide-react";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useExtraction, useExtractions, useRuleset, useRulesets } from "@/api/queries";
import type { ExtractionListItem, RulesetSummary } from "@/api/types";
import { PageHeader } from "@/components/layout/AppLayout";
import { EmptyState, ErrorState, LoadingRows } from "@/components/states/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Drawer } from "@/components/ui/drawer";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import { SELECT } from "@/pages/evaluations/display";
import { UploadDocument } from "./DocumentPanels";
import { Library } from "./DocumentsPage";
import { ExtractionHistory, unresolvedCount } from "./ExtractionHistory";
import { ExtractionReview, VersionReview } from "./review/ReviewViews";
import { RULESET_STATUS_TEXT } from "./rulesetText";
import { PolicyAnalytics } from "./PolicyAnalytics";
import { RulesView } from "./RulesView";
import { pendingCompilations } from "./workflow";

/** Where a version's content came from, as the API records it. An uploaded artifact can never be approved. */
export function sourceText(version: RulesetSummary): string {
  if (!version.compilation) return "Uploaded artifact: cannot be approved";
  if (version.compilation.extraction_id) return `From extraction ${version.compilation.extraction_id}`;
  return `Compiled from documents (${version.compilation.recording_set})`;
}

/** The review's outcome beside the status: an in-review version may be waiting, accepted; a draft may be a rejection. */
export function ReviewState({ version }: { version: RulesetSummary }) {
  if (version.status === "in_review") return version.review ? <Badge variant="info">Review accepted</Badge> : <Badge variant="neutral">Awaiting review</Badge>;
  if (version.status === "draft" && version.review?.decision === "rejected") return <Badge variant="flag">Rejected</Badge>;
  return null;
}

/** The policies a reviewer manages, in three places: the documents, the rules compiled from them, and what to review. */
export function PoliciesPage() {
  const [params, setParams] = useSearchParams();
  const [uploading, setUploading] = useState(false);
  const extractions = useExtractions({ limit: 100, offset: 0 });
  const versions = useRulesets({ limit: 100, offset: 0 });
  const pending = pendingCompilations(extractions.data?.items ?? [], versions.data?.items ?? []);
  const asked = params.get("tab");
  const tab = asked === "analytics" || asked === "rules" || asked === "review" || asked === "documents" ? asked : "documents";
  const choose = (next: Record<string, string>) => setParams(next, { replace: true });
  return (
    <>
      <PageHeader
        title="Policy Management"
        description="Documents hold the policy text. Rules are what runs on receipts, compiled from the documents. Review is where you settle how a policy was read before its rules can be approved and published."
        actions={
          tab === "documents" ? (
            <Button size="sm" onClick={() => setUploading(true)}>
              <Upload aria-hidden />
              Upload document
            </Button>
          ) : null
        }
      />
      <Tabs value={tab} onValueChange={(value) => choose({ tab: value })}>
        <TabsList>
          <TabsTrigger value="documents">Documents</TabsTrigger>
          <TabsTrigger value="rules">Rules</TabsTrigger>
          <TabsTrigger value="analytics">Analytics</TabsTrigger>
          <TabsTrigger value="review">Review{pending.length ? ` · ${pending.reduce((total, item) => total + unresolvedCount(item), 0)} to resolve` : ""}</TabsTrigger>
        </TabsList>
        <TabsContent value="analytics"><PolicyAnalytics /></TabsContent>
        <TabsContent value="documents">
          <Library onUpload={() => setUploading(true)} />
        </TabsContent>
        <TabsContent value="rules">
          <RulesTab chosen={params.get("version")} onChoose={(version) => choose({ tab: "rules", version })} />
        </TabsContent>
        <TabsContent value="review">
          <ReviewTab pending={pending} loading={extractions.isPending} chosen={params.get("compilation")} document={params.get("document")} onChoose={(compilation) => choose({ tab: "review", compilation })} />
        </TabsContent>
      </Tabs>
      <Drawer open={uploading} onClose={() => setUploading(false)} title="Upload a policy document" description="Markdown (.md) only, UTF-8.">
        <UploadDocument onDone={() => setUploading(false)} />
      </Drawer>
    </>
  );
}

function versionLabel(version: RulesetSummary): string {
  return `${version.name} v${version.version} · ${RULESET_STATUS_TEXT[version.status] ?? version.status}`;
}

function RulesTab({ chosen, onChoose }: { chosen: string | null; onChoose: (version: string) => void }) {
  const versions = useRulesets({ limit: 100, offset: 0 });
  const items = versions.data?.items ?? [];
  const fallback = items.find((item) => item.status === "published") ?? items[0];
  const id = chosen && items.some((item) => item.ruleset_id === chosen) ? chosen : fallback?.ruleset_id;
  const version = useRuleset(id);
  if (versions.isPending) return <LoadingRows rows={5} label="Loading rules" />;
  if (versions.isError) return <ErrorState error={versions.error} onRetry={() => void versions.refetch()} />;
  if (!items.length) return <EmptyState title="No rules yet" description="Approve documents, compile them, and review the result to make the first ruleset." />;
  const summary = items.find((item) => item.ruleset_id === id)!;
  return (
    <div className="space-y-3">
      <VersionHistory />
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor="rules-version" className="text-xs font-medium text-muted-foreground">
          Ruleset
        </label>
        <select id="rules-version" className={cn(SELECT, "w-full sm:w-72")} value={id} onChange={(event) => onChoose(event.target.value)}>
          {items.map((item) => (
            <option key={item.ruleset_id} value={item.ruleset_id}>
              {versionLabel(item)}
            </option>
          ))}
        </select>
        <Button asChild size="sm" variant="outline" className="ml-auto">
          <Link to={`/policies/${encodeURIComponent(summary.ruleset_id)}`}>{summary.status === "published" ? "Version details" : "Continue to approval"}</Link>
        </Button>
      </div>
      {version.isPending ? <LoadingRows rows={6} label="Loading rules" /> : version.isError ? <ErrorState error={version.error} /> : <RulesView key={version.data.ruleset_id} version={version.data} />}
    </div>
  );
}

function VersionHistory() {
  const [page, setPage] = useState(0);
  const [showSuperseded, setShowSuperseded] = useState(false);
  const versions = useRulesets({ limit: 10, offset: page * 10, include_superseded: showSuperseded });
  return <section className="rounded-lg border border-border bg-card p-4 space-y-3" aria-label="All ruleset versions">
    <div><h2 className="font-semibold">All ruleset versions</h2><p className="text-sm text-muted-foreground">Draft, in-review, approved and published versions. Delete drafts and in-review versions here; approved and published versions are protected.</p></div>
    <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={showSuperseded} onChange={event => { setShowSuperseded(event.target.checked); setPage(0); }} />Show superseded versions</label>
    {versions.isPending ? <LoadingRows rows={3} label="Loading versions" /> : versions.isError ? <ErrorState error={versions.error} /> : <>
      <div className="overflow-x-auto"><table className="w-full text-sm text-left"><thead><tr className="border-b"><th className="py-2">Version</th><th>Status</th><th>Created</th><th>Actions</th></tr></thead><tbody>{versions.data.items.map(item => <tr key={item.ruleset_id} className="border-b last:border-0"><td className="py-3">{item.name} v{item.version}</td><td>{item.superseded_by ? `Superseded by ${item.superseded_by.name} v${item.superseded_by.version}` : RULESET_STATUS_TEXT[item.status] ?? item.status}</td><td>{formatDateTime(item.created_at)}</td><td><Link className="text-primary hover:underline" to={`/policies/${encodeURIComponent(item.ruleset_id)}`}>{item.superseded_by ? "View history" : item.compilation && item.status === "approved" ? "Publish rules" : item.compilation && item.status === "in_review" ? "Review / approve rules" : item.status === "draft" ? "View / delete draft" : "View version"}</Link>{!item.superseded_by && (item.status === "draft" || item.status === "in_review") ? <span className="ml-2 inline-flex"><RecordActions path={`/v1/rulesets/${encodeURIComponent(item.ruleset_id)}`} title={item.status === "draft" ? "draft version" : "review version"} recordName={`${item.name} v${item.version}`} onDeleted={() => { if (page > 0 && versions.data.items.length === 1) setPage(p => p - 1); }} /></span> : null}</td></tr>)}</tbody></table></div>
      <div className="flex items-center justify-between gap-2 text-sm"><span>{versions.data.total} versions · Page {page + 1} of {Math.max(1, Math.ceil(versions.data.total / 10))}</span><div className="flex gap-2"><Button size="sm" variant="outline" disabled={page === 0} onClick={() => setPage(p => p - 1)}>Previous versions</Button><Button size="sm" variant="outline" disabled={(page + 1) * 10 >= versions.data.total} onClick={() => setPage(p => p + 1)}>Next versions</Button></div></div>
    </>}
  </section>;
}

function ReviewTab({ pending, loading, chosen, document, onChoose }: { pending: ExtractionListItem[]; loading: boolean; chosen: string | null; document: string | null; onChoose: (id: string) => void }) {
  const versions = useRulesets({ limit: 100, offset: 0 });
  const waiting = (versions.data?.items ?? []).filter((item) => item.status === "in_review" || item.status === "approved");
  const published = (versions.data?.items ?? []).find((item) => item.status === "published");
  const id = chosen && pending.some((item) => item.extraction_id === chosen) ? chosen : pending[0]?.extraction_id;
  const extraction = useExtraction(id ?? "");
  const history = useRuleset(id ? undefined : published?.ruleset_id);
  if (loading || versions.isPending) return <LoadingRows rows={5} label="Loading what needs review" />;
  return (
    <div className="space-y-4">
      {waiting.length ? (
        <div className="rounded-md border border-border bg-card px-4 py-3 text-[13px]" role="region" aria-label="Versions waiting">
          <p className="font-medium">Next: {waiting.length === 1 ? "a ruleset version is" : `${waiting.length} ruleset versions are`} waiting for a lifecycle step</p>
          <ul className="mt-1 space-y-0.5">
            {waiting.map((item) => (
              <li key={item.ruleset_id}>
                <Link to={`/policies/${encodeURIComponent(item.ruleset_id)}`} className="text-primary hover:underline">
                  {versionLabel(item)}: {item.status === "approved" ? "publish it" : "approve it"}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {pending.length ? (
        <>
          {pending.length > 1 ? (
            <div className="flex flex-wrap items-center gap-2">
              <label htmlFor="review-compilation" className="text-xs font-medium text-muted-foreground">
                Reviewing
              </label>
              <select id="review-compilation" className={cn(SELECT, "w-full sm:w-96")} value={id} onChange={(event) => onChoose(event.target.value)}>
                {pending.map((item) => (
                  <option key={item.extraction_id} value={item.extraction_id}>
                    Compiled {formatDateTime(item.created_at)} · {item.documents.length} documents · {unresolvedCount(item)} unresolved
                  </option>
                ))}
              </select>
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">
              Reviewing the compilation of {formatDateTime(pending[0]!.created_at)}, {pending[0]!.documents.length} documents.{" "}
              <Link className="text-primary hover:underline" to={`/documents/extractions/${encodeURIComponent(pending[0]!.extraction_id)}?tab=approval`}>
                Approval readiness
              </Link>
            </p>
          )}
          {extraction.isPending ? <LoadingRows rows={5} label="Loading the review" /> : extraction.isError ? <ErrorState error={extraction.error} /> : <ExtractionReview extraction={extraction.data} initialDocument={document} />}
        </>
      ) : published ? (
        <>
          <p className="text-[13px] text-muted-foreground">Nothing is waiting for review. These are the decisions behind the published rules, {versionLabel(published)}.</p>
          {history.isPending ? <LoadingRows rows={5} label="Loading the published review" /> : history.isError ? <ErrorState error={history.error} /> : <VersionReview version={history.data} />}
        </>
      ) : (
        <EmptyState title="Nothing to review" description="Compile approved documents to review how their rules were read." action={<Button asChild size="sm"><Link to="/policies?tab=documents">Open documents</Link></Button>} />
      )}
      <details className="rounded-lg border border-border bg-card px-4 py-3">
        <summary className="cursor-pointer text-[13px] font-medium">Compilation history</summary>
        <div className="mt-3">
          <ExtractionHistory pageSize={10} />
        </div>
      </details>
    </div>
  );
}
