import { FileCheck2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useRuns } from "@/api/queries";
import { RunsTable } from "@/components/runs/RunsTable";
import { EmptyState, ErrorState, LoadingRows } from "@/components/states/states";
import { Button } from "@/components/ui/button";
import { Pager, Section } from "./Section";

const PAGE_SIZE = 8;

export function RecentRuns() {
  const [offset, setOffset] = useState(0);
  const runs = useRuns({ limit: PAGE_SIZE, offset });

  return (
    <Section
      id="recent-runs"
      title="Recent validation runs"
      description="Newest first. Each run was validated against one exact ruleset version."
      link={{ to: "/runs", label: "Run history" }}
      className="xl:col-span-2"
    >
      {runs.isPending ? (
        <LoadingRows rows={5} label="Loading runs" />
      ) : runs.isError ? (
        <ErrorState error={runs.error} onRetry={() => void runs.refetch()} />
      ) : runs.data.items.length === 0 ? (
        <EmptyState
          icon={<FileCheck2 className="size-5" aria-hidden />}
          title="No validation runs yet"
          description={
            <>
              Runs appear here once receipts are validated against a published ruleset.
            </>
          }
          action={
            <Button asChild variant="outline" size="sm">
              <Link to="/validate">Validate receipts</Link>
            </Button>
          }
        />
      ) : (
        <>
          <div className="-mx-5 border-y border-border">
            <RunsTable runs={runs.data.items} />
          </div>
          <Pager offset={offset} limit={PAGE_SIZE} total={runs.data.total} onChange={setOffset} busy={runs.isFetching} />
        </>
      )}
    </Section>
  );
}
