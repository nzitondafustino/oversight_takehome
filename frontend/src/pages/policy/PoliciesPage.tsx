import { ShieldCheck } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useRulesets } from "@/api/queries";
import type { RulesetSummary } from "@/api/types";
import { PageHeader } from "@/components/layout/AppLayout";
import { EmptyState, ErrorState, LoadingRows } from "@/components/states/states";
import { RulesetStatusBadge } from "@/components/status/badges";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDateTime, formatRelative, shortHash } from "@/lib/format";
import { Pager } from "@/pages/dashboard/Section";

const PAGE_SIZE = 20;

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

export function PoliciesPage() {
  const [offset, setOffset] = useState(0);
  const [name, setName] = useState("");
  const names = useRulesets({ limit: 100, offset: 0 });
  const versions = useRulesets({ limit: PAGE_SIZE, offset, ...(name ? { name } : {}) });
  const nameOptions = [...new Set((names.data?.items ?? []).map((item) => item.name))].sort();

  return (
    <>
      <PageHeader
        title="Policy Management"
        description="Ruleset versions through review, approval and publication. Each version's content is immutable; only its lifecycle state moves."
        actions={
          <Button asChild size="sm" variant="outline">
            <Link to="/documents">Policy documents</Link>
          </Button>
        }
      />
      <Card>
        <CardContent className="pt-4">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <label htmlFor="ruleset-name-filter" className="text-xs font-medium text-muted-foreground">
              Name
            </label>
            <select
              id="ruleset-name-filter"
              value={name}
              onChange={(event) => {
                setName(event.target.value);
                setOffset(0);
              }}
              className="h-8 rounded-md border border-input bg-card px-2 text-[13px]"
            >
              <option value="">Any</option>
              {nameOptions.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
            {versions.data ? <span className="ml-auto text-xs text-subtle-foreground">{versions.data.total} versions</span> : null}
          </div>
          {versions.isPending ? (
            <LoadingRows rows={6} label="Loading ruleset versions" />
          ) : versions.isError ? (
            <ErrorState error={versions.error} onRetry={() => void versions.refetch()} />
          ) : versions.data.items.length === 0 ? (
            <EmptyState icon={<ShieldCheck className="size-5" aria-hidden />} title="No ruleset versions yet" description="Extract policy documents, review the extraction, and make a draft version from it." />
          ) : (
            <>
              <div className="-mx-5 border-y border-border">
                <Table className="min-w-[860px]">
                  <caption className="sr-only">Ruleset versions</caption>
                  <TableHeader>
                    <TableRow className="hover:bg-transparent">
                      <TableHead className="pl-5">Version</TableHead>
                      <TableHead>Lifecycle</TableHead>
                      <TableHead>Publication</TableHead>
                      <TableHead className="text-right">Active rules</TableHead>
                      <TableHead>Source</TableHead>
                      <TableHead className="pr-5">Created</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {versions.data.items.map((version) => (
                      <TableRow key={version.ruleset_id}>
                        <TableCell className="pl-5">
                          <Link to={`/policies/${encodeURIComponent(version.ruleset_id)}`} className="font-medium text-primary hover:underline">
                            {version.name} v{version.version}
                          </Link>
                          <p className="font-mono text-[11px] text-subtle-foreground" title={version.artifact_sha256}>
                            {version.ruleset_id} · {shortHash(version.artifact_sha256)}
                          </p>
                        </TableCell>
                        <TableCell>
                          <div className="flex flex-wrap gap-1">
                            <RulesetStatusBadge status={version.status} />
                            <ReviewState version={version} />
                          </div>
                        </TableCell>
                        <TableCell className="text-xs">
                          {version.publication_mode ? (
                            <Badge variant={version.publication_mode === "production" ? "pass" : "review"}>{version.publication_mode}</Badge>
                          ) : version.approval_mode ? (
                            <span className="text-muted-foreground">approved for {version.approval_mode}</span>
                          ) : (
                            <span className="text-subtle-foreground">—</span>
                          )}
                        </TableCell>
                        <TableCell className="tabular text-right">
                          {version.active_rule_count}/{version.rule_count}
                        </TableCell>
                        <TableCell className="max-w-72 text-xs text-muted-foreground">{sourceText(version)}</TableCell>
                        <TableCell className="pr-5 text-xs" title={formatDateTime(version.created_at)}>
                          {formatRelative(version.created_at)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
              <Pager offset={offset} limit={PAGE_SIZE} total={versions.data.total} onChange={setOffset} busy={versions.isFetching} />
            </>
          )}
        </CardContent>
      </Card>
    </>
  );
}
