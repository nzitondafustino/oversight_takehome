import { describe, expect, it } from "vitest";
import type { Backends, ReceiptResult, RulesetSummary } from "@/api/types";
import { sortByDisposition } from "@/components/results/sort";
import { JsonSyntaxError, parseJson, stringifyJson } from "@/lib/losslessJson";
import { buildValidationBody, classificationFor, configIssues, defaultRuleset, INITIAL_CONFIG, isEligibleRuleset, type ValidationConfig } from "@/pages/validate/config";
import { DEMO_RECEIPTS_JSON } from "@/pages/validate/demo";
import { formatJsonText, parseReceiptInput, previewOf } from "@/pages/validate/receipts";
import backendsJson from "./fixtures/backends.json";

const backends = backendsJson as unknown as Backends;

const receipt = (id: string, extra = "") =>
  `{"receipt_id": "${id}", "merchant_name": "Shop ${id}", "currency": "USD", "total_amount": 19.990, "line_items": [{"line_id": "${id}-L1", "amount": 19.990}]${extra}}`;

function ruleset(overrides: Partial<RulesetSummary>): RulesetSummary {
  return {
    ruleset_id: "rs_1", name: "policies", version: 1, status: "published", ruleset_version: "v1", schema_version: "1", artifact_sha256: "a",
    rule_count: 41, active_rule_count: 41, source: { kind: "upload", description: null, reference: null }, compilation: null, review: null, based_on_version: null, approval_reference: null, approval_mode: null, publication_mode: "production",
    created_at: "2026-11-06T00:00:00Z", submitted_at: null, approved_at: null, published_at: "2026-11-06T00:00:00Z", ...overrides,
  };
}

describe("lossless JSON", () => {
  it("keeps number literals exactly, where JSON.parse would round them", () => {
    const text = '{"a": 19.990, "b": 0.1000000000000000055, "c": 1e3, "d": -0}';
    expect(stringifyJson(parseJson(text))).toBe('{"a":19.990,"b":0.1000000000000000055,"c":1e3,"d":-0}');
  });

  it("refuses repeated keys and NaN, as the API does, and says where", () => {
    expect(() => parseJson('{"a": 1, "a": 2}')).toThrow(/Repeated key "a"/);
    expect(() => parseJson('{"a": NaN}')).toThrow(/NaN/);
    try {
      parseJson('{\n  "a": 1,\n  "b": }');
    } catch (error) {
      expect(error).toBeInstanceOf(JsonSyntaxError);
      expect((error as JsonSyntaxError).line).toBe(3);
    }
  });

  it("formats by re-indenting only", () => {
    expect(formatJsonText('[{"total_amount":104.990}]')).toBe('[\n  {\n    "total_amount": 104.990\n  }\n]');
  });
});

describe("receipt input", () => {
  it("reads a single receipt, a list, and a dataset-style object", () => {
    expect(parseReceiptInput(receipt("R1"), "json").receipts).toHaveLength(1);
    expect(parseReceiptInput(`[${receipt("R1")}, ${receipt("R2")}]`, "json").receipts).toHaveLength(2);
    const wrapped = parseReceiptInput(`{"name": "x", "receipts": [${receipt("R1")}, ${receipt("R2")}]}`, "json");
    expect(wrapped.issues).toEqual([]);
    expect(wrapped.receipts.map((item) => item.receipt_id)).toEqual(["R1", "R2"]);
  });

  it("reads JSONL line by line, skipping blank lines, and names the line of a broken one", () => {
    const parsed = parseReceiptInput(`${receipt("R1")}\n\n${receipt("R2")}\n{"receipt_id": "R3",\n`, "jsonl");
    expect(parsed.receipts.map((item) => item.receipt_id)).toEqual(["R1", "R2"]);
    expect(parsed.issues[0]?.location).toBe("line 4");
  });

  it("reports syntax errors with a position", () => {
    const parsed = parseReceiptInput('[{"receipt_id": "R1",]', "json");
    expect(parsed.receipts).toEqual([]);
    expect(parsed.issues[0]?.message).toMatch(/line 1, column/);
  });

  it("refuses what the API needs and cannot have: missing IDs, missing lines, repeated IDs", () => {
    const parsed = parseReceiptInput(`[{"merchant_name": "x"}, {"receipt_id": "R1"}, ${receipt("R2")}, ${receipt("R2")}, 3]`, "json");
    const messages = parsed.issues.map((issue) => issue.message);
    expect(messages).toContain("receipt_id is required and must be text.");
    expect(messages).toContain("line_items is required and must be a list.");
    expect(messages).toContain("receipt_id R2 appears more than once.");
    expect(messages).toContain("line_id R2-L1 appears more than once.");
    expect(messages).toContain("A receipt must be a JSON object.");
  });

  it("removes ground-truth answers, counts them, and keeps every other field", () => {
    const text = `[{"receipt_id": "R1", "labels": {"disposition": "flag"}, "notes": "trap", "category_hint": null, "line_items": [{"line_id": "L1", "labels": {"category": "alcohol"}, "amount": 8.99}]}]`;
    const parsed = parseReceiptInput(text, "json");
    expect(parsed.removed).toEqual({ receiptLabels: 1, notes: 1, lineLabels: 1 });
    const sent = stringifyJson(parsed.receipts);
    expect(sent).toBe('[{"receipt_id":"R1","category_hint":null,"line_items":[{"line_id":"L1","amount":8.99}]}]');
    expect(sent).not.toMatch(/labels|notes|alcohol|trap/);
  });

  it("previews only what is given, and shows times as written", () => {
    const [first] = parseReceiptInput('{"receipt_id": "R1", "transaction_datetime": "2026-04-20T14:05:00", "line_items": []}', "json").receipts;
    expect(previewOf(first!)).toEqual({ receiptId: "R1", merchant: null, datetime: "2026-04-20T14:05:00", currency: null, total: null, lineCount: 0 });
  });

  it("ships demo receipts that parse cleanly and carry no answers", () => {
    const parsed = parseReceiptInput(DEMO_RECEIPTS_JSON, "json");
    expect(parsed.issues).toEqual([]);
    expect(parsed.receipts.map((item) => item.receipt_id)).toEqual(["RCP-013", "RCP-014"]);
    expect(parsed.removed).toEqual({ receiptLabels: 0, notes: 0, lineLabels: 0 });
  });
});

