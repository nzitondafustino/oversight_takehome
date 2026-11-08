import { AlertOctagon } from "lucide-react";
import type { ReactNode } from "react";
import { Input } from "@/components/ui/misc";
import { cn } from "@/lib/utils";
import { CAP_COPY, CAP_KINDS, UNCAPPED_PHRASE, type CapKind, type EvaluationForm, type FieldKey } from "./form";

export interface FieldProps {
  form: EvaluationForm;
  change: (change: Partial<EvaluationForm>) => void;
  /** The message for a field, once it should be shown: after the field was used, or a submit. */
  error: (field: FieldKey) => string | undefined;
  touch: (field: FieldKey) => void;
}

export function FieldError({ id, message }: { id: string; message: string | undefined }) {
  return message ? (
    <p id={id} className="text-xs text-status-flag-text" role="alert">
      {message}
    </p>
  ) : null;
}

/**
 * A field's hint and its error in one slot, the hint hidden while an error shows. An error that appears when a field
 * loses focus, as a press on the next control starts, then never moves that control from under the pointer.
 */
export function HintOrError({ id, hint, message }: { id: string; hint: ReactNode; message: string | undefined }) {
  return (
    <div className="grid text-xs leading-4">
      <p id={`${id}-help`} className={cn("col-start-1 row-start-1 text-subtle-foreground", message && "invisible")}>
        {hint}
      </p>
      {message ? (
        <p id={`${id}-error`} role="alert" className="col-start-1 row-start-1 text-status-flag-text">
          {message}
        </p>
      ) : null}
    </div>
  );
}

function CapInput({ kind, form, change, error, touch, disabled }: FieldProps & { kind: CapKind; disabled?: boolean }) {
  const copy = CAP_COPY[kind];
  const message = error(kind);
  return (
    <div className="space-y-1">
      <label htmlFor={`cap-${kind}`} className="block text-xs font-medium">
        {copy.label} limit ({copy.unit})
      </label>
      <Input
        id={`cap-${kind}`}
        inputMode={kind === "spend" ? "decimal" : "numeric"}
        placeholder={copy.placeholder}
        className="h-8 font-mono text-[13px]"
        value={form.caps[kind]}
        disabled={disabled}
        aria-invalid={Boolean(message)}
        aria-describedby={message ? `cap-${kind}-error` : `cap-${kind}-help`}
        onChange={(event) => change({ caps: { ...form.caps, [kind]: event.target.value } })}
        onBlur={() => touch(kind)}
      />
      <HintOrError id={`cap-${kind}`} hint={copy.explain} message={message} />
    </div>
  );
}

