import { ArrowLeft, FilePlus2, Loader2 } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { useCreateRevision, useDocument } from "@/api/queries";
import type { DocumentDetail } from "@/api/types";
import { PageHeader } from "@/components/layout/AppLayout";
import { ErrorState, LoadingRows } from "@/components/states/states";
import { Button } from "@/components/ui/button";
import { formatDateTime, formatInteger } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Section } from "@/pages/dashboard/Section";
import { Facts } from "@/pages/evaluations/display";
import { ExtractionHistory } from "./DocumentsPage";

function NewRevision({ latest, onStored }: { latest: DocumentDetail; onStored: (revision: number) => void }) {
  const create = useCreateRevision();
  const [content, setContent] = useState(latest.content);
  const unchanged = content === latest.content;

  function submit(event: FormEvent) {
    event.preventDefault();
    if (unchanged) return;
    // based_on_revision: if someone stored another revision meanwhile, the API refuses this one (revision_conflict).
    create.mutate({ documentId: latest.document_id, body: { content, based_on_revision: latest.revision } }, { onSuccess: (document) => onStored(document.revision) });
  }

  return (
    <form onSubmit={submit} className="space-y-2" aria-label="New revision">
      <textarea aria-label="Revision text" value={content} onChange={(event) => setContent(event.target.value)} rows={14} className="w-full rounded-md border border-input bg-card p-2 font-mono text-xs" />
      <p className="text-xs text-subtle-foreground">Stored as revision {latest.revision + 1} of {latest.filename}, based on revision {latest.revision}. Earlier revisions never change.</p>
      {create.isError ? <ErrorState error={create.error} /> : null}
      <Button type="submit" size="sm" disabled={unchanged || create.isPending} title={unchanged ? "The text is the same as the latest revision" : undefined}>
        {create.isPending ? <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden /> : <FilePlus2 aria-hidden />}
        Store revision {latest.revision + 1}
      </Button>
    </form>
  );
}

export function DocumentDetailPage() {
  const { documentId = "" } = useParams();
  const latest = useDocument(documentId);
  const [chosen, setChosen] = useState<number | undefined>(undefined);
  const [editing, setEditing] = useState(false);
  const revision = useDocument(documentId, chosen);
  const shown = chosen === undefined ? latest : revision;

  const back = (
    <Button asChild variant="outline" size="sm">
      <Link to="/documents">
        <ArrowLeft aria-hidden />
        All documents
      </Link>
    </Button>
  );
  if (latest.isPending) return <><PageHeader title="Document" actions={back} /><LoadingRows rows={6} label="Loading document" /></>;
  if (latest.isError) return <><PageHeader title="Document" actions={back} /><ErrorState error={latest.error} onRetry={() => void latest.refetch()} /></>;

  const newest = latest.data;
  const revisions = Array.from({ length: newest.revision }, (_, index) => newest.revision - index);
  const document = shown.data;

  return (
    <>
      <PageHeader title={newest.filename} description={`${newest.document_id}${newest.title ? ` · ${newest.title}` : ""}`} actions={back} />
      <div className="grid items-start gap-4 xl:grid-cols-3">
        <div className="space-y-4 xl:col-span-2">
          <Section
            id="revision"
            title={document ? `Revision ${document.revision}${document.revision === newest.revision ? " (latest)" : ""}` : "Revision"}
            description="Stored exactly as sent. Rules cite the document by its filename."
            actions={
              <Button type="button" size="sm" variant={editing ? "secondary" : "outline"} onClick={() => setEditing((value) => !value)} aria-expanded={editing}>
                <FilePlus2 aria-hidden />
                New revision
              </Button>
            }
          >
            {editing ? (
              <div className="mb-4">
                <NewRevision
                  latest={newest}
                  onStored={(stored) => {
                    setEditing(false);
                    setChosen(stored);
                  }}
                />
              </div>
            ) : null}
            {shown.isPending ? (
              <LoadingRows rows={4} label="Loading revision" />
            ) : shown.isError ? (
              <ErrorState error={shown.error} />
            ) : (
              <>
                <Facts
                  className="mb-4"
                  items={[
                    ["Document ID", <span className="font-mono text-xs">{shown.data.document_id}</span>],
                    ["Content SHA-256", <span className="break-all font-mono text-xs">{shown.data.content_sha256}</span>],
                    ["Created", formatDateTime(shown.data.created_at)],
                    ["Size", `${formatInteger(shown.data.size_bytes)} bytes`],
                    ["Based on", shown.data.based_on_revision ? `revision ${shown.data.based_on_revision}` : "—"],
                    ["Source", `${shown.data.source.kind}${shown.data.source.reference ? ` · ${shown.data.source.reference}` : ""}`],
                  ]}
                />
                <pre className="max-h-[32rem] overflow-auto whitespace-pre-wrap rounded-md border border-border bg-muted/40 p-3 font-mono text-xs leading-5" aria-label="Document text">
                  {shown.data.content}
                </pre>
              </>
            )}
          </Section>
        </div>
        <div className="space-y-4">
          <Section id="revisions" title="Revisions" description="Every revision stays readable.">
            <ul className="space-y-1" aria-label="Revisions">
              {revisions.map((number) => {
                const active = (chosen ?? newest.revision) === number;
                return (
                  <li key={number}>
                    <button
                      type="button"
                      aria-pressed={active}
                      onClick={() => setChosen(number === newest.revision ? undefined : number)}
                      className={cn("w-full rounded-md border px-3 py-1.5 text-left text-[13px]", active ? "border-primary bg-primary-soft" : "border-border hover:bg-muted")}
                    >
                      Revision {number}
                      {number === newest.revision ? <span className="text-subtle-foreground"> · latest</span> : null}
                    </button>
                  </li>
                );
              })}
            </ul>
          </Section>
          <Section id="document-extractions" title="Extraction history" description="Extractions that read a revision of this document.">
            <ExtractionHistory documentId={newest.document_id} />
          </Section>
        </div>
      </div>
    </>
  );
}
