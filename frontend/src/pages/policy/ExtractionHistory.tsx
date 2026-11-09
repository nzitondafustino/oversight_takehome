import { RecordActions } from "@/components/management/RecordActions";
/** Compilation history: every extraction, newest first, with where it stands and the versions made from it. */
import { useState } from "react";
import { Link } from "react-router-dom";
import { useExtractions, useRulesets } from "@/api/queries";
import type { ExtractionListItem } from "@/api/types";
import { EmptyState, ErrorState, LoadingRows } from "@/components/states/states";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDateTime, formatRelative } from "@/lib/format";
import { Pager } from "@/pages/dashboard/Section";
import { RULESET_STATUS_TEXT } from "./rulesetText";
import { WorkflowStatusBadge } from "./WorkflowSteps";
import { latestReviews, workflowStatus } from "./workflow";

/** What is still unresolved: the latest review's undecided fields, else the compiler's blockers before any review. */
export function unresolvedCount(item: ExtractionListItem): number {
  const { latest } = latestReviews(item);
  return latest ? latest.remaining + latest.error_issues : item.ready_for_approval ? 0 : item.blockers.length;
}

function documentsText(item: ExtractionListItem): string {
  const names = item.documents.map((document) => document.filename.replace(/\.md$/, ""));
  return names.length <= 2 ? names.join(", ") : `${names.slice(0, 2).join(", ")} and ${names.length - 2} more`;
}

export function ExtractionHistory({ documentId, pageSize = 15 }: { documentId?: string; pageSize?: number }) {
  const [offset, setOffset] = useState(0);
  const extractions = useExtractions({ limit: pageSize, offset, ...(documentId ? { document_id: documentId } : {}) });
  const versions = useRulesets({ limit: 100, offset: 0 });
  const compiled = (versions.data?.items ?? []).filter(item => item.compilation);
  if (extractions.isPending) return <LoadingRows rows={3} label="Loading compilation history" />;
  if (extractions.isError) return <ErrorState error={extractions.error} onRetry={() => void extractions.refetch()} />;
  if (!extractions.data.items.length) return <div className="space-y-3"><EmptyState title="No extraction runs recorded" description="Published rulesets can retain compiler evidence without an extraction-run record here. Open Rules to view compiled versions, their source documents and approvals." className="py-6" />{!documentId && compiled.length ? <div><h3 className="text-sm font-medium">Compiled ruleset versions</h3><ul className="mt-2 space-y-1 text-sm">{compiled.map(item => <li key={item.ruleset_id}><Link className="text-primary hover:underline" to={`/policies/${encodeURIComponent(item.ruleset_id)}?tab=history`}>{item.name} v{item.version} · {RULESET_STATUS_TEXT[item.status]}</Link></li>)}</ul></div> : null}</div>;
  return (
    <>
      <div className="-mx-5 border-y border-border">
        <Table className="min-w-[760px]">
          <caption className="sr-only">Compilation history</caption>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="pl-5">Compiled</TableHead>
              <TableHead>Documents</TableHead>
              <TableHead>Mode</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Unresolved</TableHead>
              <TableHead>Ruleset</TableHead>
              <TableHead className="pr-5 text-right">
                <span className="sr-only">Actions</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {extractions.data.items.map((item) => {
              const status = workflowStatus(item);
              const to = `/documents/extractions/${encodeURIComponent(item.extraction_id)}`;
              const unresolved = unresolvedCount(item);
              return (
                <TableRow key={item.extraction_id}>
                  <TableCell className="pl-5 text-xs" title={formatDateTime(item.created_at)}>
                    {formatRelative(item.created_at)}
                  </TableCell>
                  <TableCell className="max-w-64 text-xs">
                    <span className="block truncate" title={item.documents.map((document) => `${document.filename} r${document.revision}`).join(", ")}>
                      {item.documents.length} {item.documents.length === 1 ? "document" : "documents"} · {documentsText(item)}
                    </span>
                  </TableCell>
                  <TableCell className="text-xs">{item.provenance.execution_mode === "live" ? "Live" : "Replay"}</TableCell>
                  <TableCell>
                    <WorkflowStatusBadge status={status} />
                    {item.open_change_requests ? <span className="ml-1.5 text-xs text-status-flag-text">{item.open_change_requests} change requests</span> : null}
                  </TableCell>
                  <TableCell className="tabular text-right text-xs">{unresolved}</TableCell>
                  <TableCell className="text-xs">
                    {item.versions?.length
                      ? item.versions.map((version) => (
                          <Link key={version.ruleset_id} to={`/policies/${encodeURIComponent(version.ruleset_id)}`} className="block text-primary hover:underline">
                            {version.name} v{version.version} · {RULESET_STATUS_TEXT[version.status] ?? version.status}
                          </Link>
                        ))
                      : <span className="text-subtle-foreground">None yet</span>}
                  </TableCell>
                  <TableCell className="pr-5 text-right">
                    <div className="flex justify-end gap-1">
                      <Button asChild size="sm" variant={status === "ready_for_review" || status === "changes_required" ? "default" : "outline"}>
                        <Link to={status === "ready_for_review" || status === "changes_required" ? `${to}?tab=review` : to}>{status === "ready_for_review" || status === "changes_required" ? "Continue review" : "Open results"}</Link>
                      </Button>
                      <details className="text-left text-xs"><summary className="cursor-pointer py-2">Saved reviews ({item.reviews.length})</summary><div className="space-y-2">{item.reviews.map(review => <div key={review.review_id} className="flex items-center gap-2"><span className="font-mono">{review.review_id}</span><RecordActions path={`/v1/documents/extractions/${encodeURIComponent(item.extraction_id)}/reviews/${encodeURIComponent(review.review_id)}`} title="review" recordName={review.review_id} onDeleted={() => {}} /></div>)}</div></details>
                      <Button asChild size="sm" variant="ghost">
                        <Link to={`${to}?tab=provenance`}>Provenance</Link>
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
      <Pager offset={offset} limit={pageSize} total={extractions.data.total} onChange={setOffset} busy={extractions.isFetching} />
    </>
  );
}
