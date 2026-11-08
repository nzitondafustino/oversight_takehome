import { FileText, Loader2, ScanSearch, Upload, Zap } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useBackends, useCreateDocument, useCreateExtraction, useDocuments, useExtractions, usePolicyCompiler } from "@/api/queries";
import type { DocumentSummary, ExtractionCreate, ExtractionListItem, RevisionRef } from "@/api/types";
import { PageHeader } from "@/components/layout/AppLayout";
import { EmptyState, ErrorState, LoadingRows } from "@/components/states/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/misc";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDateTime, formatInteger, formatRelative, humanize, shortHash } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Pager, Section } from "@/pages/dashboard/Section";
import { SELECT } from "@/pages/evaluations/display";

const PAGE_SIZE = 20;
/** schemas/documents.py Filename: rules cite a document by this bare .md name. */
const FILENAME = /^[A-Za-z0-9][A-Za-z0-9._-]*\.md$/;

export function ExtractionStatusBadge({ status }: { status: ExtractionListItem["status"] }) {
  const variant = status === "ready_for_approval" ? "pass" : status === "review_required" ? "review" : "flag";
  return <Badge variant={variant}>{humanize(status)}</Badge>;
}

function UploadDocument() {
  const navigate = useNavigate();
  const create = useCreateDocument();
  const [filename, setFilename] = useState("");
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");

  async function readFile(file: File | undefined) {
    if (!file) return;
    setContent(await file.text());
    if (!filename) setFilename(file.name);
  }

  const issues: string[] = [];
  if (!FILENAME.test(filename) || filename.length > 120) issues.push("A filename is a bare .md name, such as meals-policy.md.");
  if (!content.trim()) issues.push("Add the Markdown text.");

  function submit(event: FormEvent) {
    event.preventDefault();
    if (issues.length) return;
    create.mutate(
      { filename, content, title: title.trim() || undefined },
      { onSuccess: (document) => navigate(`/documents/${encodeURIComponent(document.document_id)}`) },
    );
  }

  return (
    <form onSubmit={submit} className="space-y-2.5" aria-label="Upload a policy document">
      <input type="file" accept=".md,text/markdown" aria-label="Markdown file" onChange={(event) => void readFile(event.target.files?.[0])} className="block w-full text-[13px] file:mr-3 file:rounded-md file:border file:border-border file:bg-card file:px-2.5 file:py-1 file:text-[13px]" />
      <div className="grid gap-2 sm:grid-cols-2">
        <Input aria-label="Filename" placeholder="meals-and-entertainment-policy.md" value={filename} onChange={(event) => setFilename(event.target.value.trim())} className="h-8 font-mono text-[13px]" />
        <Input aria-label="Title" placeholder="Title (optional)" value={title} onChange={(event) => setTitle(event.target.value)} className="h-8 text-[13px]" />
      </div>
      <textarea aria-label="Document text" value={content} onChange={(event) => setContent(event.target.value)} rows={6} placeholder="Or paste the Markdown text" className="w-full rounded-md border border-input bg-card p-2 font-mono text-xs" />
      <p className="text-xs leading-4 text-subtle-foreground">Stored exactly as sent, as revision 1. A later change is a new revision of the same document; no revision is ever rewritten.</p>
      {(filename || content) && issues.length ? <p className="text-xs text-status-review-text">{issues.join(" ")}</p> : null}
      {create.isError ? <ErrorState error={create.error} /> : null}
      <Button type="submit" size="sm" disabled={issues.length > 0 || create.isPending}>
        {create.isPending ? <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden /> : <Upload aria-hidden />}
        Store document
      </Button>
    </form>
  );
}

/**
 * Starts an extraction of exact revisions. Replay reads a named recording set and calls no model. Live calls a
 * paid model: offered only when the deployment can run it, and sent only with a model and an explicit confirmation.
 */
