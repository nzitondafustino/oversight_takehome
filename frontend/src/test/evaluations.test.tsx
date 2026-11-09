import { exportViewPdf } from "@/lib/exportViewPdf";
import { QueryClientProvider } from "@tanstack/react-query";
import { act, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Providers, routes } from "@/App";
import { createQueryClient, queryKeys, useCancelEvaluation, useEvaluation } from "@/api/queries";
import type {
  Backends,
  DatasetSummary,
  EvaluationComparison,
  EvaluationDetail,
  EvaluationMetrics,
  EvaluationResultPage,
  Page,
} from "@/api/types";
import { evaluationBody, formErrors, INITIAL_FORM, liveModels, resolveTranslation, updateForm, type EvaluationForm } from "@/pages/evaluations/form";
import { SpendingView } from "@/pages/evaluations/Spending";
import { page, rulesetSummary } from "./fixtures";
import backendsJson from "./fixtures/backends.json";
import { setupText } from "@/pages/validate/config";
import compareJson from "./fixtures/evaluation/compare.json";
import datasetsJson from "./fixtures/evaluation/datasets.json";
import datasetDetailJson from "./fixtures/evaluation/dataset-detail.json";
import detailQueuedJson from "./fixtures/evaluation/detail-queued.json";
import detailJson from "./fixtures/evaluation/detail.json";
import provisionalJson from "./fixtures/evaluation/metrics-provisional.json";
import metricsJson from "./fixtures/evaluation/metrics.json";
// Real stored metrics: the live Jev run on the robustness set, rescored with evaluation-metrics/3 through the API.
import rescoredJson from "./fixtures/evaluation/metrics-rescored.json";
import resultsJson from "./fixtures/evaluation/results.json";

/**
 * Real API responses, recorded from a replay evaluation of the 32 labelled receipts against a version compiled,
 * reviewed, approved and published by the backend's own test helpers (no network, no model calls).
 */
const DETAIL = detailJson as unknown as EvaluationDetail;
const QUEUED = detailQueuedJson as unknown as EvaluationDetail;
const METRICS = metricsJson as unknown as EvaluationMetrics;
const PROVISIONAL = provisionalJson as unknown as EvaluationMetrics;
const RESULTS = resultsJson as unknown as EvaluationResultPage;
const COMPARE = compareJson as unknown as EvaluationComparison;
const DATASETS = datasetsJson as unknown as Page<DatasetSummary>;
const DATASET = DATASETS.items[0]!;
const BACKENDS = backendsJson as unknown as Backends;

/** Discovery as an API with live inference allowed, a key and an allowed model would report it. */
const LIVE_BACKENDS: Backends = {
  ...BACKENDS,
  execution_modes: { ...BACKENDS.execution_modes, live: true },
  credential_configured: true,
  allowed_models: ["anthropic/claude-haiku-5.5", "tencent/hy-mt2-30b-a3b", "typesafe/jev-1.14"],
  // Each role's own allowlist: the translator is never a classification model, nor the reverse.
  allowed_classification_models: ["anthropic/claude-haiku-5.5", "typesafe/jev-1.14"],
  allowed_translation_models: ["anthropic/claude-haiku-5.5", "tencent/hy-mt2-30b-a3b"],
  backends: BACKENDS.backends.map((backend) => ({ ...backend, live: { permitted: true, credential_configured: true, available: true } })),
  classification_models: BACKENDS.classification_models.map((item) => ({ ...item, allowed: true })),
  evaluations: { uncapped_allowed: true },
  translation: {
    enabled: true,
    default_model: "tencent/hy-mt2-30b-a3b",
    models: [
      { model: "anthropic/claude-haiku-5.5", default: false, allowed: true },
      { model: "tencent/hy-mt2-30b-a3b", default: true, allowed: true },
    ],
  },
};
const PUBLISHED = rulesetSummary({ ruleset_id: DETAIL.ruleset.ruleset_id });
const DRAFT = rulesetSummary({ ruleset_id: "rs_draft", version: 2, status: "draft", publication_mode: null, approval_mode: null, published_at: null });

interface Call {
  method: string;
  path: string;
  body: string | undefined;
}
type Answer = { status: number; body: unknown };

let calls: Call[];
let answer: (method: string, url: URL) => Answer | Promise<Answer> | undefined;
let backends: Backends;

vi.mock("@/lib/exportViewPdf", () => ({ exportViewPdf: vi.fn(async () => undefined) }));

beforeEach(() => {
  calls = [];
  vi.mocked(exportViewPdf).mockClear();
  backends = BACKENDS;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = new URL(String(input), "http://localhost");
      const method = init.method ?? "GET";
      calls.push({ method, path: `${url.pathname}${url.search}`, body: typeof init.body === "string" ? init.body : undefined });
      let reply = await answer(method, url);
      if (!reply && url.pathname === "/api/v1/backends") reply = { status: 200, body: backends };
      if (!reply && url.pathname === "/api/v1/rulesets") reply = { status: 200, body: page([DRAFT, PUBLISHED]) };
      if (!reply && url.pathname === "/api/v1/evaluations/datasets") reply = { status: 200, body: DATASETS };
      if (!reply && url.pathname === "/api/health") reply = { status: 200, body: { status: "ok", application: "running", database: "ok", version: "0.1.0" } };
      reply ??= { status: 200, body: page([]) };
      return new Response(JSON.stringify(reply.body), { status: reply.status, headers: { "Content-Type": "application/json" } });
    }),
  );
});

afterEach(() => vi.unstubAllGlobals());

function renderAt(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(
    <Providers client={createQueryClient()}>
      <RouterProvider router={router} />
    </Providers>,
  );
  return router;
}

function posts(path: string) {
  return calls.filter((call) => call.method === "POST" && call.path === path);
}

function form(overrides: Partial<EvaluationForm> = {}): EvaluationForm {
  return { ...INITIAL_FORM, datasetId: DATASET.dataset_id, ...overrides };
}

const OTHER = rulesetSummary({ ruleset_id: "rs_other", name: "regional", version: 3 });
const LIVE: Partial<EvaluationForm> = { mode: "live", liveConfirmed: true, enabledCaps: ["spend"], caps: { spend: "0.50", calls: "", receipts: "" } };
const context = (over: Partial<Parameters<typeof formErrors>[1]> = {}) => ({ backends: LIVE_BACKENDS, rulesets: [DRAFT, PUBLISHED], dataset: DATASET, ...over });

