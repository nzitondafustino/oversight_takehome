/**
 * Validation configuration rules, derived only from what the API reports: the ruleset's lifecycle state
 * (GET /v1/rulesets) and the deployment's backends and modes (GET /v1/backends). The API checks all of this
 * again; these rules only stop a request it would certainly refuse, and say why.
 */
import type { BackendInfo, Backends, ClassificationModelInfo, ClassificationSetup, ExecutionMode, OrchestratorNode, RulesetSummary, ValidationClassification } from "@/api/types";
import type { JsonObject, JsonValue } from "@/lib/losslessJson";
import { toJsonText } from "@/lib/losslessJson";

/**
 * What a person chooses for classification: how lines reach a model (the orchestrator), the API provider, and the
 * model. The execution adapter a request names as its `backend` is never chosen: it follows from these (backendIdFor).
 */
export interface ClassificationChoice {
  /** "direct" or "langgraph". null: the deployment's default. */
  orchestrator: string | null;
  /** null: the chosen model's provider, else the default model's, else the only one. */
  provider: string | null;
  /** Direct only. Empty: the deployment's default model, from its config.yaml. */
  model: string;
  /**
   * LangGraph only, for this run: a classification model by node name. A node left out, or empty, keeps the model
   * config.yaml gives it. Its adapter follows from the model, as for direct classification.
   */
  nodeModels?: Record<string, string>;
  /** LangGraph only, for this run: reflection changes. A field left undefined keeps the deployment's value. */
  reflection?: ReflectionChoice;
}

export interface ReflectionChoice {
  enabled?: boolean;
  /** Typed text, checked against the deployment's maximum before it is sent as a whole number. */
  maxRetries?: string;
  maxCalls?: string;
  structured?: boolean;
}

export interface ValidationConfig extends ClassificationChoice {
  rulesetId: string | null;
  mode: ExecutionMode;
  /** Empty: the deployment's default replay set. */
  replaySet: string;
  contextComplete: boolean;
  liveConfirmed: boolean;
}

export const INITIAL_CONFIG: ValidationConfig = {
  rulesetId: null,
  mode: "replay",
  orchestrator: null,
  provider: null,
  model: "",
  nodeModels: {},
  replaySet: "",
  // On unless the user says receipts a cross-receipt rule could need are missing from the batch.
  contextComplete: true,
  liveConfirmed: false,
};

// --- Rulesets ---

/** POST /v1/validate (without preview) accepts only a version published for production (check_publication). */
export function isEligibleRuleset(ruleset: RulesetSummary): boolean {
  return ruleset.status === "published" && ruleset.publication_mode === "production";
}

export function ineligibleReason(ruleset: RulesetSummary): string {
  if (ruleset.status === "published") return `Published for ${ruleset.publication_mode ?? "an unknown mode"}, not production`;
  return `${ruleset.status.replace("_", " ")}: not published`;
}

/** The newest eligible version: the list is newest first. */
export function defaultRuleset(rulesets: readonly RulesetSummary[]): RulesetSummary | undefined {
  return rulesets.find(isEligibleRuleset);
}

// --- Modes and backends ---

export interface Availability {
  available: boolean;
  reason?: string;
}

export const MODE_COPY: Record<ExecutionMode, { label: string; description: string }> = {
  replay: { label: "Replay", description: "Uses recorded model responses. No provider is called and nothing new is spent. A receipt with no recorded answer is refused, never answered live." },
  live: { label: "Live", description: "Calls the classification providers now. It may incur provider charges, and the deployment must allow it." },
  deterministic: {
    label: "Deterministic",
    description: "No classifier runs, so no line gets a category. Rules that need a category stay undecided, and receipts come back for review.",
  },
};

export function modeAvailability(backends: Backends, mode: ExecutionMode): Availability {
  const flag = backends.execution_modes[mode];
  if (flag === true) return { available: true };
  if (mode === "live") return { available: false, reason: "This deployment does not allow live inference (EXPENSE_API_ALLOW_LIVE is off)." };
  if (mode === "replay") return { available: false, reason: "This deployment has no replay sets." };
  return { available: false, reason: "Not supported by this deployment." };
}

