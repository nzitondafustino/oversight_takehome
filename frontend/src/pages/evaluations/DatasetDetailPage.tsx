import { RecordActions } from "@/components/management/RecordActions";
import { ChevronDown, ChevronRight, FlaskConical } from "lucide-react";
import { Fragment, useState } from "react";
import { Link, useParams, useNavigate } from "react-router-dom";
import { useDataset } from "@/api/queries";
import type { DatasetCase } from "@/api/types";
import { PageHeader } from "@/components/layout/AppLayout";
import { EmptyState, ErrorState, LoadingRows } from "@/components/states/states";
import { DispositionBadge } from "@/components/status/badges";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/misc";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatInteger } from "@/lib/format";
import { Pager, Section } from "@/pages/dashboard/Section";
import { datasetLabel } from "./DatasetsPage";
import { Facts, Stat } from "./display";

function value(input: unknown): string {
  return input == null ? "—" : String(input);
}

function linesOf(item: DatasetCase): Record<string, unknown>[] {
  return Array.isArray(item.receipt.line_items) ? item.receipt.line_items as Record<string, unknown>[] : [];
}

function ReceiptDetails({ item }: { item: DatasetCase }) {
  return (
    <div className="space-y-4 bg-muted/30 px-5 py-4">
      <Facts items={[
        ["Employee", value(item.receipt.employee_id)],
        ["Country · entity", `${value(item.receipt.employee_country)} · ${value(item.receipt.entity)}`],
        ["Submitted expense type", value(item.receipt.submitted_expense_type)],
        ["Trip", value(item.receipt.trip_id)],
        ["Expected exceptions", item.expected.findings ? item.expected.findings.exceptions.map((e) => e.rule_id).join(", ") || "None" : "Not labelled"],
        ["Expected review signals", item.expected.findings ? item.expected.findings.signals.join(", ") || "None" : "Not labelled"],
      ]} />
      {item.notes ? <p className="text-xs text-muted-foreground"><span className="font-medium">Reference notes: </span>{item.notes}</p> : null}
      <Table>
        <caption className="sr-only">Line items for {value(item.receipt.receipt_id)}</caption>
        <TableHeader><TableRow><TableHead>Item</TableHead><TableHead>Description</TableHead><TableHead>Quantity</TableHead><TableHead>Amount</TableHead><TableHead>Expected category</TableHead><TableHead>Restricted</TableHead></TableRow></TableHeader>
        <TableBody>{linesOf(item).map((line) => {
          const answer = item.expected.lines[value(line.line_id)];
          return <TableRow key={value(line.line_id)}>
            <TableCell className="font-mono text-xs">{value(line.line_id)}</TableCell>
            <TableCell className="whitespace-normal">{value(line.description_raw)}</TableCell>
            <TableCell>{value(line.quantity)}</TableCell>
            <TableCell className="tabular">{value(line.amount)} {value(item.receipt.currency)}</TableCell>
            <TableCell>{answer?.category ?? "Not labelled"}</TableCell>
            <TableCell>{answer?.restricted == null ? "Not labelled" : answer.restricted ? "Yes" : "No"}</TableCell>
          </TableRow>;
        })}</TableBody>
      </Table>
    </div>
  );
}

/** Read-only preview of the immutable canonical dataset. Answers are never submitted for validation. */
export function DatasetDetailPage() {
  const { datasetId } = useParams();
  return <DatasetViewer key={datasetId} datasetId={datasetId} />;
}

