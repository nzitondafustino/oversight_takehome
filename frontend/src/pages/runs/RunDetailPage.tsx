import { ArrowLeft, ListTree, Receipt } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useFindings, useRun } from "@/api/queries";
import type { Disposition, Finding, FindingKind } from "@/api/types";
import { PageHeader } from "@/components/layout/AppLayout";
import { ValidationResults } from "@/components/results/ValidationResults";
import { SeverityBadge } from "@/components/results/style";
import { EmptyState, ErrorState, LoadingRows } from "@/components/states/states";
import { DISPOSITION_ORDER, DISPOSITIONS, DispositionBadge } from "@/components/status/badges";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";
import { Pager } from "@/pages/dashboard/Section";

const PAGE_SIZE = 25;
const KINDS: { kind: FindingKind; label: string }[] = [
  { kind: "exception", label: "Exceptions" },
  { kind: "review_signal", label: "Review signals" },
  { kind: "open_question", label: "Open questions" },
  { kind: "uncertainty", label: "Uncertainty" },
];

function text(record: Record<string, unknown>, key: string): string | null {
  const value = record[key];
  return typeof value === "string" ? value : null;
}

function FindingRow({ finding }: { finding: Finding }) {
  const { record } = finding;
  const reasons = Array.isArray(record.reasons) ? (record.reasons as unknown[]).filter((item): item is string => typeof item === "string") : [];
  return (
    <li className="py-3">
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge variant={finding.kind === "exception" ? "flag" : finding.kind === "review_signal" ? "review" : finding.kind === "open_question" ? "info" : "neutral"}>
          {KINDS.find((item) => item.kind === finding.kind)?.label.replace(/s$/, "") ?? finding.kind}
        </Badge>
        {finding.rule_id ? <span className="font-mono text-[13px] font-semibold text-foreground">{finding.rule_id}</span> : null}
        {finding.kind === "exception" ? <SeverityBadge severity={finding.severity} /> : null}
        <span className="text-xs text-subtle-foreground">on</span>
        <span className="font-mono text-xs text-foreground">{finding.receipt_id}</span>
        <DispositionBadge disposition={finding.receipt_disposition} decided={finding.receipt_decided} />
        {!finding.receipt_decided ? <Badge variant="info">Receipt undecided</Badge> : null}
        <span className="ml-auto font-mono text-[11px] text-subtle-foreground">{finding.finding_id}</span>
      </div>
      <div className="mt-1 space-y-1 text-[13px] leading-5">
        {text(record, "explanation") ? <p className="break-words text-foreground">{text(record, "explanation")}</p> : null}
        {text(record, "reason") ? <p className="break-words text-foreground">{text(record, "reason")}</p> : null}
        {reasons.length ? (
          <ul className="list-inside list-disc text-foreground/90">
            {reasons.map((reason, index) => (
              <li key={index}>{reason}</li>
            ))}
          </ul>
        ) : null}
        {finding.kind === "uncertainty" ? (
          <p className="text-xs text-muted-foreground">
            Field <span className="font-mono">{text(record, "field")}</span> · {text(record, "state")}
          </p>
        ) : null}
        <p className="text-xs text-subtle-foreground">
          Lines:{" "}
          {finding.line_ids === null ? "none named" : finding.line_ids.length ? <span className="font-mono">{finding.line_ids.join(", ")}</span> : "whole receipt"}
          {text(record, "policy_doc") ? <> · {text(record, "policy_doc")}</> : null}
        </p>
        {text(record, "evidence") ? (
          <details className="text-xs">
            <summary className="cursor-pointer font-medium text-primary">Policy evidence</summary>
            <blockquote className="mt-1 whitespace-pre-wrap break-words border-l-2 border-border pl-3 text-foreground/85">{text(record, "evidence")}</blockquote>
          </details>
        ) : null}
      </div>
    </li>
  );
}