function Choice({ checked, onSelect, children, name, disabled }: { checked: boolean; onSelect: () => void; children: ReactNode; name: string; disabled?: boolean }) {
  return (
    <label className={cn("flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-[13px]", checked ? "border-primary bg-primary-soft" : "border-border hover:bg-muted", disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer")}>
      <input type="radio" name={name} checked={checked} disabled={disabled} onChange={onSelect} className="size-3.5 accent-primary" />
      {children}
    </label>
  );
}

/**
 * The limits of a live run. The simple view sets one limit, or none; Advanced settings combine several. Values
 * typed for a limit switched off are kept, and only limits switched on are checked and sent.
 */
export function LiveLimits({ advanced, uncappedAllowed, ...props }: FieldProps & { advanced: boolean; uncappedAllowed: boolean }) {
  const { form, change, error, touch } = props;
  const single = !form.uncapped && form.enabledCaps.length === 1 ? form.enabledCaps[0] : null;

  return (
    <fieldset className="space-y-3 rounded-md border border-status-review-border bg-status-review-soft p-3" aria-describedby="limits-help">
      <legend className="px-1 text-xs font-semibold text-status-review-text">Paid provider calls</legend>
      <p id="limits-help" className="text-xs leading-4 text-foreground/85">
        Every receipt's lines are sent to the models, and each call costs money. Limits stop the run; they are checked before each call.
      </p>

      {advanced ? (
        <div className="space-y-2" role="group" aria-label="Limits">
          <p className="text-xs font-medium">Limits (combine any)</p>
          {CAP_KINDS.map((kind) => {
            const on = !form.uncapped && form.enabledCaps.includes(kind);
            return (
              <div key={kind} className="space-y-1.5 rounded-md border border-border bg-card p-2">
                <label className="flex cursor-pointer items-center gap-2 text-[13px] font-medium">
                  <input
                    type="checkbox"
                    checked={on}
                    disabled={form.uncapped}
                    onChange={(event) => {
                      change({ enabledCaps: event.target.checked ? [...form.enabledCaps, kind] : form.enabledCaps.filter((item) => item !== kind) });
                      touch("limits");
                    }}
                    className="size-3.5 accent-primary"
                  />
                  Limit {CAP_COPY[kind].label.toLowerCase()}
                </label>
                {on ? <CapInput {...props} kind={kind} /> : null}
              </div>
            );
          })}
          <label className={cn("flex items-center gap-2 text-[13px] font-medium", uncappedAllowed ? "cursor-pointer" : "cursor-not-allowed opacity-60")}>
            <input type="checkbox" checked={form.uncapped} disabled={!uncappedAllowed && !form.uncapped} onChange={(event) => change({ uncapped: event.target.checked })} className="size-3.5 accent-primary" />
            No limit (uncapped){uncappedAllowed ? "" : " · not allowed in this deployment"}
          </label>
        </div>
      ) : (
        <div className="space-y-2">
          <p id="limit-kind-label" className="text-xs font-medium">
            Limit by
          </p>
          <div role="radiogroup" aria-labelledby="limit-kind-label" className="grid gap-1.5 sm:grid-cols-2">
            {CAP_KINDS.map((kind) => (
              <Choice key={kind} name="limit-kind" checked={single === kind} onSelect={() => change({ uncapped: false, enabledCaps: [kind] })}>
                {CAP_COPY[kind].label}
              </Choice>
            ))}
            <Choice name="limit-kind" checked={form.uncapped} disabled={!uncappedAllowed && !form.uncapped} onSelect={() => change({ uncapped: true })}>
              No limit{uncappedAllowed ? "" : " (not allowed here)"}
            </Choice>
          </div>
          {!form.uncapped && form.enabledCaps.length > 1 ? (
            <p className="text-xs text-foreground/85">
              {form.enabledCaps.length} limits are combined: {form.enabledCaps.map((kind) => CAP_COPY[kind].label.toLowerCase()).join(", ")}. Change them under Advanced settings, or pick one here.
            </p>
          ) : null}
          {single ? <CapInput {...props} kind={single} /> : null}
          {!form.uncapped && form.enabledCaps.length > 1
            ? form.enabledCaps.map((kind) => <FieldError key={kind} id={`cap-${kind}-error`} message={error(kind)} />)
            : null}
        </div>
      )}
      <FieldError id="eval-limits-error" message={error("limits")} />

      {form.uncapped && uncappedAllowed ? (
        <div className="space-y-1.5 rounded-md border border-status-flag-border bg-status-flag-soft p-2.5">
          <p className="flex gap-1.5 text-xs font-semibold text-status-flag-text">
            <AlertOctagon className="size-3.5 shrink-0" aria-hidden />
            No spending, request or receipt limit
          </p>
          <p className="text-xs leading-4">
            The run calls the models for every receipt in the dataset, and nothing stops it on cost. Its spend is reported as it runs, and Cancel stops it before its next call.
          </p>
          <label htmlFor="eval-uncapped-phrase" className="block text-xs font-medium">
            Type “{UNCAPPED_PHRASE}” to confirm
          </label>
          <Input
            id="eval-uncapped-phrase"
            className="h-8 max-w-48 font-mono text-[13px]"
            autoComplete="off"
            value={form.uncappedConfirmation}
            aria-invalid={Boolean(error("uncapped"))}
            aria-describedby={error("uncapped") ? "eval-uncapped-error" : "eval-uncapped-help"}
            onChange={(event) => change({ uncappedConfirmation: event.target.value })}
            onBlur={() => touch("uncapped")}
          />
          <HintOrError id="eval-uncapped" hint="Typed, so a run with no limit is never started by a stray click." message={error("uncapped")} />
        </div>
      ) : null}

      <label className="flex cursor-pointer gap-2.5 text-[13px]">
        <input
          id="eval-confirm-live"
          type="checkbox"
          checked={form.liveConfirmed}
          onChange={(event) => {
            change({ liveConfirmed: event.target.checked });
            touch("confirm");
          }}
          className="mt-0.5 size-3.5 accent-primary"
        />
        <span className="font-medium text-status-review-text">I understand this evaluation calls paid providers{form.uncapped ? ", with no limit" : ""}</span>
      </label>
      <p className="text-xs text-subtle-foreground">Changing the mode or any limit withdraws this confirmation.</p>
      <FieldError id="eval-confirm-error" message={error("confirm")} />
    </fieldset>
  );
}
