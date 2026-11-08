/** Test fixtures in the API's response shapes. Used only by tests: the app never shows made-up data. */
import type { EvaluationSummary, Page, RulesetSummary, RunSummary } from "@/api/types";

export function page<T>(items: T[], total = items.length, limit = 100, offset = 0): Page<T> {
  return { items, total, limit, offset };
}

export function run(overrides: Partial<RunSummary> = {}): RunSummary {
  return {
    run_id: "run_6aee00000000000000000001",
    status: "completed",
    created_at: "2026-11-06T23:27:05.937000Z",
    ruleset: { ruleset_id: "rs_1", name: "policies", version: 1, status: "published", publication_mode: "production", artifact_sha256: "83c9", ruleset_version: "v1" },
    execution_mode: "replay",
    preview: false,
    context_complete: true,
    classification: { backend: null, model: null, translation_model: null, langgraph: null, replay_set: "openrouter" },
    classification_backend: "openrouter",
    receipt_count: 2,
    dispositions: { flag: 2, review: 0, pass: 0 },
    counts: { exception: 4, review_signal: 3, open_question: 1, uncertainty: 2 },
    ...overrides,
  };
}

export function evaluation(overrides: Partial<EvaluationSummary> = {}): EvaluationSummary {
  return {
    evaluation_id: "eval_1",
    label: "openrouter",
    status: "completed",
    dataset: { dataset_id: "dataset_1", name: "golden", version: 1, canonical_sha256: "c", original_sha256: "o", receipts: 32, context_complete: true },
    ruleset: { ruleset_id: "rs_1", name: "policies", version: 1, artifact_sha256: "83c9" },
    preview: false,
    execution_mode: "replay",
    configuration: { classification_backend: "openrouter", model_label: "anthropic/claude-haiku-5.5", models: [], snapshot: {}, fingerprint: {} },
    progress: { total_receipts: 32, processed_receipts: 32, failed_receipts: 0, percent: 100 },
    created_at: "2026-11-06T23:00:00Z",
    started_at: "2026-11-06T23:00:01Z",
    completed_at: "2026-11-06T23:00:30Z",
    summary: { category_accuracy: 1, macro_f1: 1, disposition_accuracy: 1, uncertainty_rate: 0.03125, coverage: 1, execution_error_rate: 0 },
    spend_summary: { new_spend_usd: "0", cost_completeness: "complete", live_calls: 0 },
    stop_reason: null,
    error: null,
    ...overrides,
  };
}

export function rulesetSummary(overrides: Partial<RulesetSummary> = {}): RulesetSummary {
  return {
    ruleset_id: "rs_prod", name: "policies", version: 1, status: "published", ruleset_version: "v1", schema_version: "1", artifact_sha256: "83c9",
    rule_count: 41, active_rule_count: 41, source: { kind: "policy_compiler", description: null, reference: null }, compilation: null, review: null,
    based_on_version: null, approval_reference: "a".repeat(64), approval_mode: "production", publication_mode: "production",
    created_at: "2026-11-06T00:00:00Z", submitted_at: null, approved_at: null, published_at: "2026-11-06T00:00:00Z", ...overrides,
  };
}
