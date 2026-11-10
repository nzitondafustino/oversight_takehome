import { exportViewPdf } from "@/lib/exportViewPdf";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Providers, routes } from "@/App";
import { createQueryClient } from "@/api/queries";
import type { Backends, Disposition, ReceiptResult, RulesetSummary, RunResponse } from "@/api/types";
import { highlightJson } from "@/components/results/JsonViewer";
import { lineFindings, lineStatus } from "@/components/results/lineStatus";
import { page, run as runSummary } from "./fixtures";
import backendsJson from "./fixtures/backends.json";
import findingsJson from "./fixtures/findings.json";
import runJson from "./fixtures/run.json";

/** Real API responses, recorded from the replay stack: the ui-demo run of RCP-013 and RCP-014. */
const RUN = runJson as unknown as RunResponse;
const BACKENDS = backendsJson as unknown as Backends;

/** Discovery as an API with EXPENSE_API_ALLOW_LIVE, a provider key and an allowed model would report it. */
const LIVE_BACKENDS: Backends = {
  ...BACKENDS,
  execution_modes: { ...BACKENDS.execution_modes, live: true },
  credential_configured: true,
  allowed_models: ["typesafe/jev-1.14"],
  allowed_classification_models: ["typesafe/jev-1.14"],
  allowed_translation_models: [],
  backends: BACKENDS.backends.map((backend) => ({
    ...backend,
    allowed_models: backend.id === "decisions" ? ["typesafe/jev-1.14"] : [],
    live: { permitted: true, credential_configured: true, available: true },
  })),
  classification_models: BACKENDS.classification_models.map((item) => ({ ...item, allowed: item.model === "typesafe/jev-1.14" })),
};

function ruleset(overrides: Partial<RulesetSummary>): RulesetSummary {
  return {
    ruleset_id: "rs_prod", name: "policies", version: 2, status: "published", ruleset_version: "v1", schema_version: "1", artifact_sha256: "a",
    rule_count: 41, active_rule_count: 41, source: { kind: "upload", description: null, reference: null }, compilation: null, review: null, based_on_version: null, approval_reference: null, approval_mode: "production", publication_mode: "production",
    created_at: "2026-11-06T00:00:00Z", submitted_at: null, approved_at: null, published_at: "2026-11-06T00:00:00Z", ...overrides,
  };
}

const RULESETS = [ruleset({ ruleset_id: "rs_draft", version: 3, status: "draft", publication_mode: null }), ruleset({})];

/** A run of one receipt, and a batch whose API order is pass, flag, review, flag: copies of real results. */
function singleRun(): RunResponse {
  const run = structuredClone(RUN);
  run.results.results = [run.results.results[1]!];
  return run;
}

function batchRun(): RunResponse {
  const run = structuredClone(RUN);
  const template = run.results.results[1]!;
  const make = (id: string, disposition: Disposition) => ({ ...structuredClone(template), receipt_id: id, disposition, line_items: template.line_items.map((line) => ({ ...line, line_id: line.line_id.replace("RCP-014", id) })) });
  // A pass the engine fully decided: nothing against it and nothing open.
  const cleared = { ...make("R-1", "pass"), decided: true, exceptions: [], review_signals: [], open_questions: [], uncertainty: [], disposition_reasons: [{ code: "nothing_found", basis: "EXC-01", rule_ids: [] }] };
  run.results.results = [cleared, make("R-2", "flag"), make("R-3", "review"), make("R-4", "flag")];
  return run;
}

/** One receipt with nothing against it and one rule still open, as the engine returns it: a provisional pass. */
function provisionalRun(): RunResponse {
  const run = singleRun();
  const result = run.results.results[0]!;
  run.results.results = [
    {
      ...result,
      disposition: "pass",
      decided: false,
      exceptions: [],
      review_signals: [],
      uncertainty: [],
      open_questions: [{ rule_id: "TIME-03", line_ids: [], reasons: ["other receipts at this merchant on this date may be missing"] }],
      disposition_reasons: [
        { code: "nothing_found", basis: "EXC-01", rule_ids: [] },
        { code: "incomplete_evidence", basis: "project_decision", rule_ids: ["TIME-03"] },
      ],
    },
  ];
  return run;
}

type Answer = { status: number; body: unknown; headers?: Record<string, string>; raw?: string };
type Handler = (url: URL, init?: RequestInit) => Answer | Promise<Answer> | undefined;
let handler: Handler;
let requests: { method: string; path: string; body?: string }[];

vi.mock("@/lib/exportViewPdf", () => ({ exportViewPdf: vi.fn(async () => undefined) }));

