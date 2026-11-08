import { describe, expect, it } from "vitest";
import { buildUrl } from "@/api/client";
import { sumDecimals } from "@/lib/decimal";
import { formatRate, formatUsd } from "@/lib/format";
import { aggregateSpend, describeScope, scopeOf } from "@/pages/dashboard/aggregate";
import { evaluation } from "./fixtures";

describe("sumDecimals", () => {
  it("adds decimal strings exactly, without float error", () => {
    expect(sumDecimals(["0.1", "0.2"])).toBe("0.3");
    expect(sumDecimals(["0.005225094", "0.0004704", "0"])).toBe("0.005695494");
    expect(sumDecimals([])).toBe("0");
    expect(sumDecimals(["12.10", "-2.10"])).toBe("10");
  });

  it("refuses anything that is not decimal text", () => {
    expect(() => sumDecimals(["1e-3"])).toThrow();
  });
});

describe("formatting", () => {
  it("shows USD exactly, never rounding a small cost to zero", () => {
    expect(formatUsd("0")).toBe("$0.00");
    expect(formatUsd("0.000473")).toBe("$0.000473");
    expect(formatUsd("1234.5")).toBe("$1,234.50");
  });

  it("keeps a null rate apart from zero", () => {
    expect(formatRate(null)).toBe("—");
    expect(formatRate(0)).toBe("0.0%");
    expect(formatRate(0.987342)).toBe("98.7%");
  });

  it("repeats list query parameters and drops missing ones", () => {
    expect(buildUrl("/v1/runs", { kind: ["exception", "uncertainty"], ruleset_id: undefined })).toBe(
      "/api/v1/runs?kind=exception&kind=uncertainty",
    );
  });
});

describe("scope labels", () => {
  it("says when an aggregate covers every record and when it covers a page", () => {
    expect(describeScope(scopeOf({ items: [1, 2], total: 2, limit: 100, offset: 0 }), "run")).toBe("all 2 runs");
    expect(describeScope(scopeOf({ items: new Array(100).fill(0), total: 240, limit: 100, offset: 0 }), "run")).toBe("the latest 100 of 240 runs");
    expect(describeScope(scopeOf({ items: [1], total: 1, limit: 100, offset: 0 }), "run")).toBe("the only run");
  });

  it("marks spending as a lower bound when any evaluation's cost is incomplete", () => {
    const spend = aggregateSpend([
      evaluation({ evaluation_id: "a", execution_mode: "live", spend_summary: { new_spend_usd: "0.0021", cost_completeness: "incomplete", live_calls: 4 } }),
      evaluation({ evaluation_id: "b", spend_summary: { new_spend_usd: "0", cost_completeness: "complete", live_calls: 0 } }),
    ]);
    expect(spend).toMatchObject({ newSpendUsd: "0.0021", incomplete: 1, liveCalls: 4, liveEvaluations: 1, replayEvaluations: 1 });
  });
});