describe("sorting", () => {
  it("orders flag, review, pass and keeps the API's order within each group, without mutating", () => {
    const results = (["pass", "flag", "review", "flag", "pass"] as const).map((disposition, index) => ({ receipt_id: `R-${index}`, disposition }) as unknown as ReceiptResult);
    const sorted = sortByDisposition(results);
    expect(sorted.map((item) => item.receipt_id)).toEqual(["R-1", "R-3", "R-2", "R-0", "R-4"]);
    expect(results.map((item) => item.receipt_id)).toEqual(["R-0", "R-1", "R-2", "R-3", "R-4"]);
  });
});

describe("validation configuration", () => {
  const rulesets = [ruleset({ ruleset_id: "rs_draft", status: "draft", publication_mode: null }), ruleset({ ruleset_id: "rs_sim", publication_mode: "simulation" }), ruleset({ ruleset_id: "rs_prod" })];
  const base: ValidationConfig = { ...INITIAL_CONFIG, rulesetId: "rs_prod", orchestrator: "direct", replaySet: "openrouter" };

  it("accepts only versions published for production, and defaults to the newest of them", () => {
    expect(rulesets.map(isEligibleRuleset)).toEqual([false, false, true]);
    expect(defaultRuleset(rulesets)?.ruleset_id).toBe("rs_prod");
    expect(configIssues({ ...base, rulesetId: "rs_sim" }, backends, rulesets)).toContain("The chosen ruleset is not published for production.");
  });

  it("defaults to replay and to a complete context", () => {
    expect(INITIAL_CONFIG.mode).toBe("replay");
    expect(INITIAL_CONFIG.contextComplete).toBe(true);
    expect(configIssues(base, backends, rulesets)).toEqual([]);
  });

  it("runs Jev through the adapter its model needs, and refuses a model the deployment does not offer", () => {
    expect(classificationFor({ ...base, replaySet: "jev", model: "typesafe/jev-1.14" }, backends)).toEqual({ backend: "decisions", model: "typesafe/jev-1.14", replay_set: "jev" });
    expect(configIssues({ ...base, replaySet: "jev", model: "typesafe/jev-1.14" }, backends, rulesets)).toEqual([]);
    expect(configIssues({ ...base, model: "vendor/unknown" }, backends, rulesets)).toContain("vendor/unknown is not a classification model this deployment offers.");
  });

  it("refuses live where the deployment does not allow it, and always needs confirmation", () => {
    expect(configIssues({ ...base, mode: "live" }, backends, rulesets).join(" ")).toMatch(/does not allow live/);
    const allowed = { ...backends, execution_modes: { ...backends.execution_modes, live: true }, backends: backends.backends.map((b) => ({ ...b, live: { permitted: true, credential_configured: true, available: true } })) };
    expect(configIssues({ ...base, mode: "live" }, allowed, rulesets)).toEqual(["Confirm that live validation may incur provider charges."]);
    expect(configIssues({ ...base, mode: "live", liveConfirmed: true }, allowed, rulesets)).toEqual([]);
  });

  it("sends a replay set only in replay mode, no classification in deterministic mode, and no single model for langgraph", () => {
    expect(classificationFor({ ...base, mode: "deterministic" }, backends)).toBeUndefined();
    expect(classificationFor({ ...base, mode: "live" }, backends)).toEqual({ backend: "chat_completions" });
    expect(classificationFor({ ...base, orchestrator: "langgraph", model: "x", replaySet: "langgraph" }, backends)).toEqual({ backend: "langgraph", replay_set: "langgraph" });
  });

  it("builds the request body with exact numbers and without answers", () => {
    const parsed = parseReceiptInput(`[${receipt("R1", ', "labels": {"disposition": "pass"}')}]`, "json");
    const body = buildValidationBody({ rulesetId: "rs_prod", receipts: parsed.receipts, config: { ...base, contextComplete: true }, backends });
    expect(body).toContain('"total_amount":19.990');
    expect(body).not.toContain("labels");
    expect(JSON.parse(body)).not.toHaveProperty("customer_id");
    expect(JSON.parse(body)).toMatchObject({ ruleset_id: "rs_prod", context_complete: true, execution_mode: "replay", classification: { backend: "chat_completions", replay_set: "openrouter" } });
  });
});