describe("evaluation form", () => {
  it("chooses the ruleset, backend and recording set only when the choice is unambiguous and compatible", () => {
    expect(formErrors(form(), context())).toEqual({});
    expect(evaluationBody(form(), context())).toEqual({
      dataset_id: DATASET.dataset_id,
      ruleset_id: PUBLISHED.ruleset_id,
      execution_mode: "replay",
      // The adapter by its own name; the deployment's replay set keeps the adapter's earlier name, openrouter.
      classification: { backend: "chat_completions", replay_set: "openrouter" },
    });
    // Two rulesets published for production: nothing is chosen for the user.
    expect(formErrors(form(), context({ rulesets: [PUBLISHED, OTHER] }))).toEqual({ ruleset: "2 rulesets are published for production: choose one." });
    // A draft is never chosen automatically, and only a preview the deployment allows can use one.
    expect(formErrors(form(), context({ rulesets: [DRAFT] }))).toEqual({ ruleset: "No ruleset version is published for production." });
    expect(formErrors(form({ rulesetId: "rs_draft" }), context()).ruleset).toMatch(/not published for production/);
    expect(formErrors(form({ rulesetId: "rs_draft", preview: true }), context())).toEqual({});
    // A backend whose answers sit in several sets, none named after it, gets no recording set by itself.
    const scattered = { ...LIVE_BACKENDS, backends: LIVE_BACKENDS.backends.map((item) => (item.id === "chat_completions" ? { ...item, replay: { supported: true, sets: [{ name: "a", present: true, answer_files: 3, folder: "." }, { name: "b", present: true, answer_files: 3, folder: "." }] } } : item)) };
    expect(formErrors(form(), context({ backends: scattered }))).toEqual({ replaySet: "2 recording sets hold the Chat completions adapter's answers: choose one." });
  });

  it("takes the adapter from the chosen model, never as a choice of its own, and keeps orchestrator and provider apart", () => {
    // Discovery: Jev is a model through OpenRouter, run by the Decisions API adapter; LangGraph is an orchestrator.
    expect(BACKENDS.providers.map((item) => [item.id, item.adapters])).toEqual([["openrouter", ["chat_completions", "decisions"]]]);
    expect(BACKENDS.backends.map((item) => [item.id, item.legacy_ids])).toEqual([["chat_completions", ["openrouter"]], ["decisions", ["jev"]], ["langgraph", []]]);
    expect(BACKENDS.orchestrators.map((item) => item.id)).toEqual(["direct", "langgraph"]);
    expect(BACKENDS.classification_models.find((item) => item.model === "typesafe/jev-1.14")).toMatchObject({ provider: "openrouter", backend: "decisions", protocol: "decisions" });
    // The request still names the adapter as its backend, as config.yaml, recordings and earlier runs do.
    // Replay sets named for an adapter's earlier name (jev, openrouter) are still found as that adapter's.
    expect(evaluationBody(form({ model: "typesafe/jev-1.14" }), context()).classification).toEqual({ backend: "decisions", model: "typesafe/jev-1.14", replay_set: "jev" });
    expect(evaluationBody(form({ orchestrator: "direct", model: "anthropic/claude-haiku-5.5" }), context()).classification).toEqual({ backend: "chat_completions", model: "anthropic/claude-haiku-5.5", replay_set: "openrouter" });
    // LangGraph sends no single model: its nodes each have their own.
    expect(evaluationBody(form({ orchestrator: "langgraph", model: "typesafe/jev-1.14" }), context()).classification).toEqual({ backend: "langgraph", replay_set: "langgraph" });
    expect(formErrors(form({ model: "vendor/unknown" }), context()).model).toBe("vendor/unknown is not a classification model this deployment offers.");
    // A provider with no default model needs a model chosen through it.
    expect(formErrors(form({ provider: "elsewhere" }), context()).model).toBe("Choose a classification model offered through elsewhere.");
  });

  it("checks only the limits switched on, and needs a limit or a typed confirmation of none", () => {
    expect(formErrors(form(LIVE), context())).toEqual({});
    expect(formErrors(form({ ...LIVE, caps: { spend: "0.50", calls: "not a number", receipts: "1" } }), context())).toEqual({});
    expect(formErrors(form({ ...LIVE, enabledCaps: [] }), context())).toEqual({ limits: "Set at least one limit, or choose No limit." });
    expect(formErrors(form({ ...LIVE, caps: { ...LIVE.caps!, spend: "0.1234567" } }), context()).spend).toMatch(/at most 6 decimals/);
    expect(formErrors(form({ ...LIVE, caps: { ...LIVE.caps!, spend: "0.000" } }), context()).spend).toMatch(/zero spending limit/);
    const combined = { ...LIVE, enabledCaps: ["spend", "calls", "receipts"] as EvaluationForm["enabledCaps"], caps: { spend: "1", calls: "100001", receipts: "31" } };
    expect(formErrors(form(combined), context())).toEqual({ calls: "Enter a whole number from 1 to 100,000.", receipts: "The dataset holds 32 receipts, more than this limit: the run would be refused." });
    expect(formErrors(form({ ...LIVE, uncapped: true }), context())).toEqual({ uncapped: "Type “uncapped” to confirm a run with no limit." });
    expect(formErrors(form({ ...LIVE, uncapped: true, uncappedConfirmation: "uncapped" }), context())).toEqual({});
    const capsOnly = { ...LIVE_BACKENDS, evaluations: { uncapped_allowed: false } };
    expect(formErrors(form({ ...LIVE, uncapped: true, uncappedConfirmation: "uncapped" }), context({ backends: capsOnly }))).toEqual({
      uncapped: "This deployment does not allow live evaluations without a limit: choose one.",
    });
    expect(formErrors(form({ ...LIVE, liveConfirmed: false }), context())).toEqual({ confirm: "Confirm that this evaluation calls paid providers." });
    // LangGraph runs live like a direct run: every node's call is metered and capped, and each node's model must be allowed.
    expect(formErrors(form({ ...LIVE, orchestrator: "langgraph" }), context())).toEqual({});
    expect(evaluationBody(form({ ...LIVE, orchestrator: "langgraph" }), context())).toMatchObject({ execution_mode: "live", classification: { backend: "langgraph" }, budget: { max_spend_usd: "0.50" } });
    expect(liveModels(form({ ...LIVE, orchestrator: "langgraph" }), LIVE_BACKENDS)).toEqual([
      { model: "typesafe/jev-1.14", role: "classification" }, { model: "anthropic/claude-haiku-5.5", role: "classification" }, { model: "tencent/hy-mt2-30b-a3b", role: "translation" }]);
    const jevNotAllowed = { ...LIVE_BACKENDS, allowed_classification_models: ["anthropic/claude-haiku-5.5"] };
    expect(formErrors(form({ ...LIVE, orchestrator: "langgraph" }), context({ backends: jevNotAllowed })).model).toBe("typesafe/jev-1.14 is not allowed for live classification here. Allowed: anthropic/claude-haiku-5.5.");
    expect(formErrors(form(LIVE), context({ backends: BACKENDS })).mode).toMatch(/does not allow live inference/);
  });

  it("runs a LangGraph node on the model chosen for it, checked like any other, and keeps the defaults otherwise", () => {
    const swapped = { orchestrator: "langgraph", nodeModels: { primary: "anthropic/claude-haiku-5.5", fallback: "typesafe/jev-1.14" } };
    // Each node keeps its name and order and takes its model's adapter; routing and reflection are never sent.
    expect(evaluationBody(form(swapped), context()).classification).toEqual({
      backend: "langgraph",
      replay_set: "langgraph",
      langgraph: { backends: [{ name: "primary", backend: "chat_completions", model: "anthropic/claude-haiku-5.5" }, { name: "fallback", backend: "decisions", model: "typesafe/jev-1.14" }] },
    });
    // Only one node changed: the other is sent as configured.
    expect(evaluationBody(form({ orchestrator: "langgraph", nodeModels: { fallback: "typesafe/jev-1.14" } }), context()).classification?.langgraph).toEqual({
      backends: [{ name: "primary", backend: "decisions", model: "typesafe/jev-1.14" }, { name: "fallback", backend: "decisions", model: "typesafe/jev-1.14" }],
    });
    // Choosing a node's configured model, or nothing, is no override at all.
    expect(evaluationBody(form({ orchestrator: "langgraph", nodeModels: { primary: "typesafe/jev-1.14", fallback: "" } }), context()).classification).toEqual({ backend: "langgraph", replay_set: "langgraph" });
    // Live counts the models the run would call: the chosen ones, never the configured ones they replace.
    expect(liveModels(form({ ...LIVE, orchestrator: "langgraph", nodeModels: { primary: "anthropic/claude-haiku-5.5" } }), LIVE_BACKENDS)).toEqual([
      { model: "anthropic/claude-haiku-5.5", role: "classification" }, { model: "tencent/hy-mt2-30b-a3b", role: "translation" }]);
    const haikuNotAllowed = { ...LIVE_BACKENDS, allowed_classification_models: ["typesafe/jev-1.14"], classification_models: LIVE_BACKENDS.classification_models.map((item) => ({ ...item, allowed: item.model === "typesafe/jev-1.14" })) };
    expect(formErrors(form({ ...LIVE, orchestrator: "langgraph", nodeModels: { fallback: "typesafe/jev-1.14" } }), context({ backends: haikuNotAllowed }))).toEqual({});
    expect(formErrors(form({ ...LIVE, orchestrator: "langgraph", nodeModels: { primary: "anthropic/claude-haiku-5.5" } }), context({ backends: haikuNotAllowed })).model).toMatch(/anthropic\/claude-haiku-5.5 is not allowed for live classification/);
    // A model discovery does not offer is refused before anything is sent, never given another adapter.
    expect(formErrors(form({ orchestrator: "langgraph", nodeModels: { primary: "meta/unknown-1" } }), context()).model).toBe("primary: meta/unknown-1 is not a classification model this deployment offers.");
  });

  it("sends reflection settings changed for one evaluation, checked against the deployment's maximums", () => {
    const limited = { ...LIVE_BACKENDS, backends: LIVE_BACKENDS.backends.map((backend) => backend.id === "langgraph" ? { ...backend, capabilities: { ...backend.capabilities, reflection: { ...(backend.capabilities.reflection as object), limits: { max_retries: 3, max_total_calls_per_line: 12 }, structured_revisers: ["chat_completions"] } } } : backend) };
    const ctx = context({ backends: limited });
    const langgraph = (reflection: EvaluationForm["reflection"]) => form({ orchestrator: "langgraph", reflection });
    expect(evaluationBody(langgraph({ enabled: true, maxRetries: "3", maxCalls: "12", structured: true }), ctx).classification?.langgraph).toEqual({
      reflection: { enabled: true, max_retries: 3, max_total_calls_per_line: 12, feedback_context: "structured" },
    });
    // Only what differs from the deployment's: its own retries (2), typed again, are not an override.
    expect(evaluationBody(langgraph({ enabled: true, maxRetries: "2" }), ctx).classification?.langgraph).toEqual({ reflection: { enabled: true } });
    expect(evaluationBody(langgraph({}), ctx).classification).toEqual({ backend: "langgraph", replay_set: "langgraph" });
    expect(formErrors(langgraph({ enabled: true, maxRetries: "0" }), ctx)).not.toHaveProperty("reflection");
    expect(formErrors(langgraph({ enabled: true, maxRetries: "4" }), ctx).reflection).toBe("Revision retries must be a whole number from 0 to 3.");
    expect(formErrors(langgraph({ enabled: true, maxCalls: "0" }), ctx).reflection).toBe("Model calls per line must be a whole number from 1 to 12.");
    expect(formErrors(langgraph({ enabled: true, maxCalls: "two" }), ctx).reflection).toBe("Model calls per line must be a whole number from 1 to 12.");
    // Turned off, its limits ask for nothing.
    expect(formErrors(langgraph({ enabled: false, maxRetries: "9" }), ctx)).not.toHaveProperty("reflection");
    // Live counts the reviewer once reflection is on for this run.
    expect(liveModels(form({ ...LIVE, orchestrator: "langgraph", reflection: { enabled: true } }), limited).map((item) => item.model)).toContain("anthropic/claude-haiku-5.5");
  });

  it("withdraws paid-call confirmations when the mode or any limit changes, and only then", () => {
    const confirmed = form({ ...LIVE, uncapped: true, uncappedConfirmation: "uncapped" });
    expect(updateForm(confirmed, { label: "nightly" })).toMatchObject({ liveConfirmed: true, uncappedConfirmation: "uncapped" });
    for (const change of [{ mode: "replay" as const }, { uncapped: false }, { enabledCaps: ["calls" as const] }, { caps: { ...confirmed.caps, spend: "0.60" } }]) {
      expect(updateForm(confirmed, change)).toMatchObject({ liveConfirmed: false, uncappedConfirmation: "" });
    }
    // Switching a limit off keeps what was typed for it.
    expect(updateForm(form(LIVE), { enabledCaps: [] }).caps.spend).toBe("0.50");
  });

  it("sends only the limits switched on, dollars as exact text, and no budget at all when uncapped", () => {
    const one = evaluationBody(form({ ...LIVE, enabledCaps: ["calls"], caps: { spend: "0.50", calls: "200", receipts: "" } }), context());
    expect(one).toMatchObject({ execution_mode: "live", confirm_live: true, budget: { max_provider_calls: 200 } });
    expect(one.budget).toEqual({ max_provider_calls: 200 });
    expect(one.classification).not.toHaveProperty("replay_set");
    const both = evaluationBody(form({ ...LIVE, enabledCaps: ["spend", "receipts"], caps: { spend: "0.50", calls: "", receipts: "32" } }), context());
    expect(both.budget).toEqual({ max_spend_usd: "0.50", max_receipts: 32 });
    const uncapped = evaluationBody(form({ ...LIVE, uncapped: true, uncappedConfirmation: "uncapped" }), context());
    expect(uncapped).toMatchObject({ confirm_live: true, confirm_uncapped: true });
    expect(uncapped).not.toHaveProperty("budget");
  });
});