export function ExtractionLauncher({ selected }: { selected: (RevisionRef & { filename: string })[] }) {
  const navigate = useNavigate();
  const compiler = usePolicyCompiler();
  const backends = useBackends();
  const create = useCreateExtraction();
  const [mode, setMode] = useState<"replay" | "live">("replay");
  const [recordingSet, setRecordingSet] = useState("");
  const [model, setModel] = useState("");
  const [confirmed, setConfirmed] = useState(false);

  if (compiler.isPending) return <LoadingRows rows={2} label="Loading the policy compiler's recording sets" />;
  if (compiler.isError) return <ErrorState error={compiler.error} onRetry={() => void compiler.refetch()} />;
  const sets = compiler.data.replay.sets;
  const live = compiler.data.live;
  const models = backends.data?.allowed_models ?? [];

  const issues: string[] = [];
  if (!selected.length) issues.push("Select the documents to extract: together they are one policy candidate.");
  // No default: discovery does not say which sets hold model readings and which hold simulation readings.
  if (mode === "replay" && !recordingSet) issues.push(sets.length ? "Choose a recording set." : "This deployment has no compiler recording set.");
  if (mode === "live") {
    if (!live.available) issues.push("Live extraction is not available here.");
    if (!model) issues.push("Choose a model from the deployment's allowed models.");
    if (!confirmed) issues.push("Confirm that live extraction calls a paid model.");
  }

  function submit() {
    if (issues.length) return;
    const documents = selected.map(({ document_id, revision }) => ({ document_id, revision }));
    const body: ExtractionCreate = mode === "live" ? { documents, execution_mode: "live", extraction_model: model } : { documents, recording_set: recordingSet };
    create.mutate(body, { onSuccess: (extraction) => navigate(`/documents/extractions/${encodeURIComponent(extraction.extraction_id)}`) });
  }

  return (
    <div className="space-y-3">
      <p className="text-[13px]">
        {selected.length ? (
          <>
            {selected.length} document{selected.length === 1 ? "" : "s"}: <span className="font-mono text-xs">{selected.map((item) => `${item.filename} r${item.revision}`).join(", ")}</span>
          </>
        ) : (
          <span className="text-muted-foreground">Select documents in the library.</span>
        )}
      </p>
      <div role="radiogroup" aria-label="Extraction mode" className="grid grid-cols-2 gap-1 rounded-md bg-muted p-1">
        {(["replay", "live"] as const).map((item) => (
          <button
            key={item}
            type="button"
            role="radio"
            aria-checked={mode === item}
            disabled={item === "live" && !live.available}
            title={item === "live" && !live.available ? "Live extraction is not available in this deployment" : undefined}
            onClick={() => {
              setMode(item);
              setConfirmed(false);
            }}
            className={cn("inline-flex h-7 items-center justify-center gap-1 rounded-sm text-xs font-medium capitalize disabled:cursor-not-allowed disabled:opacity-45", mode === item ? "bg-card shadow-sm" : "text-muted-foreground")}
          >
            {item === "live" ? <Zap className="size-3" aria-hidden /> : null}
            {item}
          </button>
        ))}
      </div>
      {mode === "replay" ? (
        <div className="space-y-1">
          <label htmlFor="recording-set" className="block text-xs font-medium">
            Recording set
          </label>
          <select id="recording-set" className={SELECT} value={recordingSet} onChange={(event) => setRecordingSet(event.target.value)}>
            <option value="">Choose a recording set</option>
            {sets.map((set) => (
              <option key={set.name} value={set.name} disabled={!set.present}>
                {set.name}
                {set.live_target ? " (live readings)" : ""}
                {set.present ? "" : " · missing"}
              </option>
            ))}
          </select>
          <p className="text-xs leading-4 text-subtle-foreground">Replay reads the compiler's recorded readings of these exact texts. No model is called; a document with no recorded reading is refused.</p>
        </div>
      ) : (
        <fieldset className="space-y-2 rounded-md border border-status-review-border bg-status-review-soft p-3">
          <legend className="px-1 text-xs font-semibold text-status-review-text">Paid model calls</legend>
          <p className="text-xs leading-4">Each document's text is sent to the model, which costs money. The readings are recorded{compiler.data.replay.sets.find((set) => set.live_target) ? ` in ${compiler.data.replay.sets.find((set) => set.live_target)!.name}` : ""}, so every later step replays them.</p>
          <label htmlFor="extraction-model" className="block text-xs font-medium">
            Model
          </label>
          <select id="extraction-model" className={SELECT} value={model} onChange={(event) => setModel(event.target.value)}>
            <option value="">Choose an allowed model</option>
            {models.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
          <label className="flex cursor-pointer gap-2 text-[13px]">
            <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} className="mt-0.5 size-3.5 accent-primary" />
            <span className="font-medium text-status-review-text">I understand live extraction calls a paid model</span>
          </label>
        </fieldset>
      )}
      {selected.length && issues.length ? <p className="text-xs text-status-review-text">{issues.join(" ")}</p> : null}
      {create.isError ? <ErrorState error={create.error} /> : null}
      <Button type="button" className="w-full" disabled={issues.length > 0 || create.isPending} onClick={submit}>
        {create.isPending ? <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden /> : mode === "live" ? <Zap aria-hidden /> : <ScanSearch aria-hidden />}
        {mode === "live" ? "Extract live (paid)" : "Extract by replay"}
      </Button>
    </div>
  );
}

