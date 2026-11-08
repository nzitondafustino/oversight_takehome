import type { UseQueryResult } from "@tanstack/react-query";
import { Check, Copy } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { ErrorState, LoadingRows } from "@/components/states/states";
import { Button } from "@/components/ui/button";
import { formatInteger } from "@/lib/format";
import { parseJson, stringifyJson, type JsonValue } from "@/lib/losslessJson";

/** One token pattern: a string (a key when a colon follows), a number, or a literal. */
const TOKEN = /("(?:\\.|[^"\\])*")(\s*:)?|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|\b(true|false|null)\b/g;

/** Colours the pretty-printed text as React nodes: no HTML is ever injected. */
export function highlightJson(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(TOKEN)) {
    const at = match.index ?? 0;
    if (at > last) nodes.push(text.slice(last, at));
    const [whole, string, colon, number, literal] = match;
    if (string && colon) {
      nodes.push(<span key={at} className="text-primary">{string}</span>, colon);
    } else if (string) {
      nodes.push(<span key={at} className="text-accent-foreground">{string}</span>);
    } else if (number) {
      nodes.push(<span key={at} className="text-status-review-text">{number}</span>);
    } else if (literal) {
      nodes.push(<span key={at} className="font-semibold text-subtle-foreground">{literal}</span>);
    } else {
      nodes.push(whole);
    }
    last = at + whole.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

/**
 * A read-only view of the API's JSON. The text is parsed and re-indented losslessly, so every number keeps the
 * exact form the API sent. `select` picks part of it, such as one receipt's result.
 */
export function JsonViewer({
  source,
  select,
  label,
  note,
}: {
  source: UseQueryResult<string>;
  select?: (root: JsonValue) => JsonValue | undefined;
  label: string;
  note?: ReactNode;
}) {
  const [copied, setCopied] = useState(false);
  const pretty = useMemo(() => {
    if (!source.data) return null;
    const root = parseJson(source.data);
    const part = select ? select(root) : root;
    return part === undefined ? null : stringifyJson(part, 2);
  }, [source.data, select]);
  const highlighted = useMemo(() => (pretty ? highlightJson(pretty) : null), [pretty]);

  if (source.isPending) return <LoadingRows rows={3} label={`Loading ${label}`} />;
  if (source.isError) return <ErrorState error={source.error} onRetry={() => void source.refetch()} />;
  if (!pretty) return <p className="text-xs text-muted-foreground">Not found in the stored run.</p>;

  async function copy() {
    try {
      await navigator.clipboard.writeText(pretty!);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-subtle-foreground">
          {note} {formatInteger(pretty.split("\n").length)} lines
        </p>
        <Button type="button" variant="outline" size="sm" onClick={() => void copy()}>
          {copied ? <Check aria-hidden /> : <Copy aria-hidden />}
          {copied ? "Copied" : "Copy JSON"}
        </Button>
      </div>
      <pre
        aria-label={label}
        tabIndex={0}
        className="max-h-[32rem] overflow-auto whitespace-pre-wrap break-words rounded-md border border-border bg-background/70 px-3 py-2.5 font-mono text-[12px] leading-5 text-foreground focus-visible:outline-2 focus-visible:outline-ring"
      >
        <code>{highlighted}</code>
      </pre>
    </div>
  );
}
