import type { UseQueryResult } from "@tanstack/react-query";
import { AlertTriangle, Loader2, ShieldCheck, ShieldOff, Zap } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import type { Backends, ExecutionMode, Page, RulesetSummary } from "@/api/types";
import { EmptyState, ErrorState, LoadingRows } from "@/components/states/states";
import { Button } from "@/components/ui/button";
import { formatInteger } from "@/lib/format";
import { cn } from "@/lib/utils";
import { ClassificationSelector } from "./ClassificationSelector";
import { backendFor, choose, defaultReplaySet, ineligibleReason, isEligibleRuleset, MODE_COPY, modeAvailability, replaySetsFor, type ClassificationChoice, type ValidationConfig } from "./config";

const SELECT =
  "h-9 w-full rounded-md border border-input bg-card px-2 text-[13px] text-foreground focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring disabled:opacity-60";

function Field({ label, htmlFor, hint, children }: { label: string; htmlFor?: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="space-y-1">
      <label htmlFor={htmlFor} className="block text-xs font-medium text-foreground">
        {label}
      </label>
      {children}
      {hint ? <p className="text-xs leading-4 text-subtle-foreground">{hint}</p> : null}
    </div>
  );
}

export function ValidationConfiguration({
  config,
  setConfig,
  rulesets,
  backends,
  issues,
  receiptCount,
  pending,
  onSubmit,
}: {
  config: ValidationConfig;
  setConfig: (update: (config: ValidationConfig) => ValidationConfig) => void;
  rulesets: UseQueryResult<Page<RulesetSummary>>;
  backends: UseQueryResult<Backends>;
  issues: string[];
  receiptCount: number;
  pending: boolean;
  onSubmit: () => void;
}) {
  const discovery = backends.data;
  const chosenBackend = discovery ? backendFor(config, discovery) : undefined;
  const items = rulesets.data?.items ?? [];
  const eligible = items.filter(isEligibleRuleset);

  function chooseMode(mode: ExecutionMode) {
    // Live is only ever chosen here, by the user, and always starts unconfirmed.
    setConfig((current) => ({ ...current, mode, liveConfirmed: false }));
  }

  function chooseClassification(change: Partial<ClassificationChoice>) {
    setConfig((current) => {
      const next = choose(current, change);
      if (!discovery) return next;
      // The replay set follows the adapter the choice now needs, as recordings are kept per adapter.
      const before = backendFor(current, discovery), after = backendFor(next, discovery);
      return after?.id === before?.id ? next : { ...next, replaySet: after ? defaultReplaySet(after) : "" };
    });
  }

  return (
    <div className="space-y-4">
      {/* Ruleset */}
      {rulesets.isPending ? (
        <LoadingRows rows={1} label="Loading rulesets" />
      ) : rulesets.isError ? (
        <ErrorState error={rulesets.error} onRetry={() => void rulesets.refetch()} />
      ) : eligible.length === 0 ? (
        <EmptyState
          icon={<ShieldOff className="size-5" aria-hidden />}
          title="No ruleset published for production"
          description={items.length ? `${items.length} version${items.length === 1 ? " exists" : "s exist"}, none published for production.` : "No ruleset versions exist yet."}
          action={
            <Button asChild variant="outline" size="sm">
              <Link to="/policies">Go to Policy Management</Link>
            </Button>
          }
          className="py-5"
        />
      ) : (
        <Field label="Ruleset version" htmlFor="ruleset" hint="Only a version published for production can be validated against.">
          <select
            id="ruleset"
            className={SELECT}
            value={config.rulesetId ?? ""}
            onChange={(event) => setConfig((current) => ({ ...current, rulesetId: event.target.value || null }))}
          >
            {items.map((ruleset) => (
              <option key={ruleset.ruleset_id} value={ruleset.ruleset_id} disabled={!isEligibleRuleset(ruleset)}>
                {ruleset.name} v{ruleset.version} · {isEligibleRuleset(ruleset) ? `published (production) · ${ruleset.active_rule_count} rules` : ineligibleReason(ruleset)}
              </option>
            ))}
          </select>
        </Field>
      )}

      {/* Mode */}
      {backends.isPending ? (
        <LoadingRows rows={2} label="Loading deployment capabilities" />
      ) : backends.isError ? (
        <ErrorState error={backends.error} onRetry={() => void backends.refetch()} />
      ) : discovery ? (
        <>
          <div className="space-y-1">
            <p id="mode-label" className="text-xs font-medium text-foreground">
              Execution mode
            </p>
            <div role="radiogroup" aria-labelledby="mode-label" className="grid grid-cols-3 gap-1 rounded-md bg-muted p-1">
              {(["replay", "live", "deterministic"] as const).map((mode) => {
                const availability = modeAvailability(discovery, mode);
                const checked = config.mode === mode;
                return (
                  <button
                    key={mode}
                    type="button"
                    role="radio"
                    aria-checked={checked}
                    disabled={!availability.available}
                    title={availability.available ? MODE_COPY[mode].description : availability.reason}
                    onClick={() => chooseMode(mode)}
                    className={cn(
                      "inline-flex h-7 items-center justify-center gap-1 rounded-sm text-xs font-medium",
                      checked ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
                      "disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:text-muted-foreground",
                    )}
                  >
                    {mode === "live" ? <Zap className="size-3" aria-hidden /> : null}
                    {MODE_COPY[mode].label}
                  </button>
                );
              })}
            </div>
            <p className="text-xs leading-4 text-subtle-foreground">{MODE_COPY[config.mode].description}</p>
            {!modeAvailability(discovery, "live").available ? (
              <p className="text-xs leading-4 text-subtle-foreground">Live unavailable: {modeAvailability(discovery, "live").reason}</p>
            ) : null}
          </div>

          {config.mode !== "deterministic" ? (
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="sm:col-span-2">
                <ClassificationSelector idPrefix="validate" selectClassName={SELECT} discovery={discovery} mode={config.mode} choice={config} onChange={chooseClassification} />
              </div>

              {config.mode === "replay" && chosenBackend ? (
                <Field label="Replay set" htmlFor="replay-set">
                  <select id="replay-set" className={SELECT} value={config.replaySet} onChange={(event) => setConfig((current) => ({ ...current, replaySet: event.target.value }))}>
                    <option value="">Deployment default</option>
                    {replaySetsFor(chosenBackend).map((set) => (
                      <option key={set.name} value={set.name}>
                        {set.name}
                        {set.answer_files != null ? ` (${formatInteger(set.answer_files)} answers)` : ""}
                      </option>
                    ))}
                  </select>
                </Field>
              ) : null}

            </div>
          ) : null}
        </>
      ) : null}

      <label className="flex cursor-pointer gap-2.5 text-[13px]">
        <input
          type="checkbox"
          checked={config.contextComplete}
          onChange={(event) => setConfig((current) => ({ ...current, contextComplete: event.target.checked }))}
          className="mt-0.5 size-3.5 accent-primary"
        />
        <span>
          <span className="font-medium text-foreground">This batch is the complete context</span>
          <span className="block text-xs leading-4 text-muted-foreground">
            Otherwise cross-receipt checks, such as split purchases or shared allowances, that receipts not sent could change stay undecided, and a receipt with nothing else against it comes back as a provisional pass.
          </span>
        </span>
      </label>

      {config.mode === "live" ? (
        <label className="flex cursor-pointer gap-2.5 rounded-md border border-status-review-border bg-status-review-soft px-3 py-2 text-[13px]">
          <input
            type="checkbox"
            checked={config.liveConfirmed}
            onChange={(event) => setConfig((current) => ({ ...current, liveConfirmed: event.target.checked }))}
            className="mt-0.5 size-3.5 accent-primary"
          />
          <span>
            <span className="font-medium text-status-review-text">I understand this calls paid providers</span>
            <span className="block text-xs leading-4 text-foreground/80">Live validation sends line text to the models and may incur charges.</span>
          </span>
        </label>
      ) : null}

      <div className="border-t border-border pt-4">
        {issues.length ? (
          <ul className="mb-3 space-y-1 text-xs leading-4 text-status-review-text" aria-label="Before validating">
            {issues.map((issue) => (
              <li key={issue} className="flex gap-1.5">
                <AlertTriangle className="mt-px size-3.5 shrink-0" aria-hidden />
                {issue}
              </li>
            ))}
          </ul>
        ) : null}
        <Button type="button" className="w-full" size="lg" disabled={pending || issues.length > 0} onClick={onSubmit} aria-busy={pending}>
          {pending ? <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden /> : <ShieldCheck aria-hidden />}
          {pending ? "Validating…" : `Validate ${receiptCount ? formatInteger(receiptCount) : ""} receipt${receiptCount === 1 ? "" : "s"}${config.mode === "live" ? " live" : ""}`}
        </Button>
        <p className="mt-1.5 text-center text-xs text-subtle-foreground">
          {MODE_COPY[config.mode].label} mode{config.mode === "replay" ? " · no new provider spending" : config.mode === "live" ? " · may incur provider charges" : ""}
        </p>
      </div>
    </div>
  );
}
