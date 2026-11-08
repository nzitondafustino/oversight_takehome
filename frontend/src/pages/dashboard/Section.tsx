import { ArrowRight } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

/** A dashboard card: a title, a one-line scope or description, an optional link, then its body. */
export function Section({
  title,
  description,
  link,
  actions,
  children,
  className,
  id,
}: {
  title: string;
  description?: ReactNode;
  link?: { to: string; label: string };
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  id?: string;
}) {
  const headingId = id ? `${id}-title` : undefined;
  return (
    <Card className={cn("flex flex-col", className)} aria-labelledby={headingId} role="region">
      <CardHeader className="flex-row items-start justify-between gap-3">
        <div className="min-w-0">
          <CardTitle id={headingId}>{title}</CardTitle>
          {description ? <CardDescription className="mt-0.5">{description}</CardDescription> : null}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {actions}
          {link ? (
            <Link to={link.to} className="inline-flex items-center gap-1 rounded-sm text-[13px] font-medium text-primary hover:underline">
              {link.label}
              <ArrowRight className="size-3.5" aria-hidden />
            </Link>
          ) : null}
        </div>
      </CardHeader>
      <CardContent className="flex-1">{children}</CardContent>
    </Card>
  );
}

export function Pager({
  offset,
  limit,
  total,
  onChange,
  busy,
}: {
  offset: number;
  limit: number;
  total: number;
  onChange: (offset: number) => void;
  busy?: boolean;
}) {
  if (total <= limit) return null;
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + limit, total);
  return (
    <div className="mt-3 flex items-center justify-between text-xs text-subtle-foreground">
      <span className="tabular" aria-live="polite">
        {from}–{to} of {total}
        {busy ? " · loading" : ""}
      </span>
      <div className="flex gap-1">
        <button
          type="button"
          className="h-7 rounded-md border border-border bg-card px-2.5 font-medium text-foreground hover:bg-muted disabled:opacity-40"
          onClick={() => onChange(Math.max(0, offset - limit))}
          disabled={offset === 0}
        >
          Previous
        </button>
        <button
          type="button"
          className="h-7 rounded-md border border-border bg-card px-2.5 font-medium text-foreground hover:bg-muted disabled:opacity-40"
          onClick={() => onChange(offset + limit)}
          disabled={offset + limit >= total}
        >
          Next
        </button>
      </div>
    </div>
  );
}
