import { ArrowLeft, ChevronDown, ChevronRight, FlaskConical, Loader2, Zap } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useBackends, useCreateEvaluation, useDatasets, useRulesets } from "@/api/queries";
import { PageHeader } from "@/components/layout/AppLayout";
import { ErrorState, LoadingRows } from "@/components/states/states";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/misc";
import { formatInteger } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Section } from "@/pages/dashboard/Section";
import { ClassificationSelector } from "@/pages/validate/ClassificationSelector";
import { choose, ineligibleReason, isEligibleRuleset, LANGGRAPH, modeAvailability, nodeText, orchestratorOf, providerName, replaySetsFor } from "@/pages/validate/config";
import { datasetLabel } from "./DatasetsPage";
import { MODE_TEXT, SELECT } from "./display";
import {
  ADVANCED_FIELDS,
  backendText,
  evaluationBody,
  formErrors,
  INITIAL_FORM,
  newIdempotencyKey,
  resolveBackend,
  resolveReplaySet,
  resolveRuleset,
  resolveTranslation,
  updateForm,
  type EvaluationForm,
  type EvaluationMode,
  type FieldKey,
} from "./form";
import { FieldError, HintOrError, LiveLimits } from "./LiveLimits";

/** The element each field's error is shown beside, for focusing the first one on submit. */
const FIELD_IDS: Record<FieldKey, string> = {
  dataset: "eval-dataset",
  ruleset: "eval-ruleset",
  mode: "eval-mode-replay",
  orchestrator: "eval-orchestrator",
  model: "eval-model",
  replaySet: "eval-replay-set",
  translation: "eval-translation",
  limits: "eval-limits-error",
  spend: "cap-spend",
  calls: "cap-calls",
  receipts: "cap-receipts",
  confirm: "eval-confirm-live",
  uncapped: "eval-uncapped-phrase",
};
const ORDER: FieldKey[] = ["dataset", "mode", "ruleset", "orchestrator", "model", "replaySet", "translation", "limits", "spend", "calls", "receipts", "uncapped", "confirm"];

function Label({ htmlFor, children, hint }: { htmlFor: string; children: ReactNode; hint?: ReactNode }) {
  return (
    <div>
      <label htmlFor={htmlFor} className="block text-xs font-medium text-foreground">
        {children}
      </label>
      {hint ? <p className="text-xs leading-4 text-subtle-foreground">{hint}</p> : null}
    </div>
  );
}

/**
 * A new evaluation: dataset and mode first, with Replay the default. The ruleset, backend and recording set are
 * chosen automatically only when the choice is unambiguous and compatible; anything else is asked for. Nothing is
 * sent until Start, and a live run needs its limits and an explicit paid-call confirmation.
 */
