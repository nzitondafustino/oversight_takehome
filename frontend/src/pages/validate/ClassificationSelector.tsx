/**
 * Orchestrator, provider and classification model, every option from GET /v1/backends. The execution adapter the
 * request names (Jev's Decisions API, or chat completions) follows from the model and is shown, never chosen.
 * LangGraph keeps its configured nodes, order, routing and reflection; only each node's model can be chosen.
 */
import type { ReactNode } from "react";
import type { Backends, ExecutionMode } from "@/api/types";
import { Input } from "@/components/ui/misc";
import {
  adapterName,
  backendIdFor,
  canRevise,
  defaultModel,
  defaultOrchestrator,
  effectiveReflection,
  LANGGRAPH,
  langgraphNodes,
  langgraphReflection,
  modelAvailability,
  modelLabel,
  modelsFor,
  type LangGraphNode,
  orchestratorOf,
  reflectionIssues,
  protocolName,
  providerName,
  providerOf,
  type ClassificationChoice,
  type ReflectionChoice,
} from "./config";

/** One node model option: the model, then the provider and adapter it runs through. */
function nodeModelLabel(item: { model: string; provider: string | null; protocol: string | null }, discovery: Backends): string {
  return `${item.model} · ${providerName(item.provider, discovery)} · ${protocolName(item.protocol)}`;
}

function title(name: string): string {
  return name.charAt(0).toUpperCase() + name.slice(1);
}

function NodeModels({
  idPrefix,
  selectClassName,
  discovery,
  mode,
  choice,
  onChange,
  error,
  reflectionError,
}: {
  idPrefix: string;
  selectClassName: string;
  discovery: Backends;
  mode: ExecutionMode;
  choice: ClassificationChoice;
  onChange: (change: Partial<ClassificationChoice>) => void;
  error?: string;
  reflectionError?: string;
}) {
  const nodes = langgraphNodes(choice, discovery);
  const reflection = effectiveReflection(choice, discovery);
  const reviewer = langgraphReflection(discovery).reviewer;
  const revisers = nodes.filter((node) => canRevise(node, discovery, reflection.structured));
  const changed = nodes.filter((node) => node.overridden);
  const choose = (node: LangGraphNode, model: string) => onChange({ nodeModels: { ...choice.nodeModels, [node.name]: model } });

  return (
    <fieldset className="space-y-2 sm:col-span-2" aria-describedby={error ? `${idPrefix}-nodes-error` : undefined}>
      <legend className="text-xs font-medium text-foreground">Model nodes, in order</legend>
      <div className="grid gap-3 sm:grid-cols-2">
        {nodes.map((node, index) => {
          const id = `${idPrefix}-node-${node.name}`;
          const configured = node.configured;
          const configuredInfo = discovery.classification_models.find((item) => item.model === configured.model);
          // The configured model is held to the same check as every other: its adapter must run in this mode.
          const configuredAvailability = configuredInfo ? modelAvailability(configuredInfo, discovery, mode) : { available: true };
          const revises = reflection.enabled ? (canRevise(node, discovery, reflection.structured) ? " · Revises with the reviewer's feedback" : " · Cannot revise: another node revises its answers") : "";
          return (
            <Labelled
              key={node.name}
              id={id}
              label={`${index + 1}. ${title(node.name)} model`}
              hint={`${providerName(node.provider, discovery)} · ${protocolName(node.protocol)}, from the model${revises}`}
            >
              <select
                id={id}
                className={`${selectClassName} font-mono`}
                value={node.overridden ? node.model ?? "" : ""}
                aria-invalid={Boolean(error) && node.overridden}
                aria-describedby={`${id}-help`}
                onChange={(event) => choose(node, event.target.value)}
              >
                <option value="" disabled={!configuredAvailability.available}>
                  Configured: {nodeModelLabel({ model: configured.model ?? "no model", provider: configured.provider, protocol: configured.protocol }, discovery)}
                  {configuredAvailability.available ? "" : ` · ${configuredAvailability.reason}`}
                </option>
                {discovery.classification_models
                  .filter((item) => item.model !== configured.model)
                  .map((item) => {
                    const availability = modelAvailability(item, discovery, mode);
                    return (
                      <option key={item.model} value={item.model} disabled={!availability.available}>
                        {nodeModelLabel(item, discovery)}
                        {availability.available ? "" : ` · ${availability.reason}`}
                      </option>
                    );
                  })}
              </select>
            </Labelled>
          );
        })}
      </div>
      <p className="text-xs leading-4 text-subtle-foreground">
        Routing, fallback and the order stay as the deployment configures them, and a change here applies to this run only.{" "}
        {reflection.enabled
          ? `Reflection is on: ${reviewer?.model ?? "the configured reviewer"} reviews (${protocolName(reviewer?.protocol)}).${revisers.length ? "" : " No node's adapter can revise, so an objection sends the line on to the next node."}`
          : "Reflection is off."}
      </p>
      <ReflectionSettings idPrefix={idPrefix} discovery={discovery} choice={choice} onChange={onChange} error={reflectionError} />
      {mode === "replay" && changed.length ? (
        <p className="text-xs leading-4 text-status-review-text" role="note">
          Recorded answers are kept by model and prompt, so this replay reads only {changed.map((node) => node.model).join(" and ")}'s answers for{" "}
          {changed.length === 1 ? `the ${changed[0]!.name} node` : "these nodes"}. A node with no recorded answer for a line counts as a failed call: the line goes on to the next node, and its audit says so. A line no node has an answer for refuses the run as missing recordings. Nothing is called live.
        </p>
      ) : null}
      {error ? (
        <p id={`${idPrefix}-nodes-error`} role="alert" className="text-xs leading-4 text-destructive">
          {error}
        </p>
      ) : null}
    </fieldset>
  );
}

