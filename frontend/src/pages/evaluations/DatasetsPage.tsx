import { Database, FlaskConical, Loader2, Upload } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "@/api/client";
import { useDatasets, useUploadDataset } from "@/api/queries";
import type { DatasetSummary } from "@/api/types";
import { PageHeader } from "@/components/layout/AppLayout";
import { EmptyState, ErrorState, LoadingRows } from "@/components/states/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/misc";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDateTime, formatInteger, formatRelative, shortHash } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Pager, Section } from "@/pages/dashboard/Section";
import { SELECT } from "./display";

const PAGE_SIZE = 20;
/** services/datasets.py: the name a dataset version is stored under. */
const DATASET_NAME = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/;

function ContextBadge({ complete }: { complete: boolean }) {
  return complete ? (
    <Badge variant="outline" title="Evaluated as one whole context: rules spanning receipts can be decided">
      Complete context
    </Badge>
  ) : (
    <Badge variant="review" title="Receipts may be missing: totals spanning receipts stay undecided">
      Incomplete context
    </Badge>
  );
}

export function datasetLabel(dataset: Pick<DatasetSummary, "name" | "version">) {
  return `${dataset.name} v${dataset.version}`;
}

/** The problems a 422 invalid_dataset lists: where in the file, and what is wrong there. */
function problemsOf(error: unknown): { location: string; message: string }[] {
  if (!(error instanceof ApiError) || !Array.isArray(error.details)) return [];
  return error.details.filter((item): item is { location: string; message: string } => typeof item?.message === "string");
}