/** Whether one backend can run in one mode. Deterministic mode uses no backend. */
export function backendAvailability(backend: BackendInfo, mode: ExecutionMode): Availability {
  if (!backend.installed) return { available: false, reason: "Not installed in this deployment" };
  if (!backend.enabled) return { available: false, reason: "Not enabled in this deployment" };
  if (mode === "replay" && !backend.replay.supported) return { available: false, reason: "No replay support" };
  if (mode === "replay" && !backend.replay.sets.some((set) => set.present)) return { available: false, reason: "No recorded answers" };
  if (mode === "live" && !backend.live.permitted) return { available: false, reason: "Live use not permitted" };
  if (mode === "live" && !backend.live.available) return { available: false, reason: "No provider credential configured" };
  return { available: backend.available };
}

// --- Orchestrator, provider, model, and the adapter they imply ---

export const DIRECT = "direct";
export const LANGGRAPH = "langgraph";

export function defaultOrchestrator(backends: Backends): string {
  return backends.default_backend === LANGGRAPH ? LANGGRAPH : DIRECT;
}

export function orchestratorOf(choice: ClassificationChoice, backends: Backends): string {
  return choice.orchestrator ?? defaultOrchestrator(backends);
}

/** config.yaml's direct classification model, as discovery reports it. */
export function defaultModel(backends: Backends): ClassificationModelInfo | undefined {
  return backends.classification_models.find((item) => item.default);
}

export function providerOf(choice: ClassificationChoice, backends: Backends): string | null {
  if (choice.provider) return choice.provider;
  const model = backends.classification_models.find((item) => item.model === choice.model) ?? defaultModel(backends);
  return model?.provider ?? (backends.providers.length === 1 ? backends.providers[0]!.id : null);
}

/** The classification models discovery offers through one provider, each with the adapter its ID needs. */
export function modelsFor(provider: string | null, backends: Backends): ClassificationModelInfo[] {
  return backends.classification_models.filter((item) => item.provider === provider);
}

/**
 * The execution adapter (a registered backend) or orchestrator a request names for this choice, or why there is none.
 * Direct classification takes the adapter discovery reports for the chosen model, never one picked apart from it.
 */
export function backendIdFor(choice: ClassificationChoice, backends: Backends): { id: string | null; reason?: string } {
  if (orchestratorOf(choice, backends) === LANGGRAPH) return { id: LANGGRAPH };
  const model = choice.model.trim();
  if (model) {
    const info = backends.classification_models.find((item) => item.model === model);
    return info ? { id: info.backend } : { id: null, reason: `${model} is not a classification model this deployment offers.` };
  }
  const fallback = defaultModel(backends);
  if (!fallback) return { id: null, reason: "Choose a classification model: the deployment has no default for direct classification." };
  if (providerOf(choice, backends) !== fallback.provider) return { id: null, reason: `Choose a classification model offered through ${providerName(providerOf(choice, backends), backends)}.` };
  return { id: fallback.backend };
}

/** A LangGraph node as this run would use it: its configured name and place, and the model chosen for it, if any. */
export interface LangGraphNode extends OrchestratorNode {
  name: string;
  /** The node as config.yaml configures it, from discovery. */
  configured: OrchestratorNode;
  /** A model other than the configured one was chosen for this node. */
  overridden: boolean;
  /** Discovery's entry for the node's model, if it offers it. */
  info: ClassificationModelInfo | undefined;
}

/** LangGraph's configured nodes, in order, as discovery reports them. */
export function configuredNodes(backends: Backends): OrchestratorNode[] {
  return backends.orchestrators.find((item) => item.id === LANGGRAPH)?.nodes ?? [];
}

/**
 * Each LangGraph node as this choice would run it. A chosen model brings the adapter, provider and protocol discovery
 * reports for it; a node left alone keeps its configured adapter. Order and names are always the configured ones.
 */
