/**
 * The new-evaluation form: its state, what it resolves automatically, what the API would certainly refuse, and the
 * POST /v1/evaluations body. Every rule comes from the API's own checks (services/evaluations.py,
 * schemas/evaluations.py) and from GET /v1/backends; the API checks all of them again.
 */
import type { BackendInfo, Backends, DatasetSummary, EvaluationCreate, RulesetSummary } from "@/api/types";
import { isZeroDecimal } from "@/lib/decimal";
import {
  adapterName,
  backendAvailability,
  backendIdFor,
  classificationFor,
  classificationAllowlist,
  INITIAL_CONFIG,
  isEligibleRuleset,
  isNamedFor,
  LANGGRAPH,
  effectiveReflection,
  langgraphNodes,
  langgraphOverride,
  nodeIssues,
  reflectionIssues,
  modeAvailability,
  replaySetsFor,
  translationAllowlist,
  type ClassificationChoice,
} from "@/pages/validate/config";

export type EvaluationMode = "replay" | "live";
export type CapKind = "spend" | "calls" | "receipts";
export const CAP_KINDS: readonly CapKind[] = ["spend", "calls", "receipts"];

export const CAP_COPY: Record<CapKind, { label: string; unit: string; placeholder: string; explain: string }> = {
  spend: { label: "Spending", unit: "USD", placeholder: "0.50", explain: "Stops before the next provider call once this much is spent. The last call can pass it by up to its own cost, and a call with no known cost stops the run." },
  calls: { label: "Provider requests", unit: "requests", placeholder: "200", explain: "Stops before the next provider call once this many have been made." },
  receipts: { label: "Receipts", unit: "receipts", placeholder: "32", explain: "Refuses the run unless the dataset holds at most this many receipts. It never cuts a run short." },
};

/** The phrase a person types to confirm a live run with no limit at all. */
export const UNCAPPED_PHRASE = "uncapped";

export interface EvaluationForm extends ClassificationChoice {
  datasetId: string | null;
  mode: EvaluationMode;
  /** null: chosen automatically when unambiguous. */
  rulesetId: string | null;
  /** Evaluate a version not published for production: only where the deployment allows previews. */
  preview: boolean;
  /** null: chosen automatically when unambiguous. */
  replaySet: string | null;
  /** Live only. Empty: config.yaml's translation model. A replay always reads the recorded translator's answers. */
  translationModel: string;
  label: string;
  /** Limit values as typed, kept while a limit is switched off: dollars stay decimal text, never a float. */
  caps: Record<CapKind, string>;
  enabledCaps: CapKind[];
  uncapped: boolean;
  liveConfirmed: boolean;
  uncappedConfirmation: string;
}

export const INITIAL_FORM: EvaluationForm = {
  datasetId: null,
  mode: "replay",
  rulesetId: null,
  preview: false,
  orchestrator: null,
  provider: null,
  model: "",
  nodeModels: {},
  reflection: {},
  replaySet: null,
  translationModel: "",
  label: "",
  caps: { spend: "", calls: "", receipts: "" },
  enabledCaps: ["spend"],
  uncapped: false,
  liveConfirmed: false,
  uncappedConfirmation: "",
};

/**
 * Applies a change, and withdraws every paid-call confirmation whenever the mode or the limits change, so a
 * confirmation always covers exactly what will be sent.
 */
export function updateForm(form: EvaluationForm, change: Partial<EvaluationForm>): EvaluationForm {
  const next = { ...form, ...change };
  const limitsChanged =
    next.mode !== form.mode || next.uncapped !== form.uncapped || [...next.enabledCaps].sort().join() !== [...form.enabledCaps].sort().join() || CAP_KINDS.some((kind) => next.caps[kind] !== form.caps[kind]);
  return limitsChanged ? { ...next, liveConfirmed: false, uncappedConfirmation: "" } : next;
}

export interface Context {
  backends: Backends | undefined;
  rulesets: readonly RulesetSummary[];
  dataset: DatasetSummary | null;
}

export interface Resolved<T> {
  value: T | null;
  automatic: boolean;
  /** Why it was chosen, or why nothing could be. */
  note: string;
}

