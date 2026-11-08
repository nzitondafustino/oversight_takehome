import { useIsFetching } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { queryKeys, useDocuments, useEvaluations, useRefreshRecords, useRulesets, useRuns } from "@/api/queries";
import { PageHeader } from "@/components/layout/AppLayout";
import { Button } from "@/components/ui/button";
import { SAMPLE_LIMIT } from "./aggregate";
import { DispositionBreakdown } from "./DispositionBreakdown";
import { EvaluationJobs } from "./EvaluationJobs";
import { KpiCards } from "./KpiCards";
import { QualityMetrics } from "./QualityMetrics";
import { RecentRuns } from "./RecentRuns";
import { SpendingSummary } from "./SpendingSummary";

export function DashboardPage() {
  // One page of the largest size the API serves, for the aggregates. Each card labels the scope it covers.
  const runs = useRuns({ limit: SAMPLE_LIMIT, offset: 0 });
  const evaluations = useEvaluations({ limit: SAMPLE_LIMIT, offset: 0 });
  const rulesets = useRulesets({ limit: SAMPLE_LIMIT, offset: 0 });
  const documents = useDocuments({ limit: 1, offset: 0 });
  const refresh = useRefreshRecords();
  const fetching = useIsFetching({ queryKey: queryKeys.records }) > 0;

  return (
    <>
      <PageHeader
        title="Dashboard"
        description={`Validation activity, evaluation jobs, quality and spending. Every figure comes from the API; totals over a page of records say which records they cover.`}
        actions={
          <Button variant="outline" size="sm" onClick={() => void refresh()} disabled={fetching}>
            <RefreshCw className={fetching ? "animate-spin motion-reduce:animate-none" : undefined} aria-hidden />
            {fetching ? "Refreshing" : "Refresh"}
          </Button>
        }
      />
      <div className="space-y-6">
        <KpiCards runs={runs} evaluations={evaluations} rulesets={rulesets} documents={documents} />
        <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
          <RecentRuns />
          <DispositionBreakdown runs={runs} />
          <EvaluationJobs evaluations={evaluations} />
          <SpendingSummary evaluations={evaluations} />
          <QualityMetrics evaluations={evaluations} />
        </div>
      </div>
    </>
  );
}
