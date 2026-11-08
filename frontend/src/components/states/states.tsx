import { AlertTriangle, Inbox, RefreshCw, WifiOff } from "lucide-react";
import type { ReactNode } from "react";
import { ApiError } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/misc";
import { cn } from "@/lib/utils";

export function LoadingRows({ rows = 4, label = "Loading" }: { rows?: number; label?: string }) {
  return (
    <div role="status" aria-live="polite" className="space-y-2.5">
      <span className="sr-only">{label}…</span>
      {Array.from({ length: rows }, (_, index) => (
        <Skeleton key={index} className="h-9 w-full" />
      ))}
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
  icon,
  className,
}: {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  icon?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center justify-center rounded-md border border-dashed border-border px-6 py-10 text-center", className)}>
      <div className="mb-3 flex size-10 items-center justify-center rounded-full bg-muted text-subtle-foreground">{icon ?? <Inbox className="size-5" aria-hidden />}</div>
      <p className="text-sm font-medium text-foreground">{title}</p>
      {description ? <p className="mt-1 max-w-sm text-[13px] leading-5 text-muted-foreground">{description}</p> : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

function describe(error: unknown): { title: string; message: string; code?: string } {
  if (error instanceof ApiError) {
    if (error.isNetwork) return { title: "API unreachable", message: error.message, code: error.code };
    if (error.code === "database_unavailable") return { title: "Database unavailable", message: error.message, code: error.code };
    return { title: `Request failed (${error.status})`, message: error.message, code: error.code };
  }
  return { title: "Something went wrong", message: error instanceof Error ? error.message : String(error) };
}

export function ErrorState({ error, onRetry, className }: { error: unknown; onRetry?: () => void; className?: string }) {
  const { title, message, code } = describe(error);
  const network = error instanceof ApiError && error.isNetwork;
  return (
    <div role="alert" className={cn("flex items-start gap-3 rounded-md border border-status-flag-border bg-status-flag-soft px-4 py-3", className)}>
      {network ? <WifiOff className="mt-0.5 size-4 shrink-0 text-status-flag-text" aria-hidden /> : <AlertTriangle className="mt-0.5 size-4 shrink-0 text-status-flag-text" aria-hidden />}
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-status-flag-text">{title}</p>
        <p className="mt-0.5 break-words text-[13px] leading-5 text-foreground/80">{message}</p>
        {code ? <p className="mt-1 font-mono text-[11px] text-muted-foreground">{code}</p> : null}
      </div>
      {onRetry ? (
        <Button variant="outline" size="sm" onClick={onRetry}>
          <RefreshCw aria-hidden />
          Retry
        </Button>
      ) : null}
    </div>
  );
}
