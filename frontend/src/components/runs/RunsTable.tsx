import { Link } from "react-router-dom";
import type { Disposition, RunSummary } from "@/api/types";
import { DISPOSITION_ORDER, DispositionBadge, ModeBadge } from "@/components/status/badges";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDateTime, formatInteger, formatRelative } from "@/lib/format";
import { adapterText, setupText } from "@/pages/validate/config";

/** Run summaries as GET /v1/runs returns them. Used by the dashboard and Run History. */
export function RunsTable({ runs, fullIds = false }: { runs: RunSummary[]; fullIds?: boolean }) {
  return (
    <Table>
      <caption className="sr-only">Validation runs, newest first</caption>
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          <TableHead className="pl-5">Run</TableHead>
          <TableHead>Ruleset</TableHead>
          <TableHead>Mode</TableHead>
          <TableHead className="text-right">Receipts</TableHead>
          <TableHead>Dispositions</TableHead>
          <TableHead className="pr-5 text-right">Exceptions</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {runs.map((run) => (
          <TableRow key={run.run_id}>
            <TableCell className="pl-5">
              <Link to={`/runs/${encodeURIComponent(run.run_id)}`} className="font-mono text-[13px] font-medium text-primary hover:underline">
                {fullIds || run.run_id.length <= 16 ? run.run_id : `${run.run_id.slice(0, 16)}…`}
              </Link>
              <p className="text-xs text-subtle-foreground" title={formatDateTime(run.created_at)}>
                {formatRelative(run.created_at)}
              </p>
            </TableCell>
            <TableCell>
              <p className="font-medium text-foreground">{run.ruleset.name}</p>
              <p className="text-xs text-subtle-foreground">
                v{run.ruleset.version} · {run.ruleset.status}
                {run.ruleset.publication_mode ? ` (${run.ruleset.publication_mode})` : ""}
              </p>
            </TableCell>
            <TableCell>
              <ModeBadge mode={run.execution_mode} preview={run.preview} />
              <p className="mt-0.5 text-xs text-subtle-foreground" title={run.setup ? adapterText(run.setup, run.classification_backend) : undefined}>
                {run.setup ? setupText(run.setup, run.classification_backend) : run.classification_backend ? `${run.classification_backend} (adapter as recorded)` : "classification not recorded"}
              </p>
            </TableCell>
            <TableCell className="tabular text-right font-medium">{formatInteger(run.receipt_count)}</TableCell>
            <TableCell>
              <div className="flex flex-wrap gap-1">
                {DISPOSITION_ORDER.filter((key: Disposition) => (run.dispositions[key] ?? 0) > 0).map((key) => (
                  <DispositionBadge key={key} disposition={key} count={run.dispositions[key]} />
                ))}
              </div>
            </TableCell>
            <TableCell className="tabular pr-5 text-right">
              {run.counts ? (
                <span className="font-medium">{formatInteger(run.counts.exception)}</span>
              ) : (
                <span className="text-xs text-subtle-foreground" title="Stored before counts were recorded: open the run for its findings">
                  not recorded
                </span>
              )}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