export function langgraphNodes(choice: ClassificationChoice, backends: Backends): LangGraphNode[] {
  return configuredNodes(backends).map((configured, index) => {
    const name = configured.name ?? String(index);
    const chosen = choice.nodeModels?.[name]?.trim() ?? "";
    const overridden = Boolean(chosen) && chosen !== configured.model;
    const model = overridden ? chosen : configured.model;
    const info = backends.classification_models.find((item) => item.model === model);
    if (!overridden) return { ...configured, name, configured, overridden, info };
    return { name, model, backend: info?.backend ?? null, adapter: info?.backend ?? null, provider: info?.provider ?? null, protocol: info?.protocol ?? null, configured, overridden, info };
  });
}

export interface LangGraphOverride {
  backends?: { name: string; backend: string; model: string }[];
  reflection?: { enabled?: boolean; max_retries?: number; max_total_calls_per_line?: number; feedback_context?: string };
}

/**
 * schemas/validation.py ClassificationSelection.langgraph for this choice: the whole node list when a node's model
 * changed, each node with its configured name, its adapter and its model, as a given list replaces config.yaml's; and
 * only the reflection fields that differ from the deployment's. Undefined when nothing changed, so the run keeps the
 * deployment's configuration exactly. Routing is never sent.
 */
export function langgraphOverride(choice: ClassificationChoice, backends: Backends): LangGraphOverride | undefined {
  const override: LangGraphOverride = {};
  const nodes = langgraphNodes(choice, backends);
  if (nodes.some((node) => node.overridden)) override.backends = nodes.map((node) => ({ name: node.name, backend: node.adapter ?? node.backend ?? "", model: node.model ?? "" }));
  const configured = langgraphReflection(backends);
  const chosen = effectiveReflection(choice, backends);
  const reflection: NonNullable<LangGraphOverride["reflection"]> = {};
  if (chosen.enabled !== configured.enabled) reflection.enabled = chosen.enabled;
  if (chosen.maxRetries !== configured.maxRetries) reflection.max_retries = chosen.maxRetries;
  if (chosen.maxCalls !== configured.maxCalls) reflection.max_total_calls_per_line = chosen.maxCalls;
  if (chosen.structured !== configured.structured) reflection.feedback_context = chosen.structured ? "structured" : "reviewer_feedback";
  if (Object.keys(reflection).length) override.reflection = reflection;
  return Object.keys(override).length ? override : undefined;
}

export interface ReflectionInfo {
  enabled: boolean;
  reviewer: { model?: string; protocol?: string | null; provider?: string | null } | null;
  maxRetries: number;
  maxCalls: number;
  structured: boolean;
  /** The deployment's maximums (EXPENSE_API_MAX_REFLECTION_*), which the API enforces. */
  limits: { maxRetries: number; maxCalls: number };
  /** The adapters whose reviser takes structured context. */
  structuredRevisers: string[];
}

/** The deployment's LangGraph reflection and its maximums, as discovery reports them. */
export function langgraphReflection(backends: Backends): ReflectionInfo {
  const orchestrator = backends.backends.find((item) => item.id === LANGGRAPH);
  const reflection = orchestrator?.capabilities.reflection as
    | { enabled?: boolean; reviewer?: ReflectionInfo["reviewer"]; max_retries?: number; max_total_calls_per_line?: number; feedback_context?: string; limits?: { max_retries?: number; max_total_calls_per_line?: number }; structured_revisers?: string[] }
    | undefined;
  const maxRetries = reflection?.max_retries ?? 2;
  const maxCalls = reflection?.max_total_calls_per_line ?? 6;
  return {
    enabled: Boolean(reflection?.enabled),
    reviewer: reflection?.reviewer ?? null,
    maxRetries,
    maxCalls,
    structured: reflection?.feedback_context === "structured",
    // A deployment that reports no maximums is held to the core's own bounds.
    limits: { maxRetries: reflection?.limits?.max_retries ?? 10, maxCalls: reflection?.limits?.max_total_calls_per_line ?? 50 },
    structuredRevisers: reflection?.structured_revisers ?? [],
  };
}

function wholeNumber(text: string | undefined): number | null {
  return text !== undefined && /^\d+$/.test(text.trim()) ? Number(text.trim()) : null;
}