describe("translation model", () => {
  const TENCENT = "tencent/hy-mt2-30b-a3b", HAIKU = "anthropic/claude-haiku-5.5", JEV = "typesafe/jev-1.14";
  /** The running deployment before the fix: Jev and Haiku allowed, config.yaml's translator not. */
  const BLOCKED: Backends = { ...LIVE_BACKENDS, allowed_models: [HAIKU, JEV], allowed_classification_models: [HAIKU, JEV], allowed_translation_models: [HAIKU], translation: { ...LIVE_BACKENDS.translation, models: [{ model: HAIKU, default: false, allowed: true }, { model: TENCENT, default: true, allowed: false }] } };
  const jevLive = form({ ...LIVE, model: JEV });

  it("resolves the configured translator by default, a live choice when made, and always the recorded one in replay", () => {
    expect(resolveTranslation(form(), LIVE_BACKENDS).value).toBe(TENCENT);
    expect(resolveTranslation(form({ ...LIVE, translationModel: HAIKU }), LIVE_BACKENDS).value).toBe(HAIKU);
    expect(resolveTranslation(form({ translationModel: HAIKU }), LIVE_BACKENDS).value).toBe(TENCENT); // replay
    expect(resolveTranslation(form(LIVE), { ...LIVE_BACKENDS, translation: { enabled: false, default_model: null, models: [] } }).value).toBeNull();
    expect(liveModels(jevLive, LIVE_BACKENDS)).toEqual([{ model: JEV, role: "classification" }, { model: TENCENT, role: "translation" }]);
  });

  it("blocks a live run whose translator is not allowed, with a way out, and accepts an allowed choice", () => {
    expect(formErrors(jevLive, context({ backends: BLOCKED }))).toEqual({
      translation: "The configured translator, tencent/hy-mt2-30b-a3b, is not allowed for live translation here, and a live run may call it for any non-English line. Choose an allowed translation model, or have it added to EXPENSE_API_ALLOWED_TRANSLATION_MODELS.",
    });
    expect(formErrors({ ...jevLive, translationModel: HAIKU }, context({ backends: BLOCKED }))).toEqual({});
    expect(formErrors({ ...jevLive, translationModel: "vendor/elsewhere" }, context({ backends: BLOCKED })).translation).toBe("vendor/elsewhere is not allowed for live translation here: choose an allowed translation model.");
    // The classification side is checked too, including a backend's default model, which the request never names.
    expect(formErrors(form({ ...LIVE, translationModel: HAIKU }), context({ backends: { ...BLOCKED, allowed_classification_models: [JEV] } })).model).toBe(`${HAIKU} is not allowed for live classification here. Allowed: ${JEV}.`);
    // Each role has its own allowlist: the translator as a classifier, or a classifier as the translator, is blocked.
    const roles = { ...BLOCKED, allowed_classification_models: [HAIKU, JEV], allowed_translation_models: [TENCENT] };
    expect(formErrors(form({ ...LIVE, model: TENCENT }), context({ backends: roles })).model).toBe(`${TENCENT} is not allowed for live classification here. Allowed: ${HAIKU}, ${JEV}.`);
    expect(formErrors(form({ ...LIVE, model: JEV, translationModel: HAIKU }), context({ backends: roles })).translation).toBe(`${HAIKU} is not allowed for live translation here: choose an allowed translation model.`);
    // A replay calls no model, so its translator needs no allowlist entry.
    expect(formErrors(form({ model: JEV }), context({ backends: BLOCKED }))).toEqual({});
  });

  it("sends a translator only for a live run that chose one", () => {
    expect(evaluationBody({ ...jevLive, translationModel: HAIKU }, context()).classification).toEqual({ backend: "decisions", model: JEV, translation_model: HAIKU });
    expect(evaluationBody(jevLive, context()).classification).not.toHaveProperty("translation_model");
    expect(evaluationBody(form({ translationModel: HAIKU }), context()).classification).not.toHaveProperty("translation_model");
  });

  it("offers only discovered translators under Advanced settings, and refuses a blocked one before anything is sent", async () => {
    backends = BLOCKED;
    answer = (method, url) => (method === "POST" && url.pathname === "/api/v1/evaluations" ? { status: 201, body: { ...QUEUED, execution_mode: "live" } } : undefined);
    renderAt(`/evaluations/new?dataset=${DATASET.dataset_id}`);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("radio", { name: "Live (paid)" }));
    await user.click(screen.getByRole("button", { name: /Advanced settings/ }));
    expect(screen.getByLabelText("Provider")).toHaveValue("openrouter");
    expect(within(screen.getByLabelText("Orchestrator")).getByRole("option", { name: "LangGraph" })).toBeEnabled();
    await user.selectOptions(screen.getByLabelText("Classification model"), JEV);
    expect(screen.getByText("Runs through OpenRouter's Decisions API, chosen from the model.")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Spending limit (USD)"), "0.50");
    await user.click(screen.getByRole("checkbox", { name: "I understand this evaluation calls paid providers" }));
    const select = screen.getByLabelText("Translation model");
    expect(within(select).getAllByRole("option").map((option) => option.textContent)).toEqual([`Configured default: ${TENCENT} · not allowed for live runs`, HAIKU]);

    await user.click(screen.getByRole("button", { name: "Start live evaluation (paid)" }));
    expect(await screen.findByText(/The configured translator, tencent\/hy-mt2-30b-a3b, is not allowed/)).toBeInTheDocument();
    expect(posts("/api/v1/evaluations")).toHaveLength(0);

    await user.selectOptions(select, HAIKU);
    expect(screen.getByLabelText("Configuration")).toHaveTextContent(`Translation${HAIKU} · Chosen`);
    await user.click(screen.getByRole("button", { name: "Start live evaluation (paid)" }));
    await waitFor(() => expect(posts("/api/v1/evaluations")).toHaveLength(1));
    expect(JSON.parse(posts("/api/v1/evaluations")[0]!.body!).classification).toEqual({ backend: "decisions", model: JEV, translation_model: HAIKU });
  });

  it("chooses LangGraph node models under Advanced settings and shows them in the summary", async () => {
    answer = (method, url) => (method === "POST" && url.pathname === "/api/v1/evaluations" ? { status: 201, body: QUEUED } : url.pathname === `/api/v1/evaluations/${QUEUED.evaluation_id}` ? { status: 200, body: QUEUED } : undefined);
    renderAt(`/evaluations/new?dataset=${DATASET.dataset_id}`);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Advanced settings/ }));
    await user.selectOptions(screen.getByLabelText("Orchestrator"), "langgraph");
    await user.selectOptions(screen.getByLabelText("2. Fallback model"), JEV);
    expect(screen.getByLabelText("Configuration")).toHaveTextContent("fallback: OpenRouter · typesafe/jev-1.14 (Decisions API) · chosen for this run");
    expect(screen.getByLabelText("Configuration")).toHaveTextContent("primary: OpenRouter · typesafe/jev-1.14 (Decisions API)");
    expect(screen.getByRole("note")).toHaveTextContent("reads only typesafe/jev-1.14's answers for the fallback node");
    await user.click(screen.getByRole("button", { name: "Start replay evaluation" }));
    await waitFor(() => expect(posts("/api/v1/evaluations")).toHaveLength(1));
    expect(JSON.parse(posts("/api/v1/evaluations")[0]!.body!).classification).toEqual({
      backend: "langgraph",
      replay_set: "langgraph",
      langgraph: { backends: [{ name: "primary", backend: "decisions", model: JEV }, { name: "fallback", backend: "decisions", model: JEV }] },
    });
  });

  it("configures reflection retries under Advanced settings and refuses one beyond the maximum before sending", async () => {
    answer = (method, url) => (method === "POST" && url.pathname === "/api/v1/evaluations" ? { status: 201, body: QUEUED } : undefined);
    renderAt(`/evaluations/new?dataset=${DATASET.dataset_id}`);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Advanced settings/ }));
    await user.selectOptions(screen.getByLabelText("Orchestrator"), "langgraph");
    await user.click(screen.getByRole("checkbox", { name: /Review each answer, and revise it/ }));
    await user.type(screen.getByLabelText("Revision retries"), "11");
    await user.click(screen.getByRole("button", { name: "Start replay evaluation" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Revision retries must be a whole number from 0 to 10.");  // the core's bound: this discovery reports no maximum
    expect(posts("/api/v1/evaluations")).toHaveLength(0);
    await user.clear(screen.getByLabelText("Revision retries"));
    await user.type(screen.getByLabelText("Revision retries"), "1");
    await user.click(screen.getByRole("button", { name: "Start replay evaluation" }));
    await waitFor(() => expect(posts("/api/v1/evaluations")).toHaveLength(1));
    expect(JSON.parse(posts("/api/v1/evaluations")[0]!.body!).classification).toEqual({ backend: "langgraph", replay_set: "langgraph", langgraph: { reflection: { enabled: true, max_retries: 1 } } });
  });

  it("shows the recorded translator in replay, where it cannot change", async () => {
    renderAt(`/evaluations/new?dataset=${DATASET.dataset_id}`);
    await userEvent.click(await screen.findByRole("button", { name: /Advanced settings/ }));
    expect(screen.queryByRole("combobox", { name: "Translation model" })).not.toBeInTheDocument();
    expect(screen.getByText(/a replay reads the translations recorded with this model, so it cannot be changed/)).toBeInTheDocument();
  });
});