/** The newest version published for production, chosen only when every such version is of one ruleset name. */
export function resolveRuleset(form: EvaluationForm, rulesets: readonly RulesetSummary[]): Resolved<RulesetSummary> {
  if (form.rulesetId) return { value: rulesets.find((item) => item.ruleset_id === form.rulesetId) ?? null, automatic: false, note: "Chosen" };
  const published = rulesets.filter(isEligibleRuleset);
  const names = new Set(published.map((item) => item.name));
  if (!published.length) return { value: null, automatic: false, note: "No ruleset version is published for production." };
  if (names.size > 1) return { value: null, automatic: false, note: `${names.size} rulesets are published for production: choose one.` };
  return { value: published[0]!, automatic: true, note: `The newest version of ${published[0]!.name} published for production` };
}

/**
 * The execution adapter (or LangGraph) the run names as its backend. Never chosen apart from the model: it is the one
 * discovery reports for the chosen model, or for the deployment's default model.
 */
export function resolveBackend(form: EvaluationForm, backends: Backends): Resolved<BackendInfo> {
  const resolved = backendIdFor(form, backends);
  const value = backends.backends.find((item) => item.id === resolved.id) ?? null;
  if (!value) return { value: null, automatic: false, note: resolved.reason ?? "Choose how lines are classified." };
  const automatic = form.orchestrator === null && !form.model.trim() && !langgraphOverride(form, backends);
  if (automatic && !backendAvailability(value, form.mode).available) return { value: null, automatic: false, note: "The deployment's default classification cannot run in this mode: choose another." };
  return { value, automatic, note: automatic ? "The deployment's default" : "Chosen" };
}

/** What a person sees for the resolved adapter: its discovered name, or LangGraph. */
export function backendText(backend: BackendInfo, backends: Backends): string {
  return backend.id === LANGGRAPH ? "LangGraph" : adapterName(backend.id, backends);
}

/**
 * The recording set a replay reads. Chosen automatically only when it certainly holds this backend's answers: the
 * set named after the backend, or the only set that has any. The deployment's default set is never assumed.
 */
export function resolveReplaySet(form: EvaluationForm, backend: BackendInfo | null): Resolved<string> {
  if (form.replaySet) return { value: form.replaySet, automatic: false, note: "Chosen" };
  if (!backend) return { value: null, automatic: false, note: "Choose a classification model first." };
  // Answers are recorded per adapter (or for LangGraph), whichever model of it ran.
  const whose = backend.id === LANGGRAPH ? "LangGraph's" : `the ${backend.name ?? backend.id}'s`;
  const sets = replaySetsFor(backend);
  const named = sets.find((set) => isNamedFor(set.name, backend));
  if (named) return { value: named.name, automatic: true, note: `Holds ${whose} recorded answers` };
  if (sets.length === 1) return { value: sets[0]!.name, automatic: true, note: `The only set with ${whose} answers` };
  return { value: null, automatic: false, note: sets.length ? `${sets.length} recording sets hold ${whose} answers: choose one.` : `No recording set holds ${whose} answers.` };
}

export type FieldKey = "dataset" | "ruleset" | "mode" | "orchestrator" | "model" | "reflection" | "replaySet" | "translation" | "limits" | CapKind | "confirm" | "uncapped";
/** The fields under Advanced settings: an error in one opens them. */
export const ADVANCED_FIELDS: readonly FieldKey[] = ["orchestrator", "model", "reflection", "replaySet", "translation"];

/**
 * The translator a run uses, as the API resolves it: none when translation is off; in live mode the chosen model or
 * config.yaml's; in replay always config.yaml's, as recorded translations are keyed by model.
 */
export function resolveTranslation(form: EvaluationForm, backends: Backends): Resolved<string> {
  const translation = backends.translation;
  if (!translation.enabled) return { value: null, automatic: true, note: "Translation is off in this deployment" };
  if (form.mode === "live" && form.translationModel) return { value: form.translationModel, automatic: false, note: "Chosen" };
  return { value: translation.default_model, automatic: true, note: form.mode === "replay" ? "Recorded translations are read: replay uses the configured translator" : "The deployment's configured translator" };
}

