import { PanelLeftClose, PanelLeftOpen, ShieldCheck } from "lucide-react";
import { NavLink } from "react-router-dom";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/misc";
import { cn } from "@/lib/utils";
import { NAV_ITEMS } from "./nav";

export function Sidebar({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  return (
    <aside
      aria-label="Primary"
      className={cn(
        "sticky top-0 flex h-screen shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground transition-[width] duration-150",
        collapsed ? "w-16" : "w-60",
      )}
    >
      <div className={cn("flex h-14 items-center gap-2.5 border-b border-sidebar-border", collapsed ? "justify-center px-0" : "px-4")}>
        <div className="flex size-8 shrink-0 items-center justify-center rounded-md bg-sidebar-accent text-primary-foreground">
          <ShieldCheck className="size-4.5" aria-hidden />
        </div>
        {!collapsed ? (
          <div className="min-w-0 leading-tight">
            <p className="truncate text-sm font-semibold text-white">Policy Validator</p>
            <p className="truncate text-[11px] text-sidebar-muted">Expense audit intelligence</p>
          </div>
        ) : null}
      </div>

      <nav className="flex-1 overflow-y-auto px-2 py-3">
        {!collapsed ? <p className="px-3 pb-2 text-[11px] font-medium uppercase tracking-wider text-sidebar-muted">Workspace</p> : null}
        <ul className="space-y-0.5">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            const link = (
              <NavLink
                to={item.to}
                end={item.to === "/"}
                className={({ isActive }) =>
                  cn(
                    "group relative flex h-9 items-center gap-3 rounded-md text-[13px] font-medium transition-colors",
                    collapsed ? "justify-center px-0" : "px-3",
                    isActive ? "bg-sidebar-active text-white" : "text-sidebar-foreground/80 hover:bg-sidebar-hover hover:text-white",
                  )
                }
              >
                {({ isActive }) => (
                  <>
                    {isActive ? <span aria-hidden className="absolute inset-y-1.5 left-0 w-0.5 rounded-full bg-sidebar-accent" /> : null}
                    <Icon className={cn("size-4 shrink-0", isActive ? "text-white" : "text-sidebar-muted group-hover:text-white")} aria-hidden />
                    {collapsed ? <span className="sr-only">{item.label}</span> : <span className="truncate">{item.label}</span>}
                  </>
                )}
              </NavLink>
            );
            return (
              <li key={item.to}>
                {collapsed ? (
                  <Tooltip>
                    <TooltipTrigger asChild>{link}</TooltipTrigger>
                    <TooltipContent side="right">{item.label}</TooltipContent>
                  </Tooltip>
                ) : (
                  link
                )}
              </li>
            );
          })}
        </ul>
      </nav>

      <div className="border-t border-sidebar-border p-2">
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={!collapsed}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          className={cn(
            "flex h-9 w-full items-center gap-3 rounded-md text-[13px] text-sidebar-muted hover:bg-sidebar-hover hover:text-white",
            collapsed ? "justify-center" : "px-3",
          )}
        >
          {collapsed ? <PanelLeftOpen className="size-4" aria-hidden /> : <PanelLeftClose className="size-4" aria-hidden />}
          {!collapsed ? <span>Collapse</span> : null}
        </button>
      </div>
    </aside>
  );
}
