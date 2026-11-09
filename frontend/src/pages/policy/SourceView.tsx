/**
 * A document revision's Markdown, line by line, with a clause highlighted where its quoted text is found. No position
 * is guessed: a quote not found in the text is said to be so, and only a heading line the compiler located is marked.
 */
import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { useDocument } from "@/api/queries";
import { ErrorState, LoadingRows } from "@/components/states/states";
import { cn } from "@/lib/utils";
import { documentErrorHint } from "./documentFiles";
import { locateQuote } from "./library";

export function SourceLines({ content, highlight, marked, className }: { content: string; highlight: { from: number; to: number } | null; marked?: number | null; className?: string }) {
  const target = useRef<HTMLLIElement>(null);
  useEffect(() => {
    target.current?.scrollIntoView?.({ block: "center" });
  }, [highlight?.from, marked]);
  const first = highlight?.from ?? marked ?? null;
  return (
    <ol className={cn("overflow-auto rounded-md border border-border bg-muted/30 py-1 font-mono text-xs leading-5", className)} aria-label="Document source">
      {content.split("\n").map((line, index) => {
        const number = index + 1;
        const lit = highlight !== null && number >= highlight.from && number <= highlight.to;
        const heading = marked === number;
        return (
          <li
            key={number}
            ref={number === first ? target : undefined}
            data-highlighted={lit || undefined}
            className={cn("grid grid-cols-[3rem_1fr] gap-2 px-2", lit && "bg-status-review-soft", heading && !lit && "bg-primary-soft")}
          >
            <span className="select-none text-right text-subtle-foreground">{number}</span>
            <span className="whitespace-pre-wrap break-words">{line || " "}</span>
          </li>
        );
      })}
    </ol>
  );
}

/** The clause in its document: highlighted where the quote is found, with a link to the whole document at that line. */
export function SourceView({ documentId, revision, quote, line }: { documentId: string; revision: number | undefined; quote: string | null; line: number | null | undefined }) {
  const document = useDocument(documentId, revision);
  if (document.isPending) return <LoadingRows rows={4} label="Loading the policy source" />;
  if (document.isError)
    return (
      <>
        <ErrorState error={document.error} />
        {documentErrorHint(document.error) ? <p className="mt-1 text-xs text-muted-foreground">{documentErrorHint(document.error)}</p> : null}
      </>
    );
  const highlight = locateQuote(document.data.content, quote);
  const at = highlight?.from ?? line ?? null;
  return (
    <div className="space-y-2">
      <p className="text-xs text-muted-foreground">
        {highlight
          ? `Lines ${highlight.from}${highlight.to !== highlight.from ? `–${highlight.to}` : ""} of ${document.data.filename}, revision ${document.data.revision}.`
          : quote
            ? "The quoted clause is not found word for word in this revision: the compiler's quote is shown above it."
            : "The compiler located no source text for this field."}{" "}
        <Link className="text-primary hover:underline" to={`/documents/${encodeURIComponent(documentId)}?revision=${document.data.revision}${at ? `&line=${at}` : ""}`}>
          Open the whole document
        </Link>
      </p>
      <SourceLines content={document.data.content} highlight={highlight} marked={highlight ? null : line} className="max-h-[28rem]" />
    </div>
  );
}
