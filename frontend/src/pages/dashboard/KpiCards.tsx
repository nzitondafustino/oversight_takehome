import type { UseQueryResult } from "@tanstack/react-query";
import { FileCheck2, FileText, FlaskConical, Receipt, ShieldCheck, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import type { DocumentSummary, EvaluationSummary, Page, RulesetSummary, RunSummary } from "@/api/types";
import { isActiveEvaluation } from "@/api/types";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/misc";
import { formatInteger } from "@/lib/format";
import { aggregateRuns, describeScope, scopeOf } from "./aggregate";

interface KpiProps {
  label: string;
  icon: LucideIcon;
  to: string;
  query: UseQueryResult<unknown>;
  value?: string;
  detail?: ReactNode;
}

function Kpi({ label, icon: Icon, to, query, value, detail }: KpiProps) {
  return (
    <Card className="p-0">
      <Link to={to} className="block rounded-lg px-4 py-4 hover:bg-muted/40 focus-visible:outline-offset-0">
        <div className="flex items-center justify-between gap-2">
          <p className="text-[13px] font-medium text-muted-foreground">{label}</p>
          <Icon className="size-4 text-subtle-foreground" aria-hidden />
        </div>
        {query.isPending ? (
          <>
            <Skeleton className="mt-2 h-8 w-20" />
            <Skeleton className="mt-2 h-4 w-32" />
          </>
        ) : query.isError ? (
          <>
            <p className="mt-1.5 text-2xl font-semibold text-subtle-foreground">—</p>
            <p className="mt-1 text-xs text-status-flag-text">Could not load</p>
          </>
        ) : (
          <>
            <p className="tabular mt-1.5 text-2xl font-semibold tracking-tight text-foreground">{value}</p>
            <p className="mt-1 min-h-4 text-xs leading-4 text-subtle-foreground">{detail}</p>
          </>
        )}
      </Link>
    </Card>
  );
}

export function KpiCards({
  runs,
  evaluations,
  rulesets,
  documents,
}: {
  runs: UseQueryResult<Page<RunSummary>>;
  evaluations: UseQueryResult<Page<EvaluationSummary>>;
  rulesets: UseQueryResult<Page<RulesetSummary>>;
  documents: UseQueryResult<Page<DocumentSummary>>;
}) {
  const runPage = runs.data;
  const runScope = runPage ? scopeOf(runPage) : null;
  const receipts = runPage ? aggregateRuns(runPage.items).receipts : 0;
  const evalPage = evaluations.data;
  const active = evalPage ? evalPage.items.filter((item) => isActiveEvaluation(item.status)).length : 0;
  const rulesetPage = rulesets.data;
  const published = rulesetPage ? rulesetPage.items.filter((item) => item.status === "published" && item.publication_mode === "production").length : 0;

  return (
    <section aria-label="Summary" className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-5">
      <Kpi label="Validation runs" icon={FileCheck2} to="/runs" query={runs} value={formatInteger(runPage?.total)} detail="Persisted runs" />
      <Kpi
        label="Receipt validations"
        icon={Receipt}
        to="/runs"
        query={runs}
        value={formatInteger(receipts)}
        detail={runScope ? (runScope.total ? `Summed over ${describeScope(runScope, "run")}` : "No runs yet") : null}
      />
      <Kpi
        label="Evaluation jobs"
        icon={FlaskConical}
        to="/evaluations"
        query={evaluations}
        value={formatInteger(evalPage?.total)}
        detail={
          !evalPage ? null : !evalPage.total ? "No jobs yet" : `${active ? active : "None"} active in ${describeScope(scopeOf(evalPage), "job")}`
        }
      />
      <Kpi
        label="Ruleset versions"
        icon={ShieldCheck}
        to="/policies"
        query={rulesets}
        value={formatInteger(rulesetPage?.total)}
        detail={
          !rulesetPage ? null : !rulesetPage.total ? "No versions yet" : `${published} published for production in ${describeScope(scopeOf(rulesetPage), "version")}`
        }
      />
      <Kpi
        label="Policy documents"
        icon={FileText}
        to="/documents"
        query={documents}
        value={formatInteger(documents.data?.total)}
        detail="Documents, each counted once at its latest revision"
      />
    </section>
  );
}
