import { FileCheck2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useRuns } from "@/api/queries";
import type { ExecutionMode, RunFilters } from "@/api/types";
import { PageHeader } from "@/components/layout/AppLayout";
import { RunsTable } from "@/components/runs/RunsTable";
import { EmptyState, ErrorState, LoadingRows } from "@/components/states/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Pager } from "@/pages/dashboard/Section";

const PAGE_SIZE = 20;

function FilterSelect({ id, label, value, onChange, options }: { id: string; label: string; value: string; onChange: (value: string) => void; options: [string, string][] }) {
  return (
    <div className="flex items-center gap-2">
      <label htmlFor={id} className="text-xs font-medium text-muted-foreground">
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="h-8 rounded-md border border-input bg-card px-2 text-[13px] focus-visible:outline-2 focus-visible:outline-ring"
      >
        {options.map(([optionValue, optionLabel]) => (
          <option key={optionValue} value={optionValue}>
            {optionLabel}
          </option>
        ))}
      </select>
    </div>
  );
}

export function RunHistoryPage() {
  const [offset, setOffset] = useState(0);
  const [mode, setMode] = useState("");
  const [preview, setPreview] = useState("");
  const filters: RunFilters = {
    ...(mode ? { execution_mode: mode as ExecutionMode } : {}),
    ...(preview ? { preview: preview === "true" } : {}),
  };
  const runs = useRuns({ limit: PAGE_SIZE, offset, ...filters });
  const filtered = Boolean(mode || preview);

  return (
    <>
      <PageHeader
        title="Run History"
        description={`Every persisted validation run, newest first. Opening a run reads its stored result; nothing is validated again.`}
        actions={
          <Button asChild size="sm">
            <Link to="/validate">Validate receipts</Link>
          </Button>
        }
      />
      <Card>
        <CardContent className="pt-4">
          <div className="mb-3 flex flex-wrap items-center gap-4" role="group" aria-label="Filters">
            <FilterSelect
              id="filter-mode"
              label="Mode"
              value={mode}
              onChange={(value) => {
                setMode(value);
                setOffset(0);
              }}
              options={[
                ["", "Any"],
                ["replay", "Replay"],
                ["live", "Live"],
                ["deterministic", "Deterministic"],
              ]}
            />
            <FilterSelect
              id="filter-preview"
              label="Ruleset"
              value={preview}
              onChange={(value) => {
                setPreview(value);
                setOffset(0);
              }}
              options={[
                ["", "Any"],
                ["false", "Published for production"],
                ["true", "Preview"],
              ]}
            />
            {runs.data ? <span className="ml-auto text-xs text-subtle-foreground">{runs.data.total} runs</span> : null}
          </div>
          {runs.isPending ? (
            <LoadingRows rows={6} label="Loading runs" />
          ) : runs.isError ? (
            <ErrorState error={runs.error} onRetry={() => void runs.refetch()} />
          ) : runs.data.items.length === 0 ? (
            <EmptyState
              icon={<FileCheck2 className="size-5" aria-hidden />}
              title={filtered ? "No runs match these filters" : "No validation runs yet"}
              description={filtered ? "Clear a filter to see more runs." : "Runs appear here once receipts are validated."}
            />
          ) : (
            <>
              <div className="-mx-5 border-y border-border">
                <RunsTable runs={runs.data.items} fullIds />
              </div>
              <Pager offset={offset} limit={PAGE_SIZE} total={runs.data.total} onChange={setOffset} busy={runs.isFetching} />
            </>
          )}
        </CardContent>
      </Card>
    </>
  );
}