/** This run's reflection: each field as chosen, else the deployment's. A number not yet valid keeps the deployment's. */
export function effectiveReflection(choice: ClassificationChoice, backends: Backends): { enabled: boolean; maxRetries: number; maxCalls: number; structured: boolean } {
  const configured = langgraphReflection(backends);
  const chosen = choice.reflection ?? {};
  return {
    enabled: chosen.enabled ?? configured.enabled,
    maxRetries: wholeNumber(chosen.maxRetries) ?? configured.maxRetries,
    maxCalls: wholeNumber(chosen.maxCalls) ?? configured.maxCalls,
    structured: chosen.structured ?? configured.structured,
  };
}

/** What the API would certainly refuse in the reflection settings, as the deployment's maximums say. */
export function reflectionIssues(choice: ClassificationChoice, backends: Backends): string[] {
  const configured = langgraphReflection(backends);
  const chosen = choice.reflection ?? {};
  const enabled = chosen.enabled ?? configured.enabled;
  if (!enabled) return [];
  const issues: string[] = [];
  if (!configured.reviewer) issues.push("Reflection needs a reviewer, and this deployment configures none.");
  const { maxRetries, maxCalls } = configured.limits;
  if (chosen.maxRetries !== undefined && chosen.maxRetries.trim() !== "") {
    const value = wholeNumber(chosen.maxRetries);
    if (value === null || value > maxRetries) issues.push(`Revision retries must be a whole number from 0 to ${maxRetries}.`);
  }
  if (chosen.maxCalls !== undefined && chosen.maxCalls.trim() !== "") {
    const value = wholeNumber(chosen.maxCalls);
    if (value === null || value < 1 || value > maxCalls) issues.push(`Model calls per line must be a whole number from 1 to ${maxCalls}.`);
  }
  // Left untyped, the deployment's own value applies, and the API refuses it if it is over the maximum too.
  if (!chosen.maxRetries?.trim() && configured.maxRetries > maxRetries) issues.push(`The deployment's ${configured.maxRetries} revision retries exceed its maximum of ${maxRetries}: choose fewer.`);
  if (!chosen.maxCalls?.trim() && configured.maxCalls > maxCalls) issues.push(`The deployment's ${configured.maxCalls} calls per line exceed its maximum of ${maxCalls}: choose fewer.`);
  return issues;
}

/**
 * Whether a node's adapter can revise with a reviewer's feedback (the core's REVISERS), as discovery reports it; with
 * structured context, only an adapter whose reviser takes it.
 */
export function canRevise(node: OrchestratorNode, backends: Backends, structured = false): boolean {
  const adapter = node.adapter ?? node.backend;
  if (structured && adapter && !langgraphReflection(backends).structuredRevisers.includes(adapter)) return false;
  return backends.backends.find((item) => item.id === adapter)?.capabilities.revise === true;
}

/**
 * What the API or core would certainly refuse in the chosen node models: a model discovery does not offer, one whose
 * adapter cannot run in this mode, or, live, one outside the classification allowlist. Nodes left alone are checked
 * live too once any node changes, as the whole list is then sent and every model in it checked.
 */
export function nodeIssues(choice: ClassificationChoice, backends: Backends, mode: ExecutionMode): string[] {
  const nodes = langgraphNodes(choice, backends);
  const sent = nodes.some((node) => node.overridden);
  const issues: string[] = [];
  for (const node of nodes) {
    if (!node.overridden && !(sent && mode === "live")) continue;
    if (!node.info) {
      issues.push(`${node.name}: ${node.model} is not a classification model this deployment offers.`);
      continue;
    }
    const availability = modelAvailability(node.info, backends, mode);
    if (!availability.available) issues.push(`${node.name}: ${node.model}: ${availability.reason ?? "unavailable"}.`);
  }
  return issues;
}

export function backendFor(choice: ClassificationChoice, backends: Backends): BackendInfo | undefined {
  const { id } = backendIdFor(choice, backends);
  return backends.backends.find((item) => item.id === id);
}

