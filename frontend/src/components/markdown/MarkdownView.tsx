/**
 * A policy document's Markdown rendered as clean, readable text. Raw HTML in the source is shown as text, never
 * run, and links open outside the app. The exact stored text stays available through the Source view.
 */
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";

const COMPONENTS: Components = {
  h1: ({ children }) => <h1 className="mb-3 mt-1 text-xl font-semibold leading-7">{children}</h1>,
  h2: ({ children }) => <h2 className="mb-2 mt-6 border-b border-border pb-1 text-base font-semibold">{children}</h2>,
  h3: ({ children }) => <h3 className="mb-1.5 mt-4 text-sm font-semibold">{children}</h3>,
  h4: ({ children }) => <h4 className="mb-1 mt-3 text-[13px] font-semibold">{children}</h4>,
  p: ({ children }) => <p className="my-2 leading-6">{children}</p>,
  ul: ({ children }) => <ul className="my-2 list-disc space-y-1 pl-5">{children}</ul>,
  ol: ({ children }) => <ol className="my-2 list-decimal space-y-1 pl-5">{children}</ol>,
  li: ({ children }) => <li className="leading-6">{children}</li>,
  strong: ({ children }) => <strong className="font-semibold text-foreground">{children}</strong>,
  a: ({ children, href }) => (
    <a href={href} target="_blank" rel="noopener noreferrer" className="text-primary underline underline-offset-2">
      {children}
    </a>
  ),
  blockquote: ({ children }) => <blockquote className="my-2 border-l-2 border-border pl-3 text-muted-foreground">{children}</blockquote>,
  code: ({ children }) => <code className="rounded bg-muted px-1 py-0.5 font-mono text-[12px]">{children}</code>,
  pre: ({ children }) => <pre className="my-2 overflow-auto rounded-md bg-muted p-3 font-mono text-xs">{children}</pre>,
  hr: () => <hr className="my-4 border-border" />,
  table: ({ children }) => (
    <div className="my-3 overflow-x-auto">
      <table className="w-full border-collapse text-[13px]">{children}</table>
    </div>
  ),
  th: ({ children }) => <th className="border border-border bg-muted/50 px-2 py-1 text-left font-semibold">{children}</th>,
  td: ({ children }) => <td className="border border-border px-2 py-1 align-top">{children}</td>,
};

/** Rendered Markdown with no frame of its own, for a part of a document. */
export function MarkdownBody({ content }: { content: string }) {
  return (
    <Markdown remarkPlugins={[remarkGfm]} components={COMPONENTS} skipHtml>
      {content}
    </Markdown>
  );
}

export function MarkdownView({ content, className }: { content: string; className?: string }) {
  return (
    <div className={cn("overflow-auto rounded-md border border-border bg-card px-5 py-4 text-[13px] text-foreground/90", className)} aria-label="Formatted document">
      <MarkdownBody content={content} />
    </div>
  );
}