function UploadForm() {
  const upload = useUploadDataset();
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [context, setContext] = useState<"" | "true" | "false">("");

  const issues: string[] = [];
  if (!file) issues.push("Choose a .yaml, .yml, .json or .jsonl file.");
  if (file?.name.toLowerCase().endsWith(".jsonl") && !name.trim()) issues.push("A JSONL dataset needs a name.");
  if (name.trim() && !DATASET_NAME.test(name.trim())) issues.push("A name is 1 to 64 letters, digits, '.', '_' or '-'.");

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!file || issues.length) return;
    upload.mutate(
      { file, name: name.trim() || undefined, description: description.trim() || undefined, context_complete: context ? context === "true" : undefined },
    );
  }

  const problems = problemsOf(upload.error);
  return (
    <form onSubmit={submit} className="space-y-2.5 rounded-md border border-border p-3" aria-label="Upload a dataset">
      <input
        type="file"
        aria-label="Dataset file"
        accept=".yaml,.yml,.json,.jsonl"
        onChange={(event) => setFile(event.target.files?.[0] ?? null)}
        className="block w-full text-[13px] file:mr-3 file:rounded-md file:border file:border-border file:bg-card file:px-2.5 file:py-1 file:text-[13px]"
      />
      <div className="grid gap-2 sm:grid-cols-2">
        <Input aria-label="Dataset name" placeholder="Name (required for JSONL)" value={name} onChange={(event) => setName(event.target.value)} className="h-8 text-[13px]" />
        <select aria-label="Context" className={cn(SELECT, "h-8")} value={context} onChange={(event) => setContext(event.target.value as typeof context)}>
          <option value="">Context: as the file says (complete by default)</option>
          <option value="true">Complete: every receipt that could matter is here</option>
          <option value="false">Incomplete: receipts may be missing</option>
        </select>
      </div>
      <Input aria-label="Description" placeholder="Description (optional)" value={description} onChange={(event) => setDescription(event.target.value)} className="h-8 text-[13px]" />
      <p className="text-xs leading-4 text-subtle-foreground">
        Labels in the file are kept as the answers for scoring and never reach the validator. Uploading the same bytes again returns the stored version.
      </p>
      {issues.length && file ? <p className="text-xs text-status-review-text">{issues.join(" ")}</p> : null}
      {upload.isError ? (
        <div>
          <ErrorState error={upload.error} />
          {problems.length ? (
            <ul className="mt-2 max-h-40 space-y-1 overflow-auto text-xs" aria-label="Dataset problems">
              {problems.map((problem, index) => (
                <li key={index}>
                  <span className="font-mono text-subtle-foreground">{problem.location || "file"}</span> {problem.message}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
      {upload.isSuccess ? (
        <p className="text-xs text-foreground" role="status">
          Stored as <span className="font-medium">{datasetLabel(upload.data)}</span> · {formatInteger(upload.data.stats.receipts)} receipts.{" "}
          <Link to={`/evaluations/new?dataset=${encodeURIComponent(upload.data.dataset_id)}`} className="font-medium text-primary hover:underline">
            Evaluate it
          </Link>
        </p>
      ) : null}
      <Button type="submit" size="sm" variant="outline" disabled={issues.length > 0 || upload.isPending}>
        {upload.isPending ? <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden /> : <Upload aria-hidden />}
        Upload dataset
      </Button>
    </form>
  );
}

/**
 * Evaluation datasets: every uploaded version, newest first, and uploads. A version never changes; uploading
 * changed content under a name makes its next version, and the same bytes again return the stored one.
 */
export function DatasetsPage() {
  const [offset, setOffset] = useState(0);
  const [name, setName] = useState("");
  const [uploading, setUploading] = useState(false);
  const names = useDatasets({ limit: 100, offset: 0 });
  const datasets = useDatasets({ limit: PAGE_SIZE, offset, ...(name ? { name } : {}) });
  const nameOptions = [...new Set((names.data?.items ?? []).map((item) => item.name))].sort();

  return (
    <>
      <PageHeader
        title="Datasets"
        description="Labelled receipts for evaluations. Each upload is an immutable version; labels are kept as the answers and never reach the validator."
        actions={
          <Button asChild variant="outline" size="sm">
            <Link to="/evaluations">Evaluations</Link>
          </Button>
        }
      />
      <Section
        id="datasets"
        title="Dataset versions"
        description="Newest first. Choose a name to see its version history."
        actions={
          <Button type="button" size="sm" variant={uploading ? "secondary" : "default"} onClick={() => setUploading((value) => !value)} aria-expanded={uploading}>
            <Upload aria-hidden />
            Upload dataset
          </Button>
        }
      >
        {uploading ? (
          <div className="mb-4 max-w-2xl">
            <UploadForm />
          </div>
        ) : null}
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <label htmlFor="dataset-name-filter" className="text-xs font-medium text-muted-foreground">
            Name
          </label>
          <select
            id="dataset-name-filter"
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
          {datasets.data ? <span className="ml-auto text-xs text-subtle-foreground">{datasets.data.total} versions</span> : null}
        </div>
        {datasets.isPending ? (
          <LoadingRows rows={4} label="Loading datasets" />
        ) : datasets.isError ? (
          <ErrorState error={datasets.error} onRetry={() => void datasets.refetch()} />
        ) : datasets.data.items.length === 0 ? (
          <EmptyState icon={<Database className="size-5" aria-hidden />} title="No datasets yet" description="Upload labelled receipts (YAML, JSON or JSONL) to evaluate against." className="py-6" />
        ) : (
          <>
            <div className="-mx-5 border-y border-border">
              <Table className="min-w-[900px]">
                <caption className="sr-only">Dataset versions</caption>
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead className="pl-5">Version</TableHead>
                    <TableHead>Context</TableHead>
                    <TableHead className="text-right">Receipts · lines</TableHead>
                    <TableHead>Labelled</TableHead>
                    <TableHead>File</TableHead>
                    <TableHead>Created</TableHead>
                    <TableHead className="pr-5">
                      <span className="sr-only">Actions</span>
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {datasets.data.items.map((dataset) => (
                    <TableRow key={dataset.dataset_id}>
                      <TableCell className="pl-5">
                        <p className="font-medium">{datasetLabel(dataset)}</p>
                        <p className="font-mono text-[11px] text-subtle-foreground">{dataset.dataset_id}</p>
                        {dataset.description ? <p className="max-w-64 text-xs text-muted-foreground">{dataset.description}</p> : null}
                      </TableCell>
                      <TableCell>
                        <ContextBadge complete={dataset.context_complete} />
                      </TableCell>
                      <TableCell className="tabular text-right">
                        {formatInteger(dataset.stats.receipts)} · {formatInteger(dataset.stats.lines)}
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {formatInteger(dataset.stats.labelled.lines_with_category)} categories · {formatInteger(dataset.stats.labelled.receipts_with_disposition)} dispositions ·{" "}
                        {formatInteger(dataset.stats.labelled.receipts_with_findings)} findings
                      </TableCell>
                      <TableCell className="text-xs">
                        {dataset.filename} <span className="text-subtle-foreground">({dataset.format}, {dataset.schema_version})</span>
                        <p className="font-mono text-[11px] text-subtle-foreground" title={`canonical ${dataset.canonical_sha256} · original ${dataset.original_sha256}`}>
                          {shortHash(dataset.canonical_sha256)}
                        </p>
                      </TableCell>
                      <TableCell className="text-xs" title={formatDateTime(dataset.created_at)}>
                        {formatRelative(dataset.created_at)}
                      </TableCell>
                      <TableCell className="pr-5 text-right">
                        <Button asChild size="sm" variant="outline">
                          <Link to={`/evaluations/new?dataset=${encodeURIComponent(dataset.dataset_id)}`} aria-label={`Evaluate ${datasetLabel(dataset)}`}>
                            <FlaskConical aria-hidden />
                            Evaluate
                          </Link>
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
            <Pager offset={offset} limit={PAGE_SIZE} total={datasets.data.total} onChange={setOffset} busy={datasets.isFetching} />
          </>
        )}
      </Section>
    </>
  );
}
