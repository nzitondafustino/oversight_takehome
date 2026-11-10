import { ArrowLeft, ChevronLeft, ChevronRight, CircleCheck, CircleDashed } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import type { ClassificationSetup, ReceiptResult } from "@/api/types";
import { dispositionDisplay, isProvisionalPass } from "@/components/status/badges";
import { Button } from "@/components/ui/button";
import { useRunJson } from "@/api/queries";
import { isJsonObject, type JsonObject, type JsonValue, scalarText } from "@/lib/losslessJson";
import { cn } from "@/lib/utils";
import { inputLines } from "@/pages/validate/receipts";
import { JsonViewer } from "./JsonViewer";
import { ExpenseItems, lineAnchor } from "./LineItemTable";
import { PolicyFindings } from "./PolicyFindings";
import { WhyResult, type FindingSelection } from "./WhyResult";
import { setupFacts } from "@/pages/validate/config";

/** Receipt fields shown when the receipt was submitted here; a stored run holds results only. */
const INPUT_FIELDS: [string, string][] = [
  ["merchant_name", "Merchant"],
  ["transaction_datetime", "Date and time (local)"],
  ["total_amount", "Total"],
  ["currency", "Currency"],
  ["submitted_expense_type", "Expense type"],
  ["employee_id", "Employee"],
  ["employee_country", "Employee country"],
  ["entity", "Entity"],
  ["mcc_description", "Merchant category"],
  ["attendee_count", "Attendees"],
  ["trip_id", "Trip"],
  ["pre_approval_ref", "Pre-approval"],
];

function scrollToElement(id: string) {
  requestAnimationFrame(() => {
    const element = document.getElementById(id);
    if (!element) return;
    const details = element.closest("details");
    if (details && !details.open) details.open = true;
    element.scrollIntoView?.({ block: "center" });
    element.focus({ preventScroll: true });
  });
}

export interface ReceiptNavigation {
  position: number;
  total: number;
  onPrevious: () => void;
  onNext: () => void;
  /** Back to the receipt list. Hidden from desktop width up, where the list is beside the detail. */
  onBack?: () => void;
}

function Fact({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-subtle-foreground">{label}</dt>
      <dd className="break-words text-[13px] font-medium text-foreground">{value ?? <span className="text-xs font-normal italic text-subtle-foreground">Unavailable</span>}</dd>
    </div>
  );
}

export function Disclosure({ title, hint, children, onOpen }: { title: string; hint?: string; children: ReactNode; onOpen?: () => void }) {
  return (
    <details className="group rounded-lg border border-border bg-card" onToggle={(event) => event.currentTarget.open && onOpen?.()}>
      <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-2.5 text-[13px] font-semibold text-foreground [&::-webkit-details-marker]:hidden">
        <ChevronRight className="size-4 text-subtle-foreground transition-transform group-open:rotate-90" aria-hidden />
        {title}
        {hint ? <span className="ml-auto text-xs font-normal text-subtle-foreground">{hint}</span> : null}
      </summary>
      <div className="border-t border-border px-4 py-3">{children}</div>
    </details>
  );
}

/** This receipt's result inside the stored run's JSON: results.results[i] with its receipt_id. */
function receiptIn(receiptId: string) {
  return (root: JsonValue): JsonValue | undefined => {
    // The run export lists each receipt, in the brief's shape, under "receipts".
    const receipts = isJsonObject(root) ? root.receipts : undefined;
    return Array.isArray(receipts) ? receipts.find((item) => isJsonObject(item) && item.receipt_id === receiptId) : undefined;
  };
}