describe("evaluation workbench", () => {
  it("keeps the landing page to evaluations, with one way to start a new one", async () => {
    answer = (_, url) => (url.pathname === "/api/v1/evaluations" ? { status: 200, body: page([DETAIL]) } : undefined);
    const router = renderAt("/evaluations");
    expect(await screen.findByRole("table", { name: "Evaluation jobs" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Dataset")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Start/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("link", { name: "New evaluation" }));
    expect(router.state.location.pathname).toBe("/evaluations/new");
  });

  it("shows errors only once used or submitted, and starts a replay with what it resolved", async () => {
    answer = (method, url) => (method === "POST" && url.pathname === "/api/v1/evaluations" ? { status: 201, body: QUEUED } : undefined);
    const router = renderAt("/evaluations/new");
    const user = userEvent.setup();
    const start = await screen.findByRole("button", { name: "Start replay evaluation" });
    expect(screen.queryAllByRole("alert")).toHaveLength(0);
    expect(screen.getByRole("radio", { name: "Replay (no cost)" })).toHaveAttribute("aria-checked", "true");

    await user.click(start);
    expect(await screen.findByText("Choose a dataset version.")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText("Dataset")).toHaveFocus());
    expect(posts("/api/v1/evaluations")).toHaveLength(0);

    await user.selectOptions(screen.getByLabelText("Dataset"), DATASET.dataset_id);
    expect(screen.getByLabelText("Configuration")).toHaveTextContent(`policies v1`);
    expect(screen.getByLabelText("Configuration")).toHaveTextContent("OrchestratorDirect · The deployment's default");
    expect(screen.getByLabelText("Configuration")).toHaveTextContent("Provider · modelOpenRouter · anthropic/claude-haiku-5.5 · Chat completions adapter");
    expect(screen.getByLabelText("Configuration")).toHaveTextContent("openrouter · Holds the Chat completions adapter's recorded answers");
    await user.click(start);
    await waitFor(() => expect(router.state.location.pathname).toBe(`/evaluations/${QUEUED.evaluation_id}`));
    const body = JSON.parse(posts("/api/v1/evaluations")[0]!.body!);
    expect(body).toMatchObject({ dataset_id: DATASET.dataset_id, ruleset_id: PUBLISHED.ruleset_id, execution_mode: "replay", classification: { backend: "chat_completions", replay_set: "openrouter" } });
    expect(body).not.toHaveProperty("confirm_live");
    expect(body).not.toHaveProperty("budget");
  });

  it("asks for a ruleset when several are published, rather than choosing one", async () => {
    answer = (_, url) => (url.pathname === "/api/v1/rulesets" ? { status: 200, body: page([OTHER, PUBLISHED]) } : undefined);
    renderAt(`/evaluations/new?dataset=${DATASET.dataset_id}`);
    const select = await screen.findByLabelText("Ruleset version");
    expect(select).toHaveValue("");
    await userEvent.click(screen.getByRole("button", { name: "Start replay evaluation" }));
    expect(await screen.findByText("2 rulesets are published for production: choose one.")).toBeInTheDocument();
    expect(posts("/api/v1/evaluations")).toHaveLength(0);
  });

  it("starts a capped live run only after its limit and confirmation, and withdraws the confirmation when the limit changes", async () => {
    backends = LIVE_BACKENDS;
    answer = (method, url) => (method === "POST" && url.pathname === "/api/v1/evaluations" ? { status: 201, body: { ...QUEUED, execution_mode: "live" } } : undefined);
    renderAt(`/evaluations/new?dataset=${DATASET.dataset_id}`);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("radio", { name: "Live (paid)" }));
    await user.click(screen.getByRole("radio", { name: "Provider requests" }));
    await user.type(screen.getByLabelText("Provider requests limit (requests)"), "200");
    const confirm = screen.getByRole("checkbox", { name: "I understand this evaluation calls paid providers" });
    await user.click(confirm);
    await user.type(screen.getByLabelText("Provider requests limit (requests)"), "0");
    expect(confirm).not.toBeChecked();

    await user.click(screen.getByRole("button", { name: "Start live evaluation (paid)" }));
    expect(await screen.findByText("Confirm that this evaluation calls paid providers.")).toBeInTheDocument();
    expect(posts("/api/v1/evaluations")).toHaveLength(0);
    await user.click(confirm);
    await user.click(screen.getByRole("button", { name: "Start live evaluation (paid)" }));
    await waitFor(() => expect(posts("/api/v1/evaluations")).toHaveLength(1));
    const body = JSON.parse(posts("/api/v1/evaluations")[0]!.body!);
    expect(body).toMatchObject({ execution_mode: "live", confirm_live: true, budget: { max_provider_calls: 2000 } });
    expect(body.budget).toEqual({ max_provider_calls: 2000 });
  });

  it("needs the typed confirmation for an uncapped live run, and sends no budget", async () => {
    backends = LIVE_BACKENDS;
    const queued = { ...QUEUED, execution_mode: "live" as const };
    answer = (method, url) => {
      if (method === "POST" && url.pathname === "/api/v1/evaluations") return { status: 201, body: queued };
      // The page it opens reads the job it queued: answered as the API would, not with a list page.
      if (method === "GET" && url.pathname === `/api/v1/evaluations/${QUEUED.evaluation_id}`) return { status: 200, body: queued };
      if (method === "GET" && url.pathname === `/api/v1/evaluations/${QUEUED.evaluation_id}/metrics`) return { status: 200, body: PROVISIONAL };
      return undefined;
    };
    const router = renderAt(`/evaluations/new?dataset=${DATASET.dataset_id}`);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("radio", { name: "Live (paid)" }));
    await user.click(screen.getByRole("radio", { name: "No limit" }));
    await user.click(screen.getByRole("checkbox", { name: "I understand this evaluation calls paid providers, with no limit" }));
    const start = screen.getByRole("button", { name: "Start uncapped live evaluation (paid)" });
    await user.click(start);
    expect(await screen.findByText("Type “uncapped” to confirm a run with no limit.")).toBeInTheDocument();
    expect(posts("/api/v1/evaluations")).toHaveLength(0);
    await user.type(screen.getByLabelText("Type “uncapped” to confirm"), "uncapped");
    await user.click(start);
    await waitFor(() => expect(posts("/api/v1/evaluations")).toHaveLength(1));
    const body = JSON.parse(posts("/api/v1/evaluations")[0]!.body!);
    expect(body).toMatchObject({ confirm_live: true, confirm_uncapped: true });
    expect(body).not.toHaveProperty("budget");
    // It opens the queued job, which renders as a job: never a page the mock was not asked for.
    await waitFor(() => expect(router.state.location.pathname).toBe(`/evaluations/${QUEUED.evaluation_id}`));
    expect(await screen.findByRole("heading", { name: QUEUED.label ?? QUEUED.evaluation_id })).toBeInTheDocument();
  });

  it("shows a limit's error in place of its hint, so nothing below moves when it appears", async () => {
    backends = LIVE_BACKENDS;
    renderAt(`/evaluations/new?dataset=${DATASET.dataset_id}`);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("radio", { name: "Live (paid)" }));
    await user.type(screen.getByLabelText("Spending limit (USD)"), "0");
    await user.click(screen.getByRole("checkbox", { name: "I understand this evaluation calls paid providers" }));
    // The press on the checkbox blurred the field and showed its error, and still checked the box.
    expect(screen.getByRole("checkbox", { name: "I understand this evaluation calls paid providers" })).toBeChecked();
    const error = screen.getByText("A zero spending limit can make no call: set more than $0.");
    const hint = document.getElementById("cap-spend-help")!;
    expect(hint).toHaveClass("invisible");
    expect(error.parentElement).toBe(hint.parentElement);
  });

  it("never offers No limit where the deployment does not allow uncapped evaluations", async () => {
    backends = { ...LIVE_BACKENDS, evaluations: { uncapped_allowed: false } };
    renderAt(`/evaluations/new?dataset=${DATASET.dataset_id}`);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("radio", { name: "Live (paid)" }));
    expect(screen.getByRole("radio", { name: "No limit (not allowed here)" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /Advanced settings/ }));
    expect(screen.getByRole("checkbox", { name: /No limit \(uncapped\) · not allowed in this deployment/ })).toBeDisabled();
  });

  it("combines limits under Advanced settings, and keeps every value while they are collapsed", async () => {
    backends = LIVE_BACKENDS;
    renderAt(`/evaluations/new?dataset=${DATASET.dataset_id}`);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("radio", { name: "Live (paid)" }));
    await user.type(screen.getByLabelText("Spending limit (USD)"), "0.50");
    const toggle = screen.getByRole("button", { name: /Advanced settings/ });
    await user.click(toggle);
    await user.type(screen.getByLabelText("Label (optional)"), "nightly");
    await user.click(screen.getByRole("checkbox", { name: "Limit receipts" }));
    await user.type(screen.getByLabelText("Receipts limit (receipts)"), "40");
    await user.click(toggle);
    expect(screen.getByText(/2 limits are combined: spending, receipts/)).toBeInTheDocument();
    await user.click(toggle);
    expect(screen.getByLabelText("Label (optional)")).toHaveValue("nightly");
    expect(screen.getByLabelText("Spending limit (USD)")).toHaveValue("0.50");
    expect(screen.getByLabelText("Receipts limit (receipts)")).toHaveValue("40");
    expect(posts("/api/v1/evaluations")).toHaveLength(0);
  });

  it("keeps datasets in their own area, from which one can be evaluated", async () => {
    const router = renderAt("/evaluations/datasets");
    const table = await screen.findByRole("table", { name: "Dataset versions" });
    expect(within(table).getByText("golden v1")).toBeInTheDocument();
    expect(within(table).getByText("Complete context")).toBeInTheDocument();
    await userEvent.click(within(table).getByRole("link", { name: "Evaluate golden v1" }));
    expect(router.state.location.pathname).toBe("/evaluations/new");
    expect(await screen.findByLabelText("Dataset")).toHaveValue(DATASET.dataset_id);
  });

  it("opens a dataset and paginates, searches and expands its reference receipts", async () => {
    answer = (_, url) => url.pathname === `/api/v1/evaluations/datasets/${DATASET.dataset_id}` ? { status: 200, body: datasetDetailJson } : undefined;
    const user = userEvent.setup();
    const router = renderAt("/evaluations/datasets");
    await user.click(await screen.findByRole("link", { name: "View golden v1" }));
    expect(router.state.location.pathname).toBe(`/evaluations/datasets/${DATASET.dataset_id}`);
    const table = await screen.findByRole("table", { name: "Dataset receipts" });
    expect(within(table).getByRole("button", { name: "RCP-001" })).toBeInTheDocument();
    expect(within(table).queryByRole("button", { name: "RCP-011" })).not.toBeInTheDocument();
    expect(screen.getByText("1–10 of 32")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("11–20 of 32")).toBeInTheDocument();
    expect(within(table).queryByRole("button", { name: "RCP-001" })).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Rows per page"), "20");
    expect(screen.getByText("1–20 of 32")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("21–32 of 32")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    await user.type(screen.getByLabelText("Search receipts"), "Harvest Table Kitchen");
    expect(screen.getByText("1 of 32 receipts")).toBeInTheDocument();
    const toggle = screen.getByRole("button", { name: "RCP-001" });
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const lines = screen.getByRole("table", { name: "Line items for RCP-001" });
    expect(within(lines).getByText("Grilled salmon entree")).toBeInTheDocument();
    expect(within(lines).getAllByText("food_beverage")).toHaveLength(3);
    expect(screen.getByText(/Baseline clean receipt/)).toBeInTheDocument();
    expect(calls.some((call) => call.path.includes("include_cases=true"))).toBe(true);
    expect(calls.filter((call) => call.method === "POST")).toHaveLength(0);
    await user.clear(screen.getByLabelText("Search receipts"));
    await user.type(screen.getByLabelText("Search receipts"), "no such merchant");
    expect(screen.getByText("No matching receipts")).toBeInTheDocument();
  });

  it("shows a dataset load failure and lets the user retry", async () => {
    answer = (_, url) => url.pathname.includes(`/datasets/${DATASET.dataset_id}`) ? { status: 404, body: { error: { code: "dataset_not_found", message: "Dataset not found", details: null } } } : undefined;
    renderAt(`/evaluations/datasets/${DATASET.dataset_id}`);
    expect(await screen.findByRole("alert", {}, { timeout: 7000 })).toHaveTextContent("Dataset not found");
    answer = (_, url) => url.pathname.includes(`/datasets/${DATASET.dataset_id}`) ? { status: 200, body: datasetDetailJson } : undefined;
    await userEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(await screen.findByRole("table", { name: "Dataset receipts" })).toBeInTheDocument();
  });

  it("polls a running job, labels its metrics provisional, and shows final metrics once it finishes", async () => {
    let polls = 0;
    const running = { ...QUEUED, status: "running" as const, progress: { ...QUEUED.progress, processed_receipts: 10, percent: 31.2 } };
    answer = (_, url) => {
      if (url.pathname === `/api/v1/evaluations/${QUEUED.evaluation_id}`) {
        polls += 1;
        return { status: 200, body: polls >= 2 ? { ...DETAIL, evaluation_id: QUEUED.evaluation_id, updated_at: "2099-01-01T00:00:00Z" } : running };
      }
      if (url.pathname === `/api/v1/evaluations/${QUEUED.evaluation_id}/metrics`) return { status: 200, body: polls >= 2 ? METRICS : PROVISIONAL };
      return undefined;
    };
    renderAt(`/evaluations/${QUEUED.evaluation_id}`);
    expect(await screen.findByText("Provisional metrics.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export JSON" })).toBeDisabled();
    expect(screen.getByText(/Refreshing every 2 s/)).toBeInTheDocument();

    expect(await screen.findByText("Final metrics", {}, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.queryByText("Provisional metrics.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export JSON" })).toBeEnabled();
    expect(screen.getByText("Finished: execution results are final.")).toBeInTheDocument();
  }, 10_000);

  it("says a replay made no provider call and spent nothing new", async () => {
    render(<SpendingView job={DETAIL} />);
    expect(screen.getByText("Replay: no new spending.")).toBeInTheDocument();
    expect(screen.getByText(/90 recorded answers were read; no provider was called/)).toBeInTheDocument();
    expect(screen.queryByText(/New spend/)).not.toBeInTheDocument();
  });

  it("shows live spending against the budget, as a lower bound when a call has no known cost", () => {
    const spend = { ...DETAIL.spend, new_spend_usd: "0.000473", cost_completeness: "incomplete" as const, unpriced_usage: { calls: 2, input_tokens: 10, output_tokens: 4 } };
    const live: EvaluationDetail = { ...DETAIL, execution_mode: "live", status: "budget_exhausted", stop_reason: "max_spend_usd", budget: { max_spend_usd: "0.0005", max_provider_calls: 50, max_receipts: 32 }, spend };
    render(<SpendingView job={live} />);
    expect(screen.getAllByText("$0.000473 (lower bound)").length).toBeGreaterThan(0);
    expect(screen.getByText(/2 calls have no known cost/)).toBeInTheDocument();
    expect(screen.getByText(/Limits: \$0.0005 spending, 50 provider requests, 32 receipts/)).toBeInTheDocument();
    expect(screen.getByText(/Stopped at a limit: Max spend usd/)).toBeInTheDocument();
  });

  it("keeps verified, estimated and unknown costs apart, and never shows a lower bound as a total", () => {
    const spend = { ...DETAIL.spend, actual_cost_usd: "0.0004", estimated_cost_usd: "0.000021", new_spend_usd: "0.000421", cost_completeness: "incomplete" as const, unpriced_usage: { calls: 3, input_tokens: 900, output_tokens: 120 } };
    const live: EvaluationDetail = { ...DETAIL, execution_mode: "live", spend, budget: null, live: { confirmed: true, uncapped: true, answers: "x", retries: "none" } };
    const { unmount } = render(<SpendingView job={live} />);
    expect(screen.getByText("Provider-reported").nextSibling).toHaveTextContent("$0.0004");
    expect(screen.getByText("Estimated").nextSibling).toHaveTextContent("$0.000021");
    expect(screen.getByText("Unknown cost").nextSibling).toHaveTextContent("3 calls");
    expect(screen.getByText(/Not in the total: 900 tokens in, 120 out/)).toBeInTheDocument();
    expect(screen.getAllByText("$0.000421 (lower bound)").length).toBeGreaterThan(0);
    expect(screen.queryByText("$0.000421")).not.toBeInTheDocument();
    unmount();
    // A replay's recorded cost is history, and a lower bound when some recorded calls hold none.
    const partial = { ...DETAIL.spend.historical, cost_usd: "0.0052", calls_with_cost: 80, calls_without_cost: 10 };
    render(<SpendingView job={{ ...DETAIL, spend: { ...DETAIL.spend, historical: partial, by_purpose: { classification: { ...DETAIL.spend.by_purpose.classification!, historical: partial } } } }} />);
    expect(screen.getByText("at least $0.0052 (10 calls without a recorded cost)")).toBeInTheDocument();
  });

  it("shows only the limits a live run had, and an uncapped run as such", () => {
    const live: EvaluationDetail = { ...DETAIL, execution_mode: "live", budget: { max_spend_usd: null, max_provider_calls: 50, max_receipts: null }, live: { confirmed: true, uncapped: false, answers: "x", retries: "none" } };
    const { unmount } = render(<SpendingView job={live} />);
    expect(screen.getByText(/Limits: 50 provider requests\./)).toBeInTheDocument();
    unmount();
    render(<SpendingView job={{ ...live, budget: null, live: { ...live.live!, uncapped: true } }} />);
    expect(screen.getByText("Uncapped: no spending, request or receipt limit was set.")).toBeInTheDocument();
  });

  it("shows expected against predicted results, never an undecided pass as a pass, and filters on the API", async () => {
    const items = structuredClone(RESULTS.items);
    const undecided = items[0]!;
    undecided.result = { ...undecided.result!, disposition: "pass", decided: false, open_questions: [{ rule_id: "TIME-03", line_ids: [], reasons: ["context"] }] };
    const failed = items[1]!;
    failed.status = "error";
    failed.error = { code: "replay_recording_missing", message: "1 model answers are not in the recordings" };
    failed.result = null;
    answer = (_, url) => {
      if (url.pathname === `/api/v1/evaluations/${DETAIL.evaluation_id}`) return { status: 200, body: DETAIL };
      if (url.pathname === `/api/v1/evaluations/${DETAIL.evaluation_id}/metrics`) return { status: 200, body: METRICS };
      if (url.pathname === `/api/v1/evaluations/${DETAIL.evaluation_id}/results`) return { status: 200, body: { ...RESULTS, items } };
      return undefined;
    };
    renderAt(`/evaluations/${DETAIL.evaluation_id}`);
    const table = await screen.findByRole("table", { name: "Receipt results" });
    const rows = within(table).getAllByRole("row");

    const first = rows.find((row) => within(row).queryByText(undecided.receipt_id))!;
    expect(within(first).getByText("Provisional pass")).toBeInTheDocument();
    expect(within(first).getByText(/Undecided · 1 open/)).toBeInTheDocument();
    // Scoring compares dispositions only: the match is qualified, never a plain "matches".
    expect(within(first).getByText("disposition matches · rules still open")).toBeInTheDocument();
    expect(screen.queryByText("Receipt errors")).not.toBeInTheDocument(); // error_summary is {}
    const second = rows.find((row) => within(row).queryByText(failed.receipt_id))!;
    expect(within(second).getByText("Not evaluated")).toBeInTheDocument();
    expect(within(second).getByText("replay_recording_missing")).toBeInTheDocument();

    await userEvent.click(within(first).getByRole("button", { name: undecided.receipt_id }));
    expect(screen.getByRole("table", { name: `Lines of ${undecided.receipt_id}` })).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Errors"), "true");
    await waitFor(() => expect(calls.some((call) => call.path === `/api/v1/evaluations/${DETAIL.evaluation_id}/results?limit=20&offset=0&error=true`)).toBe(true));
  });

  it("compares completed runs, warning when they are not directly comparable", async () => {
    const [first, second] = COMPARE.evaluations;
    const live = { ...second!, execution_mode: "live" as const, new_spend_usd: "0.0021", cost_completeness: "incomplete" as const, mean_latency_per_receipt_ms: 900 };
    const comparison: EvaluationComparison = {
      comparability: { directly_comparable: false, issues: ["replay and live runs are mixed: a replay repeats recorded answers, a live run asks the models again"], note: COMPARE.comparability.note },
      evaluations: [first!, live],
    };
    answer = (method, url) => (method === "POST" && url.pathname === "/api/v1/evaluations/compare" ? { status: 200, body: comparison } : undefined);
    renderAt(`/evaluations/compare?ids=${first!.evaluation_id},${second!.evaluation_id}`);

    expect(await screen.findByText("Not directly comparable")).toBeInTheDocument();
    expect(screen.getByText(/replay and live runs are mixed/)).toBeInTheDocument();
    expect(JSON.parse(posts("/api/v1/evaluations/compare")[0]!.body!)).toEqual({ evaluation_ids: [first!.evaluation_id, second!.evaluation_id] });
    expect(screen.getByText("No new spend (replay)")).toBeInTheDocument();
    expect(screen.getByText("$0.0021 (lower bound)")).toBeInTheDocument();
    expect(screen.getAllByText(/\+\d[\d,]* ms vs first/).length).toBeGreaterThan(0);
    expect(document.querySelectorAll("[data-differs]").length).toBeGreaterThan(0);
  });

  it("says the configuration differs when only the translation models differ, and shows both", async () => {
    const [first, second] = COMPARE.evaluations;
    const comparison: EvaluationComparison = {
      comparability: { directly_comparable: false, issues: ["different translation models: anthropic/claude-haiku-5.5, tencent/hy-mt2-30b-a3b"], issue_codes: ["translation_model"], note: COMPARE.comparability.note },
      evaluations: [{ ...first!, translation_model: "tencent/hy-mt2-30b-a3b", translation_known: true }, { ...second!, translation_model: "anthropic/claude-haiku-5.5", translation_known: true }],
    };
    answer = (method, url) => (method === "POST" && url.pathname === "/api/v1/evaluations/compare" ? { status: 200, body: comparison } : undefined);
    renderAt(`/evaluations/compare?ids=${first!.evaluation_id},${second!.evaluation_id}`);
    expect(await screen.findByText("Configuration differs — translation model", { selector: "p" })).toBeInTheDocument();
    expect(screen.queryByText(/Directly comparable/)).not.toBeInTheDocument();
    const row = screen.getByRole("row", { name: /Translation model/ });
    expect(row).toHaveTextContent("tencent/hy-mt2-30b-a3b");
    expect(row).toHaveTextContent("anthropic/claude-haiku-5.5");
    expect(within(row).getByText("differs")).toBeInTheDocument();
  });

  it("never takes an unrecorded translator to match, and keeps other issues in the heading", async () => {
    const [first, second] = COMPARE.evaluations;
    const comparison: EvaluationComparison = {
      comparability: { directly_comparable: false, issues: ["different datasets", "some runs did not record their translation model, so it cannot be shown to match"], issue_codes: ["dataset", "translation_model_unknown"], note: COMPARE.comparability.note },
      evaluations: [{ ...first!, translation_model: "tencent/hy-mt2-30b-a3b", translation_known: true }, { ...second!, translation_model: null, translation_known: false }],
    };
    answer = (method, url) => (method === "POST" && url.pathname === "/api/v1/evaluations/compare" ? { status: 200, body: comparison } : undefined);
    renderAt(`/evaluations/compare?ids=${first!.evaluation_id},${second!.evaluation_id}`);
    expect(await screen.findByText("Not directly comparable")).toBeInTheDocument();
    expect(screen.getByText(/Configuration differs — translation model: some runs did not record/)).toBeInTheDocument();
    const row = screen.getByRole("row", { name: /Translation model/ });
    expect(within(row).getByText("Not recorded (an older evaluation)")).toBeInTheDocument();
    expect(within(row).getByText("differs")).toBeInTheDocument();
  });

  it("says when runs are directly comparable, and still not a benchmark", async () => {
    answer = (method, url) => (method === "POST" && url.pathname === "/api/v1/evaluations/compare" ? { status: 200, body: COMPARE } : undefined);
    renderAt(`/evaluations/compare?ids=${COMPARE.evaluations.map((run) => run.evaluation_id).join(",")}`);
    expect(await screen.findByText(/Directly comparable:/)).toBeInTheDocument();
    expect(screen.getByText(/not a controlled head-to-head benchmark/)).toBeInTheDocument();
    // A run recorded with backend jev reads as a direct run through OpenRouter, its adapter shown apart and not as a provider.
    expect(COMPARE.evaluations.map((run) => run.backend)).toEqual(["openrouter", "jev"]);
    const adapter = screen.getByRole("row", { name: /^Adapter/ });
    expect(adapter).toHaveTextContent("Chat completions");
    expect(adapter).toHaveTextContent("Decisions API");
    expect(screen.getByRole("row", { name: /^Orchestrator · provider/ })).toHaveTextContent("Direct · OpenRouterDirect · OpenRouter");
    expect(screen.getByRole("row", { name: /^Classification model/ })).toHaveTextContent("anthropic/claude-haiku-5.5typesafe/jev-1.14");
    // The adapters' earlier names, recorded by these runs, are never shown as providers, adapters or models.
    const table = adapter.closest("table")!;
    // (typesafe/jev-1.14 is the model's own ID, which stays.)
    expect(table.textContent).not.toMatch(/(?<!typesafe\/)\bjev\b(?!-1)|\(openrouter\)|=jev:|=openrouter:/);
  });

  it("matches a run recorded before the adapters' rename with one after it, on what each ran", async () => {
    const [, jev] = COMPARE.evaluations;
    // The same Jev classification, recorded once as jev (before the rename) and once as decisions (after).
    const later = { ...jev!, evaluation_id: "eval_after_rename", label: "after", backend: "decisions", setup: { ...jev!.setup!, backend: "decisions" } };
    const comparison = { ...COMPARE, evaluations: [jev!, later] };
    answer = (method, url) => (method === "POST" && url.pathname === "/api/v1/evaluations/compare" ? { status: 200, body: comparison } : undefined);
    renderAt(`/evaluations/compare?ids=${jev!.evaluation_id},${later.evaluation_id}`);
    const adapter = await screen.findByRole("row", { name: /^Adapter/ });
    expect(within(adapter).queryByText("differs")).not.toBeInTheDocument();
    expect(within(screen.getByRole("row", { name: /^Classification model/ })).queryByText("differs")).not.toBeInTheDocument();
    expect(adapter).toHaveTextContent("Decisions APIDecisions API");
  });

  it("reads an older run's recorded backend, without a setup, as it was recorded", () => {
    expect(setupText(undefined, "jev")).toBe("jev");
    expect(setupText(COMPARE.evaluations[1]!.setup, "jev")).toBe("Direct · OpenRouter · typesafe/jev-1.14 (Decisions API)");
  });
});