function DatasetViewer({ datasetId }: { datasetId: string | undefined }) {
  const dataset = useDataset(datasetId, true);
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [limit, setLimit] = useState(10);
  const [expanded, setExpanded] = useState<string | null>(null);
  const cases = dataset.data?.cases ?? [];
  const query = search.trim().toLowerCase();
  const filtered = cases.filter((item) => [item.receipt.receipt_id, item.receipt.merchant_name, item.receipt.employee_id,
    item.receipt.submitted_expense_type, ...linesOf(item).map((line) => line.description_raw)]
    .some((field) => field != null && String(field).toLowerCase().includes(query)));
  const page = filtered.slice(offset, offset + limit);

  return <>
    <PageHeader title={dataset.data ? datasetLabel(dataset.data) : "Dataset"}
      description="Browse the stored receipts and reference labels. This view does not run classification or policy evaluation."
      actions={<div className="flex gap-2"><Button asChild variant="outline" size="sm"><Link to="/evaluations/datasets">All datasets</Link></Button>
        {dataset.data ? <RecordActions path={`/v1/evaluations/datasets/${encodeURIComponent(dataset.data.dataset_id)}`} title="dataset" field="description" value={dataset.data.description} onDeleted={() => navigate("/evaluations/datasets")} /> : null}
        {dataset.data ? <Button asChild size="sm"><Link to={`/evaluations/new?dataset=${encodeURIComponent(dataset.data.dataset_id)}`}><FlaskConical aria-hidden />Evaluate dataset</Link></Button> : null}</div>} />
    {dataset.isPending ? <LoadingRows rows={5} label="Loading dataset" /> : dataset.isError ? <ErrorState error={dataset.error} onRetry={() => void dataset.refetch()} /> : <div className="space-y-5">
      <dl className="grid gap-3 sm:grid-cols-3">
        <Stat label="Receipts" value={formatInteger(dataset.data.stats.receipts)} hint={`${formatInteger(dataset.data.stats.lines)} line items`} />
        <Stat label="Reference labels" value={formatInteger(dataset.data.stats.labelled.lines_with_category)} hint={`${formatInteger(dataset.data.stats.labelled.receipts_with_disposition)} receipts with expected dispositions`} />
        <Stat label="Context" value={dataset.data.context_complete ? "Complete" : "Incomplete"} hint={dataset.data.description ?? dataset.data.filename} />
      </dl>
      <Section id="dataset-receipts" title="Receipts" description="Expand a receipt to inspect its items and expected policy findings.">
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <label htmlFor="dataset-search" className="sr-only">Search receipts</label>
          <Input id="dataset-search" placeholder="Search receipts, merchants, employees or items…" value={search} className="max-w-md" onChange={(e) => { setSearch(e.target.value); setOffset(0); setExpanded(null); }} />
          <label htmlFor="dataset-page-size" className="ml-auto text-xs text-muted-foreground">Rows per page</label>
          <select id="dataset-page-size" className="h-9 rounded-md border border-input bg-card px-2 text-sm" value={limit} onChange={(e) => { setLimit(Number(e.target.value)); setOffset(0); setExpanded(null); }}>
            {[10, 20, 50].map((size) => <option key={size} value={size}>{size}</option>)}
          </select>
          <span className="text-xs text-muted-foreground" role="status">{formatInteger(filtered.length)} of {formatInteger(cases.length)} receipts</span>
        </div>
        {dataset.data.cases === null ? <EmptyState title="Receipt data unavailable" description="This dataset response did not include its receipt cases." /> : !page.length ? <EmptyState title={query ? "No matching receipts" : "No receipts"} description={query ? "Try a different receipt, merchant, employee or item." : "This dataset contains no receipt cases."} /> : <div className="overflow-x-auto">
          <Table className="min-w-[850px]">
            <caption className="sr-only">Dataset receipts</caption>
            <TableHeader><TableRow><TableHead>Receipt</TableHead><TableHead>Merchant</TableHead><TableHead>Date</TableHead><TableHead>Expense type</TableHead><TableHead>Total</TableHead><TableHead>Items</TableHead><TableHead>Expected disposition</TableHead></TableRow></TableHeader>
            <TableBody>{page.map((item) => {
              const id = value(item.receipt.receipt_id);
              const open = expanded === id;
              return <Fragment key={id}><TableRow>
                <TableCell><button type="button" className="inline-flex items-center gap-1.5 rounded-sm font-mono text-xs font-medium text-primary hover:underline focus-visible:outline-2 focus-visible:outline-ring" aria-expanded={open} aria-controls={`receipt-${id}`} onClick={() => setExpanded(open ? null : id)}>
                  {open ? <ChevronDown className="size-4" aria-hidden /> : <ChevronRight className="size-4" aria-hidden />}{id}</button></TableCell>
                <TableCell>{value(item.receipt.merchant_name)}</TableCell>
                <TableCell className="text-xs">{value(item.receipt.transaction_datetime).replace("T", " ")}</TableCell>
                <TableCell>{value(item.receipt.submitted_expense_type)}</TableCell>
                <TableCell className="tabular">{value(item.receipt.total_amount)} {value(item.receipt.currency)}</TableCell>
                <TableCell>{linesOf(item).length}</TableCell>
                <TableCell>{item.expected.disposition ? <DispositionBadge disposition={item.expected.disposition} /> : <span className="text-xs text-muted-foreground">Not labelled</span>}</TableCell>
              </TableRow>{open ? <TableRow id={`receipt-${id}`}><TableCell colSpan={7} className="p-0"><ReceiptDetails item={item} /></TableCell></TableRow> : null}</Fragment>;
            })}</TableBody>
          </Table>
        </div>}
        <Pager offset={offset} limit={limit} total={filtered.length} onChange={(next) => { setOffset(next); setExpanded(null); }} />
      </Section>
    </div>}
  </>;
}