export function ReceiptDetail({
  result,
  input,
  navigation,
  run,
}: {
  result: ReceiptResult;
  input: JsonObject | null;
  navigation?: ReceiptNavigation;
  /** The stored run this result belongs to, for its exact JSON. */
  run: { runId: string; setup?: ClassificationSetup | null };
}) {
  const [selection, setSelection] = useState<FindingSelection | null>(null);
  const [jsonOpen, setJsonOpen] = useState(false);
  const runJson = useRunJson(run.runId, jsonOpen);
  const selectReceipt = useMemo(() => receiptIn(result.receipt_id), [result.receipt_id]);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [focusedFinding, setFocusedFinding] = useState<string | null>(null);

  const lines = useMemo(() => (input ? inputLines(input) : null), [input]);
  const currency = input ? scalarText(input.currency) : null;
  const merchant = input ? scalarText(input.merchant_name) : null;
  const total = input ? scalarText(input.total_amount) : null;
  const when = input ? scalarText(input.transaction_datetime) : null;
  const highlighted = useMemo(() => new Set(selection?.lineIds ?? []), [selection]);

  function selectFinding(next: FindingSelection) {
    const same = selection?.key === next.key;
    setSelection(same ? null : next);
    if (!same && next.lineIds[0]) scrollToElement(lineAnchor(result.receipt_id, next.lineIds[0]));
  }

  function toggleLine(lineId: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(lineId)) next.delete(lineId);
      else next.add(lineId);
      return next;
    });
  }

  /** From the full findings: open the item and bring it into view. */
  function showLine(lineId: string) {
    setExpanded((current) => new Set(current).add(lineId));
    setSelection({ key: `line-${lineId}`, lineIds: [lineId] });
    scrollToElement(lineAnchor(result.receipt_id, lineId));
  }

  const disposition = dispositionDisplay(result.disposition, result.decided);
  const DispositionIcon = disposition.icon;
  const { metadata, provenance } = result;
  const openCount = result.open_questions.length;

  return (
    <article aria-label={`Receipt ${result.receipt_id}`} className="min-w-0">
      <header className="border-b border-border px-5 pb-4 pt-4">
        {navigation ? (
          <div className="mb-3 flex items-center justify-between gap-2">
            {navigation.onBack ? (
              <Button type="button" variant="ghost" size="sm" className="-ml-2 lg:invisible" onClick={navigation.onBack}>
                <ArrowLeft aria-hidden />
                All receipts
              </Button>
            ) : (
              <span />
            )}
            <div className="flex items-center gap-1">
              <span className="tabular mr-1 text-xs text-subtle-foreground">
                {navigation.position} of {navigation.total}
              </span>
              <Button type="button" variant="outline" size="icon" className="size-8" onClick={navigation.onPrevious} disabled={navigation.position <= 1} aria-label="Previous receipt">
                <ChevronLeft aria-hidden />
              </Button>
              <Button type="button" variant="outline" size="icon" className="size-8" onClick={navigation.onNext} disabled={navigation.position >= navigation.total} aria-label="Next receipt">
                <ChevronRight aria-hidden />
              </Button>
            </div>
          </div>
        ) : null}

        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <h2 className="break-words text-lg font-semibold leading-6 text-foreground">{merchant ?? result.receipt_id}</h2>
            <p className="mt-0.5 text-[13px] text-muted-foreground">
              {merchant ? <span className="font-mono">{result.receipt_id}</span> : null}
              {merchant && when ? " · " : null}
              {when}
              {!input ? <span className="text-subtle-foreground">Merchant and totals are not stored with a run</span> : null}
            </p>
          </div>
          <div className="flex items-center gap-4">
            {total ? (
              <p className="tabular text-right text-lg font-semibold text-foreground">
                {total}
                {currency ? <span className="ml-1 text-sm font-normal text-subtle-foreground">{currency}</span> : null}
              </p>
            ) : null}
            <p
              className={cn(
                "inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-sm font-semibold",
                result.disposition === "flag" && "border-status-flag-border bg-status-flag-soft text-status-flag-text",
                result.disposition === "review" && "border-status-review-border bg-status-review-soft text-status-review-text",
                result.disposition === "pass" && "border-status-pass-border bg-status-pass-soft text-status-pass",
              )}
            >
              <DispositionIcon className="size-4" aria-hidden />
              <span>
                <span className="sr-only">Disposition: </span>
                {disposition.label}
              </span>
            </p>
          </div>
        </div>
        {/* Completeness is separate from the disposition: a flagged receipt can still be undecided. */}
        <p className={cn("mt-2 flex items-center gap-1.5 text-xs", result.decided ? "text-subtle-foreground" : "text-primary")} aria-label="Evaluation completeness">
          {result.decided ? <CircleCheck className="size-3.5" aria-hidden /> : <CircleDashed className="size-3.5" aria-hidden />}
          {result.decided
            ? "Fully evaluated: every rule was decided."
            : `Not fully evaluated: ${openCount ? `${openCount} open question${openCount === 1 ? "" : "s"}, ` : ""}not violations, still need information.${isProvisionalPass(result) ? " Nothing was found against it so far, so this pass is provisional, not a clearance." : ""}`}
        </p>
      </header>

      <div className="space-y-5 px-5 py-5">
        <WhyResult result={result} selected={selection?.key ?? null} onSelect={selectFinding} />

        <section aria-label="Expense items">
          <div className="mb-2 flex items-baseline justify-between gap-2">
            <h3 className="text-[13px] font-semibold text-foreground">Expense items · {result.line_items.length}</h3>
            {selection && selection.lineIds.length === 0 ? (
              <p className="text-xs text-muted-foreground">The selected finding applies to the whole receipt, not to one item.</p>
            ) : (
              <p className="text-xs text-subtle-foreground">Select an item for its classification and evidence.</p>
            )}
          </div>
          <ExpenseItems result={result} inputs={lines} currency={currency} expanded={expanded} onToggle={toggleLine} highlighted={highlighted} />
        </section>

        <div className="space-y-2">
          <Disclosure title="Full policy findings" hint="exceptions, signals, open questions, uncertainty, evidence">
            <PolicyFindings
              result={result}
              focused={focusedFinding}
              onSelectLine={(lineId) => {
                setFocusedFinding(null);
                showLine(lineId);
              }}
            />
          </Disclosure>
          <Disclosure title="Receipt fields and classification provenance">
            <dl className="grid grid-cols-2 gap-x-4 gap-y-2.5 sm:grid-cols-3">
              {input ? INPUT_FIELDS.map(([key, label]) => <Fact key={key} label={label} value={scalarText(input[key])} />) : null}
              {/* The run's setup applies to this receipt only when it names the same adapter its own provenance records. */}
              {setupFacts(run.setup && (run.setup.orchestrator === "langgraph" ? provenance.classification_backend === "langgraph" : run.setup.backend === provenance.classification_backend) ? run.setup : null, provenance.classification_backend, run.setup?.orchestrator === "langgraph" ? null : metadata.model).map(([label, value]) => (
                <Fact key={label} label={label} value={value} />
              ))}
              <Fact label="Translation model" value={metadata.translation_model} />
              <Fact label="Answers" value={metadata.replayed ? "Replayed" : provenance.mode === "deterministic" ? "None (deterministic)" : "Live"} />
              <Fact label="Latency" value={metadata.latency_ms != null ? `${metadata.latency_ms} ms` : null} />
              <Fact
                label="Context"
                value={`${provenance.context_complete ? "Complete" : "Incomplete"} · ${provenance.receipts_in_context} receipt${provenance.receipts_in_context === 1 ? "" : "s"}`}
              />
              <Fact label="Ruleset version" value={metadata.ruleset_version} />
              <Fact label="Ruleset SHA-256" value={provenance.ruleset_sha256} />
              <Fact label="Prompt SHA-256" value={metadata.prompt_sha256} />
            </dl>
            {!input ? (
              <p className="mt-3 text-xs text-subtle-foreground">
                Merchant, totals and line amounts are not stored with a run: the API keeps the core's result, which does not repeat the submitted receipt.
              </p>
            ) : null}
          </Disclosure>
          <Disclosure title="Result JSON" hint="this receipt, exactly as stored" onOpen={() => setJsonOpen(true)}>
            <JsonViewer
              source={runJson}
              select={selectReceipt}
              label={`JSON of receipt ${result.receipt_id}`}
              note={<>This receipt as the run's JSON export gives it, in the brief's shape.</>}
            />
          </Disclosure>
        </div>
      </div>
    </article>
  );
}