beforeEach(() => {
  requests = [];
  vi.mocked(exportViewPdf).mockClear();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      requests.push({ method: init?.method ?? "GET", path: `${url.pathname}${url.search}`, body: typeof init?.body === "string" ? init.body : undefined });
      const answer =
        (await handler(url, init)) ??
        (url.pathname === "/api/health"
          ? { status: 200, body: { status: "ok", application: "running", database: "ok", version: "0.1.0" } }
          : url.pathname === "/api/v1/backends"
            ? { status: 200, body: BACKENDS }
            : url.pathname === "/api/v1/rulesets"
              ? { status: 200, body: page(RULESETS) }
              : { status: 200, body: page([]) });
      return new Response(answer.raw ?? JSON.stringify(answer.body), { status: answer.status, headers: { "Content-Type": "application/json", ...answer.headers } });
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderAt(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(
    <Providers client={createQueryClient()}>
      <RouterProvider router={router} />
    </Providers>,
  );
  return router;
}

const currentStep = () => within(screen.getByRole("list", { name: "Validation steps" })).queryAllByRole("listitem").find((item) => item.getAttribute("aria-current") === "step") ?? null;
const submitButton = () => screen.getByRole("button", { name: /^Validat/ });
const validatesWith = (run: RunResponse): Handler => (url) => (url.pathname === "/api/v1/validate" ? { status: 201, body: run } : undefined);

async function loadDemo() {
  await userEvent.click(await screen.findByRole("button", { name: "Load demo receipts" }));
}

async function validateDemo() {
  await loadDemo();
  await waitFor(() => expect(submitButton()).toBeEnabled());
  await userEvent.click(submitButton());
}

describe("configuration", () => {
  it("defaults to replay and the published production version, and refuses others", async () => {
    handler = () => undefined;
    renderAt("/validate");
    const select = await screen.findByLabelText("Ruleset version");
    expect(select).toHaveValue("rs_prod");
    expect(within(select).getByRole("option", { name: /policies v3 · draft: not published/ })).toBeDisabled();
    expect(screen.getByRole("radio", { name: /Replay/ })).toBeChecked();
    expect(screen.getByLabelText("Orchestrator")).toHaveValue("direct");
    expect(screen.getByLabelText("Provider")).toHaveValue("openrouter");
    expect(screen.getByLabelText("Classification model")).toHaveValue("");
    expect(within(screen.getByLabelText("Classification model")).getByRole("option", { name: "Default: anthropic/claude-haiku-5.5 · Chat completions adapter" })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /complete context/ })).toBeChecked();
  });

  it("directs to Policy Management when no version is published for production", async () => {
    handler = (url) => (url.pathname === "/api/v1/rulesets" ? { status: 200, body: page([RULESETS[0]]) } : undefined);
    renderAt("/validate");
    expect(await screen.findByText("No ruleset published for production")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to Policy Management" })).toHaveAttribute("href", "/policies");
  });

  it("offers Jev as a model through OpenRouter, with its adapter and replay set following from it", async () => {
    handler = (url) => (url.pathname === "/api/v1/validate" ? { status: 201, body: RUN } : undefined);
    renderAt("/validate");
    await loadDemo();
    expect(within(await screen.findByLabelText("Provider")).getAllByRole("option").map((option) => option.textContent)).toEqual(["OpenRouter"]);
    await userEvent.selectOptions(screen.getByLabelText("Classification model"), "typesafe/jev-1.14");
    expect(screen.getByText("Runs through OpenRouter's Decisions API, chosen from the model.")).toBeInTheDocument();
    expect(screen.getByLabelText("Replay set")).toHaveValue("jev");
    expect(submitButton()).toBeEnabled();
    await userEvent.click(submitButton());
    await screen.findByText(RUN.run_id);
    const body = JSON.parse(requests.find((request) => request.method === "POST")!.body!);
    // The adapter by its own name; the deployment's replay set keeps the adapter's earlier name, jev.
    expect(body.classification).toEqual({ backend: "decisions", model: "typesafe/jev-1.14", replay_set: "jev" });
  });

});

/** Discovery with LangGraph's reflection on, as config.yaml would configure it. */
function withReflection(backends: Backends): Backends {
  return {
    ...backends,
    backends: backends.backends.map((backend) =>
      backend.id === "langgraph" ? { ...backend, capabilities: { ...backend.capabilities, reflection: { ...(backend.capabilities.reflection as object), enabled: true } } } : backend,
    ),
  };
}

/** Discovery with the deployment's reflection maximums and structured revisers, as the API reports them. */
function withLimits(backends: Backends, structuredRevisers = ["chat_completions"]): Backends {
  return {
    ...backends,
    backends: backends.backends.map((backend) =>
      backend.id === "langgraph"
        ? { ...backend, capabilities: { ...backend.capabilities, reflection: { ...(backend.capabilities.reflection as object), feedback_context: "reviewer_feedback", limits: { max_retries: 3, max_total_calls_per_line: 12 }, structured_revisers: structuredRevisers } } }
        : backend,
    ),
  };
}

describe("LangGraph node models", () => {
  const primary = () => screen.getByLabelText("1. Primary model");
  const fallback = () => screen.getByLabelText("2. Fallback model");
  const postedClassification = () => JSON.parse(requests.find((request) => request.method === "POST")!.body!).classification;

  async function chooseLangGraph() {
    await loadDemo();
    await userEvent.selectOptions(await screen.findByLabelText("Orchestrator"), "langgraph");
  }

  it("offers a model per configured node, each with its provider and adapter, and sends none it was not given", async () => {
    handler = validatesWith(singleRun());
    renderAt("/validate");
    await chooseLangGraph();
    expect(screen.queryByLabelText("Classification model")).not.toBeInTheDocument();
    // Each node starts on its configured model; every option names its provider and adapter.
    expect(primary()).toHaveValue("");
    expect(within(primary()).getByRole("option", { name: /^Configured: typesafe\/jev-1.14 · OpenRouter · Decisions API/ })).toBeInTheDocument();
    expect(within(primary()).getByRole("option", { name: /^anthropic\/claude-haiku-5.5 · OpenRouter · Chat completions/ })).toBeInTheDocument();
    expect(within(fallback()).getByRole("option", { name: /^Configured: anthropic\/claude-haiku-5.5 · OpenRouter · Chat completions/ })).toBeInTheDocument();
    // The translator is never offered to a node.
    expect(screen.queryByRole("option", { name: /tencent\/hy-mt2/ })).not.toBeInTheDocument();
    expect(screen.getByText(/Reflection is off/)).toBeInTheDocument();

    await userEvent.click(submitButton());
    await screen.findByText(RUN.run_id);
    // Nothing changed: the deployment's LangGraph configuration runs exactly, so no node list is sent.
    expect(postedClassification()).toEqual({ backend: "langgraph", replay_set: "langgraph" });
  });

  it("changes Primary and Fallback independently, each node keeping its name and taking its model's adapter", async () => {
    handler = validatesWith(singleRun());
    renderAt("/validate");
    await chooseLangGraph();

    await userEvent.selectOptions(primary(), "anthropic/claude-haiku-5.5");
    expect(fallback()).toHaveValue("");  // the other node is left alone
    expect(screen.getByText("OpenRouter · Chat completions, from the model", { selector: "#validate-node-primary-help" })).toBeInTheDocument();
    await userEvent.selectOptions(fallback(), "typesafe/jev-1.14");
    expect(screen.getByText("OpenRouter · Decisions API, from the model", { selector: "#validate-node-fallback-help" })).toBeInTheDocument();

    await userEvent.click(submitButton());
    await screen.findByText(RUN.run_id);
    // The whole node list, in its configured order and names; each adapter is the one its model needs.
    expect(postedClassification()).toEqual({
      backend: "langgraph",
      replay_set: "langgraph",
      langgraph: { backends: [{ name: "primary", backend: "chat_completions", model: "anthropic/claude-haiku-5.5" }, { name: "fallback", backend: "decisions", model: "typesafe/jev-1.14" }] },
    });
  });

  it("sends the configured node unchanged beside one that changed, and nothing once both are back to configured", async () => {
    handler = validatesWith(singleRun());
    renderAt("/validate");
    await chooseLangGraph();
    await userEvent.selectOptions(fallback(), "typesafe/jev-1.14");
    await userEvent.selectOptions(fallback(), "");
    await userEvent.click(submitButton());
    await screen.findByText(RUN.run_id);
    expect(postedClassification()).toEqual({ backend: "langgraph", replay_set: "langgraph" });
  });

  it("disables a model its adapter cannot run in this mode, and a live model outside the allowlist", async () => {
    // No recorded Decisions API answers in any set: replay cannot run Jev on any node.
    const unrecorded = { ...BACKENDS, backends: BACKENDS.backends.map((b) => (b.id === "decisions" ? { ...b, replay: { ...b.replay, sets: b.replay.sets.map((set) => ({ ...set, present: false })) } } : b)) };
    handler = (url) => (url.pathname === "/api/v1/backends" ? { status: 200, body: unrecorded } : undefined);
    renderAt("/validate");
    await chooseLangGraph();
    expect(within(fallback()).getByRole("option", { name: /^typesafe\/jev-1.14 · OpenRouter · Decisions API · No recorded answers/ })).toBeDisabled();
    // The configured model is held to the same check.
    expect(within(primary()).getByRole("option", { name: /^Configured: typesafe\/jev-1.14 .* · No recorded answers/ })).toBeDisabled();
  });

  it("refuses a live run whose node model is not allowed for classification", async () => {
    handler = (url) => (url.pathname === "/api/v1/backends" ? { status: 200, body: LIVE_BACKENDS } : undefined);
    renderAt("/validate");
    await chooseLangGraph();
    await userEvent.click(screen.getByRole("radio", { name: /Live/ }));
    // Only Jev is allowed live: Haiku is disabled for every node, configured or not.
    expect(within(primary()).getByRole("option", { name: /^anthropic\/claude-haiku-5.5 .* · Not allowed for live use/ })).toBeDisabled();
    expect(within(fallback()).getByRole("option", { name: /^Configured: anthropic\/claude-haiku-5.5 .* · Not allowed for live use/ })).toBeDisabled();
  });

  it("says which nodes can revise when reflection is on, never assuming every model can", async () => {
    handler = (url) => (url.pathname === "/api/v1/backends" ? { status: 200, body: withReflection(BACKENDS) } : undefined);
    renderAt("/validate");
    await chooseLangGraph();
    expect(screen.getByText(/Reflection is on: anthropic\/claude-haiku-5.5 reviews \(Chat completions\)\./)).toBeInTheDocument();
    expect(screen.getByText(/Cannot revise: another node revises its answers/, { selector: "#validate-node-primary-help" })).toBeInTheDocument();
    expect(screen.getByText(/Revises with the reviewer's feedback/, { selector: "#validate-node-fallback-help" })).toBeInTheDocument();
    // With Jev on both nodes, no node's adapter can revise, and the page says what the core then does.
    await userEvent.selectOptions(fallback(), "typesafe/jev-1.14");
    expect(screen.getByText(/No node's adapter can revise, so an objection sends the line on to the next node\./)).toBeInTheDocument();
  });

  it("explains that a changed node replays only its own model's recordings, and shows the refusal when they are missing", async () => {
    handler = (url) =>
      url.pathname === "/api/v1/validate" ? { status: 409, body: { error: { code: "replay_recording_missing", message: "3 answers are not in the replay cache", details: { missing_answers: 3 } } } } : undefined;
    renderAt("/validate");
    await chooseLangGraph();
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
    await userEvent.selectOptions(primary(), "anthropic/claude-haiku-5.5");
    expect(screen.getByRole("note")).toHaveTextContent(
      "this replay reads only anthropic/claude-haiku-5.5's answers for the primary node. A node with no recorded answer for a line counts as a failed call: the line goes on to the next node, and its audit says so. A line no node has an answer for refuses the run as missing recordings. Nothing is called live.",
    );
    await userEvent.click(submitButton());
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("replay_recording_missing");
    expect(postedClassification().langgraph.backends[0]).toEqual({ name: "primary", backend: "chat_completions", model: "anthropic/claude-haiku-5.5" });
  });

  it("sends only the reflection settings changed for this run, within the deployment's maximums", async () => {
    handler = (url) => (url.pathname === "/api/v1/backends" ? { status: 200, body: withLimits(BACKENDS) } : validatesWith(singleRun())(url));
    renderAt("/validate");
    await chooseLangGraph();
    const enable = screen.getByRole("checkbox", { name: /Review each answer, and revise it/ });
    expect(enable).not.toBeChecked();
    expect(screen.queryByLabelText("Revision retries")).not.toBeInTheDocument();
    await userEvent.click(enable);
    // The deployment's defaults and maximums, from discovery.
    expect(screen.getByLabelText("Revision retries")).toHaveAttribute("placeholder", "2");
    expect(screen.getByText("0 to 3; 0 revises nothing. Default 2.")).toBeInTheDocument();
    expect(screen.getByText(/^1 to 12: every classification, review, fallback and revision/)).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText("Revision retries"), "4");
    expect(screen.getByRole("alert")).toHaveTextContent("Revision retries must be a whole number from 0 to 3.");
    expect(submitButton()).toBeDisabled();
    await userEvent.clear(screen.getByLabelText("Revision retries"));
    await userEvent.type(screen.getByLabelText("Revision retries"), "0");  // zero: no revisions at all
    await userEvent.type(screen.getByLabelText("Model calls per line"), "4");
    await userEvent.click(screen.getByRole("checkbox", { name: /Structured feedback context/ }));
    await userEvent.click(submitButton());
    await screen.findByText(RUN.run_id);
    expect(postedClassification()).toEqual({
      backend: "langgraph",
      replay_set: "langgraph",
      langgraph: { reflection: { enabled: true, max_retries: 0, max_total_calls_per_line: 4, feedback_context: "structured" } },
    });
  });

  it("sends nothing for reflection left as the deployment configures it", async () => {
    handler = (url) => (url.pathname === "/api/v1/backends" ? { status: 200, body: withLimits(withReflection(BACKENDS)) } : validatesWith(singleRun())(url));
    renderAt("/validate");
    await chooseLangGraph();
    expect(screen.getByRole("checkbox", { name: /Review each answer, and revise it/ })).toBeChecked();
    await userEvent.type(screen.getByLabelText("Revision retries"), "2");  // the default, typed: no change
    await userEvent.click(submitButton());
    await screen.findByText(RUN.run_id);
    expect(postedClassification()).toEqual({ backend: "langgraph", replay_set: "langgraph" });
  });

  it("says which nodes can revise with structured context, from the revisers discovery names", async () => {
    const only = withLimits(withReflection(BACKENDS), []);  // a deployment whose reviser takes no structured context
    handler = (url) => (url.pathname === "/api/v1/backends" ? { status: 200, body: only } : undefined);
    renderAt("/validate");
    await chooseLangGraph();
    expect(screen.getByText(/Revises with the reviewer's feedback/, { selector: "#validate-node-fallback-help" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: /Structured feedback context/ }));
    expect(screen.getByText(/Cannot revise/, { selector: "#validate-node-fallback-help" })).toBeInTheDocument();
    expect(screen.getByText(/No node's adapter can revise/)).toBeInTheDocument();
  });

  it("clears node models when the orchestrator changes", async () => {
    handler = () => undefined;
    renderAt("/validate");
    await chooseLangGraph();
    await userEvent.selectOptions(primary(), "anthropic/claude-haiku-5.5");
    await userEvent.selectOptions(screen.getByLabelText("Orchestrator"), "direct");
    await userEvent.selectOptions(screen.getByLabelText("Orchestrator"), "langgraph");
    expect(primary()).toHaveValue("");
  });
});

describe("live mode follows the API's capability response", () => {
  it("stays disabled, with the API's reason, when the deployment does not allow it", async () => {
    handler = () => undefined;
    renderAt("/validate");
    expect(await screen.findByRole("radio", { name: /Live/ })).toBeDisabled();
    expect(screen.getByText(/Live unavailable: This deployment does not allow live inference/)).toBeInTheDocument();
  });

  it("is selectable when the API permits it, and still needs confirmation every time", async () => {
    handler = (url) => (url.pathname === "/api/v1/backends" ? { status: 200, body: LIVE_BACKENDS } : undefined);
    renderAt("/validate");
    await loadDemo();
    const live = await screen.findByRole("radio", { name: /Live/ });
    expect(live).toBeEnabled();
    expect(screen.queryByText(/Live unavailable/)).not.toBeInTheDocument();
    await userEvent.click(live);
    expect(submitButton()).toBeDisabled();
    expect(screen.getByText("Confirm that live validation may incur provider charges.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: /calls paid providers/ }));
    expect(submitButton()).toHaveTextContent("Validate 2 receipts live");
    // Switching away and back asks again.
    await userEvent.click(screen.getByRole("radio", { name: /Replay/ }));
    await userEvent.click(live);
    expect(screen.getByRole("checkbox", { name: /calls paid providers/ })).not.toBeChecked();
    expect(requests.some((request) => request.method === "POST")).toBe(false);
  });

  it("disables a model whose adapter the API reports unavailable live, without guessing", async () => {
    const partial = { ...LIVE_BACKENDS, backends: LIVE_BACKENDS.backends.map((b) => (b.id === "chat_completions" ? { ...b, live: { permitted: true, credential_configured: false, available: false } } : b)) };
    handler = (url) => (url.pathname === "/api/v1/backends" ? { status: 200, body: partial } : undefined);
    renderAt("/validate");
    await userEvent.click(await screen.findByRole("radio", { name: /Live/ }));
    const select = screen.getByLabelText("Classification model");
    expect(within(select).getByRole("option", { name: "typesafe/jev-1.14 · Decisions API adapter" })).toBeEnabled();
    expect(within(select).getByRole("option", { name: "Default: anthropic/claude-haiku-5.5 · Chat completions adapter · No provider credential configured" })).toBeDisabled();
  });

  it("offers only allowed models in live mode, and sends the live request once confirmed", async () => {
    handler = (url) => {
      if (url.pathname === "/api/v1/backends") return { status: 200, body: LIVE_BACKENDS };
      if (url.pathname === "/api/v1/validate") return { status: 201, body: RUN };
      return undefined;
    };
    renderAt("/validate");
    await loadDemo();
    await userEvent.click(await screen.findByRole("radio", { name: /Live/ }));
    expect(within(screen.getByLabelText("Classification model")).getByRole("option", { name: /anthropic\/claude-haiku-5.5 · Chat completions adapter$/ })).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Classification model"), "typesafe/jev-1.14");
    await userEvent.click(screen.getByRole("checkbox", { name: /calls paid providers/ }));
    await userEvent.click(submitButton());
    await screen.findByText(RUN.run_id);
    const body = JSON.parse(requests.find((request) => request.method === "POST")!.body!);
    expect(body).toMatchObject({ execution_mode: "live", classification: { backend: "decisions", model: "typesafe/jev-1.14" } });
    expect(body.classification.replay_set).toBeUndefined();
  });
});

describe("input", () => {
  it("keeps the text when it is invalid, and blocks submission with the problem", async () => {
    handler = () => undefined;
    renderAt("/validate");
    const editor = await screen.findByLabelText("Receipt JSON");
    fireEvent.change(editor, { target: { value: '[{"receipt_id": "R1",]' } });
    expect(await screen.findByText("Fix the input before validating")).toBeInTheDocument();
    expect(editor).toHaveValue('[{"receipt_id": "R1",]');
    expect(submitButton()).toBeDisabled();
  });

  it("reads a JSONL file, shows its name and count, and removes it", async () => {
    handler = () => undefined;
    renderAt("/validate");
    await userEvent.click(await screen.findByRole("tab", { name: /Upload file/ }));
    const lines = ['{"receipt_id": "A", "merchant_name": "Alpha", "line_items": [{"line_id": "A-1"}], "labels": {"disposition": "pass"}}', '{"receipt_id": "B", "line_items": []}'];
    await userEvent.upload(screen.getByLabelText("Choose a JSON or JSONL file"), new File([lines.join("\n")], "batch.jsonl"));
    expect(await screen.findByText("batch.jsonl")).toBeInTheDocument();
    expect(screen.getByText(/2 receipts read/)).toBeInTheDocument();
    expect(screen.getByText(/Ground-truth answers will not be sent/)).toBeInTheDocument();
    expect(screen.getByText("Alpha")).toBeInTheDocument();
    expect(screen.getAllByText("Unavailable").length).toBeGreaterThan(0);
    await userEvent.click(screen.getByRole("button", { name: /Remove/ }));
    expect(screen.getByText("Drop a .json or .jsonl file here")).toBeInTheDocument();
  });

  it("refuses files that are not JSON or JSONL", async () => {
    handler = () => undefined;
    renderAt("/validate");
    await userEvent.click(await screen.findByRole("tab", { name: /Upload file/ }));
    fireEvent.change(screen.getByLabelText("Choose a JSON or JSONL file"), { target: { files: [new File(["%PDF"], "receipt.pdf")] } });
    expect(await screen.findByText(/receipt.pdf is not a .json or .jsonl file/)).toBeInTheDocument();
  });

  it("names the line of a broken JSONL record", async () => {
    handler = () => undefined;
    renderAt("/validate");
    await userEvent.click(await screen.findByRole("tab", { name: /Upload file/ }));
    await userEvent.upload(screen.getByLabelText("Choose a JSON or JSONL file"), new File(['{"receipt_id": "A", "line_items": []}\n{"receipt_id": '], "bad.jsonl"));
    expect(await screen.findByText("bad.jsonl cannot be validated as it is")).toBeInTheDocument();
    expect(screen.getByText(/line 2:/)).toBeInTheDocument();
  });
});

describe("submission", () => {
  it("sends one exact request, shows pending, then folds the intake and shows results", async () => {
    let release: (answer: Answer) => void = () => {};
    handler = (url, init) => (url.pathname === "/api/v1/validate" && init?.method === "POST" ? new Promise<Answer>((resolve) => (release = resolve)) : undefined);
    renderAt("/validate");
    await validateDemo();
    expect(await screen.findByRole("button", { name: /Validating/ })).toBeDisabled();
    const status = screen.getByRole("region", { name: "Validation status" });
    expect(within(status).getByRole("status")).toHaveTextContent("Validation in progress");
    expect(status).toHaveTextContent("2 receipts · 6 line items · policies v2");
    expect(status).toHaveTextContent("Elapsed 0s");
    expect(status).toHaveTextContent("Replay uses recorded responses; no paid provider calls.");
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Receipt JSON")).toBeDisabled();
    expect(screen.getByLabelText("Ruleset version")).toBeDisabled();
    expect(screen.getByLabelText("Orchestrator")).toBeDisabled();
    expect(screen.getByLabelText("Classification model")).toBeDisabled();
    expect(screen.getByRole("radio", { name: "Deterministic" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Load demo receipts" })).toBeDisabled();
    const submittedText = (screen.getByLabelText("Receipt JSON") as HTMLTextAreaElement).value;
    fireEvent.change(screen.getByLabelText("Receipt JSON"), { target: { value: "[]" } });
    expect(screen.getByLabelText("Receipt JSON")).toHaveValue(submittedText);
    await userEvent.click(screen.getByRole("button", { name: /Validating/ })); // a second click sends nothing
    const posts = requests.filter((request) => request.method === "POST");
    expect(posts).toHaveLength(1);
    expect(JSON.parse(posts[0]!.body!)).toMatchObject({ ruleset_id: "rs_prod", context_complete: true, execution_mode: "replay", classification: { backend: "chat_completions", replay_set: "openrouter" } });
    expect(posts[0]!.body).toContain('"fx_rate_to_usd":1.0');
    expect(posts[0]!.body).not.toMatch(/"labels"|"notes"/);

    release({ status: 201, body: RUN });
    expect(await screen.findByText(RUN.run_id)).toBeInTheDocument();
    // Results first: no banner, no steps and no intake; the outcome is in the results' own summary.
    expect(within(status).queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByText("Validation completed")).not.toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Validation steps" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Receipts and configuration/ })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Receipt JSON")).not.toBeInTheDocument();
    expect(screen.getByText("2 not fully evaluated")).toBeInTheDocument();
    // New validation brings back the steps and the intake, with the input kept.
    await userEvent.click(screen.getByRole("button", { name: "New validation" }));
    expect(screen.getByLabelText("Receipt JSON")).not.toHaveValue("");
    expect(screen.getByRole("list", { name: "Validation steps" })).toBeInTheDocument();
  });

  it("shows the API's refusal with its code and field paths, and no result", async () => {
    handler = (url) =>
      url.pathname === "/api/v1/validate" ? { status: 422, body: { detail: [{ loc: ["body", "receipts", 0, "line_items"], msg: "Field required", type: "missing" }] } } : undefined;
    renderAt("/validate");
    await validateDemo();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Validation failed (422): no result was produced");
    expect(alert).toHaveTextContent("receipts.0.line_items: Field required");
    expect(screen.queryByText(RUN.run_id)).not.toBeInTheDocument();
  });

  it("explains a missing replay recording", async () => {
    handler = (url) =>
      url.pathname === "/api/v1/validate" ? { status: 409, body: { error: { code: "replay_recording_missing", message: "6 answers are not in the replay cache", details: { missing_answers: 6 } } } } : undefined;
    renderAt("/validate");
    await validateDemo();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("replay_recording_missing");
    expect(alert).toHaveTextContent("Choose the set recorded for these receipts");
    expect(alert).toHaveTextContent('{"missing_answers":6}');
  });

  it("separates a failed rerun from the previous successful results", async () => {
    handler = validatesWith(RUN);
    renderAt("/validate");
    await validateDemo();
    await screen.findByText(RUN.run_id);
    expect(screen.queryByRole("list", { name: "Validation steps" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "New validation" }));
    handler = (url) => url.pathname === "/api/v1/validate"
      ? { status: 409, body: { error: { code: "replay_recording_missing", message: "One recorded answer is missing" } } }
      : undefined;
    await userEvent.click(submitButton());
    const alert = await screen.findByRole("alert");
    expect(within(screen.getByRole("region", { name: "Validation status" })).getByRole("alert")).toBe(alert);
    expect(currentStep()).toBeNull(); // a failed rerun is not a completed validation
    const previous = screen.getByText("Previous successful run · 2 receipts").closest("details")!;
    expect(previous).not.toHaveAttribute("open");
    expect(screen.getByRole("article", { name: "Receipt RCP-013" })).not.toBeVisible();
    fireEvent.click(previous.querySelector("summary")!);
    expect(await screen.findByRole("article", { name: "Receipt RCP-013" })).toBeVisible();
    expect(within(previous).getByText("These results belong to an earlier submission, not the current attempt.")).toBeVisible();
    expect(requests.filter((request) => request.method === "POST")).toHaveLength(2);
  });

  it("does not claim a network failure means the server produced no result or retry it", async () => {
    handler = (url) => {
      if (url.pathname === "/api/v1/validate") throw new TypeError("Connection lost");
      return undefined;
    };
    renderAt("/validate");
    await validateDemo();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Validation outcome could not be confirmed");
    expect(alert).toHaveTextContent("The server may have completed this request");
    expect(alert).not.toHaveTextContent("no result was produced");
    expect(within(alert).getByRole("link", { name: "Run History" })).toHaveAttribute("href", "/runs");
    expect(requests.filter((request) => request.method === "POST")).toHaveLength(1);
    expect(screen.getByLabelText("Receipt JSON")).toBeEnabled();
  });
});

describe("results", () => {
  const RCP014 = RUN.results.results[1]!;

  it("shows a single receipt in full, with no list, steps or intake", async () => {
    handler = validatesWith(singleRun());
    renderAt("/validate");
    await validateDemo();
    const detail = await screen.findByRole("article", { name: "Receipt RCP-014" });
    expect(screen.queryByRole("list", { name: "Receipts, flag first" })).not.toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Validation steps" })).not.toBeInTheDocument();
    expect(within(detail).queryByRole("button", { name: "Next receipt" })).not.toBeInTheDocument();
    expect(within(detail).getByRole("heading", { level: 2 })).toHaveTextContent("FreshMart Supermarket");
    expect(within(detail.querySelector("header")!).getByText("47.76")).toBeInTheDocument();
    expect(within(detail).getByText("Disposition:").parentElement).toHaveTextContent("Flag");
    const why = within(detail).getByRole("region", { name: "Why this result" });
    expect(why).toHaveTextContent("Policy exceptions that flag this receipt");
    for (const rule of ["ALC-03", "DOC-03", "HOUSE-01"]) expect(within(why).getByText(rule)).toBeInTheDocument();
    expect(why).toHaveTextContent("Not yet decided (not violations)");
    expect(within(why).getByText("TIME-03")).toBeInTheDocument();
  });

  it("keeps an undecided flag visibly undecided, and a decided one marked fully evaluated", async () => {
    const run = singleRun();
    handler = validatesWith(run);
    renderAt("/validate");
    await validateDemo();
    const completeness = await screen.findByLabelText("Evaluation completeness");
    expect(completeness).toHaveTextContent("Not fully evaluated: 1 open question, not violations, still need information.");
    expect(screen.getByText("1 not fully evaluated")).toBeInTheDocument();
  });

  it("shows a pass with open questions as provisional, never as cleared or as needing review", async () => {
    handler = validatesWith(provisionalRun());
    renderAt("/validate");
    await validateDemo();
    const detail = await screen.findByRole("article", { name: "Receipt RCP-014" });
    expect(within(detail).getByText("Disposition:").parentElement).toHaveTextContent("Provisional pass");
    expect(screen.getByLabelText("Evaluation completeness")).toHaveTextContent("this pass is provisional, not a clearance");
    const why = within(detail).getByRole("region", { name: "Why this result" });
    expect(why).toHaveTextContent("A provisional pass");
    expect(why).not.toHaveTextContent("every rule was decided");
    expect(why).not.toHaveTextContent("Needs review");
    expect(why).toHaveTextContent("Not yet decided (not violations)");
    expect(within(why).getByText("TIME-03")).toBeInTheDocument();
    expect(screen.getByText(/1 not fully evaluated/)).toHaveTextContent("1 not fully evaluated, 1 of them a provisional pass");
  });

  it("shows a fully decided pass as a plain pass", async () => {
    const run = provisionalRun();
    Object.assign(run.results.results[0]!, { decided: true, open_questions: [], disposition_reasons: [{ code: "nothing_found", basis: "EXC-01", rule_ids: [] }] });
    handler = validatesWith(run);
    renderAt("/validate");
    await validateDemo();
    const detail = await screen.findByRole("article", { name: "Receipt RCP-014" });
    expect(within(detail).getByText("Disposition:").parentElement).toHaveTextContent(/^Disposition: Pass$/);
    expect(within(detail).getByRole("region", { name: "Why this result" })).toHaveTextContent("No active findings, and every rule was decided.");
  });

  it("says fully evaluated only when the engine decided every rule", async () => {
    const run = singleRun();
    run.results.results[0]!.decided = true;
    run.results.results[0]!.open_questions = [];
    handler = validatesWith(run);
    renderAt("/validate");
    await validateDemo();
    expect(await screen.findByLabelText("Evaluation completeness")).toHaveTextContent("Fully evaluated: every rule was decided.");
    expect(screen.getByText("All fully evaluated")).toBeInTheDocument();
  });

  it("lists a batch flag, review, then pass with only ID and disposition, keeping the API's order within groups", async () => {
    handler = validatesWith(batchRun());
    renderAt("/validate");
    await validateDemo();
    const list = await screen.findByRole("list", { name: "Receipts, flag first" });
    const rows = within(list).getAllByRole("button");
    expect(rows.map((button) => button.getAttribute("aria-label"))).toEqual(["R-2: Flag", "R-4: Flag", "R-3: Review", "R-1: Pass"]);
    expect(list).not.toHaveTextContent(/exc|signal|Undecided|Decided|open/);
    expect(screen.getByRole("article", { name: "Receipt R-2" })).toBeInTheDocument();
  });

  it("never steps onto receipts the filters hide", async () => {
    handler = validatesWith(batchRun());
    const router = renderAt("/validate");
    await validateDemo();
    const filters = await screen.findByRole("group", { name: "Filter by disposition" });
    // The selected receipt (R-2, a flag) is filtered out: the detail follows the list, never a hidden receipt.
    await userEvent.click(within(filters).getByRole("button", { name: /Review/ }));
    expect(screen.getByRole("article", { name: "Receipt R-3" })).toBeInTheDocument();
    expect(screen.getByText("1 of 1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next receipt" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Previous receipt" })).toBeDisabled();

    await userEvent.click(within(filters).getByRole("button", { name: /Flag/ }));
    expect(screen.getByRole("article", { name: "Receipt R-2" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Next receipt" }));
    expect(screen.getByRole("article", { name: "Receipt R-4" })).toBeInTheDocument();
    expect(screen.getByText("2 of 2")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next receipt" })).toBeDisabled(); // R-3 and R-1 are hidden

    await userEvent.click(within(filters).getByRole("button", { name: /All/ }));
    await userEvent.type(screen.getByLabelText("Search receipts"), "R-1");
    expect(screen.getByRole("article", { name: "Receipt R-1" })).toBeInTheDocument();
    await userEvent.clear(screen.getByLabelText("Search receipts"));
    expect(screen.getByRole("article", { name: "Receipt R-1" })).toBeInTheDocument(); // the selection stays where the search left it
    screen.getByRole("button", { name: "R-1: Pass" }).focus();
    await userEvent.keyboard("{ArrowUp}");
    expect(screen.getByRole("article", { name: "Receipt R-3" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/validate");
  });

  it("offers a way back to the list on narrow screens", async () => {
    handler = validatesWith(batchRun());
    renderAt("/validate");
    await validateDemo();
    await userEvent.click(await screen.findByRole("button", { name: "R-3: Review" }));
    await userEvent.click(screen.getByRole("button", { name: "All receipts" }));
    expect(screen.getByRole("complementary", { name: "Receipts" })).not.toHaveClass("max-lg:hidden");
  });

  it("gives each item a status from the findings that name it, never from its category", () => {
    const status = (result: ReceiptResult, lineId: string) => lineStatus(lineFindings(result, lineId)).label;
    const [rcp013, rcp014] = RUN.results.results as [ReceiptResult, ReceiptResult];
    expect(["RCP-014-L1", "RCP-014-L2", "RCP-014-L3", "RCP-014-L4"].map((id) => status(rcp014, id))).toEqual(["Exception", "Exception", "Exception", "Exception"]);
    expect(status(rcp013, "RCP-013-L1")).toBe("Exception"); // GIFT-02 names it
    expect(status(rcp013, "RCP-013-L2")).toBe("Uncertain"); // an uncertain recipient type, and GIFT-04 still open
    // The same restricted alcohol line with no finding naming it has no item finding: the category is not a verdict.
    const noFindings: ReceiptResult = { ...rcp014, exceptions: [], open_questions: [], uncertainty: [] };
    expect(noFindings.line_items[3]!.restricted).toBe(true);
    expect(status(noFindings, "RCP-014-L4")).toBe("No item findings");
    expect(lineStatus({ exceptions: [], questions: [{ key: "q", question: { rule_id: "X", line_ids: ["a"], reasons: [] } }], uncertainty: [], classificationError: null }).label).toBe("Open question");
    expect(lineStatus({ exceptions: [], questions: [], uncertainty: [], classificationError: "timeout" }).label).toBe("Classification error");
  });

  it("keeps receipt-level findings at receipt level, and highlights only the items a finding names", async () => {
    handler = validatesWith(singleRun());
    renderAt("/validate");
    await validateDemo();
    const why = await screen.findByRole("region", { name: "Why this result" });
    const highlighted = () => [...document.querySelectorAll("[data-highlighted]")].map((element) => element.id);

    await userEvent.click(within(why).getByRole("button", { name: /HOUSE-01/ }));
    expect(highlighted()).toEqual(["line-RCP-014-RCP-014-L1", "line-RCP-014-RCP-014-L2", "line-RCP-014-RCP-014-L3"]);

    const doc03 = within(why).getByRole("button", { name: /DOC-03/ });
    expect(doc03).toHaveTextContent("Whole receipt");
    await userEvent.click(doc03);
    expect(highlighted()).toEqual([]);
    expect(screen.getByText("The selected finding applies to the whole receipt, not to one item.")).toBeInTheDocument();
    // A review signal names no lines: it is shown for the whole receipt, never on an item.
    expect(within(why).getByText("MCC-02").closest("li")).toHaveTextContent("Whole receipt · review signal, not an exception");
  });

  it("shows a LangGraph line's attempts, reviews and why it stopped", async () => {
    const run = singleRun();
    const line = run.results.results[0]!.line_items[0]!;
    line.confidence = null;
    line.classification_details = {
      orchestration: {
        answered_by: "fallback", result: "retries_exhausted", attempts: [
          { name: "primary", backend: "decisions", model: "typesafe/jev-1.14", outcome: "accepted", confidence: 0.95, error: null },
          { name: "fallback", backend: "chat_completions", model: "anthropic/claude-haiku-5.5", outcome: "error", confidence: null, error: "item_kind contradicts category", revision: 1, max_retries: 2, retried_validation_error: false },
          { name: "fallback", backend: "chat_completions", model: "anthropic/claude-haiku-5.5", outcome: "accepted", confidence: 0.9, error: null, revision: 2, max_retries: 2, retried_validation_error: true },
        ],
        reflection: {
          calls: 5, revisions: 2, unresolved: true, withheld_confidence: 0.9, feedback_context: "structured", stop_reason: "retries_exhausted", max_retries: 2, max_calls: 6, fallbacks: [],
          reviews: [{ of: "primary", revision: 0, reviewer: "chat_completions", model: "anthropic/claude-haiku-5.5", outcome: "verdict", answered: true, verdict: "revise", reason: "the line names a wine", suggested_category: "alcohol", feedback: "check whether this is a drink", error: null }],
        },
      },
    };
    handler = validatesWith(run);
    renderAt("/validate");
    await validateDemo();
    const items = await screen.findByRole("list", { name: "Expense items" });
    await userEvent.click(within(items).getByRole("button", { name: new RegExp(line.description_raw.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")) }));
    const history = within(items).getByRole("group", { name: "Classification attempts" });
    expect(history).toHaveTextContent("Stopped: Retry limit reached · 3 attempts · 5 model calls of 6 · 2 revisions of 2");
    expect(history).toHaveTextContent("Unresolved: confidence withheld");
    const attempts = within(history).getAllByRole("listitem");
    expect(attempts[0]).toHaveTextContent("Classification by primary · typesafe/jev-1.14 · accepted · confidence 0.95");
    expect(attempts[1]).toHaveTextContent("Revision 1 by fallback · anthropic/claude-haiku-5.5 · failed validationitem_kind contradicts category");
    expect(attempts[2]).toHaveTextContent("Revision 2 by fallback · anthropic/claude-haiku-5.5 · accepted · confidence 0.90 · retried after a failed revision");
    expect(attempts[3]).toHaveTextContent("Review of primary · revisethe line names a wineFeedback: check whether this is a drinkSuggested: alcohol");
  });

  it("reveals an item's classification and full evidence when expanded", async () => {
    handler = validatesWith(singleRun());
    renderAt("/validate");
    await validateDemo();
    const items = await screen.findByRole("list", { name: "Expense items" });
    const toggle = within(items).getByRole("button", { name: /Cooking wine - Marsala 375ml/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(within(items).queryByText(RCP014.exceptions[0]!.evidence)).not.toBeInTheDocument();
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const panel = document.getElementById(toggle.getAttribute("aria-controls")!)!;
    expect(panel).toHaveTextContent("alcohol");
    expect(panel).toHaveTextContent("Confidence0.90");
    expect(within(panel).getByText("ALC-03")).toBeInTheDocument();
    expect(within(panel).getByText(RCP014.exceptions[0]!.evidence)).toBeInTheDocument();
    expect(within(panel).getByText(RCP014.exceptions[0]!.source_quote)).toBeInTheDocument();
  });

  it("keeps technical details collapsed, and every finding and field available", async () => {
    handler = validatesWith(RUN);
    renderAt("/validate");
    await validateDemo();
    await screen.findByRole("article", { name: "Receipt RCP-013" });
    const runDetails = screen.getByText("Run details").closest("details")!;
    expect(runDetails).not.toHaveAttribute("open");
    for (const value of [RUN.run_id, RUN.ruleset.artifact_sha256, "Incomplete: cross-receipt checks may stay open", "core 0.1.0"]) expect(runDetails).toHaveTextContent(value);
    expect(within(runDetails).getByRole("button", { name: "Export JSON" })).toBeInTheDocument();

    for (const receipt of ["RCP-013", "RCP-014"]) {
      await userEvent.click(screen.getByRole("button", { name: new RegExp(`^${receipt}`) }));
      const detail = screen.getByRole("article", { name: `Receipt ${receipt}` });
      const result = RUN.results.results.find((item) => item.receipt_id === receipt)!;
      const full = within(detail).getByText("Full policy findings").closest("details")!;
      expect(full).not.toHaveAttribute("open");
      // Nothing the engine returned is dropped: every finding's text and evidence is still on the page.
      for (const exception of result.exceptions) for (const text of [exception.explanation, exception.evidence, exception.source_quote, exception.policy_doc]) expect(full).toHaveTextContent(text);
      for (const signal of result.review_signals) expect(full).toHaveTextContent(signal.reason);
      for (const question of result.open_questions) for (const reason of question.reasons) expect(full).toHaveTextContent(reason);
      for (const item of result.uncertainty) expect(full).toHaveTextContent(item.reason);
      for (const line of result.line_items) expect(detail).toHaveTextContent(line.description_raw);
      const provenance = within(detail).getByText("Receipt fields and classification provenance").closest("details")!;
      expect(provenance).toHaveTextContent(result.metadata.model!);
      expect(provenance).toHaveTextContent(result.provenance.ruleset_sha256);
    }
  });
});

describe("JSON view", () => {
  // The export as the API sends it: the run without "results", each receipt under "receipts", and a number
  // written as 0.90 that the view must keep exactly.
  const { results, ...runFields } = RUN;
  const EXPORT = { export_schema: "run-export/2", ...runFields, receipts: results.results };
  const RAW = JSON.stringify(EXPORT).replace(/"confidence":0\.9\b/, '"confidence":0.90');
  const exportHandler: Handler = (url) => {
    if (url.pathname === "/api/v1/validate") return { status: 201, body: RUN };
    if (url.pathname === `/api/v1/runs/${RUN.run_id}/export`) return { status: 200, body: null, raw: RAW };
    return undefined;
  };
  const exportCalls = () => requests.filter((request) => request.path.startsWith(`/api/v1/runs/${RUN.run_id}/export`));

  it("loads the stored run's JSON only when opened, and shows it exactly", async () => {
    handler = exportHandler;
    renderAt("/validate");
    await validateDemo();
    await screen.findByRole("article", { name: "Receipt RCP-013" });
    expect(exportCalls()).toHaveLength(0);

    await userEvent.click(screen.getByText("Run details"));
    await userEvent.click(screen.getByText("Run JSON"));
    const view = await screen.findByLabelText(`JSON of run ${RUN.run_id}`);
    expect(exportCalls().map((call) => call.path)).toEqual([`/api/v1/runs/${RUN.run_id}/export?format=json`]);
    expect(view).toHaveTextContent('"confidence": 0.90'); // not rounded to 0.9
    expect(view).toHaveTextContent(`"run_id": "${RUN.run_id}"`);
    expect([...view.querySelectorAll(".text-primary")].map((key) => key.textContent)).toContain('"run_id"'); // keys are highlighted
  });

  it("shows one receipt's result on its own, from the same single fetch", async () => {
    handler = exportHandler;
    renderAt("/validate");
    await validateDemo();
    const detail = await screen.findByRole("article", { name: "Receipt RCP-013" });
    await userEvent.click(within(detail).getByText("Result JSON"));
    const view = await screen.findByLabelText("JSON of receipt RCP-013");
    expect(view).toHaveTextContent('"receipt_id": "RCP-013"');
    expect(view).not.toHaveTextContent("RCP-014");
    expect(view).not.toHaveTextContent('"run_id"');

    await userEvent.click(screen.getByText("Run details"));
    await userEvent.click(screen.getByText("Run JSON"));
    await screen.findByLabelText(`JSON of run ${RUN.run_id}`);
    expect(exportCalls()).toHaveLength(1); // a stored run never changes: fetched once
  });

  it("copies the shown JSON", async () => {
    const writeText = vi.fn((_text: string) => Promise.resolve());
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    handler = exportHandler;
    renderAt("/validate");
    await validateDemo();
    const detail = await screen.findByRole("article", { name: "Receipt RCP-013" });
    await userEvent.click(within(detail).getByText("Result JSON"));
    await screen.findByLabelText("JSON of receipt RCP-013");
    await userEvent.click(within(detail).getByRole("button", { name: "Copy JSON" }));
    expect(writeText).toHaveBeenCalledTimes(1);
    expect(JSON.parse(writeText.mock.calls[0]![0] as string).receipt_id).toBe("RCP-013");
    expect(await within(detail).findByRole("button", { name: "Copied" })).toBeInTheDocument();
  });

  it("highlights tokens as text, never as HTML", () => {
    const { container } = render(<pre>{highlightJson('{\n  "a": "<b>x</b>",\n  "n": 1.50,\n  "ok": true\n}')}</pre>);
    expect(container.querySelector("b")).toBeNull();
    expect(container.textContent).toBe('{\n  "a": "<b>x</b>",\n  "n": 1.50,\n  "ok": true\n}');
    expect([...container.querySelectorAll("span")].map((span) => span.textContent)).toEqual(['"a"', '"<b>x</b>"', '"n"', "1.50", '"ok"', "true"]);
  });
});

describe("run history", () => {
  it("lists runs", async () => {
    handler = (url) => (url.pathname === "/api/v1/runs" ? { status: 200, body: page([runSummary({ run_id: RUN.run_id })]) } : undefined);
    renderAt("/runs");
    expect(await screen.findByRole("link", { name: RUN.run_id })).toHaveAttribute("href", `/runs/${RUN.run_id}`);
  });

  it("shows the empty state when there are no runs", async () => {
    handler = () => undefined;
    renderAt("/runs");
    expect(await screen.findByText("No validation runs yet")).toBeInTheDocument();
  });

  it("opens a stored run in the same workspace without validating again, and reads its findings", async () => {
    handler = (url) => {
      if (url.pathname === `/api/v1/runs/${RUN.run_id}`) return { status: 200, body: RUN };
      if (url.pathname === `/api/v1/runs/${RUN.run_id}/findings`) return { status: 200, body: findingsJson };
      return undefined;
    };
    renderAt(`/runs/${RUN.run_id}`);
    expect(await screen.findByText(RUN.run_id)).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Receipts, flag first" })).toBeInTheDocument();
    expect(screen.getByText("Merchant and totals are not stored with a run")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: /All findings/ }));
    expect(await screen.findByText("12 findings")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Exceptions" }));
    await waitFor(() => expect(requests.some((request) => request.path.includes("/findings?") && request.path.includes("kind=exception"))).toBe(true));
    expect(requests.some((request) => request.method === "POST")).toBe(false);
  });

  it("downloads PDF directly from results and JSON/CSV from run details", async () => {
    const created: Blob[] = [];
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: (blob: Blob) => (created.push(blob), "blob:x"), revokeObjectURL: () => {} }));
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    handler = (url) => {
      if (url.pathname === `/api/v1/runs/${RUN.run_id}`) return { status: 200, body: RUN };
      if (url.pathname.endsWith("/export")) {
        const format = url.searchParams.get("format")!;
        return { status: 200, body: null, raw: format === "csv" ? "run_id,ruleset_id\r\n" : "{}\n", headers: { "Content-Disposition": `attachment; filename="${RUN.run_id}.${format}"` } };
      }
      return undefined;
    };
    renderAt(`/runs/${RUN.run_id}`);
    await userEvent.click(await screen.findByRole("button", { name: "Export PDF" }));
    await userEvent.click(await screen.findByText("Run details"));
    await userEvent.click(screen.getByRole("button", { name: "Export JSON" }));
    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    await waitFor(() => expect(click).toHaveBeenCalledTimes(2));
    expect(exportViewPdf).toHaveBeenCalledWith(RUN.run_id);
    expect(requests.filter((request) => request.path.includes("/export?")).map((request) => request.path)).toEqual([
      `/api/v1/runs/${RUN.run_id}/export?format=json`,
      `/api/v1/runs/${RUN.run_id}/export?format=csv`,
    ]);
    expect(created).toHaveLength(2);
    click.mockRestore();
  });

  it("reports an export the API refuses instead of saving it", async () => {
    handler = (url) => {
      if (url.pathname === `/api/v1/runs/${RUN.run_id}`) return { status: 200, body: RUN };
      if (url.pathname.endsWith("/export")) return { status: 500, body: { error: { code: "corrupt_run", message: "the stored result does not match its hash" } } };
      return undefined;
    };
    renderAt(`/runs/${RUN.run_id}`);
    await userEvent.click(await screen.findByText("Run details"));
    await userEvent.click(screen.getByRole("button", { name: "Export JSON" }));
    expect(await screen.findByText("the stored result does not match its hash")).toBeInTheDocument();
  });
});

describe("draft", () => {
  it("keeps input and configuration across navigation", async () => {
    handler = () => undefined;
    const router = renderAt("/validate");
    await loadDemo();
    await userEvent.click(screen.getByRole("checkbox", { name: /complete context/ }));
    await act(() => router.navigate("/runs"));
    await act(() => router.navigate("/validate"));
    expect(await screen.findByLabelText("Receipt JSON")).not.toHaveValue("");
    // Checked by default, so the click unchecked it, and that choice survives navigation.
    expect(screen.getByRole("checkbox", { name: /complete context/ })).not.toBeChecked();
  });
});
