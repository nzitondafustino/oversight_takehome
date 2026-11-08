/**
 * Orchestrator, provider and classification model, every option from GET /v1/backends. The execution adapter the
 * request names (Jev's Decisions API, or chat completions) follows from the model and is shown, never chosen.
 */
import type { ReactNode } from "react";
import type { Backends, ExecutionMode } from "@/api/types";
import {
  adapterName,
  backendIdFor,
  defaultModel,
  defaultOrchestrator,
  LANGGRAPH,
  modelAvailability,
  modelLabel,
  modelsFor,
  nodeText,
  orchestratorOf,
  protocolName,
  providerName,
  providerOf,
  type ClassificationChoice,
} from "./config";

export interface SelectorErrors {
  orchestrator?: string;
  model?: string;
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
  const nodes = discovery.orchestrators.find((item) => item.id === LANGGRAPH)?.nodes ?? [];
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
        <div className="space-y-1 sm:col-span-2" aria-label="LangGraph model nodes">
          <p className="text-xs font-medium text-foreground">Model nodes, in order</p>
          <ol className="space-y-0.5 text-[13px]">
            {nodes.map((node, index) => (
              <li key={`${node.name}-${index}`}>
                <span className="font-mono">{nodeText(node)}</span>
              </li>
            ))}
          </ol>
          <p className="text-xs leading-4 text-subtle-foreground">From the deployment's configuration. Each node has its own provider and model.</p>
        </div>
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