/**
 * Every model a live run may call, each with its role, as the API counts them (models_by_role): the classification
 * model or the default, or every LangGraph node's model and its reviewer's when reflection is on, and the translator
 * whenever translation is on, though it translates only non-English lines.
 */
export function liveModels(form: EvaluationForm, backends: Backends): { model: string; role: "classification" | "translation" }[] {
  const models: { model: string; role: "classification" | "translation" }[] = [];
  const add = (model: string | null | undefined, role: "classification" | "translation") => {
    if (model && !models.some((item) => item.model === model && item.role === role)) models.push({ model, role });
  };
  const backend = resolveBackend(form, backends).value;
  if (backend?.id === LANGGRAPH) {
    for (const node of langgraphNodes(form, backends)) add(node.model, "classification");
    const reflection = backend.capabilities.reflection as { reviewer?: { model?: string } | null } | undefined;
    if (effectiveReflection(form, backends).enabled) add(reflection?.reviewer?.model, "classification");
  } else add(form.model.trim() || backend?.default_model, "classification");
  const translator = resolveTranslation(form, backends).value;
  if (translator) add(translator, "translation");
  return models;
}

/** schemas/evaluations.py Budget.max_spend_usd: at most 12 digits, 6 of them decimals. */
const USD = /^\d{1,6}(\.\d{1,6})?$/;
const MAX_PROVIDER_CALLS = 100_000;

function wholeNumber(text: string): number | null {
  return /^\d+$/.test(text.trim()) ? Number(text.trim()) : null;
}

/** One message per field the API would refuse. Only the limits switched on are checked. */
export function formErrors(form: EvaluationForm, context: Context): Partial<Record<FieldKey, string>> {
  const { backends, rulesets, dataset } = context;
  const errors: Partial<Record<FieldKey, string>> = {};
  if (!dataset) errors.dataset = "Choose a dataset version.";
  if (!backends) return { ...errors, mode: "Deployment capabilities have not loaded." };

  const ruleset = resolveRuleset(form, rulesets);
  if (form.preview && !backends.rulesets.preview_allowed) errors.ruleset = "This deployment does not allow previews.";
  else if (!ruleset.value) errors.ruleset = form.rulesetId ? "That ruleset version is no longer listed." : ruleset.note;
  else if (!isEligibleRuleset(ruleset.value) && !(form.preview && backends.rulesets.preview_allowed)) errors.ruleset = "This version is not published for production: tick Preview, where allowed, or choose another.";

  const mode = modeAvailability(backends, form.mode);
  if (!mode.available) errors.mode = mode.reason ?? "This execution mode is not available.";

  const backend = resolveBackend(form, backends);
  // A LangGraph problem is the orchestrator's; a direct one is the chosen model's, as its adapter follows from it.
  const field: FieldKey = form.orchestrator === LANGGRAPH || backend.value?.id === LANGGRAPH ? "orchestrator" : "model";
  if (!backend.value) errors[field] = backend.note;
  else {
    const availability = backendAvailability(backend.value, form.mode);
    if (!availability.available) errors[field] = `${backendText(backend.value, backends)}: ${availability.reason ?? "unavailable"}.`;
    // A model chosen for a LangGraph node is held to the same checks as a direct one: offered, and runnable in this mode.
    const nodes = backend.value.id === LANGGRAPH ? nodeIssues(form, backends, form.mode) : [];
    if (nodes.length) errors.model = nodes.join(" ");
    const reflection = backend.value.id === LANGGRAPH ? reflectionIssues(form, backends) : [];
    if (reflection.length) errors.reflection = reflection.join(" ");
    if (form.mode === "replay" && !errors[field] && !errors.orchestrator) {
      const set = resolveReplaySet(form, backend.value);
      if (!set.value) errors.replaySet = set.note;
    }
  }

  if (form.mode === "live") {
    // Every model the run may call must be allowed for its own role, as the API checks before queuing: a translator is
    // never accepted as a classifier, nor the reverse. Name each blocked one and its role.
    for (const { model, role } of liveModels(form, backends)) {
      const allowed = role === "classification" ? classificationAllowlist(backends) : translationAllowlist(backends);
      if (allowed.includes(model)) continue;
      if (role === "classification") errors.model = `${model} is not allowed for live classification here. Allowed: ${allowed.join(", ") || "none"}.`;
      else
        errors.translation = form.translationModel
          ? `${model} is not allowed for live translation here: choose an allowed translation model.`
          : `The configured translator, ${model}, is not allowed for live translation here, and a live run may call it for any non-English line. Choose an allowed translation model, or have it added to EXPENSE_API_ALLOWED_TRANSLATION_MODELS.`;
    }
    if (form.uncapped && !backends.evaluations.uncapped_allowed) errors.uncapped = "This deployment does not allow live evaluations without a limit: choose one.";
    else if (form.uncapped) {
      if (form.uncappedConfirmation.trim().toLowerCase() !== UNCAPPED_PHRASE) errors.uncapped = `Type “${UNCAPPED_PHRASE}” to confirm a run with no limit.`;
    } else {
      if (!form.enabledCaps.length) errors.limits = "Set at least one limit, or choose No limit.";
      if (form.enabledCaps.includes("spend")) {
        const text = form.caps.spend.trim();
        if (!USD.test(text)) errors.spend = "Enter US dollars, such as 0.50, with at most 6 decimals.";
        else if (isZeroDecimal(text)) errors.spend = "A zero spending limit can make no call: set more than $0.";
      }
      if (form.enabledCaps.includes("calls")) {
        const calls = wholeNumber(form.caps.calls);
        if (calls === null || calls < 1 || calls > MAX_PROVIDER_CALLS) errors.calls = `Enter a whole number from 1 to ${MAX_PROVIDER_CALLS.toLocaleString("en-US")}.`;
      }
      if (form.enabledCaps.includes("receipts")) {
        const receipts = wholeNumber(form.caps.receipts);
        if (receipts === null || receipts < 1) errors.receipts = "Enter a whole number of at least 1.";
        else if (dataset && receipts < dataset.stats.receipts) errors.receipts = `The dataset holds ${dataset.stats.receipts} receipts, more than this limit: the run would be refused.`;
      }
    }
    if (!form.liveConfirmed) errors.confirm = "Confirm that this evaluation calls paid providers.";
  }
  return errors;
}

