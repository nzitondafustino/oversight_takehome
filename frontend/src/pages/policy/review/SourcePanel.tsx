/**
 * The original Markdown beside a compared field: scrolled to its clause, the clause highlighted, and within it the
 * disputed values marked, all only as the API verified them against the exact revision. When it could not verify a
 * position, the evidence is shown with that said plainly: nothing is highlighted on a guess.
 */
import { CheckCircle2, CircleAlert } from "lucide-react";
import { Fragment, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useComparisonSource } from "@/api/queries";
import { MarkdownBody } from "@/components/markdown/MarkdownView";
import { ErrorState, LoadingRows } from "@/components/states/states";
import { cn } from "@/lib/utils";
import { documentErrorHint } from "../documentFiles";
import { displayTitle } from "../library";

type Owner = { extractionId: string } | { rulesetId: string };

const MARK: Record<string, string> = {
  regex: "bg-status-flag-soft text-status-flag-text ring-1 ring-status-flag-border",
  llm: "bg-status-info-soft text-primary ring-1 ring-status-info-border",
  clause: "bg-status-review text-white",
};

function Line({ text, marks }: { text: string; marks: { start: number; end: number; side: string; text: string }[] }) {
  if (!marks.length) return <>{text || " "}</>;
  const parts = [];
  let at = 0;
  for (const mark of marks) {
    if (mark.start < at) continue;
    parts.push(<Fragment key={`t${at}`}>{text.slice(at, mark.start)}</Fragment>);
    parts.push(
      <mark
        key={`m${mark.start}`}
        className={cn("rounded px-0.5", MARK[mark.side])}
        data-side={mark.side}
        title={mark.side === "clause" ? "A number in the clause" : `Only in the ${mark.side === "regex" ? "text pattern" : "AI"} reading`}
      >
        {text.slice(mark.start, mark.end)}
      </mark>,
    );
    at = mark.end;
  }
  parts.push(<Fragment key="rest">{text.slice(at)}</Fragment>);
  return <>{parts}</>;
}

type View = "formatted" | "source";
const VIEW_KEY = "epv.sourceView";

function storedView(): View {
  try {
    return window.localStorage.getItem(VIEW_KEY) === "source" ? "source" : "formatted";
  } catch {
    return "formatted";
  }
}

/** The document rendered, with the verified clause lines rendered apart in a highlighted block. */
function Formatted({ lines, clause, scrollKey }: { lines: string[]; clause: { from: number; to: number } | null; scrollKey: unknown }) {
  const box = useRef<HTMLDivElement>(null);
  const target = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const container = box.current;
    const block = target.current;
    if (container && block) container.scrollTop = Math.max(0, block.offsetTop - container.clientHeight / 3);
  }, [scrollKey]);
  const part = (from: number, to: number) => lines.slice(from - 1, to).join("\n");
  return (
    <div ref={box} className="relative max-h-[26rem] overflow-auto rounded-md border border-border bg-card px-4 py-2 text-[13px] text-foreground/90" aria-label="Formatted policy document">
      {clause ? (
        <>
          <MarkdownBody content={part(1, clause.from - 1)} />
          <div ref={target} data-clause className="-mx-2 my-1 rounded-md border-l-4 border-status-review-border bg-status-review-soft px-3 py-0.5">
            <MarkdownBody content={part(clause.from, clause.to)} />
          </div>
          <MarkdownBody content={part(clause.to + 1, lines.length)} />
        </>
      ) : (
        <MarkdownBody content={lines.join("\n")} />
      )}
    </div>
  );
}