/** GET /v1/runs/{id}/findings: the stored run's findings in the API's fixed order, filtered by the API. */
function RunFindings({ runId }: { runId: string }) {
  const [kinds, setKinds] = useState<FindingKind[]>([]);
  const [dispositions, setDispositions] = useState<Disposition[]>([]);
  const [offset, setOffset] = useState(0);
  const findings = useFindings(runId, {
    limit: PAGE_SIZE,
    offset,
    ...(kinds.length ? { kind: kinds } : {}),
    ...(dispositions.length ? { disposition: dispositions } : {}),
  });

  function toggle<T>(list: T[], value: T, set: (next: T[]) => void) {
    set(list.includes(value) ? list.filter((item) => item !== value) : [...list, value]);
    setOffset(0);
  }

  return (
    <Card>
      <CardContent className="pt-4">
        <div className="mb-2 flex flex-wrap items-center gap-2" role="group" aria-label="Filter findings">
          {KINDS.map(({ kind, label }) => (
            <button
              key={kind}
              type="button"
              aria-pressed={kinds.includes(kind)}
              onClick={() => toggle(kinds, kind, setKinds)}
              className={cn("h-7 rounded-md border px-2.5 text-xs font-medium", kinds.includes(kind) ? "border-primary bg-primary-soft text-primary" : "border-border text-muted-foreground hover:bg-muted")}
            >
              {label}
            </button>
          ))}
          <span className="mx-1 h-5 w-px bg-border" aria-hidden />
          {DISPOSITION_ORDER.map((disposition) => (
            <button
              key={disposition}
              type="button"
              aria-pressed={dispositions.includes(disposition)}
              onClick={() => toggle(dispositions, disposition, setDispositions)}
              className={cn(
                "h-7 rounded-md border px-2.5 text-xs font-medium",
                dispositions.includes(disposition) ? "border-primary bg-primary-soft text-primary" : "border-border text-muted-foreground hover:bg-muted",
              )}
            >
              {DISPOSITIONS[disposition].label} receipts
            </button>
          ))}
          {findings.data ? <span className="ml-auto text-xs text-subtle-foreground">{findings.data.total} findings</span> : null}
        </div>
        {findings.isPending ? (
          <LoadingRows rows={5} label="Loading findings" />
        ) : findings.isError ? (
          <ErrorState error={findings.error} onRetry={() => void findings.refetch()} />
        ) : findings.data.items.length === 0 ? (
          <EmptyState title="No findings" description={kinds.length || dispositions.length ? "None match these filters." : "This run's receipts have no findings."} className="py-6" />
        ) : (
          <>
            <ul className="divide-y divide-border">
              {findings.data.items.map((finding) => (
                <FindingRow key={finding.finding_id} finding={finding} />
              ))}
            </ul>
            <Pager offset={offset} limit={PAGE_SIZE} total={findings.data.total} onChange={setOffset} busy={findings.isFetching} />
          </>
        )}
      </CardContent>
    </Card>
  );
}

export function RunDetailPage() {
  const { runId = "" } = useParams();
  const run = useRun(runId);

  return (
    <>
      <PageHeader
        title="Validation run"
        description="The run exactly as stored: the core's result for each receipt. Nothing is validated again."
        actions={
          <Button asChild variant="ghost" size="sm">
            <Link to="/runs">
              <ArrowLeft aria-hidden />
              Run History
            </Link>
          </Button>
        }
      />
      {run.isPending ? (
        <LoadingRows rows={6} label="Loading run" />
      ) : run.isError ? (
        <ErrorState error={run.error} onRetry={() => void run.refetch()} />
      ) : (
        <Tabs defaultValue="receipts">
          <TabsList aria-label="Run views">
            <TabsTrigger value="receipts">
              <Receipt aria-hidden />
              Receipts
            </TabsTrigger>
            <TabsTrigger value="findings">
              <ListTree aria-hidden />
              All findings
            </TabsTrigger>
          </TabsList>
          <TabsContent value="receipts">
            <ValidationResults run={run.data} inputs={null} historyLink={false} />
          </TabsContent>
          <TabsContent value="findings">
            <RunFindings runId={run.data.run_id} />
          </TabsContent>
        </Tabs>
      )}
      {run.isSuccess && run.data.results.results.length === 0 ? <EmptyState title="This run holds no receipts" /> : null}
    </>
  );
}