/** The request, with only what was chosen or resolved. A replay never carries confirmations or limits. */
export function evaluationBody(form: EvaluationForm, context: Context & { backends: Backends }, idempotencyKey?: string): EvaluationCreate {
  const ruleset = resolveRuleset(form, context.rulesets).value!;
  const backend = resolveBackend(form, context.backends).value!;
  const replaySet = form.mode === "replay" ? resolveReplaySet(form, backend).value ?? "" : "";
  const choice = { orchestrator: form.orchestrator, provider: form.provider, model: form.model, nodeModels: form.nodeModels, reflection: form.reflection };
  const classification = classificationFor({ ...INITIAL_CONFIG, ...choice, mode: form.mode, replaySet }, context.backends);
  const body: EvaluationCreate = { dataset_id: form.datasetId!, ruleset_id: ruleset.ruleset_id, execution_mode: form.mode };
  if (form.preview) body.preview = true;
  if (classification) body.classification = classification;
  if (form.mode === "live" && form.translationModel) body.classification = { ...body.classification, translation_model: form.translationModel };
  if (form.label.trim()) body.label = form.label.trim();
  if (idempotencyKey) body.idempotency_key = idempotencyKey;
  if (form.mode === "live") {
    body.confirm_live = form.liveConfirmed;
    if (form.uncapped) body.confirm_uncapped = true;
    else {
      body.budget = {};
      if (form.enabledCaps.includes("spend")) body.budget.max_spend_usd = form.caps.spend.trim();
      if (form.enabledCaps.includes("calls")) body.budget.max_provider_calls = Number(form.caps.calls.trim());
      if (form.enabledCaps.includes("receipts")) body.budget.max_receipts = Number(form.caps.receipts.trim());
    }
  }
  return body;
}

/** A fresh key for one form state. Without crypto.randomUUID, as on a plain-HTTP origin, no key is sent. */
export function newIdempotencyKey(): string | undefined {
  return globalThis.crypto?.randomUUID?.();
}