export function providerName(id: string | null | undefined, backends?: Backends): string {
  if (!id) return "—";
  return backends?.providers.find((item) => item.id === id)?.name ?? PROVIDER_LABELS[id] ?? id;
}

/** The adapter's discovered name, such as "Decisions API adapter". */
export function adapterName(id: string | null | undefined, backends?: Backends): string {
  if (!id) return "—";
  return backends?.backends.find((item) => item.id === id)?.name ?? (PROTOCOL_LABELS[id] ? `${PROTOCOL_LABELS[id]} adapter` : id);
}

// Display only, for records read without discovery: the options themselves always come from GET /v1/backends.
const PROVIDER_LABELS: Record<string, string> = { openrouter: "OpenRouter" };
const PROTOCOL_LABELS: Record<string, string> = { chat_completions: "Chat completions", decisions: "Decisions API" };

export function protocolName(protocol: string | null | undefined): string {
  return protocol ? PROTOCOL_LABELS[protocol] ?? protocol : "—";
}

export function nodeText(node: OrchestratorNode): string {
  return `${node.name ?? "node"}: ${providerName(node.provider)} · ${node.model ?? "no model"} (${protocolName(node.protocol)})`;
}

/**
 * A recorded classification in one line, from the setup the API works out when read: "Direct · OpenRouter ·
 * typesafe/jev-1.14 (Decisions API)", or LangGraph with each node. Falls back to the recorded backend.
 */
export function setupText(setup: ClassificationSetup | undefined, backend: string | null | undefined): string {
  if (!setup) return backend ?? "—";
  if (setup.orchestrator === LANGGRAPH) return `LangGraph · ${(setup.nodes ?? []).map(nodeText).join(" → ") || "no nodes recorded"}`;
  if (setup.orchestrator === null) return "No classifier";
  return `Direct · ${providerName(setup.provider)} · ${setup.model ?? "default model"} (${protocolName(setup.protocol)})`;
}

/** The choice in a few words, for summaries: "OpenRouter · typesafe/jev-1.14", or LangGraph. */
export function choiceText(choice: ClassificationChoice, backends: Backends): string {
  if (orchestratorOf(choice, backends) === LANGGRAPH) {
    const nodes = langgraphNodes(choice, backends);
    return nodes.some((node) => node.overridden) ? `LangGraph · ${nodes.map((node) => `${node.name}: ${node.model ?? "no model"}`).join(" → ")}` : "LangGraph";
  }
  const backend = backendFor(choice, backends);
  return backend ? `${providerName(backend.provider, backends)} · ${choice.model.trim() || defaultModel(backends)?.model || "default model"}` : "—";
}

export function orchestratorName(setup: ClassificationSetup | undefined): string {
  if (!setup) return "—";
  return setup.orchestrator === LANGGRAPH ? "LangGraph" : setup.orchestrator === null ? "None: no classifier ran" : "Direct";
}

/**
 * One recorded classification as labelled facts, for detail views: orchestrator, provider (or each LangGraph node),
 * model and adapter. A record without a setup shows only its recorded backend, never a guessed provider.
 */
export function setupFacts(setup: ClassificationSetup | null | undefined, backend: string | null | undefined, model?: string | null): [string, string][] {
  if (!setup) return [["Adapter (recorded backend)", backend ?? "—"], ["Classification model", model ?? "—"]];
  if (setup.orchestrator === null) return [["Orchestrator", "None: no classifier ran"]];
  if (setup.orchestrator === LANGGRAPH) {
    return [["Orchestrator", "LangGraph"], ["Model nodes", setup.nodes ? setup.nodes.map(nodeText).join(" → ") : "Not recorded"]];
  }
  return [
    ["Orchestrator", "Direct"],
    ["Provider", providerName(setup.provider)],
    ["Classification model", model ?? setup.model ?? "Not recorded"],
    ["Adapter", adapterText(setup, backend)],
  ];
}