export interface SelectorErrors {
  orchestrator?: string;
  model?: string;
  /** The reflection settings' problem. Without one, the selector shows what it finds itself. */
  reflection?: string;
}

/**
 * Reflection for this run: whether each answer is reviewed, how many feedback-guided revisions a line may have, the
 * hard limit on its model calls, and what each revision is told. Limits come from discovery; the API enforces them.
 */
function ReflectionSettings({ idPrefix, discovery, choice, onChange, error }: { idPrefix: string; discovery: Backends; choice: ClassificationChoice; onChange: (change: Partial<ClassificationChoice>) => void; error?: string }) {
  const configured = langgraphReflection(discovery);
  const reflection = effectiveReflection(choice, discovery);
  const chosen = choice.reflection ?? {};
  const set = (change: ReflectionChoice) => onChange({ reflection: { ...chosen, ...change } });
  const problem = error ?? (reflectionIssues(choice, discovery).join(" ") || undefined);
  const reviewer = configured.reviewer;
  return (
    <div className="space-y-2 rounded-md border border-border px-3 py-2.5">
      <label className="flex cursor-pointer gap-2 text-[13px]">
        <input
          id={`${idPrefix}-reflection-enabled`}
          type="checkbox"
          className="mt-0.5 size-3.5 accent-primary"
          checked={reflection.enabled}
          disabled={!reviewer}
          onChange={(event) => set({ enabled: event.target.checked })}
        />
        <span>
          Review each answer, and revise it with the reviewer's feedback
          <span className="block text-xs text-subtle-foreground">
            {reviewer ? `Reviewer: ${reviewer.model ?? "configured"} (${protocolName(reviewer.protocol)}), as the deployment configures it.` : "This deployment configures no reviewer."}{" "}
            {configured.enabled ? "On by default here." : "Off by default here."}
          </span>
        </span>
      </label>
      {reflection.enabled ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <Labelled id={`${idPrefix}-reflection-retries`} label="Revision retries" hint={`0 to ${configured.limits.maxRetries}; 0 revises nothing. Default ${configured.maxRetries}.`}>
            <Input
              id={`${idPrefix}-reflection-retries`}
              inputMode="numeric"
              className="h-9 text-[13px]"
              placeholder={String(configured.maxRetries)}
              value={chosen.maxRetries ?? ""}
              aria-invalid={Boolean(problem)}
              aria-describedby={`${idPrefix}-reflection-retries-help`}
              onChange={(event) => set({ maxRetries: event.target.value })}
            />
          </Labelled>
          <Labelled
            id={`${idPrefix}-reflection-calls`}
            label="Model calls per line"
            hint={`1 to ${configured.limits.maxCalls}: every classification, review, fallback and revision, checked before each call. Default ${configured.maxCalls}.`}
          >
            <Input
              id={`${idPrefix}-reflection-calls`}
              inputMode="numeric"
              className="h-9 text-[13px]"
              placeholder={String(configured.maxCalls)}
              value={chosen.maxCalls ?? ""}
              aria-invalid={Boolean(problem)}
              aria-describedby={`${idPrefix}-reflection-calls-help`}
              onChange={(event) => set({ maxCalls: event.target.value })}
            />
          </Labelled>
          <label className="flex cursor-pointer gap-2 text-[13px] sm:col-span-2">
            <input id={`${idPrefix}-reflection-structured`} type="checkbox" className="mt-0.5 size-3.5 accent-primary" checked={reflection.structured} onChange={(event) => set({ structured: event.target.checked })} />
            <span>
              Structured feedback context
              <span className="block text-xs text-subtle-foreground">
                Each revision is also told the review's verdict, reason and suggested category, the previous confidence, the categories in question and its retry
                number. A revision that fails validation is retried with its error, and only a reviewer's accept counts as verified. It is a new request, so a replay
                needs recordings made with it.
              </span>
            </span>
          </label>
        </div>
      ) : null}
      {problem ? (
        <p id={`${idPrefix}-reflection-error`} role="alert" className="text-xs leading-4 text-destructive">
          {problem}
        </p>
      ) : null}
    </div>
  );
}

