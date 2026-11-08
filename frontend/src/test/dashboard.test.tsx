import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Providers, routes } from "@/App";
import { createQueryClient } from "@/api/queries";
import { evaluation, page, run } from "./fixtures";
import backendsFixture from "./fixtures/backends.json";

type Handler = (url: URL) => { status: number; body: unknown } | undefined;

let handler: Handler;
const calls: string[] = [];

function respond(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

beforeEach(() => {
  calls.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = new URL(String(input), "http://localhost");
      calls.push(`${url.pathname}${url.search}`);
      if (url.pathname === "/api/health") return respond(200, { status: "ok", application: "running", database: "ok", version: "0.1.0" });
      if (url.pathname === "/api/v1/backends") return respond(200, backendsFixture);
      const answer = handler(url);
      return answer ? respond(answer.status, answer.body) : respond(200, page([]));
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function renderAt(path = "/") {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(
    <Providers client={createQueryClient()}>
      <RouterProvider router={router} />
    </Providers>,
  );
  return router;
}

describe("dashboard", () => {
  it("shows loading placeholders before the API answers", () => {
    handler = () => undefined;
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    renderAt();
    expect(screen.getAllByRole("status").length).toBeGreaterThan(0);
    expect(screen.getByText("Loading runs…")).toBeInTheDocument();
  });

  it("shows explicit empty states when there are no records", async () => {
    handler = () => undefined;
    renderAt();
    expect(await screen.findByText("No validation runs yet")).toBeInTheDocument();
    expect(screen.getByText("No evaluation jobs yet")).toBeInTheDocument();
    expect(screen.getByText("No completed evaluations")).toBeInTheDocument();
    expect(screen.getByText("No spending recorded")).toBeInTheDocument();
  });

  it("shows the API's error message and code, with a retry", async () => {
    handler = (url) =>
      url.pathname === "/api/v1/runs" ? { status: 503, body: { error: { code: "database_unavailable", message: "MongoDB did not answer" } } } : undefined;
    renderAt();
    const alerts = await screen.findAllByRole("alert", {}, { timeout: 7000 });
    expect(alerts[0]).toHaveTextContent("Database unavailable");
    expect(alerts[0]).toHaveTextContent("MongoDB did not answer");
    expect(within(alerts[0]!).getByRole("button", { name: /retry/i })).toBeInTheDocument();
  }, 10_000);

  it("renders API values and labels the scope of every page-wide sum", async () => {
    handler = (url) => {
      if (url.pathname === "/api/v1/runs") return { status: 200, body: page([run(), run({ run_id: "run_2", receipt_count: 3, dispositions: { flag: 0, review: 1, pass: 2 }, counts: null })], 240) };
      if (url.pathname === "/api/v1/evaluations") return { status: 200, body: page([evaluation()], 1) };
      return undefined;
    };
    renderAt();
    expect(await screen.findByText("Summed over the latest 2 of 240 runs")).toBeInTheDocument();
    expect(screen.getByText(/Receipts across the latest 2 of 240 runs/)).toBeInTheDocument();
    expect(screen.getByText(/1 run was stored before counts were recorded/)).toBeInTheDocument();
    expect(screen.getByText("Summed exactly over the only evaluation.")).toBeInTheDocument();
    expect(screen.getByText("Accounting complete.")).toBeInTheDocument();
    const runLink = screen.getAllByRole("link", { name: /run_6aee/ })[0]!;
    expect(runLink).toHaveAttribute("href", "/runs/run_6aee00000000000000000001");
  });

  it("loads company-wide history without a customer selector or scope parameter", async () => {
    handler = () => undefined;
    renderAt();
    await screen.findByText("No validation runs yet");
    expect(calls.some((c) => c.startsWith("/api/v1/runs"))).toBe(true);
    expect(calls.every((c) => !c.includes("customer_id"))).toBe(true);
    expect(screen.queryByRole("button", { name: /customer scope/i })).not.toBeInTheDocument();
  });

  it("polls only active evaluation jobs, and stops when they finish", async () => {
    let detailCalls = 0;
    handler = (url) => {
      if (url.pathname === "/api/v1/evaluations")
        return {
          status: 200,
          body: page([evaluation({ evaluation_id: "eval_running", status: "running", summary: null }), evaluation({ evaluation_id: "eval_done" })]),
        };
      if (url.pathname === "/api/v1/evaluations/eval_running") {
        detailCalls += 1;
        return { status: 200, body: evaluation({ evaluation_id: "eval_running", status: detailCalls >= 2 ? "completed" : "running" }) };
      }
      return undefined;
    };
    renderAt();
    await waitFor(() => expect(detailCalls).toBeGreaterThanOrEqual(2), { timeout: 6000 });
    const settled = detailCalls;
    await new Promise((resolve) => setTimeout(resolve, 2500));
    expect(detailCalls).toBe(settled);
    expect(calls.some((c) => c.startsWith("/api/v1/evaluations/eval_done"))).toBe(false);
  }, 12_000);
});

describe("navigation", () => {
  it("routes every sidebar entry to its page", async () => {
    handler = () => undefined;
    const router = renderAt();
    const nav = screen.getByRole("complementary", { name: "Primary" });
    for (const [label, heading, path] of [
      ["Validate Receipts", "Validate Receipts", "/validate"],
      ["Evaluations", "Evaluations", "/evaluations"],
      ["Policy Management", "Policy Management", "/policies"],
      ["Documents", "Documents", "/documents"],
      ["Run History", "Run History", "/runs"],
      ["Dashboard", "Dashboard", "/"],
    ] as const) {
      await userEvent.click(within(nav).getByRole("link", { name: label }));
      expect(router.state.location.pathname).toBe(path);
      expect(await screen.findByRole("heading", { level: 1, name: heading })).toBeInTheDocument();
    }
  });
});