/** A recorded run's orchestrator and provider, for lists: "Direct · OpenRouter", or LangGraph. */
export function setupHeadline(setup: ClassificationSetup | undefined, backend: string | null | undefined): string {
  if (!setup) return backend ?? "—";
  if (setup.orchestrator === LANGGRAPH) return "LangGraph";
  if (setup.orchestrator === null) return "No classifier";
  return `Direct · ${providerName(setup.provider)}`;
}

/** A recorded run's execution adapter, with the backend identifier it was recorded under: "Decisions API (jev)". */
/** A recorded run's execution adapter, by its protocol: "Decisions API", whether it recorded decisions or jev. */
export function adapterText(setup: ClassificationSetup | undefined, backend: string | null | undefined): string {
  if (setup?.orchestrator === LANGGRAPH) return "Per node";
  if (!setup?.protocol) return backend ?? "—";
  return protocolName(setup.protocol);
}

/** The adapter's own name for a recorded run, for matching runs across the rename; else the name it recorded. */
export function adapterIdentity(setup: ClassificationSetup | undefined, backend: string | null | undefined): string | null {
  return setup?.adapter ?? backend ?? null;
}

/**
 * A recorded run's classification model(s), from its setup: the model, or each LangGraph node's, never the recorded
 * label, which names adapters by the names of the day ("primary=jev:…" before the rename).
 */
export function modelText(setup: ClassificationSetup | undefined, label: string | null | undefined): string {
  if (!setup) return label ?? "—";
  if (setup.orchestrator === LANGGRAPH) return setup.nodes ? setup.nodes.map((node) => `${node.name ?? "node"}: ${node.model ?? "no model"}`).join(" → ") : label ?? "—";
  if (setup.orchestrator === null) return "—";
  return setup.model ?? label ?? "—";
}

/** A LangGraph orchestration runs its configured order of backends, each with its own model: no single model. */
export function takesSingleModel(backend: BackendInfo): boolean {
  return !Array.isArray(backend.capabilities.order);
}

/**
 * Whether a discovered model can be chosen in this mode: its adapter must be able to run, and a live run may name only
 * an allowed model, as the API refuses any other.
 */
export function modelAvailability(model: ClassificationModelInfo, backends: Backends, mode: ExecutionMode): Availability {
  const adapter = backends.backends.find((item) => item.id === model.backend);
  if (!adapter) return { available: false, reason: "Its adapter is not installed" };
  const availability = backendAvailability(adapter, mode);
  if (!availability.available) return availability;
  if (mode === "live" && !model.allowed) return { available: false, reason: "Not allowed for live use" };
  return { available: true };
}

/** One option's text: the model, then the adapter it needs. The provider is chosen beside it. */
export function modelLabel(model: ClassificationModelInfo, backends: Backends): string {
  return `${model.model} · ${adapterName(model.backend, backends)}`;
}

/** Changes the choice, clearing what the change makes stale: another orchestrator or provider resets the model(s). */
export function choose<T extends ClassificationChoice>(current: T, change: Partial<ClassificationChoice>): T {
  const next = { ...current, ...change };
  if ("orchestrator" in change && change.orchestrator !== current.orchestrator) {
    next.model = "";
    next.nodeModels = {};
    next.reflection = {};
  }
  if ("provider" in change && change.provider !== current.provider) next.model = "";
  return next;
}

/** The models a live request may choose for classification: the deployment's classification allowlist. */
export function classificationAllowlist(backends: Backends): string[] {
  return backends.allowed_classification_models ?? backends.allowed_models;
}

/** The models a live request may choose for translation: the deployment's translation allowlist. */
export function translationAllowlist(backends: Backends): string[] {
  return backends.allowed_translation_models ?? backends.allowed_models;
}

/** Whether a replay set is named for this adapter, by its own name or an earlier one, as deployments name them. */
export function isNamedFor(setName: string, backend: BackendInfo): boolean {
  return setName === backend.id || (backend.legacy_ids ?? []).includes(setName);
}

/** Replay sets in which this backend has recorded answers. Presence, not coverage of these receipts. */
export function replaySetsFor(backend: BackendInfo) {
  return backend.replay.sets.filter((set) => set.present);
}

