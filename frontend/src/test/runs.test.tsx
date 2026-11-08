import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Providers, routes } from "@/App";
import { createQueryClient } from "@/api/queries";
import type { ClassificationSetup, RunResponse } from "@/api/types";
import { page, run } from "./fixtures";
import backendsFixture from "./fixtures/backends.json";
import runJson from "./fixtures/run.json";

/**
 * Validation run history and a run's receipts read as orchestrator, provider, model and execution adapter, as the
 * Evaluation Workbench does. run.json is a run recorded before the adapters were renamed, with backend openrouter.
 */
const RECORDED = runJson as unknown as RunResponse;
const DIRECT: ClassificationSetup = { orchestrator: "direct", provider: "openrouter", backend: "chat_completions", protocol: "chat_completions", model: "anthropic/claude-haiku-5.5", nodes: null };
const PRE_RENAME_JEV: ClassificationSetup = { orchestrator: "direct", provider: "openrouter", backend: "jev", protocol: "decisions", model: "typesafe/jev-1.14", nodes: null };
const LANGGRAPH_SETUP: ClassificationSetup = {
  orchestrator: "langgraph", provider: null, backend: null, protocol: null, model: null,
  nodes: [
    { name: "primary", backend: "decisions", model: "typesafe/jev-1.14", provider: "openrouter", protocol: "decisions" },
    { name: "fallback", backend: "chat_completions", model: "anthropic/claude-haiku-5.5", provider: "openrouter", protocol: "chat_completions" },
  ],
};

let handler: (url: URL) => { status: number; body: unknown } | undefined;

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = new URL(String(input), "http://localhost");
      const reply = handler(url) ??
        (url.pathname === "/api/v1/backends" ? { status: 200, body: backendsFixture } : url.pathname === "/api/health" ? { status: 200, body: { status: "ok", application: "running", database: "ok", version: "0.1.0" } } : { status: 404, body: { error: { code: "not_found", message: "not mocked", details: null } } });
      return Promise.resolve(new Response(JSON.stringify(reply.body), { status: reply.status, headers: { "Content-Type": "application/json" } }));
    }),
  );
});

afterEach(() => vi.unstubAllGlobals());

function renderAt(path: string) {
  render(
    <Providers client={createQueryClient()}>
      <RouterProvider router={createMemoryRouter(routes, { initialEntries: [path] })} />
    </Providers>,
  );
}

describe("run history", () => {
  it("shows each run's orchestrator, provider and model, and an older record's adapter as recorded", async () => {
    const runs = [
      run({ run_id: "run_direct", classification_backend: "chat_completions", setup: DIRECT }),
      run({ run_id: "run_jev", classification_backend: "jev", setup: PRE_RENAME_JEV }),
      run({ run_id: "run_graph", classification_backend: "langgraph", setup: LANGGRAPH_SETUP }),
      run({ run_id: "run_unrecorded", classification_backend: "openrouter", setup: undefined }),
      run({ run_id: "run_phase1", classification_backend: null, setup: null }),
    ];
    handler = (url) => (url.pathname === "/api/v1/runs" ? { status: 200, body: page(runs) } : undefined);
    renderAt("/runs");
    const row = async (id: string) => (await screen.findByText(id)).closest("tr")!;
    expect(await row("run_direct")).toHaveTextContent("Direct · OpenRouter · anthropic/claude-haiku-5.5 (Chat completions)");
    // A Jev run recorded as jev: a model through OpenRouter's Decisions API, never a provider of its own.
    expect(await row("run_jev")).toHaveTextContent("Direct · OpenRouter · typesafe/jev-1.14 (Decisions API)");
    expect(await row("run_graph")).toHaveTextContent("LangGraph · primary: OpenRouter · typesafe/jev-1.14 (Decisions API) → fallback: OpenRouter · anthropic/claude-haiku-5.5 (Chat completions)");
    // Nothing is guessed for a record the API could not describe: its adapter is shown by the name it was recorded under.
    expect(await row("run_unrecorded")).toHaveTextContent("openrouter (adapter as recorded)");
    expect(await row("run_phase1")).toHaveTextContent("classification not recorded");
  });
});

describe("a run's receipts", () => {
  async function openProvenance(body: RunResponse) {
    handler = (url) => (url.pathname === `/api/v1/runs/${body.run_id}` ? { status: 200, body } : undefined);
    renderAt(`/runs/${body.run_id}`);
    const summary = await screen.findByText("Receipt fields and classification provenance");
    await userEvent.setup().click(summary);
    return summary.closest("details")!;
  }

  it("shows the receipt's orchestrator, provider, model and adapter, reading an earlier adapter name as that adapter", async () => {
    const recordedSetup: ClassificationSetup = { ...DIRECT, backend: "openrouter" };
    const section = await openProvenance({ ...RECORDED, setup: recordedSetup });
    for (const [label, value] of [["Orchestrator", "Direct"], ["Provider", "OpenRouter"], ["Classification model", "anthropic/claude-haiku-5.5"], ["Adapter", "Chat completions"]]) {
      expect(within(section).getByText(label!).parentElement).toHaveTextContent(value!);
    }
    expect(within(section).queryByText("Classified by")).not.toBeInTheDocument();
    expect(within(section).getByText("Adapter").parentElement!.textContent).not.toMatch(/openrouter/); // the earlier name is never shown
  });

  it("shows only the recorded adapter and model when the API gives no setup", async () => {
    const section = await openProvenance(RECORDED);
    expect(within(section).getByText("Adapter (recorded backend)").parentElement).toHaveTextContent("openrouter");
    expect(within(section).getByText("Classification model").parentElement).toHaveTextContent("anthropic/claude-haiku-5.5");
    expect(within(section).queryByText("Provider")).not.toBeInTheDocument();
  });
});