export function ExtractionHistory({ documentId }: { documentId?: string }) {
  const [offset, setOffset] = useState(0);
  const extractions = useExtractions({ limit: 10, offset, ...(documentId ? { document_id: documentId } : {}) });
  if (extractions.isPending) return <LoadingRows rows={3} label="Loading extractions" />;
  if (extractions.isError) return <ErrorState error={extractions.error} onRetry={() => void extractions.refetch()} />;
  if (!extractions.data.items.length) return <EmptyState title="No extractions yet" description="Select documents and extract them to compare the compiler's readings." className="py-6" />;
  return (
    <>
      <ul className="divide-y divide-border">
        {extractions.data.items.map((item) => {
          const ready = item.reviews.some((review) => review.ready_for_approval);
          return (
            <li key={item.extraction_id} className="py-2.5 text-[13px]">
              <div className="flex flex-wrap items-center gap-1.5">
                <Link to={`/documents/extractions/${encodeURIComponent(item.extraction_id)}`} className="font-mono font-medium text-primary hover:underline">
                  {item.extraction_id}
                </Link>
                <ExtractionStatusBadge status={item.status} />
                {ready ? <Badge variant="pass">Ready review</Badge> : item.reviews.length ? <Badge variant="neutral">{item.reviews.length} review{item.reviews.length === 1 ? "" : "s"}, none ready</Badge> : null}
                <Badge variant={item.provenance.reading_kind === "model" ? "outline" : "review"}>{item.provenance.reading_kind} readings</Badge>
                <span className="ml-auto text-xs text-subtle-foreground" title={formatDateTime(item.created_at)}>
                  {formatRelative(item.created_at)}
                </span>
              </div>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {item.provenance.execution_mode} · {item.provenance.recording_set} · {item.documents.length} documents · {formatInteger(item.comparison_count)} fields compared · {item.blockers.length} unresolved
              </p>
            </li>
          );
        })}
      </ul>
      <Pager offset={offset} limit={10} total={extractions.data.total} onChange={setOffset} busy={extractions.isFetching} />
    </>
  );
}

export function DocumentsPage() {
  const [offset, setOffset] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [selected, setSelected] = useState<Map<string, RevisionRef & { filename: string }>>(new Map());
  const documents = useDocuments({ limit: PAGE_SIZE, offset });

  function toggle(document: DocumentSummary) {
    setSelected((current) => {
      const next = new Map(current);
      if (next.has(document.document_id)) next.delete(document.document_id);
      else next.set(document.document_id, { document_id: document.document_id, revision: document.revision, filename: document.filename });
      return next;
    });
  }

  return (
    <>
      <PageHeader title="Documents" description="Policy documents as immutable revisions, and the compiler's extractions of chosen revisions." />
      <div className="grid items-start gap-4 xl:grid-cols-3">
        <Section
          id="library"
          title="Policy document library"
          description="Each document's latest revision."
          className="xl:col-span-2"
          actions={
            <Button type="button" size="sm" variant={uploading ? "secondary" : "outline"} onClick={() => setUploading((value) => !value)} aria-expanded={uploading}>
              <Upload aria-hidden />
              Upload
            </Button>
          }
        >
          {uploading ? (
            <div className="mb-4 rounded-md border border-border p-3">
              <UploadDocument />
            </div>
          ) : null}
          {documents.isPending ? (
            <LoadingRows rows={5} label="Loading documents" />
          ) : documents.isError ? (
            <ErrorState error={documents.error} onRetry={() => void documents.refetch()} />
          ) : documents.data.items.length === 0 ? (
            <EmptyState icon={<FileText className="size-5" aria-hidden />} title="No policy documents yet" description="Upload the policy Markdown documents to extract rules from them." />
          ) : (
            <>
              <div className="-mx-5 border-y border-border">
                <Table className="min-w-[720px]">
                  <caption className="sr-only">Policy documents</caption>
                  <TableHeader>
                    <TableRow className="hover:bg-transparent">
                      <TableHead className="pl-5">
                        <span className="sr-only">Select</span>
                      </TableHead>
                      <TableHead>Document</TableHead>
                      <TableHead>Revision</TableHead>
                      <TableHead>Content hash</TableHead>
                      <TableHead className="pr-5">Created</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {documents.data.items.map((document) => (
                      <TableRow key={document.document_id}>
                        <TableCell className="w-8 pl-5">
                          <input type="checkbox" aria-label={`Select ${document.filename}`} checked={selected.has(document.document_id)} onChange={() => toggle(document)} className="size-3.5 accent-primary" />
                        </TableCell>
                        <TableCell>
                          <Link to={`/documents/${encodeURIComponent(document.document_id)}`} className="font-medium text-primary hover:underline">
                            {document.filename}
                          </Link>
                          <p className="font-mono text-[11px] text-subtle-foreground">{document.document_id}</p>
                          {document.title ? <p className="text-xs text-muted-foreground">{document.title}</p> : null}
                        </TableCell>
                        <TableCell className="tabular">r{document.revision}</TableCell>
                        <TableCell className="font-mono text-xs" title={document.content_sha256}>
                          {shortHash(document.content_sha256)}
                          <span className="block font-sans text-subtle-foreground">{formatInteger(document.size_bytes)} bytes</span>
                        </TableCell>
                        <TableCell className="pr-5 text-xs" title={formatDateTime(document.created_at)}>
                          {formatRelative(document.created_at)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
              <Pager offset={offset} limit={PAGE_SIZE} total={documents.data.total} onChange={setOffset} busy={documents.isFetching} />
            </>
          )}
        </Section>
        <div className="space-y-4">
          <Section id="extract" title="Extract a policy candidate" description="The compiler reads the selected revisions, by regex and by model, and compares every field.">
            <ExtractionLauncher selected={[...selected.values()]} />
          </Section>
          <Section id="extractions" title="Extraction history" description="Newest first.">
            <ExtractionHistory />
          </Section>
        </div>
      </div>
    </>
  );
}