export function NewEvaluationPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const backends = useBackends();
  const rulesets = useRulesets({ limit: 100, offset: 0 });
  const datasets = useDatasets({ limit: 100, offset: 0 });
  const create = useCreateEvaluation();
  const [form, setForm] = useState<EvaluationForm>(() => ({ ...INITIAL_FORM, datasetId: params.get("dataset") }));
  const [touched, setTouched] = useState<Set<FieldKey>>(new Set());
  const [submitted, setSubmitted] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  // One key per form state: a double submit returns the same job, and any change is a new request.
  const idempotencyKey = useMemo(newIdempotencyKey, [form]);

  const discovery = backends.data;
  const versions = rulesets.data?.items ?? [];
  const dataset = datasets.data?.items.find((item) => item.dataset_id === form.datasetId) ?? null;
  const context = { backends: discovery, rulesets: versions, dataset };
  const errors = formErrors(form, context);
  const change = (next: Partial<EvaluationForm>) => setForm((current) => updateForm(current, next));
  const touch = (field: FieldKey) => setTouched((current) => (current.has(field) ? current : new Set(current).add(field)));
  const error = (field: FieldKey) => (submitted || touched.has(field) ? errors[field] : undefined);

  function submit() {
    setSubmitted(true);
    const first = ORDER.find((field) => errors[field]);
    if (first) {
      // The ruleset select sits under Advanced settings unless no ruleset could be chosen automatically.
      const rulesetInAdvanced = Boolean(form.rulesetId) || Boolean(resolveRuleset(form, versions).value);
      if (ADVANCED_FIELDS.includes(first) || (first === "ruleset" && rulesetInAdvanced)) setAdvanced(true);
      requestAnimationFrame(() => document.getElementById(FIELD_IDS[first])?.focus());
      return;
    }
    if (!discovery) return;
    create.mutate(evaluationBody(form, { ...context, backends: discovery }, idempotencyKey), {
      onSuccess: (job) => navigate(`/evaluations/${encodeURIComponent(job.evaluation_id)}`),
    });
  }

  const back = (
    <Button asChild variant="outline" size="sm">
      <Link to="/evaluations">
        <ArrowLeft aria-hidden />
        Evaluations
      </Link>
    </Button>
  );
  if (backends.isPending || rulesets.isPending || datasets.isPending) return <><PageHeader title="New evaluation" actions={back} /><LoadingRows rows={5} label="Loading datasets and deployment capabilities" /></>;
  const failed = backends.error ?? rulesets.error ?? datasets.error;
  if (failed || !discovery) return <><PageHeader title="New evaluation" actions={back} /><ErrorState error={failed} onRetry={() => void Promise.all([backends.refetch(), rulesets.refetch(), datasets.refetch()])} /></>;

  const ruleset = resolveRuleset(form, versions);
  const backend = resolveBackend(form, discovery);
  const replaySet = form.mode === "replay" ? resolveReplaySet(form, backend.value) : null;
  const translation = resolveTranslation(form, discovery);
  const rulesetUnresolved = !form.rulesetId && !ruleset.value;
  const orchestrator = orchestratorOf(form, discovery);
  const translationProvider = discovery.translation.models.find((item) => item.default)?.provider;
  const langgraphNodes = discovery.orchestrators.find((item) => item.id === LANGGRAPH)?.nodes ?? [];
  const chosenModel = backend.value && backend.value.id !== LANGGRAPH ? form.model.trim() || backend.value.default_model : null;
  const live = modeAvailability(discovery, "live");

  const rulesetSelect = (
    <div className="space-y-1">
      <Label htmlFor="eval-ruleset" hint="One exact version. Only versions published for production, unless this is a preview.">
        Ruleset version
      </Label>
      <select
        id="eval-ruleset"
        className={SELECT}
        value={form.rulesetId ?? ""}
        aria-invalid={Boolean(error("ruleset"))}
        onChange={(event) => {
          change({ rulesetId: event.target.value || null });
          touch("ruleset");
        }}
      >
        <option value="">{ruleset.automatic && !form.rulesetId ? `Automatic: ${ruleset.value!.name} v${ruleset.value!.version}` : "Choose a version"}</option>
        {versions.map((item) => (
          <option key={item.ruleset_id} value={item.ruleset_id} disabled={!isEligibleRuleset(item) && !(form.preview && discovery.rulesets.preview_allowed)}>
            {item.name} v{item.version} · {isEligibleRuleset(item) ? `published (production) · ${item.active_rule_count} rules` : ineligibleReason(item)}
          </option>
        ))}
      </select>
      <FieldError id="eval-ruleset-error" message={error("ruleset")} />
    </div>
  );

  return (
    <>
      <PageHeader title="New evaluation" description="Score a classifier and a ruleset version against a labelled dataset, in the background." actions={back} />
      <div className="mx-auto max-w-3xl space-y-4">
        <Section id="new-evaluation" title="What to evaluate">
          <form
            noValidate
            className="space-y-5"
            onSubmit={(event) => {
              event.preventDefault();
              submit();
            }}
          >
            <div className="space-y-1">
              <Label htmlFor="eval-dataset" hint={<>Labelled receipts. <Link to="/evaluations/datasets" className="text-primary hover:underline">Upload or browse datasets</Link></>}>
                Dataset
              </Label>
              <select
                id="eval-dataset"
                className={SELECT}
                value={form.datasetId ?? ""}
                aria-invalid={Boolean(error("dataset"))}
                onChange={(event) => {
                  change({ datasetId: event.target.value || null });
                  touch("dataset");
                }}
              >
                <option value="">Choose a dataset version</option>
                {(datasets.data?.items ?? []).map((item) => (
                  <option key={item.dataset_id} value={item.dataset_id}>
                    {datasetLabel(item)} · {formatInteger(item.stats.receipts)} receipts · {item.context_complete ? "complete context" : "incomplete context"}
                  </option>
                ))}
              </select>
              <FieldError id="eval-dataset-error" message={error("dataset")} />
            </div>

            <div className="space-y-1">
              <p id="eval-mode-label" className="text-xs font-medium">
                Execution mode
              </p>
              <div role="radiogroup" aria-labelledby="eval-mode-label" className="grid grid-cols-2 gap-1 rounded-md bg-muted p-1">
                {(["replay", "live"] as const).map((mode: EvaluationMode) => {
                  const available = mode === "replay" ? modeAvailability(discovery, "replay").available : live.available;
                  return (
                    <button
                      key={mode}
                      id={`eval-mode-${mode}`}
                      type="button"
                      role="radio"
                      aria-checked={form.mode === mode}
                      disabled={!available}
                      onClick={() => change({ mode })}
                      className={cn(
                        "inline-flex h-8 items-center justify-center gap-1 rounded-sm text-[13px] font-medium",
                        form.mode === mode ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
                        "disabled:cursor-not-allowed disabled:opacity-45",
                      )}
                    >
                      {mode === "live" ? <Zap className="size-3.5" aria-hidden /> : null}
                      {mode === "replay" ? "Replay (no cost)" : "Live (paid)"}
                    </button>
                  );
                })}
              </div>
              <p className="text-xs leading-4 text-subtle-foreground">{MODE_TEXT[form.mode]}</p>
              {!live.available ? <p className="text-xs leading-4 text-subtle-foreground">Live unavailable: {live.reason}</p> : null}
              <FieldError id="eval-mode-error" message={error("mode")} />
            </div>

            {rulesetUnresolved ? rulesetSelect : null}

            <dl className="grid gap-x-4 gap-y-1.5 rounded-md border border-border bg-muted/40 px-3 py-2.5 text-[13px] sm:grid-cols-[auto_1fr]" aria-label="Configuration">
              <dt className="text-muted-foreground">Ruleset</dt>
              <dd>
                {ruleset.value ? (
                  <>
                    {ruleset.value.name} v{ruleset.value.version} <span className="text-xs text-subtle-foreground">· {ruleset.note}</span>
                  </>
                ) : (
                  <span className="text-muted-foreground">Not chosen yet</span>
                )}
              </dd>
              <dt className="text-muted-foreground">Orchestrator</dt>
              <dd>
                {orchestrator === LANGGRAPH ? "LangGraph" : "Direct"} <span className="text-xs text-subtle-foreground">· {backend.note}</span>
              </dd>
              {orchestrator === LANGGRAPH ? (
                <>
                  <dt className="text-muted-foreground">Model nodes</dt>
                  <dd className="space-y-0.5">
                    {langgraphNodes.map((node, index) => (
                      <span key={`${node.name}-${index}`} className="block">
                        {nodeText(node)}
                      </span>
                    ))}
                  </dd>
                </>
              ) : (
                <>
                  <dt className="text-muted-foreground">Provider · model</dt>
                  <dd>
                    {backend.value ? `${providerName(backend.value.provider, discovery)} · ${chosenModel ?? "no default model"}` : "—"}{" "}
                    {backend.value ? <span className="text-xs text-subtle-foreground">· {backendText(backend.value, discovery)}</span> : null}
                  </dd>
                </>
              )}
              <dt className="text-muted-foreground">Translation</dt>
              <dd>
                {translation.value ?? "Off"} <span className="text-xs text-subtle-foreground">· {translation.note}</span>
              </dd>
              {replaySet ? (
                <>
                  <dt className="text-muted-foreground">Recording set</dt>
                  <dd>
                    {replaySet.value ?? "—"} <span className="text-xs text-subtle-foreground">· {replaySet.note}</span>
                  </dd>
                </>
              ) : null}
            </dl>

            {form.mode === "live" ? <LiveLimits form={form} change={change} error={error} touch={touch} advanced={advanced} uncappedAllowed={discovery.evaluations.uncapped_allowed} /> : null}

            <div className="rounded-md border border-border">
              <button
                type="button"
                aria-expanded={advanced}
                aria-controls="eval-advanced"
                onClick={() => setAdvanced((value) => !value)}
                className="flex w-full items-center gap-1.5 px-3 py-2 text-left text-[13px] font-medium hover:bg-muted"
              >
                {advanced ? <ChevronDown className="size-4" aria-hidden /> : <ChevronRight className="size-4" aria-hidden />}
                Advanced settings
                <span className="ml-auto text-xs font-normal text-subtle-foreground">Ruleset, orchestrator, provider, model, recording set, translation, label{form.mode === "live" ? ", combined limits" : ""}</span>
              </button>
              {advanced ? (
                <div id="eval-advanced" className="space-y-4 border-t border-border p-3">
                  {!rulesetUnresolved ? rulesetSelect : null}
                  {discovery.rulesets.preview_allowed ? (
                    <label className="flex cursor-pointer gap-2 text-[13px]">
                      <input type="checkbox" checked={form.preview} onChange={(event) => change({ preview: event.target.checked })} className="mt-0.5 size-3.5 accent-primary" />
                      <span>
                        Preview a version not published for production
                        <span className="block text-xs text-subtle-foreground">This deployment allows previews; the run records that it is one.</span>
                      </span>
                    </label>
                  ) : null}
                  <div className="grid gap-3 sm:grid-cols-2">
                    <div className="sm:col-span-2">
                      <ClassificationSelector
                        idPrefix="eval"
                        selectClassName={SELECT}
                        discovery={discovery}
                        mode={form.mode}
                        choice={form}
                        automatic
                        errors={{ orchestrator: error("orchestrator"), model: error("model") }}
                        onChange={(next) => {
                          change({ ...choose(form, next), replaySet: null });
                          touch("orchestrator" in next ? "orchestrator" : "model");
                        }}
                      />
                    </div>
                    {form.mode === "replay" && backend.value ? (
                      <div className="space-y-1">
                        <Label htmlFor="eval-replay-set">Recording set</Label>
                        <select
                          id="eval-replay-set"
                          className={SELECT}
                          value={form.replaySet ?? ""}
                          aria-invalid={Boolean(error("replaySet"))}
                          onChange={(event) => {
                            change({ replaySet: event.target.value || null });
                            touch("replaySet");
                          }}
                        >
                          <option value="">{replaySet?.automatic && !form.replaySet ? `Automatic: ${replaySet.value}` : "Choose a recording set"}</option>
                          {/* Only sets holding this backend's answers: never one it cannot replay. */}
                          {replaySetsFor(backend.value).map((set) => (
                            <option key={set.name} value={set.name}>
                              {set.name}
                              {set.answer_files != null ? ` (${formatInteger(set.answer_files)} answers)` : ""}
                            </option>
                          ))}
                        </select>
                        <FieldError id="eval-replay-set-error" message={error("replaySet")} />
                      </div>
                    ) : null}
                  </div>
                  {discovery.translation.enabled ? (
                    <div className="space-y-1">
                      <Label htmlFor="eval-translation">Translation model</Label>
                      {form.mode === "live" ? (
                        <>
                          <select
                            id="eval-translation"
                            className={SELECT}
                            value={form.translationModel}
                            aria-invalid={Boolean(error("translation"))}
                            aria-describedby={error("translation") ? "eval-translation-error" : "eval-translation-help"}
                            onChange={(event) => {
                              change({ translationModel: event.target.value });
                              touch("translation");
                            }}
                          >
                            <option value="">
                              Configured default: {discovery.translation.default_model}
                              {discovery.translation.models.find((item) => item.default)?.allowed ? "" : " · not allowed for live runs"}
                            </option>
                            {/* Discovered models only: the allowed ones the core accepts as chat models. */}
                            {discovery.translation.models
                              .filter((item) => !item.default)
                              .map((item) => (
                                <option key={item.model} value={item.model} disabled={!item.allowed}>
                                  {item.model}
                                  {item.allowed ? "" : " · not allowed"}
                                </option>
                              ))}
                          </select>
                          <HintOrError
                            id="eval-translation"
                            hint={`Translates non-English lines before they are classified${translationProvider ? `, through ${providerName(translationProvider, discovery)}` : ""}. Chosen apart from the classification model, and recorded with the run.`}
                            message={error("translation")}
                          />
                        </>
                      ) : (
                        <p id="eval-translation" className="text-[13px]">
                          {discovery.translation.default_model} <span className="text-xs text-subtle-foreground">· a replay reads the translations recorded with this model, so it cannot be changed</span>
                        </p>
                      )}
                    </div>
                  ) : null}
                  <div className="space-y-1">
                    <Label htmlFor="eval-label">Label (optional)</Label>
                    <Input id="eval-label" className="h-9 text-[13px]" maxLength={120} value={form.label} onChange={(event) => change({ label: event.target.value })} />
                  </div>
                </div>
              ) : null}
            </div>

            <div className="border-t border-border pt-4">
              {create.isError ? <ErrorState error={create.error} className="mb-3" /> : null}
              <Button type="submit" className="w-full" disabled={create.isPending} aria-busy={create.isPending}>
                {create.isPending ? <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden /> : form.mode === "live" ? <Zap aria-hidden /> : <FlaskConical aria-hidden />}
                {form.mode === "live" ? (form.uncapped ? "Start uncapped live evaluation (paid)" : "Start live evaluation (paid)") : "Start replay evaluation"}
              </Button>
              <p className="mt-1.5 text-center text-xs text-subtle-foreground">
                {form.mode === "live" ? "Calls paid providers" : "Replay · no provider calls and no new spending"} · runs in the background
              </p>
            </div>
          </form>
        </Section>
      </div>
    </>
  );
}
