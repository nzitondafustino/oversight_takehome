/**
 * The library's panels: uploading a Markdown document with its business description, describing an existing
 * document (which never touches its revisions), and the detail drawer with revision history and technical details.
 */
import { ExternalLink, GitCompare, Loader2, Upload } from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useCreateDocument, useRevisionApproval, useRevisionApprovalAction, useUpdateDocumentProfile } from "@/api/queries";
import type { DocumentApprovalStatus, DocumentCategory, DocumentScope, DocumentSummary } from "@/api/types";
import { ErrorState } from "@/components/states/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Drawer } from "@/components/ui/drawer";
import { Input } from "@/components/ui/misc";
import { formatDateTime, formatInteger, formatRelative } from "@/lib/format";
import { SELECT } from "@/pages/evaluations/display";
import { ActorField, useActor } from "./actor";
import { documentErrorHint, FILENAME, readMarkdownFile, type ReadFile } from "./documentFiles";
import { APPROVAL_TEXT, approvalOf, CATEGORIES, categoryLabel, displayTitle, scopeText, type DocumentUsage } from "./library";

const FIELD = "block text-xs font-medium text-foreground";

function list(text: string): string[] {
  return text
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

interface ProfileDraft {
  category: DocumentCategory | "";
  description: string;
  regions: string;
  entities: string;
  expenseTypes: string;
}

function draftOf(document?: DocumentSummary): ProfileDraft {
  return {
    category: document?.category ?? "",
    description: document?.description ?? "",
    regions: document?.scope?.regions.join(", ") ?? "",
    entities: document?.scope?.entities.join(", ") ?? "",
    expenseTypes: document?.scope?.expense_types.join(", ") ?? "",
  };
}

function scopeOf(draft: ProfileDraft): DocumentScope {
  return { regions: list(draft.regions), entities: list(draft.entities), expense_types: list(draft.expenseTypes) };
}

/** Category, description and scope. A category is chosen, never guessed: leaving it empty keeps a document unclassified. */
function ProfileFields({ idPrefix, draft, onChange }: { idPrefix: string; draft: ProfileDraft; onChange: (change: Partial<ProfileDraft>) => void }) {
  return (
    <div className="grid gap-2.5 sm:grid-cols-2">
      <div className="space-y-1">
        <label htmlFor={`${idPrefix}-category`} className={FIELD}>
          Policy category
        </label>
        <select id={`${idPrefix}-category`} className={SELECT} value={draft.category} onChange={(event) => onChange({ category: event.target.value as DocumentCategory | "" })}>
          <option value="">Unclassified</option>
          {CATEGORIES.map((item) => (
            <option key={item.id} value={item.id}>
              {item.label}
            </option>
          ))}
        </select>
      </div>
      <div className="space-y-1">
        <label htmlFor={`${idPrefix}-regions`} className={FIELD}>
          Regions
        </label>
        <Input id={`${idPrefix}-regions`} className="h-9 text-[13px]" placeholder="Such as EMEA, APAC" value={draft.regions} onChange={(event) => onChange({ regions: event.target.value })} />
      </div>
      <div className="space-y-1 sm:col-span-2">
        <label htmlFor={`${idPrefix}-description`} className={FIELD}>
          Description
        </label>
        <Input id={`${idPrefix}-description`} className="h-9 text-[13px]" maxLength={500} placeholder="What this policy covers, in a sentence" value={draft.description} onChange={(event) => onChange({ description: event.target.value })} />
      </div>
      <div className="space-y-1">
        <label htmlFor={`${idPrefix}-entities`} className={FIELD}>
          Entities
        </label>
        <Input id={`${idPrefix}-entities`} className="h-9 text-[13px]" placeholder="Comma-separated" value={draft.entities} onChange={(event) => onChange({ entities: event.target.value })} />
      </div>
      <div className="space-y-1">
        <label htmlFor={`${idPrefix}-expense-types`} className={FIELD}>
          Expense types
        </label>
        <Input id={`${idPrefix}-expense-types`} className="h-9 text-[13px]" placeholder="Comma-separated" value={draft.expenseTypes} onChange={(event) => onChange({ expenseTypes: event.target.value })} />
      </div>
    </div>
  );
}

export function UploadDocument({ onDone }: { onDone?: () => void }) {
  const navigate = useNavigate();
  const create = useCreateDocument();
  const [filename, setFilename] = useState("");
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [file, setFile] = useState<ReadFile | null>(null);
  const [profile, setProfile] = useState<ProfileDraft>(draftOf());

  async function readFile(chosen: File | undefined) {
    if (!chosen) return;
    const read = await readMarkdownFile(chosen);
    setFile(read);
    if (!read.ok) return;
    setContent(read.content);
    if (!filename) setFilename(read.name);
  }

  const issues: string[] = [];
  if (file && !file.ok) issues.push(file.error);
  if (!FILENAME.test(filename) || filename.length > 120) issues.push("A filename is a bare .md name, such as meals-policy.md.");
  if (!(file && !file.ok) && !content.trim()) issues.push("Add the Markdown text.");

  function submit(event: FormEvent) {
    event.preventDefault();
    if (issues.length) return;
    const scope = scopeOf(profile);
    const described = scope.regions.length || scope.entities.length || scope.expense_types.length;
    create.mutate(
      {
        filename,
        content,
        title: title.trim() || undefined,
        ...(profile.category ? { category: profile.category } : {}),
        ...(profile.description.trim() ? { description: profile.description.trim() } : {}),
        ...(described ? { scope } : {}),
      },
      {
        onSuccess: (document) => {
          onDone?.();
          navigate(`/documents/${encodeURIComponent(document.document_id)}`);
        },
      },
    );
  }

  return (
    <form onSubmit={submit} className="space-y-3" aria-label="Upload a policy document">
      <input type="file" accept=".md,text/markdown" aria-label="Markdown file" onChange={(event) => void readFile(event.target.files?.[0])} className="block w-full text-[13px] file:mr-3 file:rounded-md file:border file:border-border file:bg-card file:px-2.5 file:py-1 file:text-[13px]" />
      {file?.ok ? (
        <p className="text-xs text-muted-foreground" aria-label="Chosen file">
          <span className="font-mono">{file.name}</span> · {formatInteger(file.size)} bytes · UTF-8 Markdown
        </p>
      ) : null}
      <div className="grid gap-2.5 sm:grid-cols-2">
        <div className="space-y-1">
          <label htmlFor="upload-filename" className={FIELD}>
            Filename
          </label>
          <Input id="upload-filename" aria-label="Filename" placeholder="meals-and-entertainment-policy.md" value={filename} onChange={(event) => setFilename(event.target.value.trim())} className="h-9 font-mono text-[13px]" />
        </div>
        <div className="space-y-1">
          <label htmlFor="upload-title" className={FIELD}>
            Title
          </label>
          <Input id="upload-title" aria-label="Title" placeholder="Meals and Entertainment Policy" value={title} onChange={(event) => setTitle(event.target.value)} className="h-9 text-[13px]" />
        </div>
      </div>
      <ProfileFields idPrefix="upload" draft={profile} onChange={(change) => setProfile((current) => ({ ...current, ...change }))} />
      <textarea aria-label="Document text" value={content} onChange={(event) => setContent(event.target.value)} rows={8} placeholder="Or paste the Markdown text" className="w-full rounded-md border border-input bg-card p-2 font-mono text-xs" />
      <p className="text-xs leading-4 text-subtle-foreground">Stored exactly as sent, as revision 1. A later change is a new revision of the same document; no revision is ever rewritten.</p>
      {(filename || content || file) && issues.length ? (
        <p role="alert" className="text-xs text-status-review-text">
          {issues.join(" ")}
        </p>
      ) : null}
      {create.isPending ? (
        <p role="status" className="text-xs text-muted-foreground">
          Storing {filename}…
        </p>
      ) : null}
      {create.isError ? (
        <>
          <ErrorState error={create.error} />
          {documentErrorHint(create.error) ? <p className="text-xs text-muted-foreground">{documentErrorHint(create.error)}</p> : null}
        </>
      ) : null}
      <Button type="submit" size="sm" disabled={issues.length > 0 || create.isPending}>
        {create.isPending ? <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden /> : <Upload aria-hidden />}
        Store document
      </Button>
    </form>
  );
}

/** Change a document's category, description and scope. Its revisions, their text and hashes, never change. */
export function ProfileEditor({ document, onSaved }: { document: DocumentSummary; onSaved?: () => void }) {
  const update = useUpdateDocumentProfile();
  const [actor, setActor] = useActor();
  const [draft, setDraft] = useState<ProfileDraft>(draftOf(document));
  const who = actor.trim();
  return (
    <form
      className="space-y-3"
      aria-label="Describe this document"
      onSubmit={(event) => {
        event.preventDefault();
        update.mutate(
          { documentId: document.document_id, body: { actor: who, category: draft.category || null, description: draft.description.trim() || null, scope: scopeOf(draft) } },
          { onSuccess: () => onSaved?.() },
        );
      }}
    >
      <ProfileFields idPrefix="profile" draft={draft} onChange={(change) => setDraft((current) => ({ ...current, ...change }))} />
      <ActorField actor={actor} setActor={setActor} id="profile-actor" />
      {update.isError ? <ErrorState error={update.error} /> : null}
      <p className="text-xs text-subtle-foreground">This describes the document for finding it. No revision, and no compiled rule, changes.</p>
      <Button type="submit" size="sm" disabled={!who || update.isPending}>
        {update.isPending ? <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden /> : null}
        Save description
      </Button>
    </form>
  );
}

const APPROVAL_VARIANT: Record<DocumentApprovalStatus, "neutral" | "review" | "pass"> = { draft: "neutral", needs_review: "review", approved: "pass" };

/** This exact revision's approval, beside its title. */
export function ApprovalBadge({ status }: { status: DocumentApprovalStatus }) {
  return <Badge variant={APPROVAL_VARIANT[status]}>{APPROVAL_TEXT[status]}</Badge>;
}

/**
 * Approve one exact revision: its status, the one next step, and every step taken. The API reads the revision's text
 * and checks its hash before any step, and the step names that hash, so approval is of these bytes alone.
 */
export function ApprovalPanel({ document }: { document: DocumentSummary }) {
  const approval = useRevisionApproval(document.document_id, document.revision);
  const act = useRevisionApprovalAction();
  const [actor, setActor] = useActor();
  const [note, setNote] = useState("");
  const status = approval.data?.status ?? approvalOf(document);
  const who = actor.trim();
  const send = (action: "submit" | "approve" | "return_to_draft") =>
    act.mutate(
      { documentId: document.document_id, revision: document.revision, body: { action, actor: who, content_sha256: document.content_sha256, ...(note.trim() ? { note: note.trim() } : {}) } },
      { onSuccess: () => { setNote(""); void approval.refetch(); } },
    );
  return (
    <section className="space-y-2.5" aria-label="Approval of this revision">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-semibold">Approval of revision {document.revision}</h3>
        <ApprovalBadge status={status} />
      </div>
      <p className="text-xs text-muted-foreground">
        {status === "draft"
          ? "Not yet submitted. Each revision is approved on its own: a new revision never inherits an earlier approval."
          : status === "needs_review"
            ? "Waiting for a reviewer to approve exactly this text."
            : "Approved: this exact text was checked and signed off. Publishing a ruleset is a separate step."}
      </p>
      <ActorField actor={actor} setActor={setActor} id="approval-actor" />
      {status !== "draft" ? <Input aria-label="Note" placeholder={status === "approved" ? "Why it returns to draft (required)" : "Note (required to return to draft)"} className="h-8 text-[13px]" value={note} onChange={(event) => setNote(event.target.value)} /> : null}
      <div className="flex flex-wrap gap-2">
        {status === "draft" ? (
          <Button type="button" size="sm" disabled={!who || act.isPending} onClick={() => send("submit")}>
            Submit for review
          </Button>
        ) : null}
        {status === "needs_review" ? (
          <Button type="button" size="sm" disabled={!who || act.isPending} onClick={() => send("approve")}>
            Approve this revision
          </Button>
        ) : null}
        {status !== "draft" ? (
          <Button type="button" size="sm" variant="outline" disabled={!who || !note.trim() || act.isPending} onClick={() => send("return_to_draft")}>
            Return to draft
          </Button>
        ) : null}
      </div>
      {act.isError ? <ErrorState error={act.error} /> : null}
      {approval.data?.history.length ? (
        <ol className="space-y-1 border-l border-border pl-3 text-xs" aria-label="Approval history">
          {[...approval.data.history].reverse().map((event, index) => (
            <li key={index}>
              <span className="font-medium">{APPROVAL_TEXT[event.to_status]}</span> by {event.actor.claimed} · {formatDateTime(event.at)}
              {event.note ? <span className="block text-muted-foreground">{event.note}</span> : null}
            </li>
          ))}
        </ol>
      ) : null}
    </section>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[8rem_1fr] gap-3 py-1.5 text-[13px]">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  );
}

/** One document in context: what it is, where it stands, its revisions, and its identity for anyone who needs it. */
export function DocumentDrawer({ document, usage, twins, similar, onClose }: {
  document: DocumentSummary | null;
  usage?: DocumentUsage;
  twins: DocumentSummary[];
  similar: DocumentSummary[];
  onClose: () => void;
}) {
  const [editing, setEditing] = useState(false);
  if (!document) return null;
  const revisions = Array.from({ length: document.revision }, (_, index) => document.revision - index);
  const id = encodeURIComponent(document.document_id);
  return (
    <Drawer
      open
      onClose={() => {
        setEditing(false);
        onClose();
      }}
      title={displayTitle(document)}
      description={<span className="font-mono">{document.filename}</span>}
      footer={
        <div className="flex flex-wrap gap-2">
          <Button asChild size="sm">
            <Link to={`/documents/${id}`}>
              <ExternalLink aria-hidden />
              Open document
            </Link>
          </Button>
          {document.revision > 1 ? (
            <Button asChild size="sm" variant="outline">
              <Link to={`/documents/compare?a=${id}:${document.revision - 1}&b=${id}:${document.revision}`}>
                <GitCompare aria-hidden />
                Compare with revision {document.revision - 1}
              </Link>
            </Button>
          ) : null}
        </div>
      }
    >
      {editing ? (
        <ProfileEditor document={document} onSaved={() => setEditing(false)} />
      ) : (
        <div className="space-y-5">
          <dl className="divide-y divide-border">
            <Row label="Category">{categoryLabel(document.category ?? null)}</Row>
            <Row label="Description">{document.description ?? <span className="text-subtle-foreground">Not described</span>}</Row>
            <Row label="Scope">{scopeText(document) ?? <span className="text-subtle-foreground">Not stated</span>}</Row>
            <Row label="Revision">
              {document.revision}
              {document.based_on_revision ? <span className="text-subtle-foreground"> · based on revision {document.based_on_revision}</span> : null}
            </Row>
            <Row label="Last updated">
              <span title={formatDateTime(document.created_at)}>{formatRelative(document.created_at)}</span>
            </Row>
            <Row label="Used in rules">{usage ? <span title={usage.detail}>{usage.label}</span> : "—"}</Row>
            <Row label="Source">
              {document.source.kind}
              {document.source.reference ? ` · ${document.source.reference}` : ""}
              {document.source.description ? ` · ${document.source.description}` : ""}
            </Row>
          </dl>
          <ApprovalPanel document={document} />
          <Button type="button" size="sm" variant="outline" onClick={() => setEditing(true)}>
            Edit description
          </Button>
          {twins.length ? (
            <div className="rounded-md border border-status-info-border bg-status-info-soft px-3 py-2 text-[13px]">
              <p className="font-medium">Identical content</p>
              <p className="text-xs text-muted-foreground">The latest revision holds exactly the same text as:</p>
              <ul className="mt-1 text-xs">
                {twins.map((twin) => (
                  <li key={twin.document_id}>
                    {displayTitle(twin)} <span className="font-mono text-subtle-foreground">{twin.filename} · copy …{twin.document_id.slice(-6)}</span> <ApprovalBadge status={approvalOf(twin)} />
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {similar.length ? (
            <div className="text-[13px]">
              <p className="font-medium">Similarly named</p>
              <ul className="mt-1 space-y-1 text-xs">
                {similar.map((other) => (
                  <li key={other.document_id} className="flex flex-wrap items-center gap-2">
                    <span>
                      {displayTitle(other)} <span className="font-mono text-subtle-foreground">{other.filename}</span> · r{other.revision} · {categoryLabel(other.category ?? null)}
                    </span>
                    <Link className="text-primary hover:underline" to={`/documents/compare?a=${id}:${document.revision}&b=${encodeURIComponent(other.document_id)}:${other.revision}`}>
                      Compare side by side
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          <div>
            <h3 className="text-sm font-semibold">Revision history</h3>
            <ol className="mt-1.5 space-y-1 text-[13px]" aria-label="Revision history">
              {revisions.map((revision) => (
                <li key={revision} className="flex items-center gap-2">
                  <Link to={`/documents/${id}?revision=${revision}`} className="text-primary hover:underline">
                    Revision {revision}
                  </Link>
                  {revision === document.revision ? <Badge variant="outline">Latest</Badge> : null}
                  {revision > 1 ? (
                    <Link to={`/documents/compare?a=${id}:${revision - 1}&b=${id}:${revision}`} className="ml-auto text-xs text-muted-foreground hover:text-foreground hover:underline">
                      Changes from {revision - 1}
                    </Link>
                  ) : null}
                </li>
              ))}
            </ol>
          </div>
          <details className="text-xs">
            <summary className="cursor-pointer text-muted-foreground">Technical details</summary>
            <dl className="mt-2 divide-y divide-border">
              <Row label="Document ID">
                <span className="font-mono">{document.document_id}</span>
              </Row>
              <Row label="Content SHA-256">
                <span className="break-all font-mono">{document.content_sha256}</span>
              </Row>
              <Row label="Size">{formatInteger(document.size_bytes)} bytes</Row>
              <Row label="Stored">{formatDateTime(document.created_at)}</Row>
            </dl>
          </details>
        </div>
      )}
    </Drawer>
  );
}