/** The set named after the backend when it has one, as the deployment ships them; else the first present. */
export function defaultReplaySet(backend: BackendInfo): string {
  const sets = replaySetsFor(backend);
  return (sets.find((set) => isNamedFor(set.name, backend)) ?? sets[0])?.name ?? "";
}

// --- The request ---

export function configIssues(config: ValidationConfig, backends: Backends | undefined, rulesets: readonly RulesetSummary[]): string[] {
  const issues: string[] = [];
  const ruleset = rulesets.find((item) => item.ruleset_id === config.rulesetId);
  if (!ruleset) issues.push("Choose a ruleset version.");
  else if (!isEligibleRuleset(ruleset)) issues.push("The chosen ruleset is not published for production.");
  if (!backends) return [...issues, "Backend discovery has not loaded."];
  const mode = modeAvailability(backends, config.mode);
  if (!mode.available) issues.push(mode.reason ?? "This execution mode is not available.");
  if (config.mode !== "deterministic") {
    const resolved = backendIdFor(config, backends);
    const backend = backends.backends.find((item) => item.id === resolved.id);
    if (!backend) issues.push(resolved.reason ?? "Choose how lines are classified.");
    else {
      const availability = backendAvailability(backend, config.mode);
      if (!availability.available) issues.push(`${backend.kind === "orchestrator" ? "LangGraph" : adapterName(backend.id, backends)}: ${availability.reason ?? "unavailable"}.`);
      // authorize_classification: a live request may name only a model in the deployment's classification allowlist.
      if (backend.id === LANGGRAPH) issues.push(...nodeIssues(config, backends, config.mode), ...reflectionIssues(config, backends));
      const model = backend.id === LANGGRAPH ? "" : config.model.trim();
      const allowed = classificationAllowlist(backends);
      if (config.mode === "live" && model && !allowed.includes(model)) {
        issues.push(
          allowed.length
            ? `Live mode allows only these classification models: ${allowed.join(", ")}.`
            : "Live mode allows no chosen classification model here: leave the model empty to use the default.",
        );
      }
    }
  }
  if (config.mode === "live" && !config.liveConfirmed) issues.push("Confirm that live validation may incur provider charges.");
  return issues;
}

/** schemas/validation.py ClassificationSelection, with only what the user chose. Deterministic mode sends none. */
export function classificationFor(config: ValidationConfig, backends: Backends): ValidationClassification | undefined {
  if (config.mode === "deterministic") return undefined;
  const id = backendIdFor(config, backends).id;
  const backend = backends.backends.find((item) => item.id === id);
  // The adapter the chosen model needs, as discovery reports it: requests and records keep naming it as the backend.
  const classification: ValidationClassification = { backend: id };
  if (backend && takesSingleModel(backend) && config.model.trim()) classification.model = config.model.trim();
  // Only the node list, and only when a node's model changed: routing and reflection stay config.yaml's.
  const langgraph = id === LANGGRAPH ? langgraphOverride(config, backends) : undefined;
  if (langgraph) classification.langgraph = langgraph;
  if (config.mode === "replay" && config.replaySet) classification.replay_set = config.replaySet;
  return classification;
}

/**
 * The POST /v1/validate body (schemas/validation.py ValidationRequest), serialized so that every receipt
 * value, numbers included, is sent exactly as it was read.
 */
export function buildValidationBody(input: {
  rulesetId: string;
  receipts: JsonObject[];
  config: ValidationConfig;
  backends: Backends;
}): string {
  const body: JsonObject = Object.create(null);
  body.ruleset_id = input.rulesetId;
  body.receipts = input.receipts as JsonValue[];
  body.context_complete = input.config.contextComplete;
  body.execution_mode = input.config.mode;
  const classification = classificationFor(input.config, input.backends);
  if (classification) {
    const selection: JsonObject = Object.create(null);
    for (const [key, value] of Object.entries(classification)) if (value != null) selection[key] = value;
    body.classification = selection;
  }
  // Receipt values keep their exact JSON text; the selection's plain values, such as reflection limits, are numbers.
  return toJsonText(body);
}