export function SourcePanel({ owner, comparisonId, evidence }: { owner: Owner; comparisonId: string; evidence: string | null }) {
  const source = useComparisonSource(owner, comparisonId);
  const [view, setView] = useState<View>(storedView);
  const choose = (next: View) => {
    setView(next);
    try {
      window.localStorage.setItem(VIEW_KEY, next);
    } catch {
      /* the choice is only a convenience */
    }
  };
  const list = useRef<HTMLOListElement>(null);
  const target = useRef<HTMLLIElement>(null);
  const data = source.data;
  useEffect(() => {
    // Scroll the panel, not the page, so the clause sits a third of the way down with its context above it.
    const container = list.current;
    const line = target.current;
    if (container && line) container.scrollTop = Math.max(0, line.offsetTop - container.clientHeight / 3);
  }, [data, view]);

  if (source.isPending) return <LoadingRows rows={5} label="Loading the policy document" />;
  if (source.isError)
    return (
      <>
        <ErrorState error={source.error} />
        {documentErrorHint(source.error) ? <p className="mt-1 text-xs text-muted-foreground">{documentErrorHint(source.error)}</p> : null}
      </>
    );
  const { document, location } = data!;
  const lines = document.content.split("\n");
  const first = location?.from_line ?? data!.heading_line ?? null;
  const verified = data!.revision_verified && location !== null;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
        <span className="font-medium text-foreground">{displayTitle({ title: null, filename: document.filename })}</span>
        <span className="text-muted-foreground">
          {document.revision ? `Revision ${document.revision}` : "As compiled"}
          {data!.section ? ` · ${data!.section}` : ""}
        </span>
        {verified ? (
          <span className="inline-flex items-center gap-1 text-status-pass" role="status">
            <CheckCircle2 className="size-3.5" aria-hidden />
            Lines {location!.from_line}–{location!.to_line}, verified in this revision
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 text-status-review-text" role="status">
            <CircleAlert className="size-3.5" aria-hidden />
            Precise location unavailable
          </span>
        )}
        <div role="radiogroup" aria-label="Show the document" className="ml-auto inline-flex rounded-md bg-muted p-0.5">
          {(["formatted", "source"] as const).map((item) => (
            <button
              key={item}
              type="button"
              role="radio"
              aria-checked={view === item}
              onClick={() => choose(item)}
              className={cn("rounded px-2 py-0.5 font-medium", view === item ? "bg-card shadow-sm" : "text-muted-foreground hover:text-foreground")}
            >
              {item === "formatted" ? "Formatted" : "Source"}
            </button>
          ))}
        </div>
        {document.revision ? (
          <Link className=" text-primary hover:underline" to={`/documents/${encodeURIComponent(document.document_id)}?revision=${document.revision}${first ? `&line=${first}` : ""}`}>
            Open document
          </Link>
        ) : null}
      </div>
      {!verified ? (
        <div className="space-y-1 rounded-md border border-status-review-border bg-status-review-soft px-3 py-2 text-xs">
          <p>{data!.reason ?? "The clause could not be placed in this revision."}</p>
          {evidence ? <blockquote className="whitespace-pre-wrap border-l-2 border-status-review-border pl-2 text-[13px] text-foreground/90">{evidence}</blockquote> : null}
        </div>
      ) : null}
      {view === "formatted" ? (
        <>
          <Formatted lines={lines} clause={verified ? { from: location!.from_line, to: location!.to_line } : null} scrollKey={data} />
          {data!.highlights.length ? <p className="text-[11px] text-muted-foreground">The disputed values are marked exactly in Source.</p> : null}
        </>
      ) : null}
      {view === "source" ? (
        <ol ref={list} className="relative max-h-[26rem] overflow-auto rounded-md border border-border bg-muted/30 py-1 font-mono text-xs leading-5" aria-label="Policy document">
          {lines.map((text, index) => {
            const number = index + 1;
            const inClause = verified && number >= location!.from_line && number <= location!.to_line;
            const heading = !inClause && number === data!.heading_line;
            const marks = data!.highlights.filter((item) => item.line === number).sort((a, b) => a.start - b.start);
            return (
              <li
                key={number}
                ref={number === first ? target : undefined}
                data-clause={inClause || undefined}
                className={cn("grid grid-cols-[3rem_1fr] gap-2 px-2", inClause && "bg-status-review-soft", heading && "border-l-2 border-primary")}
              >
                <span className="select-none text-right text-subtle-foreground">{number}</span>
                <span className="whitespace-pre-wrap break-words">
                  <Line text={text} marks={marks} />
                </span>
              </li>
            );
          })}
        </ol>
      ) : null}
      {view === "source" && data!.highlights.length ? (
        <p className="flex flex-wrap gap-3 text-[11px] text-muted-foreground">
          {data!.highlights.some((item) => item.side === "regex") ? (
            <span>
              <mark className={cn("rounded px-1", MARK.regex)}>value</mark> only in the text-match reading
            </span>
          ) : null}
          {data!.highlights.some((item) => item.side === "llm") ? (
            <span>
              <mark className={cn("rounded px-1", MARK.llm)}>value</mark> only in the AI reading
            </span>
          ) : null}
          {data!.highlights.some((item) => item.side === "clause") ? (
            <span>
              <mark className={cn("rounded px-1", MARK.clause)}>number</mark> in the clause, not read as a limit
            </span>
          ) : null}
        </p>
      ) : null}
    </div>
  );
}
