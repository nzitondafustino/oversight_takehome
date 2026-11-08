import { QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/api/client";
import {
  createEvaluation,
  createRuleset,
  getRulesetComparisons,
  listEvaluationResults,
  transitionRuleset,
  uploadDataset,
} from "@/api/endpoints";
import {
  createQueryClient,
  queryKeys,
  useCreateEvaluation,
  useEvaluation,
  useEvaluationMetrics,
  useEvaluations,
  useExtraction,
  useExtractionComparisons,
  useExtractions,
  usePolicyCompiler,
  useReviewExtraction,
  useRulesets,
  useRulesetTransition,
} from "@/api/queries";
import type { EvaluationDetail } from "@/api/types";
import { JsonNumber, parseJson, toJsonText, type JsonObject } from "@/lib/losslessJson";
import { evaluation, page } from "./fixtures";
import backendsFixture from "./fixtures/backends.json";

interface Call {
  method: string;
  path: string;
  body: BodyInit | null | undefined;
  headers: Record<string, string>;
}

type Answer = { status: number; body: unknown } | undefined;

let calls: Call[];
let answer: (method: string, url: URL) => Answer;

beforeEach(() => {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = new URL(String(input), "http://localhost");
      const method = init.method ?? "GET";
      calls.push({ method, path: `${url.pathname}${url.search}`, body: init.body, headers: (init.headers ?? {}) as Record<string, string> });
      const reply = answer(method, url) ?? { status: 200, body: page([]) };
      return Promise.resolve(new Response(JSON.stringify(reply.body), { status: reply.status, headers: { "Content-Type": "application/json" } }));
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function count(method: string, path: string) {
  return calls.filter((call) => call.method === method && call.path.split("?")[0] === path).length;
}

function wrapper() {
  const client = createQueryClient();
  return ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

const refused = (status: number, code: string) => ({ status, body: { error: { code, message: code } } });

describe("request serialization", () => {
  it("writes each JsonNumber exactly as read, and plain values as JSON.stringify does", () => {
    const parsed = parseJson('{"threshold": 75.50, "rate": 0.1000000000000000055}') as JsonObject;
    expect(toJsonText({ parsed, plain: 1.5, skipped: undefined, list: [new JsonNumber("1e3"), undefined, "x"] })).toBe(
      '{"parsed":{"threshold":75.50,"rate":0.1000000000000000055},"plain":1.5,"list":[1e3,null,"x"]}',
    );
    expect(() => toJsonText({ value: Number.NaN })).toThrow(/NaN/);
  });

  it("sends an uploaded artifact's numbers to the API unchanged", async () => {
    answer = () => ({ status: 201, body: {} });
    const artifact = parseJson('{"rules": [{"params": {"max_usd": 75.50}}], "ratio": 0.1000000000000000055}') as JsonObject;
    await createRuleset({ name: "policies", artifact });
    const [call] = calls;
    expect(call).toMatchObject({ method: "POST", path: "/api/v1/rulesets", headers: { "Content-Type": "application/json" } });
    expect(call!.body).toBe('{"name":"policies","artifact":{"rules":[{"params":{"max_usd":75.50}}],"ratio":0.1000000000000000055}}');
  });

  it("keeps a budget's dollars as decimal text and adds nothing the caller did not choose", async () => {
    answer = () => ({ status: 201, body: evaluation() });
    await createEvaluation({ dataset_id: "ds_1", ruleset_id: "rs_1" });
    await createEvaluation({ dataset_id: "ds_1", ruleset_id: "rs_1", execution_mode: "live", confirm_live: true, budget: { max_spend_usd: "0.50", max_provider_calls: 10, max_receipts: 5 } });
    expect(calls.map((call) => call.body)).toEqual([
      '{"dataset_id":"ds_1","ruleset_id":"rs_1"}',
      '{"dataset_id":"ds_1","ruleset_id":"rs_1","execution_mode":"live","confirm_live":true,"budget":{"max_spend_usd":"0.50","max_provider_calls":10,"max_receipts":5}}',
    ]);
  });

  it("uploads a dataset as multipart, leaving its Content-Type and boundary to the browser", async () => {
    answer = () => ({ status: 201, body: {} });
    const file = new File(['{"receipt_id": "R-1"}\n'], "golden.jsonl", { type: "application/x-ndjson" });
    await uploadDataset({ file, name: "golden", context_complete: false });
    const [call] = calls;
    expect(call).toMatchObject({ method: "POST", path: "/api/v1/evaluations/datasets" });
    expect(call!.headers).not.toHaveProperty("Content-Type");
    const form = call!.body as FormData;
    expect((form.get("file") as File).name).toBe("golden.jsonl");
    expect([form.get("name"), form.get("context_complete"), form.has("description")]).toEqual(["golden", "false", false]);
  });

  it("repeats list filters and leaves out false flags the API defaults", async () => {
    answer = () => ({ status: 200, body: {} });
    await listEvaluationResults("eval_1", { category: ["meals", "gifts"], disposition: ["flag"], error: false, limit: 50 });
    await getRulesetComparisons("rs_1");
    await getRulesetComparisons("rs_1", true);
    expect(calls.map((call) => call.path)).toEqual([
      "/api/v1/evaluations/eval_1/results?category=meals&category=gifts&disposition=flag&error=false&limit=50",
      "/api/v1/rulesets/rs_1/comparisons",
      "/api/v1/rulesets/rs_1/comparisons?review_required=true",
    ]);
  });

  it("posts each lifecycle step to its own route, and reports the API's refusal as an ApiError", async () => {
    answer = () => refused(422, "approval_does_not_hold");
    const failure = transitionRuleset("rs_1", { step: "approve", body: { actor: "a.pprover", mode: "production" } });
    await expect(failure).rejects.toMatchObject({ name: "ApiError", status: 422, code: "approval_does_not_hold" });
    expect(calls[0]).toMatchObject({ method: "POST", path: "/api/v1/rulesets/rs_1/approve", body: '{"actor":"a.pprover","mode":"production"}' });
  });
});

describe("query behaviour", () => {
  it("reads the policy compiler's recording sets and live availability from GET /v1/backends", async () => {
    answer = (_, url) => (url.pathname === "/api/v1/backends" ? { status: 200, body: backendsFixture } : undefined);
    const { result } = renderHook(() => usePolicyCompiler(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data!.replay.sets.map((set) => set.name)).toEqual(["fixtures", "live"]);
    expect(result.current.data!.live.available).toBe(false);
  });

  it("polls a running evaluation until it finishes, then refreshes its provisional metrics and the lists", async () => {
    let detailCalls = 0;
    answer = (_, url) => {
      if (url.pathname === "/api/v1/evaluations/eval_1") {
        detailCalls += 1;
        return { status: 200, body: evaluation({ status: detailCalls >= 2 ? "completed" : "running", summary: null }) };
      }
      if (url.pathname === "/api/v1/evaluations/eval_1/metrics") return { status: 200, body: { evaluation_id: "eval_1", provisional: detailCalls < 2 } };
      return undefined;
    };
    renderHook(() => [useEvaluation("eval_1"), useEvaluationMetrics("eval_1"), useEvaluations()], { wrapper: wrapper() });

    await waitFor(() => expect(detailCalls).toBeGreaterThanOrEqual(2), { timeout: 5000 });
    await waitFor(() => expect(count("GET", "/api/v1/evaluations/eval_1/metrics")).toBe(2));
    await waitFor(() => expect(count("GET", "/api/v1/evaluations")).toBe(2));
    const settled = detailCalls;
    await new Promise((resolve) => setTimeout(resolve, 2500));
    expect(detailCalls).toBe(settled);
  }, 12_000);

  it("never polls a finished evaluation", async () => {
    answer = (_, url) => (url.pathname === "/api/v1/evaluations/eval_1" ? { status: 200, body: evaluation() } : undefined);
    const { result } = renderHook(() => useEvaluation("eval_1"), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    await new Promise((resolve) => setTimeout(resolve, 2500));
    expect(count("GET", "/api/v1/evaluations/eval_1")).toBe(1);
  }, 8000);
});

describe("mutations", () => {
  it("caches a created evaluation as its detail and refreshes the evaluation lists", async () => {
    const queued = { ...evaluation({ evaluation_id: "eval_new", status: "queued", summary: null }), spend: {} };
    answer = (method, url) => (method === "POST" && url.pathname === "/api/v1/evaluations" ? { status: 201, body: queued } : undefined);
    const client = createQueryClient();
    const { result } = renderHook(() => ({ create: useCreateEvaluation(), list: useEvaluations() }), {
      wrapper: ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>,
    });
    await waitFor(() => expect(result.current.list.isSuccess).toBe(true));

    await act(() => result.current.create.mutateAsync({ dataset_id: "ds_1", ruleset_id: "rs_1" }));
    expect(client.getQueryData<EvaluationDetail>(queryKeys.evaluation("eval_new"))?.status).toBe("queued");
    await waitFor(() => expect(count("GET", "/api/v1/evaluations")).toBe(2));
    expect(count("GET", "/api/v1/evaluations/eval_new")).toBe(0);
  });

  it("never retries a refused or failed mutation, even a transient one", async () => {
    answer = (method) => (method === "POST" ? refused(503, "database_unavailable") : undefined);
    const { result } = renderHook(() => ({ create: useCreateEvaluation(), step: useRulesetTransition() }), { wrapper: wrapper() });
    await act(async () => {
      await expect(result.current.create.mutateAsync({ dataset_id: "ds_1", ruleset_id: "rs_1" })).rejects.toBeInstanceOf(ApiError);
      await expect(result.current.step.mutateAsync({ rulesetId: "rs_1", transition: { step: "publish", body: {} } })).rejects.toMatchObject({ code: "database_unavailable" });
    });
    expect(calls.filter((call) => call.method === "POST")).toHaveLength(2);
  });

  it("caches a ruleset version after a lifecycle step and refreshes the version lists", async () => {
    const published = { ruleset_id: "rs_1", status: "published", artifact: {}, transitions: [] };
    answer = (method, url) => (method === "POST" && url.pathname === "/api/v1/rulesets/rs_1/publish" ? { status: 200, body: published } : undefined);
    const { result } = renderHook(() => ({ step: useRulesetTransition(), list: useRulesets() }), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.list.isSuccess).toBe(true));
    await act(() => result.current.step.mutateAsync({ rulesetId: "rs_1", transition: { step: "publish", body: { actor: "p.ublisher" } } }));
    await waitFor(() => expect(count("GET", "/api/v1/rulesets")).toBe(2));
    expect(calls.find((call) => call.method === "POST")?.body).toBe('{"actor":"p.ublisher"}');
  });

  it("refreshes an extraction and the extraction lists after a review, but not its stored comparisons", async () => {
    const extraction = { extraction_id: "ext_1", extraction_sha256: "e".repeat(64), reviews: [], review_required: [] };
    answer = (method, url) => {
      if (method === "POST") return { status: 201, body: { review_id: "rev_1", extraction_id: "ext_1", ready_for_approval: true } };
      if (url.pathname === "/api/v1/documents/extractions/ext_1") return { status: 200, body: extraction };
      if (url.pathname === "/api/v1/documents/extractions/ext_1/comparisons") return { status: 200, body: { ...page([]), extraction_sha256: extraction.extraction_sha256 } };
      return undefined;
    };
    const { result } = renderHook(
      () => ({ review: useReviewExtraction(), detail: useExtraction("ext_1"), comparisons: useExtractionComparisons("ext_1"), list: useExtractions() }),
      { wrapper: wrapper() },
    );
    await waitFor(() => expect([result.current.detail.isSuccess, result.current.comparisons.isSuccess, result.current.list.isSuccess]).toEqual([true, true, true]));

    const body = { actor: "r.eviewer", extraction_sha256: extraction.extraction_sha256, decisions: [] };
    await act(() => result.current.review.mutateAsync({ extractionId: "ext_1", body }));
    await waitFor(() => expect(count("GET", "/api/v1/documents/extractions/ext_1")).toBe(2));
    await waitFor(() => expect(count("GET", "/api/v1/documents/extractions")).toBe(2));
    expect(count("GET", "/api/v1/documents/extractions/ext_1/comparisons")).toBe(1);
  });
});