function Labelled({ id, label, hint, error, children }: { id: string; label: string; hint?: ReactNode; error?: string; children: ReactNode }) {
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-xs font-medium text-foreground">
        {label}
      </label>
      {children}
      {error ? (
        <p id={`${id}-error`} role="alert" className="text-xs leading-4 text-destructive">
          {error}
        </p>
      ) : hint ? (
        <p id={`${id}-help`} className="text-xs leading-4 text-subtle-foreground">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export function ClassificationSelector({
  idPrefix,
  selectClassName,
  discovery,
  mode,
  choice,
  onChange,
  automatic = false,
  errors = {},
  orchestratorBlocked,
}: {
  idPrefix: string;
  selectClassName: string;
  discovery: Backends;
  mode: ExecutionMode;
  choice: ClassificationChoice;
  onChange: (change: Partial<ClassificationChoice>) => void;
  /** Offer "Automatic: the deployment's default" for the orchestrator: the request then names none. */
  automatic?: boolean;
  errors?: SelectorErrors;
  /** Why an orchestrator cannot be chosen here beyond discovery, such as LangGraph in a live evaluation. */
  orchestratorBlocked?: (id: string) => string | undefined;
}) {
  const orchestrator = orchestratorOf(choice, discovery);
  const provider = providerOf(choice, discovery);
  const fallback = defaultModel(discovery);
  const offersDefault = Boolean(fallback && fallback.provider === provider);
  const models = modelsFor(provider, discovery).filter((item) => !(offersDefault && item.model === fallback!.model));
  const adapter = backendIdFor(choice, discovery).id;
  // In live mode the request leaves the default unnamed, so the allowlist is the API's to check; its adapter is not.
  const defaultAvailability = offersDefault ? modelAvailability({ ...fallback!, allowed: true }, discovery, mode) : null;
  const chosenModel = discovery.classification_models.find((item) => item.model === (choice.model.trim() || (offersDefault ? fallback!.model : "")));
  const defaultName = discovery.orchestrators.find((item) => item.id === defaultOrchestrator(discovery))?.name ?? defaultOrchestrator(discovery);

  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <Labelled id={`${idPrefix}-orchestrator`} label="Orchestrator" hint="Direct sends each line to one model. LangGraph runs its configured model nodes in order." error={errors.orchestrator}>
        <select
          id={`${idPrefix}-orchestrator`}
          className={selectClassName}
          value={automatic ? choice.orchestrator ?? "" : orchestrator}
          aria-invalid={Boolean(errors.orchestrator)}
          onChange={(event) => onChange({ orchestrator: event.target.value || null })}
        >
          {automatic ? <option value="">Automatic: the deployment's default ({defaultName})</option> : null}
          {discovery.orchestrators.map((item) => {
            const blocked = !item.installed ? "Not installed in this deployment" : !item.enabled ? "Not enabled in this deployment" : orchestratorBlocked?.(item.id);
            return (
              <option key={item.id} value={item.id} disabled={Boolean(blocked)}>
                {item.name}
                {item.default ? " (default)" : ""}
                {blocked ? ` · ${blocked}` : ""}
              </option>
            );
          })}
        </select>
      </Labelled>

      {orchestrator === LANGGRAPH ? (
        <NodeModels idPrefix={idPrefix} selectClassName={selectClassName} discovery={discovery} mode={mode} choice={choice} onChange={onChange} error={errors.model} reflectionError={errors.reflection} />
      ) : (
        <>
          <Labelled id={`${idPrefix}-provider`} label="Provider" hint="The API the model is called through.">
            <select id={`${idPrefix}-provider`} className={selectClassName} value={provider ?? ""} onChange={(event) => onChange({ provider: event.target.value || null })}>
              {provider === null ? <option value="">Choose a provider</option> : null}
              {discovery.providers.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </Labelled>

          <div className="sm:col-span-2">
            <Labelled
              id={`${idPrefix}-model`}
              label="Classification model"
              error={errors.model}
              hint={chosenModel ? `Runs through ${providerName(chosenModel.provider, discovery)}'s ${protocolName(chosenModel.protocol)}, chosen from the model.` : undefined}
            >
              <select
                id={`${idPrefix}-model`}
                className={`${selectClassName} font-mono`}
                value={choice.model}
                aria-invalid={Boolean(errors.model)}
                aria-describedby={errors.model ? `${idPrefix}-model-error` : `${idPrefix}-model-help`}
                onChange={(event) => onChange({ model: event.target.value })}
              >
                {/* The default is held to the same check as every other model: its adapter must run in this mode. */}
                {offersDefault ? (
                  <option value="" disabled={!defaultAvailability!.available}>
                    Default: {modelLabel(fallback!, discovery)}
                    {defaultAvailability!.available ? "" : ` · ${defaultAvailability!.reason}`}
                  </option>
                ) : (
                  <option value="">Choose a model</option>
                )}
                {models.map((item) => {
                  const availability = modelAvailability(item, discovery, mode);
                  return (
                    <option key={item.model} value={item.model} disabled={!availability.available}>
                      {modelLabel(item, discovery)}
                      {availability.available ? "" : ` · ${availability.reason}`}
                    </option>
                  );
                })}
              </select>
            </Labelled>
            {adapter && !chosenModel ? <p className="mt-1 text-xs text-subtle-foreground">Adapter: {adapterName(adapter, discovery)}</p> : null}
          </div>
        </>
      )}
    </div>
  );
}