describe("quality dashboard", () => {
  const RESCORED = rescoredJson as unknown as EvaluationMetrics;
  const metricsAt = (body: unknown, extra: (method: string, url: URL) => Answer | undefined = () => undefined) => (method: string, url: URL) =>
    extra(method, url) ??
    (url.pathname === `/api/v1/evaluations/${DETAIL.evaluation_id}` ? { status: 200, body: DETAIL } : url.pathname === `/api/v1/evaluations/${DETAIL.evaluation_id}/metrics` ? { status: 200, body } : url.pathname === `/api/v1/evaluations/${DETAIL.evaluation_id}/results` ? { status: 200, body: RESULTS } : undefined);

  it("reports every run compared in the compare view: per category, per rule, dispositions, baseline, evidence and failures", async () => {
    const [first, second] = COMPARE.evaluations;
    // The second run was scored with version 1: what that version lacks is said, never shown as zero.
    const { baseline: _baseline, evidence: _evidence, error_analysis: _errors, coverage: _coverage, ...older } = RESCORED;
    const earlier = { ...older, evaluation_id: second!.evaluation_id, schema_version: "evaluation-metrics/1" };
    answer = (method, url) => {
      if (method === "POST" && url.pathname === "/api/v1/evaluations/compare") return { status: 200, body: COMPARE };
      if (url.pathname === `/api/v1/evaluations/${first!.evaluation_id}/metrics`) return { status: 200, body: { ...RESCORED, evaluation_id: first!.evaluation_id } };
      if (url.pathname === `/api/v1/evaluations/${second!.evaluation_id}/metrics`) return { status: 200, body: earlier };
      return undefined;
    };
    renderAt(`/evaluations/compare?ids=${first!.evaluation_id},${second!.evaluation_id}`);
    const user = userEvent.setup();
    expect(await screen.findByRole("row", { name: /^Adapter/ })).toBeInTheDocument();  // the comparison table stays
    const report = (await screen.findByRole("heading", { name: "Evaluation report" })).closest("div")!;
    expect(await within(report).findByText(/scored with different versions/)).toBeInTheDocument();
    // Classification: per category, one column per run.
    expect(within(report).getByRole("table", { name: "Scores per expected category by run" })).toHaveTextContent("alcohol");
    // Rules: an untested rule is "Not tested", never a perfect or a zero score.
    // Every section is on the page, in order, each under its own heading: no tab hides one.
    expect(within(report).getAllByRole("heading", { level: 3 }).map((heading) => heading.textContent)).toEqual(["1 · Classification", "2 · Rules", "3 · Dispositions", "4 · Keyword baseline", "5 · Evidence", "6 · Error analysis"]);
    expect(within(report).queryByRole("tab")).not.toBeInTheDocument();
    await user.selectOptions(within(report).getByLabelText("Rules shown"), "all");
    const rules = within(report).getByRole("table", { name: "Scores per rule by run" });
    expect(within(rules).getByRole("row", { name: /^ALC-02/ })).toHaveTextContent("Not tested");
    expect(within(rules).getByRole("row", { name: /^ENT-01/ })).toHaveTextContent(/·\d+·\d+/);
    // Dispositions: a 3×3 matrix for each run.
    expect(within(report).getAllByRole("table", { name: /Expected dispositions by predicted ones/ })).toHaveLength(2);
    // Baseline: the model's wins and losses; the version-1 run has no baseline, and says so.
    const baseline = within(report).getByRole("table", { name: "The model against the keyword baseline by run" });
    expect(within(baseline).getByRole("row", { name: /^Model wins/ })).toHaveTextContent("4Not in this scoring");
    expect(within(baseline).getByRole("row", { name: /^Model losses/ })).toHaveTextContent("2Not in this scoring");
    // Evidence: exact line items, scored for the run that has it.
    expect(within(report).getByRole("row", { name: /^Exact line items/ })).toHaveTextContent(/100\.0%.*Not in this scoring/);
    // Errors: the diagnosed failures, each with its stage and the run it is from.
    const errors = within(report).getByRole("region", { name: "Error analysis" });
    expect(within(within(errors).getByRole("list", { name: "Diagnosed failures" })).getAllByRole("listitem")).toHaveLength(2);
    expect(within(report).getByRole("row", { name: /^Retrieval/ })).toHaveTextContent("Not attributed");
  });

  it("never keeps a run's provisional metrics from while it ran: the compare view reads the final ones and shows classification", async () => {
    const [first, second] = COMPARE.evaluations;
    // As the API answers before any receipt has run: provisional, with nothing scored yet.
    const nothingRun = { available: false, reason: "no receipt has been run yet" };
    const provisional = { ...RESCORED, provisional: true, status: "running", scored: { kind: "provisional" }, classification: nothingRun, disposition: nothingRun, findings: nothingRun, evidence: nothingRun, baseline: nothingRun };
    let finished = false;
    answer = (method, url) => {
      if (method === "POST" && url.pathname === "/api/v1/evaluations/compare") return { status: 200, body: COMPARE };
      for (const run of [first!, second!])
        if (url.pathname === `/api/v1/evaluations/${run.evaluation_id}/metrics`) return { status: 200, body: { ...(finished ? RESCORED : provisional), evaluation_id: run.evaluation_id } };
      return undefined;
    };
    const path = `/evaluations/compare?ids=${first!.evaluation_id},${second!.evaluation_id}`;
    const router = renderAt(path);
    const report = () => screen.getByRole("heading", { name: "Evaluation report" }).closest("div")!;
    await screen.findByRole("heading", { name: "Evaluation report" });
    // The reason is shown, never a bare "Not scored"; the snapshot is said to be provisional.
    expect(await within(report()).findByText(/^Provisional metrics, from while/)).toBeInTheDocument();
    expect(within(within(report()).getByRole("table", { name: "Classification quality by run" })).getByRole("row", { name: /^Category accuracy/ })).toHaveTextContent("Not scored: no receipt has been run yet");
    // The runs finish. Opened again in the same session, the comparison reads the final metrics, not the cached snapshot.
    finished = true;
    await act(() => router.navigate("/evaluations"));
    await act(() => router.navigate(path));
    const alcohol = await within(await screen.findByRole("table", { name: "Scores per expected category by run" })).findByRole("row", { name: /^alcohol/ });
    expect(alcohol).toHaveTextContent(/0\.800.*support 2/);
    const summary = within(report()).getByRole("table", { name: "Classification quality by run" });
    expect(within(summary).getByRole("row", { name: /^Category accuracy/ })).toHaveTextContent("88.2%");
    expect(within(summary).queryByText(/Not scored/)).not.toBeInTheDocument();
    expect(within(report()).queryByText(/^Provisional metrics/)).not.toBeInTheDocument();
    expect(within(report()).getAllByRole("table", { name: /^Expected categories by predicted ones/ })).toHaveLength(2);  // one confusion matrix per run
  });

  it("answers the five questions from a real run's stored metrics, each in its own section", async () => {
    answer = metricsAt(RESCORED);
    renderAt(`/evaluations/${DETAIL.evaluation_id}`);
    const head = await screen.findByLabelText("Headline");
    expect(head).toHaveTextContent("Category accuracy88.2%");
    expect(head).toHaveTextContent("Restricted F10.909");
    expect(head).toHaveTextContent("Finding F10.857");
    for (const title of ["1 · Classification quality", "2 · Policy decision quality", "3 · Exception detection", "4 · Evidence correctness", "5 · Keyword baseline", "6 · Coverage and error analysis"])
      expect(screen.getByRole("region", { name: title })).toBeInTheDocument();
    expect(screen.getByText(/Rescored with evaluation-metrics\/3 on .* It finished scored with evaluation-metrics\/1, which is kept\./)).toBeInTheDocument();

    const rules = screen.getByRole("table", { name: "Exception detection per rule" });
    const row = (rule: string) => within(rules).getAllByRole("row").find((item) => within(item).queryByText(rule))!;
    expect(row("ENT-01")).toHaveTextContent("ENT-01Tested100 · 0 · 1—0.0000.000—");
    expect(row("EXC-03")).toHaveTextContent("No positive case, raised");
    expect(screen.getByText(/No positive case in the answers, and never raised \(34\)/)).toBeInTheDocument();  // never shown as perfect

    const versus = screen.getByRole("table", { name: "The engine against the keyword baseline on the restricted decision" });
    expect(within(versus).getAllByRole("row").find((item) => within(item).queryByText("F1"))).toHaveTextContent("F10.9090.333+0.576");
    expect(screen.getByText("Of 17 lines: both right on 11; the engine alone on 4; the baseline alone on 2; both wrong on 0.")).toBeInTheDocument();
    expect(within(screen.getByRole("table", { name: "The baseline is right, the engine wrong" })).getByText("Guinness 0.0 pint draught")).toBeInTheDocument();

    const failures = screen.getByRole("list", { name: "Diagnosed failures" });
    expect(within(failures).getAllByRole("listitem")).toHaveLength(2);
    expect(within(failures).getAllByRole("listitem")[0]).toHaveTextContent("Classified as alcohol instead of food_beverage.");
    expect(within(failures).getAllByRole("listitem")[0]).toHaveTextContent("Led to: ALC-01 was raised but not expected; disposition flag instead of pass.");
    expect(within(screen.getByRole("table", { name: "Expected dispositions by predicted ones, with receipts not evaluated" })).getAllByRole("row")[1]).toHaveTextContent("flag61(1 undecided)007");
  });

  it("says a run scored with an earlier version lacks the new sections, and rescores it on request", async () => {
    const earlier = { ...METRICS, scored: { kind: "original", original_schema_version: "evaluation-metrics/1", current_schema_version: "evaluation-metrics/3", rescorings: [] } };
    answer = metricsAt(earlier, (method, url) => (method === "POST" && url.pathname.endsWith("/rescore") ? { status: 200, body: RESCORED } : undefined));
    renderAt(`/evaluations/${DETAIL.evaluation_id}`);
    expect(await screen.findByText(/Scored with evaluation-metrics\/1 when the run finished\. The current scoring, evaluation-metrics\/3, adds/)).toBeInTheDocument();
    expect(screen.getByText(/The keyword baseline is not in this run's scoring \(evaluation-metrics\/1\)/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Rescore with evaluation-metrics/3" }));
    await waitFor(() => expect(calls.some((call) => call.method === "POST" && call.path === `/api/v1/evaluations/${DETAIL.evaluation_id}/rescore`)).toBe(true));
  });
});

describe("stale evaluation state", () => {
  function deferred() {
    let resolve!: (answer: Answer) => void;
    const promise = new Promise<Answer>((done) => (resolve = done));
    return { promise, resolve };
  }

  function wrapper() {
    const client = createQueryClient();
    return { client, wrapper: ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider> };
  }

  const id = QUEUED.evaluation_id;
  const at = (status: EvaluationDetail["status"], updated_at: string): EvaluationDetail => ({ ...QUEUED, status, updated_at });

  it("keeps the newer job state when an older poll answers last", async () => {
    const slow = deferred();
    let polls = 0;
    answer = (_, url) => {
      if (url.pathname !== `/api/v1/evaluations/${id}`) return undefined;
      polls += 1;
      return polls === 1 ? { status: 200, body: at("running", "2026-11-07T10:00:00Z") } : slow.promise;
    };
    const { client, wrapper: Wrapper } = wrapper();
    const { result } = renderHook(() => useEvaluation(id), { wrapper: Wrapper });
    await waitFor(() => expect(result.current.data?.status).toBe("running"));

    const refetch = result.current.refetch();
    act(() => client.setQueryData(queryKeys.evaluation(id), at("completed", "2026-11-07T10:00:05Z")));
    slow.resolve({ status: 200, body: at("running", "2026-11-07T10:00:01Z") });
    await act(() => refetch);
    expect(client.getQueryData<EvaluationDetail>(queryKeys.evaluation(id))?.status).toBe("completed");
    await waitFor(() => expect(result.current.data?.status).toBe("completed"));
  });

  it("keeps a cancel's answer when a poll sent before it answers after it", async () => {
    const slow = deferred();
    let polls = 0;
    answer = (method, url) => {
      if (method === "POST" && url.pathname === `/api/v1/evaluations/${id}/cancel`) return { status: 200, body: at("cancel_requested", "2026-11-07T10:00:05Z") };
      if (url.pathname !== `/api/v1/evaluations/${id}`) return undefined;
      polls += 1;
      return polls === 1 ? { status: 200, body: at("running", "2026-11-07T10:00:00Z") } : slow.promise;
    };
    const { client, wrapper: Wrapper } = wrapper();
    const { result } = renderHook(() => ({ job: useEvaluation(id), cancel: useCancelEvaluation() }), { wrapper: Wrapper });
    await waitFor(() => expect(result.current.job.data?.status).toBe("running"));

    act(() => void result.current.job.refetch());
    await act(() => result.current.cancel.mutateAsync(id));
    // The stale poll answers last; its state update happens inside act, as every update in a test must.
    await act(async () => {
      slow.resolve({ status: 200, body: at("running", "2026-11-07T10:00:01Z") });
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(client.getQueryData<EvaluationDetail>(queryKeys.evaluation(id))?.status).toBe("cancel_requested");
    expect(result.current.job.data?.status).toBe("cancel_requested");
  });
});

it("downloads the comparison PDF in the selected baseline order", async () => {
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => "blob:pdf", revokeObjectURL: () => {} }));
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  const ids = COMPARE.evaluations.map(run => run.evaluation_id);
  answer = (method, url) => method === "POST" && url.pathname === "/api/v1/evaluations/compare" ? { status: 200, body: COMPARE } : undefined;
  renderAt(`/evaluations/compare?ids=${ids.join(",")}`);
  const button = await screen.findByRole("button", { name: "Export PDF" });
  await waitFor(() => expect(button).toBeEnabled());
  await userEvent.setup().click(button);
  await waitFor(() => expect(exportViewPdf).toHaveBeenCalledWith("evaluation-comparison"));
  expect(calls.some(call => call.path.includes("/compare/export"))).toBe(false);
  expect(JSON.parse(posts("/api/v1/evaluations/compare")[0]!.body!)).toEqual({ evaluation_ids: ids });
  click.mockRestore();
});

it("exports evaluation PDF with the selected original scoring view", async () => {
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => "blob:pdf", revokeObjectURL: () => {} }));
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  answer = (_, url) => {
    if (url.pathname === `/api/v1/evaluations/${DETAIL.evaluation_id}`) return { status: 200, body: DETAIL };
    if (url.pathname.endsWith("/metrics")) return { status: 200, body: { ...METRICS, scored: { kind: "rescored", original_schema_version: "evaluation-metrics/1", current_schema_version: METRICS.schema_version, rescorings: [{ schema_version: METRICS.schema_version, rescored_at: DETAIL.created_at }] } } };
    return undefined;
  };
  renderAt(`/evaluations/${DETAIL.evaluation_id}`);
  await userEvent.setup().click(await screen.findByRole("button", { name: /Show as first scored/ }));
  await userEvent.setup().click(screen.getByRole("button", { name: "Export PDF" }));
  await waitFor(() => expect(exportViewPdf).toHaveBeenCalledWith(DETAIL.evaluation_id));
  expect(calls.some(call => call.path === `/api/v1/evaluations/${DETAIL.evaluation_id}/metrics?scoring=original`)).toBe(true);
  expect(calls.some(call => call.path.includes("/export?"))).toBe(false);
  click.mockRestore();
});
