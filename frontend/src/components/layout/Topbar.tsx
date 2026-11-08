import { useLocation } from "react-router-dom";
import { useHealth } from "@/api/queries";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/misc";
import { cn } from "@/lib/utils";
import { NAV_ITEMS } from "./nav";

function sectionFor(pathname: string) {
  return [...NAV_ITEMS].sort((a, b) => b.to.length - a.to.length).find((item) => (item.to === "/" ? pathname === "/" : pathname.startsWith(item.to)));
}

function ApiStatus() {
  const health = useHealth();
  let tone: "ok" | "degraded" | "down" | "pending" = "pending";
  let label = "Checking API";
  let detail = "Contacting /health";
  if (health.data?.status === "ok") {
    tone = "ok";
    label = "API online";
    detail = `API ${health.data.version} · database ok`;
  } else if (health.data?.status === "degraded") {
    tone = "degraded";
    label = "Database unavailable";
    detail = `API ${health.data.version} is running, but MongoDB does not answer`;
  } else if (health.isError) {
    tone = "down";
    label = "API unreachable";
    detail = health.error instanceof Error ? health.error.message : "No answer from /health";
  }
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          tabIndex={0}
          role="status"
          className="inline-flex h-9 items-center gap-2 rounded-md px-2 text-xs font-medium text-muted-foreground"
        >
          <span
            aria-hidden
            className={cn(
              "size-2 rounded-full",
              tone === "ok" && "bg-status-pass",
              tone === "degraded" && "bg-status-review",
              tone === "down" && "bg-status-flag",
              tone === "pending" && "bg-status-neutral",
            )}
          />
          <span className="hidden md:inline">{label}</span>
          <span className="sr-only md:hidden">{label}</span>
        </span>
      </TooltipTrigger>
      <TooltipContent>{detail}</TooltipContent>
    </Tooltip>
  );
}

export function Topbar() {
  const { pathname } = useLocation();
  const section = sectionFor(pathname);
  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-border bg-card/95 px-4 backdrop-blur supports-[backdrop-filter]:bg-card/85 md:px-6">
      <nav aria-label="Breadcrumb" className="min-w-0 flex-1">
        <ol className="flex items-center gap-2 text-[13px]">
          <li className="hidden text-subtle-foreground sm:block">Expense Policy Validator</li>
          <li aria-hidden className="hidden text-border sm:block">
            /
          </li>
          <li className="truncate font-medium text-foreground" aria-current="page">
            {section?.label ?? "Not found"}
          </li>
        </ol>
      </nav>
      <ApiStatus />
    </header>
  );
}
