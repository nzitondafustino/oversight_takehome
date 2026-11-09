/**
 * Two document revisions side by side, line by line: two revisions of one document, or two documents that look
 * alike. "Identical content" is said only when the stored hashes are equal.
 */
import { ArrowLeft } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useDocument } from "@/api/queries";
import type { DocumentDetail } from "@/api/types";
import { PageHeader } from "@/components/layout/AppLayout";
import { EmptyState, ErrorState, LoadingRows } from "@/components/states/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatDateTime, formatInteger, shortHash } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Section } from "@/pages/dashboard/Section";
import { documentErrorHint } from "./documentFiles";
import { categoryLabel, displayTitle, lineDiff, scopeText, type DiffLine } from "./library";

/** "doc_…:3" as a document and revision; null when malformed. */
export function parseRef(text: string | null): { documentId: string; revision: number } | null {
  const match = /^(doc_[0-9a-f]{24}):(\d+)$/.exec(text ?? "");
  return match ? { documentId: match[1]!, revision: Number(match[2]) } : null;
}

type Row = { left: DiffLine | null; right: DiffLine | null };

/** Pairs removed and added runs, so a changed line sits beside its replacement. */
function rows(lines: DiffLine[]): Row[] {
  const out: Row[] = [];
  for (let index = 0; index < lines.length; ) {
    const line = lines[index]!;
    if (line.kind === "same") {
      out.push({ left: line, right: line });
      index++;
      continue;
    }
    const removed: DiffLine[] = [];
    const added: DiffLine[] = [];
    while (index < lines.length && lines[index]!.kind !== "same") {
      (lines[index]!.kind === "removed" ? removed : added).push(lines[index]!);
      index++;
    }
    for (let k = 0; k < Math.max(removed.length, added.length); k++) out.push({ left: removed[k] ?? null, right: added[k] ?? null });
  }
  return out;
}

function Side({ document, label }: { document: DocumentDetail; label: string }) {
  return (
    <div className="min-w-0 space-y-0.5">
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <p className="text-[13px] font-semibold">{displayTitle(document)}</p>
      <p className="font-mono text-[11px] text-subtle-foreground">{document.filename}</p>
      <p className="text-xs text-muted-foreground">
        Revision {document.revision} · {categoryLabel(document.category ?? null)}
        {scopeText(document) ? ` · ${scopeText(document)}` : ""} · {formatDateTime(document.created_at)}
      </p>
      {document.description ? <p className="text-xs text-muted-foreground">{document.description}</p> : null}
      <p className="font-mono text-[11px] text-subtle-foreground" title={document.content_sha256}>
        {shortHash(document.content_sha256)} · {formatInteger(document.size_bytes)} bytes
      </p>
    </div>
  );
}

function Cell({ line, side }: { line: DiffLine | null; side: "left" | "right" }) {
  const changed = line && line.kind !== "same";
  return (
    <>
      <span className="select-none text-right text-subtle-foreground">{line ? (side === "left" ? line.left : line.right) : ""}</span>
      <span
        className={cn("whitespace-pre-wrap break-words px-1", changed && side === "left" && "bg-status-flag-soft text-status-flag-text", changed && side === "right" && "bg-status-pass-soft text-status-pass", !line && "bg-muted/60")}
        data-change={changed ? (side === "left" ? "removed" : "added") : undefined}
      >
        {line ? line.text || " " : ""}
      </span>
    </>
  );
}

export function DocumentComparePage() {
  const [params] = useSearchParams();
  const a = parseRef(params.get("a"));
  const b = parseRef(params.get("b"));
  const left = useDocument(a?.documentId, a?.revision);
  const right = useDocument(b?.documentId, b?.revision);
  const [onlyChanges, setOnlyChanges] = useState(false);
  const diff = useMemo(() => (left.data && right.data ? lineDiff(left.data.content, right.data.content) : null), [left.data, right.data]);
  const back = (
    <Button asChild variant="outline" size="sm">
      <Link to="/documents">
        <ArrowLeft aria-hidden />
        Documents
      </Link>
    </Button>
  );
  if (!a || !b) return <><PageHeader title="Compare documents" actions={back} /><EmptyState title="Choose two document revisions" description="Select two documents in the library, or a revision in a document's history, to compare them." /></>;
  if (left.isPending || right.isPending) return <><PageHeader title="Compare documents" actions={back} /><LoadingRows rows={6} label="Loading both revisions" /></>;
  const failed = left.error ?? right.error;
  if (failed || !left.data || !right.data || !diff)
    return (
      <>
        <PageHeader title="Compare documents" actions={back} />
        <ErrorState error={failed} />
        {documentErrorHint(failed) ? <p className="mt-2 text-xs text-muted-foreground">{documentErrorHint(failed)}</p> : null}
      </>
    );
  const same = left.data.content_sha256 === right.data.content_sha256;
  const sameDocument = left.data.document_id === right.data.document_id;
  const all = rows(diff.lines);
  const changed = all.filter((row) => row.left?.kind !== "same");
  const shown = onlyChanges ? changed : all;
  return (
    <>
      <PageHeader title={sameDocument ? `Revision ${left.data.revision} and ${right.data.revision} of ${displayTitle(left.data)}` : "Compare documents"} actions={back} />
      <Section id="compared" title="Compared" actions={same ? <Badge variant="info">Identical content</Badge> : <Badge variant="review">{changed.length === 1 ? "1 line differs" : `${changed.length} lines differ`}</Badge>}>
        <div className="grid gap-4 sm:grid-cols-2">
          <Side document={left.data} label={sameDocument ? "Earlier" : "Left"} />
          <Side document={right.data} label={sameDocument ? "Later" : "Right"} />
        </div>
        {same && !sameDocument ? <p className="mt-3 text-[13px]">Both hold exactly the same text, by their content hashes, though they are separate documents.</p> : null}
        {!same && !sameDocument && displayTitle(left.data) === displayTitle(right.data) ? (
          <p className="mt-3 text-[13px]">These documents share a name but not their text: they are not duplicates.</p>
        ) : null}
      </Section>
      <Section
        id="differences"
        title="Differences"
        className="mt-4"
        actions={
          <label className="flex items-center gap-1.5 text-xs">
            <input type="checkbox" checked={onlyChanges} onChange={(event) => setOnlyChanges(event.target.checked)} className="size-3.5 accent-primary" />
            Only changed lines
          </label>
        }
      >
        {diff.approximate ? <p className="mb-2 text-xs text-status-review-text">These texts are long, so the changed part is shown as a whole rather than line by line.</p> : null}
        {same ? (
          <p className="text-[13px] text-muted-foreground">No differences.</p>
        ) : (
          <div className="-mx-5 overflow-x-auto border-y border-border">
            <div role="table" aria-label="Line differences" className="min-w-[760px] font-mono text-xs leading-5">
              {shown.map((row, index) => (
                <div role="row" key={index} className="grid grid-cols-[3rem_1fr_3rem_1fr] gap-x-1 border-b border-border/50 px-2 last:border-0">
                  <Cell line={row.left} side="left" />
                  <Cell line={row.right} side="right" />
                </div>
              ))}
            </div>
          </div>
        )}
      </Section>
    </>
  );
}
