import type { JsonNumber } from "@/lib/losslessJson";

/**
 * Response types, transcribed from backend/src/expense_api/schemas (Pydantic) and the services that fill
 * their dict fields. Only fields the API returns are listed. Datetimes arrive as ISO 8601 strings, and money
 * as exact decimal strings ("0.00073"), never numbers.
 */

/** Every list endpoint: offset pagination with the full match count. */
export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface PageParams {
  limit?: number;
  offset?: number;
}

/**
 * Free-form JSON a request carries whole, such as an uploaded artifact or an approval record. A JsonNumber
 * (from parseJson) is sent exactly as written, so a value read from a file is never rounded on the way.
 */
export type JsonInput = null | boolean | string | number | JsonNumber | JsonInput[] | { [key: string]: JsonInput };

/** The core's rate (evaluation/metrics.py): value is null when the denominator is 0. */
export interface Rate {
  numerator: number;
  denominator: number;
  value: number | null;
  wilson_95: [number, number] | null;
}

/** The core's prf: precision, recall and count-form F1, each null when there was nothing to score. */
export interface Prf {
  tp: number;
  fp: number;
  fn: number;
  precision: number | null;
  recall: number | null;
  f1: number | null;
}

/** A metric block with nothing to score: never reported as zero. */
export interface Unavailable {
  available: false;
  reason: string;
}

/** Who a request says acted. Nothing authenticates it in this deployment. */
export interface ClaimedActor {
  claimed: string;
  verified: false;
}

/** schemas/common.py ErrorResponse */
export interface ApiErrorBody {
  error: { code: string; message: string; details?: unknown };
}

// --- Health: api/v1/health.py ---

export interface Health {
  status: "ok" | "degraded";
  application: "running";
  database: "ok" | "unavailable";
  version: string;
}

// --- Runs: schemas/runs.py, schemas/validation.py ---

export type Disposition = "flag" | "review" | "pass";
export type ExecutionMode = "replay" | "live" | "deterministic";

export interface RunRuleset {
  ruleset_id: string;
  name: string;
  version: number;
  status: string;
  publication_mode: string | null;
  artifact_sha256: string;
  ruleset_version: string;
}

export interface ClassificationSelection {
  backend: string | null;
  model: string | null;
  translation_model: string | null;
  langgraph: Record<string, unknown> | null;
  replay_set: string | null;
}

export interface FindingCounts {
  exception: number;
  review_signal: number;
  open_question: number;
  uncertainty: number;
}

export interface RunSummary {
  run_id: string;
  status: "completed";
  created_at: string;
  ruleset: RunRuleset;
  execution_mode: ExecutionMode;
  preview: boolean;
  context_complete: boolean;
  classification: ClassificationSelection;
  /** As the receipts' provenance reports it. Null for older runs and deterministic runs. */
  classification_backend: string | null;
  /** Orchestrator, provider, adapter and model, worked out when read; null for a run stored before summaries. */
  setup?: ClassificationSetup | null;
  receipt_count: number;
  /** Receipts per disposition: flag, review, pass. */
  dispositions: Partial<Record<Disposition, number>> & Record<string, number>;
  /** Null for a run stored before counts were recorded. */
  counts: FindingCounts | null;
}

export interface RunFilters {
  ruleset_id?: string;
  execution_mode?: ExecutionMode;
  preview?: boolean;
}

// --- Rulesets: schemas/rulesets.py ---

export type RulesetStatus = "draft" | "in_review" | "approved" | "published";
export type PublishMode = "production" | "simulation";

export interface RulesetSource {
  kind: string;
  description: string | null;
  reference: string | null;
}

export interface RulesetReview {
  decision: "accepted" | "rejected";
  reviewer: ClaimedActor;
  reason: string | null;
  decided_at: string;
  policy: string | null;
  decisions: Record<string, unknown>[] | null;
  /** The core's hash of the reviewed interpretation, which an approval binds. */
  review_sha256: string | null;
}

export interface CompiledDocument {
  document_id: string;
  filename: string;
  sha256: string;
  /** The stored revision, for a version made from an extraction. */
  revision: number | null;
}

export interface Compilation {
  recording_set: string;
  recorded_with: RecordedWith | null;
  extraction_id: string | null;
  prepared_review: { review_id: string; policy: string; review_sha256: string; decisions?: unknown[] } | null;
  /** Reviewed corrections of the rule logic this version was compiled with, each with who and why. */
  rule_overrides?: RuleOverride[];
  /** simulation readings never authorize production. */
  reading_kind: "model" | "simulation";
  extraction_sha256: string;
  output_sha256: string;
  /** Without review decisions. */
  ready_for_approval: boolean;
  blockers: string[];
  comparisons_needing_review: number;
  documents: CompiledDocument[];
}

export interface RulesetSummary {
  superseded_by?: { ruleset_id: string; name: string; version: number } | null;
  ruleset_id: string;
  name: string;
  version: number;
  status: RulesetStatus;
  ruleset_version: string;
  schema_version: string;
  artifact_sha256: string;
  rule_count: number;
  active_rule_count: number;
  source: RulesetSource;
  based_on_version: number | null;
  /** Null for an uploaded artifact, which no approval can cover. */
  compilation: Compilation | null;
  review: RulesetReview | null;
  /** For a version made by editing another: what changed, by whom and why. */
  edits?: RuleEdits | null;
  approval_reference: string | null;
  approval_mode: PublishMode | null;
  publication_mode: PublishMode | null;
  created_at: string;
  submitted_at: string | null;
  approved_at: string | null;
  published_at: string | null;
}

export interface RulesetTransitionRecord {
  from_status: RulesetStatus | null;
  to_status: RulesetStatus;
  at: string;
  actor: ClaimedActor | null;
  content_hash: string;
  approval_reference: string | null;
  reason: string | null;
}

export interface RulesetDetail extends RulesetSummary {
  /** The core's canonical serialization: its numbers are exact decimal strings, so JSON.parse keeps them. */
  artifact: Record<string, unknown>;
  transitions: RulesetTransitionRecord[];
}

/** GET /v1/rulesets/{id}/comparisons: every comparison at once, unlike an extraction's paged comparisons. */
export interface RulesetComparisons {
  extraction_sha256: string;
  comparisons: FieldComparison[];
}

export interface PolicyDocumentInput {
  document_id: string;
  filename: string;
  content: string;
}

interface RulesetCreateBase {
  /** The versions of one name form one history. */
  name: string;
  /** The version this one replaces: any other version created since is a 409 version_conflict. */
  based_on_version?: number;
  source?: Partial<RulesetSource>;
}

/** Exactly one source. An uploaded artifact is checked structurally only and can never be approved. */
export type RulesetCreate = RulesetCreateBase &
  (
    | { extraction: { extraction_id: string; review_id?: string } }
    | { compile: { documents: PolicyDocumentInput[]; recording_set: string } }
    | { artifact: { [key: string]: JsonInput } }
  );

export interface TransitionRequest {
  actor?: string;
  reason?: string;
}

export interface RulesetReviewRequest {
  actor: string;
  decision: "accepted" | "rejected";
  /** Required to reject. */
  reason?: string;
  review_policy?: ReviewPolicy;
  /** The core's ReviewDecision records; none with a rejection. */
  decisions?: ReviewDecision[];
}

export interface ApproveRequest {
  /** Becomes the approval's approved_by: a claim, not an authenticated identity. */
  actor: string;
  mode?: PublishMode;
  note?: string;
  /** An approval record made elsewhere with the core's PolicyCompiler.approve. */
  approval?: { [key: string]: JsonInput };
}

export interface PublishRequest {
  actor?: string;
  /** The approval to publish: another one is a 409 publication_conflict. */
  approval_reference?: string;
}

/** One lifecycle step, POST /v1/rulesets/{id}/{step}: draft → in_review → approved → published. */
export type RulesetTransition =
  | { step: "submit"; body: TransitionRequest }
  | { step: "review"; body: RulesetReviewRequest }
  | { step: "approve"; body: ApproveRequest }
  | { step: "publish"; body: PublishRequest };

// --- Documents and extractions: schemas/documents.py, the core's FieldComparison and ReviewDecision ---

export interface DocumentSource {
  kind: string;
  description: string | null;
  reference: string | null;
}

/** What a document is about, chosen by a person, never guessed from its filename. null: unclassified. */
export type DocumentCategory =
  | "meals_entertainment"
  | "gifts_awards"
  | "approvals_exceptions"
  | "travel_regional"
  | "receipts_documentation"
  | "merchant_restrictions"
  | "technology_equipment";

/** Where a document applies, as given. An empty list is not stated. */
export interface DocumentScope {
  regions: string[];
  entities: string[];
  expense_types: string[];
}

export interface DocumentSummary {
  document_id: string;
  revision: number;
  filename: string;
  title: string | null;
  content_sha256: string;
  size_bytes: number;
  source: DocumentSource;
  based_on_revision: number | null;
  created_at: string;
  /** The document's profile, the same for every revision. Absent or null for one never described. */
  category?: DocumentCategory | null;
  description?: string | null;
  scope?: DocumentScope | null;
  /** Another document with identical content that is kept: this one is hidden as its duplicate. */
  duplicate_of?: string | null;
  profile_updated_at?: string | null;
  /** This exact revision's approval. Absent in a response from before approval existed: a draft. */
  approval?: RevisionApprovalState;
}

export type DocumentApprovalStatus = "draft" | "needs_review" | "approved";

/** The approval of one exact revision. Every revision starts as a draft: none inherits another's approval. */
export interface RevisionApprovalState {
  status: DocumentApprovalStatus;
  content_sha256?: string | null;
  updated_at?: string | null;
  updated_by?: ClaimedActor | null;
}

export interface ApprovalEvent {
  action: "submit" | "approve" | "return_to_draft";
  from_status: DocumentApprovalStatus;
  to_status: DocumentApprovalStatus;
  actor: ClaimedActor;
  note: string | null;
  content_sha256: string;
  at: string;
}

export interface RevisionApproval extends RevisionApprovalState {
  document_id: string;
  revision: number;
  history: ApprovalEvent[];
}

export interface ApprovalActionBody {
  action: "submit" | "approve" | "return_to_draft";
  actor: string;
  /** The exact text acted on: another revision's hash is a 409 revision_mismatch. */
  content_sha256: string;
  note?: string;
}

/** A compared field's clause, verified against the exact revision the compiler read; never approximate. */
export interface SourceLocation {
  comparison_id: string;
  document: { document_id: string; revision: number | null; filename: string; content_sha256: string; content: string };
  revision_verified: boolean;
  clause_verified: boolean;
  location: { from_line: number; to_line: number } | null;
  section: string | null;
  heading_line: number | null;
  highlights: { line: number; start: number; end: number; text: string; side: "regex" | "llm" | "clause" }[];
  reason: string | null;
}

/** PATCH /v1/documents/{id}/profile: only the fields given change; no revision ever does. */
export interface ProfileUpdate {
  actor: string;
  category?: DocumentCategory | null;
  description?: string | null;
  scope?: DocumentScope | null;
  /** Marks this document a hidden duplicate of another with identical content; null restores it. Nothing is deleted. */
  duplicate_of?: string | null;
}

/** POST /v1/documents/extraction-jobs: an extraction running in the background, and how far it is. */
export interface ExtractionJob {
  job_id: string;
  status: "queued" | "running" | "succeeded" | "failed";
  execution_mode: "replay" | "live";
  extraction_model: string | null;
  documents: string[];
  progress: { stage: "queued" | "reading" | "building" | "done"; done: number; total: number; current: string | null };
  extraction_id: string | null;
  error: { code: string; message: string; details?: unknown } | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string;
}

export interface DocumentDetail extends DocumentSummary {
  /** UTF-8 Markdown, exactly as stored. */
  content: string;
}

export interface DocumentCreate {
  /** A bare .md name. Rules cite a document by it, so every revision keeps it. */
  filename: string;
  content: string;
  title?: string;
  source?: Partial<DocumentSource>;
  category?: DocumentCategory;
  description?: string;
  scope?: DocumentScope;
}

export interface RevisionCreate {
  content: string;
  /** Another revision created since is a 409 revision_conflict. */
  based_on_revision?: number;
  title?: string;
  source?: Partial<DocumentSource>;
}

export interface RevisionRef {
  document_id: string;
  revision: number;
}

export interface RecordedWith {
  extraction_model: string;
  temperature: number;
  /** Set when the compilation that recorded these readings asked the model again on purpose. */
  reading_id?: string | null;
}

/** Replay reads a named recording set. Live calls a paid model, and the deployment must allow it. */
/** A reviewed correction of one draft rule's logic, compiled in and validated like the rule logic itself. */
export interface RuleOverride {
  rule_id: string;
  set: Record<string, unknown>;
  rationale: string | null;
  reviewer?: string;
  /** A new rule for a clause no rule implements: set also gives its scope, source_quote and params. */
  add?: boolean;
}

/** Corrections of the draft rules: each needs a rationale, and the request an actor. */
type Corrections = { actor?: string; rule_overrides?: RuleOverride[]; carry_overrides_from?: string };

export type ExtractionCreate =
  | ({ documents: RevisionRef[]; execution_mode?: "replay"; recording_set: string; recorded_with?: RecordedWith } & Corrections)
  | ({ documents: RevisionRef[]; execution_mode: "live"; extraction_model: string; temperature?: number; timeout_seconds?: number; fresh_reading?: boolean } & Corrections);

/** A compiled rule that does not do what its clause states: the core's RuleDiscrepancy. */
export interface RuleDiscrepancy {
  rule_id: string;
  policy_doc: string;
  /** rule: the documents state a clause under its own rule ID that no rule implements. */
  field: "thresholds" | "severities" | "review_signal" | "source_quote" | "rule";
  /** thresholds: {value, unit}[]; severities: string[]; review_signal: boolean; source_quote: the clause text;
   * rule: {title, text, severities, review_signal, thresholds}. */
  stated: unknown;
  /** thresholds: each policy param by name, {value, unit}; severities: string[]; review_signal: boolean. */
  compiled: unknown;
}

/** A field of a compilation beside the same field in the published version, with the decision it was published with. */
export interface PreviousDecision {
  comparison_id: string;
  /** unchanged: same clause and readings, so the earlier decision answers the same question; changed: decide again. */
  state: "unchanged" | "changed";
  previous: { comparison_id: string; status: ComparisonStatus; regex_value: unknown; llm_value: unknown; evidence: string | null; clause_hash: string | null };
  decision: ReviewDecision;
}

/** A version beside the version published before it. */
export interface VersionChanges {
  against: { ruleset_id: string; name: string; version: number; published_at: string | null } | null;
  rule_changes: { rule_id: string; field: string; before: unknown; after: unknown }[];
}

/** A compilation beside the latest published version. */
export interface Baseline {
  published: { ruleset_id: string; name: string; version: number; published_at: string | null } | null;
  rule_changes?: { rule_id: string; field: string; before: unknown; after: unknown }[];
  changed_clauses?: { policy_doc: string; rule_id: string }[];
  decisions?: PreviousDecision[];
}

export type ExtractionStatus = "no_candidate" | "review_required" | "ready_for_approval";
export type ComparisonStatus = "agreed" | "disagree" | "unconfirmed" | "only_regex" | "only_llm";
export type ReviewAction = "confirm" | "accept_regex" | "accept_llm" | "override" | "reject";
export type ReviewPolicy = "exceptions_only" | "all_fields";

export interface FieldComparison {
  comparison_id: string;
  document_id: string;
  policy_doc: string;
  rule_id: string;
  field: string;
  status: ComparisonStatus;
  regex_value: unknown;
  llm_value: unknown;
  agreed_value: unknown;
  evidence: string | null;
  location: { section?: string | null; line?: number | null; [key: string]: unknown } | null;
  clause_hash: string | null;
  extraction_sha256: string;
  /** Whether an exceptions_only review must decide it. */
  review_required: boolean;
}

/** The core's ReviewDecision. value is given only with override, where null is a value. */
export interface ReviewDecision {
  comparison_id: string;
  extraction_sha256: string;
  action: ReviewAction;
  value?: JsonInput;
  reviewer: string;
  rationale?: string | null;
}

export interface ExtractedDocument {
  document_id: string;
  revision: number;
  filename: string;
  content_sha256: string;
  reading_error: string | null;
}

export interface ExtractionProvenance {
  execution_mode: "replay" | "live";
  recording_set: string;
  /** The settings a later replay must use. */
  recorded_with: RecordedWith | null;
  reading_kind: "model" | "simulation";
  extraction_model: string | null;
  temperature: number | null;
  timeout_seconds: number | null;
  overrides: string[];
  prompt_sha256: string;
}

export interface ExtractionReviewSummary {
  review_id: string;
  review_sha256: string;
  policy: string;
  ready_for_approval: boolean;
  remaining: number;
  error_issues: number;
  created_at: string;
}

/** GET /v1/documents/extractions: the stored summary, without the fields read from the core's result. */
export interface CompilationAnalytics {
  duration_ms: number;
  provider_latency_ms: number;
  live_calls: number;
  failed_calls: number;
  reused_documents: number;
  documents: number;
  new_spend_usd: string;
  cost_completeness: "complete" | "incomplete";
  unpriced_calls: number;
  input_tokens: number | null;
  output_tokens: number | null;
  calls: { model: string | null; document_sha256: string; latency_ms: number; failed: boolean; cost_usd: string | null; input_tokens: number | null; output_tokens: number | null }[];
}

export interface ExtractionListItem {
  analytics?: CompilationAnalytics | null;
  extraction_id: string;
  /** Never "approved": approval is a ruleset version's lifecycle step. */
  status: ExtractionStatus;
  documents: ExtractedDocument[];
  provenance: ExtractionProvenance;
  extraction_sha256: string;
  output_sha256: string;
  candidate_ruleset_sha256: string | null;
  result_sha256: string;
  /** As the compiler says, before any review. */
  ready_for_approval: boolean;
  blockers: string[];
  agreement_summary: Record<string, unknown>;
  comparison_count: number;
  reviews: ExtractionReviewSummary[];
  created_at: string;
  /** The ruleset versions made from this extraction. Absent in a response from before it was reported. */
  versions?: VersionRef[];
  open_change_requests?: number;
  /** Reviewed corrections of the draft rules this compilation was built with. */
  rule_overrides?: RuleOverride[];
}

export interface VersionRef {
  ruleset_id: string;
  name: string;
  version: number;
  status: RulesetStatus;
}

/** A reviewer's request that one compared field be corrected. It rewrites nothing; while open it blocks use. */
export interface ChangeRequest {
  change_request_id: string;
  extraction_id: string;
  comparison_id: string;
  rule_id: string;
  field: string;
  policy_doc: string;
  document_id: string;
  requested_correction: string;
  comment: string | null;
  status: "open" | "resolved";
  requested_by: ClaimedActor;
  created_at: string;
  resolved_by: ClaimedActor | null;
  resolution_note: string | null;
  resolved_at: string | null;
}

export interface ChangeRequestCreate {
  actor: string;
  comparison_id: string;
  requested_correction: string;
  comment?: string;
}

export interface ExtractionSummary extends ExtractionListItem {
  /** Rules whose compiled behaviour differs from what their clause states: why there is no candidate. */
  discrepancies?: RuleDiscrepancy[];
  /** Each candidate rule's title, by rule_id, when the compiler built a candidate. */
  rule_titles?: Record<string, string>;
  /** The fields an exceptions_only review must decide. */
  review_required: FieldComparison[];
  /** How each rule under review applies its params: its conditions, params, and where each param is compared. */
  rule_logic?: Record<string, RuleLogic>;
}

/** How a helper compares one of its params (the core's policy/param_use.py). */
export interface HelperParamUse {
  measure: string;
  operator: string;
  applies_to: string;
  threshold_of: string | null;
}

/** What a candidate rule does, as the ruleset states it: sent for each rule under review and each it replaces or is replaced by. */
export interface RuleLogic {
  kind?: string;
  scope?: string;
  helper?: string | null;
  outcome?: string;
  severity?: string | null;
  severity_cases?: { when: unknown; severity: string }[] | null;
  title?: string;
  status?: string;
  policy_doc?: string;
  applies_when?: unknown;
  select?: unknown;
  replaces?: { rule_id: string; level: string }[] | null;
  waived_by_ref?: string | null;
  modifies?: string[] | null;
  requires_ref?: string | null;
  directive?: string | null;
  params?: Record<string, { type: "number"; value: string; unit: string; source?: string } | { type: "set"; values: string[] }>;
  param_uses: Record<string, { in_conditions: ("applies_when" | "select")[]; helper: HelperParamUse | null }>;
}

export interface ExtractionComparisonPage extends Page<FieldComparison> {
  extraction_sha256: string;
}

export interface ExtractionReviewCreate {
  actor: string;
  /** The snapshot this review is of: another is a 409 stale_extraction. */
  extraction_sha256: string;
  review_policy?: ReviewPolicy;
  decisions: ReviewDecision[];
}

export interface ReviewIssue {
  level: string;
  code: string;
  message: string;
  comparison_id: string | null;
}

export interface ExtractionReviewDetail {
  review_id: string;
  extraction_id: string;
  actor: ClaimedActor;
  extraction_sha256: string;
  review_sha256: string;
  candidate_ruleset_sha256: string | null;
  policy: string;
  decisions: ReviewDecision[];
  /** False: this review cannot become a candidate version. */
  ready_for_approval: boolean;
  complete: boolean;
  remaining: string[];
  issues: ReviewIssue[];
  created_at: string;
}

// --- Evaluations: schemas/evaluations.py, services/evaluations.py, evaluation_metrics.py, spending.py ---

export type EvaluationStatus =
  | "queued"
  | "running"
  | "cancel_requested"
  | "completed"
  | "failed"
  | "cancelled"
  | "budget_exhausted";

export const ACTIVE_EVALUATION_STATUSES: readonly EvaluationStatus[] = ["queued", "running", "cancel_requested"];

export function isActiveEvaluation(status: EvaluationStatus): boolean {
  return ACTIVE_EVALUATION_STATUSES.includes(status);
}

export interface EvaluationProgress {
  total_receipts: number;
  processed_receipts: number;
  failed_receipts: number;
  percent: number;
}

/** evaluation_metrics.summary: each value is null when there was nothing to score. */
export interface EvaluationKeyMetrics {
  category_accuracy: number | null;
  macro_f1: number | null;
  disposition_accuracy: number | null;
  uncertainty_rate: number | null;
  coverage: number | null;
  execution_error_rate: number | null;
}

export type CostCompleteness = "complete" | "incomplete";

export interface SpendSummary {
  new_spend_usd: string;
  /** incomplete: some live call has no cost, so new_spend_usd is a lower bound. */
  cost_completeness: CostCompleteness;
  live_calls: number;
}

export interface EvaluationDatasetRef {
  dataset_id: string;
  name: string;
  version: number;
  canonical_sha256: string;
  original_sha256: string;
  receipts: number;
  context_complete: boolean;
}

export interface EvaluationRulesetRef {
  ruleset_id: string;
  name: string;
  version: number;
  artifact_sha256: string;
  [key: string]: unknown;
}

/**
 * A recorded classification, as the API works it out when read (services/discovery.py describe_setup): the
 * orchestrator, the provider, the execution adapter (`backend`, as recorded) and the model. Never stored, so older
 * runs, such as one recorded with backend jev, read the same way.
 */
export interface ClassificationSetup {
  orchestrator: "direct" | "langgraph" | null;
  provider: string | null;
  backend: string | null;
  protocol: string | null;
  /** The adapter's own name, whichever name the record holds in backend (jev reads as decisions). */
  adapter?: string | null;
  model: string | null;
  nodes: OrchestratorNode[] | null;
}

export interface EvaluationConfiguration {
  /** The execution adapter or orchestrator recorded for the run: its identity, kept as recorded. */
  classification_backend: string | null;
  /** Absent from responses of an API older than it. */
  setup?: ClassificationSetup;
  model_label: string | null;
  models: string[];
  /** The translator the run used and where it came from. Absent on jobs made before it was recorded. */
  translation?: { enabled: boolean; model: string | null; source: "request" | "config" } | null;
  snapshot: Record<string, unknown>;
  fingerprint: Record<string, unknown>;
}

export interface EvaluationSummary {
  evaluation_id: string;
  label: string | null;
  status: EvaluationStatus;
  dataset: EvaluationDatasetRef;
  ruleset: EvaluationRulesetRef;
  preview: boolean;
  execution_mode: "replay" | "live";
  configuration: EvaluationConfiguration;
  progress: EvaluationProgress;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  /** Key quality metrics, once finished. */
  summary: EvaluationKeyMetrics | null;
  spend_summary: SpendSummary;
  stop_reason: string | null;
  error: { code?: string; message?: string; [key: string]: unknown } | null;
}

/** services/spending.py summarize: spending of a set of calls. USD is exact decimal text. */
export interface SpendTotals {
  calls: { total: number; live: number; cached: number; replayed: number; failed: number };
  tokens: { input: number | null; output: number | null };
  actual_cost_usd: string;
  estimated_cost_usd: string;
  unpriced_usage: { calls: number; input_tokens: number | null; output_tokens: number | null };
  /** Live calls only; a lower bound when cost_completeness is incomplete. */
  new_spend_usd: string;
  cost_completeness: CostCompleteness;
  /** Known cost of the recorded calls a replay reused. */
  historical: { cost_usd: string | null; calls_with_cost: number; calls_without_cost: number };
}

/** spend_report: the totals, and the same by model, backend and call purpose. */
export interface SpendReport extends SpendTotals {
  by_model: Record<string, SpendTotals>;
  by_backend: Record<string, SpendTotals>;
  by_purpose: Record<string, SpendTotals>;
  pricing_versions: string[];
}

/** A live run's limits as stored: each null where no limit of that kind was set. */
export interface EvaluationBudget {
  /** US dollars as decimal text, such as "0.50": never a float. */
  max_spend_usd: string | null;
  max_provider_calls: number | null;
  /** Refuses a larger dataset at creation; never cuts a run short. */
  max_receipts: number | null;
}

export interface EvaluationDetail extends EvaluationSummary {
  /** As requested, with the replay set the API chose. */
  classification: ClassificationSelection;
  replay: { set: string; sha256: string } | null;
  /** uncapped is absent on jobs made before uncapped runs existed: they always had a budget. */
  live: { confirmed: boolean; uncapped?: boolean; answers: string; retries: string } | null;
  /** Null for a replay, and for an uncapped live run. */
  budget: EvaluationBudget | null;
  attempts: number;
  updated_at: string;
  cancel_requested_at: string | null;
  lease: { worker_id?: string; heartbeat_at?: string; expires_at?: string } | null;
  result_context: ResultContext | null;
  context_complete: boolean | null;
  note: string | null;
  error_summary: Record<string, number> | null;
  metrics_schema_version: string | null;
  /** Worked out from the calls stored so far while the job runs. */
  spend: SpendReport;
}

export interface EvaluationFilters {
  dataset_id?: string;
  ruleset_id?: string;
  backend?: string;
  /** Any model the run may call. */
  model?: string;
  status?: EvaluationStatus;
  execution_mode?: "replay" | "live";
  /** ISO 8601 with a time zone: inclusive. */
  created_from?: string;
  /** ISO 8601 with a time zone: exclusive. */
  created_to?: string;
}

/**
 * POST /v1/evaluations. Replay is the default and calls no provider. Live calls paid providers, and the API
 * refuses it without confirm_live, limits (or confirm_uncapped) and the deployment's permission. confirm_live,
 * confirm_uncapped and budget are refused on a replay.
 */
export interface EvaluationCreate {
  dataset_id: string;
  ruleset_id: string;
  preview?: boolean;
  execution_mode?: "replay" | "live";
  classification?: Partial<ClassificationSelection>;
  confirm_live?: boolean;
  /** Live: at least one limit, unless confirm_uncapped. A limit left out is no limit of that kind. */
  budget?: { max_spend_usd?: string; max_provider_calls?: number; max_receipts?: number };
  /** Live, with no budget: confirms a run with no spending, call or receipt limit. */
  confirm_uncapped?: boolean;
  /** The same key and request return the same evaluation; a different request is a 409. */
  idempotency_key?: string;
  label?: string;
}

// --- Evaluation datasets: schemas/evaluations.py, services/datasets.py ---

export interface DatasetStats {
  receipts: number;
  lines: number;
  labelled: { receipts_with_disposition: number; receipts_with_findings: number; lines_with_category: number };
}

export interface DatasetSummary {
  dataset_id: string;
  name: string;
  version: number;
  description: string | null;
  format: string;
  filename: string;
  schema_version: string;
  context_complete: boolean;
  original_sha256: string;
  canonical_sha256: string;
  stats: DatasetStats;
  created_at: string;
}

/** A dataset's answers for one receipt: what scoring compares against, never sent to the validator. */
export interface ExpectedAnswers {
  disposition: Disposition | null;
  findings: { exceptions: { rule_id: string; severity: string | null; line_ids: string[] }[]; signals: string[] } | null;
  lines: Record<string, { category: string | null; restricted: boolean | null }>;
}

export interface DatasetCase {
  /** The raw receipt. The API shows its numbers as standard JSON numbers, so they are for display only. */
  receipt: Record<string, unknown>;
  expected: ExpectedAnswers;
  notes: string | null;
}

export interface DatasetDetail extends DatasetSummary {
  /** With include_cases=true. */
  cases: DatasetCase[] | null;
}

/** POST /v1/evaluations/datasets, multipart. The same bytes again return the stored version. */
export interface DatasetUpload {
  /** .yaml, .yml, .json or .jsonl */
  file: File;
  /** Required for JSONL; JSON and YAML may name the dataset inside the file. */
  name?: string;
  description?: string;
  context_complete?: boolean;
}

// --- Evaluation metrics: services/evaluation_metrics.py (evaluation-metrics/1) ---

export type ResultContext = "dataset" | "isolated_receipts";
export type LineOutcome = "correct" | "incorrect" | "missing" | "classification_error" | "execution_error";

export interface ClassificationMetrics {
  available: true;
  lines: number;
  outcomes: Record<LineOutcome, number>;
  /** correct / every labelled line, whatever its outcome. */
  accuracy: Rate;
  accuracy_when_predicted: Rate;
  coverage: Rate;
  macro: { precision: number | null; recall: number | null; f1: number | null };
  per_category: Record<string, Prf & { support: number }>;
  /** Expected categories by predicted ones; "(none)" is the column for a line with no category. */
  confusion_matrix: { rows: string[]; columns: string[]; counts: Record<string, Record<string, number>> };
  restricted: Prf & { lines: number };
  /** Scoring version 2. Answer (true, false) by prediction (true, false, none); positive is restricted = true. */
  restricted_matrix?: { rows_gold_columns_predicted: Record<"true" | "false", Record<"true" | "false" | "none", number>>; tn: number };
  /** Scoring version 2. Categories predicted that no answer has, with their line counts. */
  extra_predicted?: Record<string, number>;
}

export interface DispositionMetrics {
  available: true;
  receipts: number;
  evaluated: number;
  execution_errors: number;
  /** A receipt that could not be evaluated counts as not correct. */
  accuracy: Rate;
  accuracy_when_evaluated: Rate;
  per_disposition: Record<Disposition, Prf & { support: number }>;
  matrix_gold_by_predicted: Record<Disposition, Record<Disposition, number>>;
  execution_errors_by_gold: Record<Disposition, number>;
  undetermined: Rate;
  false_pass: Rate;
  missed_flag_passed: Rate;
  missed_flag_any: Rate;
  false_flag: Rate;
  review: Rate;
  abstention: Rate;
  flagged_undecided: Rate;
  /** Scoring version 2: every labelled receipt, with those not evaluated in their own column. */
  matrix_with_not_evaluated?: { columns: string[]; rows: Record<Disposition, Record<string, number>>; row_totals: Record<Disposition, number>; correct: number; incorrect: number };
  /** Scoring version 2: of each cell, the receipts the engine did not fully decide. */
  undecided_by_cell?: Record<Disposition, Record<Disposition, number>>;
}

export type RuleStatus = "tested" | "raised_without_answers" | "not_tested";

export interface RuleDetection extends Prf {
  expected: number;
  predicted: number;
  duplicates: number;
  status: RuleStatus;
  evidence_exact: Rate;
}

export interface MissedFinding {
  receipt_id: string;
  rule_id: string;
  kind: "exception" | "signal";
  expected_line_ids: string[] | null;
  reason: "not_raised" | "raised" | "not_evaluated";
}

export interface UnexpectedFinding {
  receipt_id: string;
  rule_id: string;
  kind: "exception" | "signal";
  line_ids: string[];
  duplicate: boolean;
}

export interface EvidenceMetrics {
  available: true;
  matched_exceptions: number;
  scorable: number;
  not_scorable: number;
  exact: Rate;
  line_precision: Rate;
  line_recall: Rate;
  severity_exact: Rate;
  mismatches: { receipt_id: string; rule_id: string; expected_line_ids: string[]; cited_line_ids: string[]; missing_line_ids: string[]; extra_line_ids: string[] }[];
}

export interface BaselineLine {
  receipt_id: string;
  line_id: string;
  description: string;
  gold_category: string;
  gold_restricted: boolean;
  main: { category: string | null; restricted: boolean | null };
  baseline: { category: string | null; restricted: boolean; matched: string | null };
}

export interface BaselineMetrics {
  available: true;
  version: string;
  lines: number;
  restricted: Record<"main" | "baseline", Prf & { matrix: Record<"true" | "false", Record<"true" | "false" | "none", number>> }>;
  restricted_categories: Record<"main" | "baseline", { lines: number; accuracy: Rate; macro: { precision: number | null; recall: number | null; f1: number | null } } | null>;
  difference: Record<"precision" | "recall" | "f1", number | null>;
  lines_by_outcome: Record<"both_right" | "main_only" | "baseline_only" | "both_wrong", number>;
  outcomes: Record<"both_right" | "main_only" | "baseline_only" | "both_wrong", BaselineLine[]>;
  not_comparable: Record<string, string>;
}

export type Stage = "translation" | "classification" | "retrieval" | "rule_logic";

export interface DiagnosedFailure {
  id: string;
  receipt_id: string;
  line_ids: string[];
  rule_id: string | null;
  expected: Record<string, unknown>;
  actual: Record<string, unknown>;
  description: string;
  stage: Stage;
  evidence: Record<string, unknown>;
  consequences: string[];
  recommendation: string;
  placed_by: string;
}

export interface ErrorAnalysis {
  available: true;
  observed: number;
  by_stage: Record<Stage, number>;
  failures: DiagnosedFailure[];
  retrieval: string;
  method: string;
}

export interface RuleCoverageRow {
  kind: string;
  execution: "evaluated" | "gate_only" | "not_evaluated" | "never_runs" | "every_receipt";
  gate_receipts: number;
  subject_receipts: number;
  note: string | null;
  expected: number;
  predicted: number;
  undecided_receipts: number;
  positive_cases: boolean;
}

export interface CoverageMetrics {
  available: true;
  receipts: number;
  rules: number;
  executable_rules: number;
  exercised: string[];
  not_exercised: string[];
  with_positive_cases: string[];
  without_positive_cases: string[];
  with_undecided: string[];
  per_rule: Record<string, RuleCoverageRow>;
}

export interface FindingsMetrics {
  available: true;
  receipts: number;
  evaluated: number;
  execution_errors: number;
  /** Counts every EXC-03 uncertainty signal as a false positive, as the answers never hold one. */
  micro: Prf;
  micro_without_uncertainty_signals: Prf;
  /** Scoring version 1 only: version 2 keeps evidence in its own block. */
  evidence_exact?: Rate;
  per_rule: Record<string, Prf & Partial<RuleDetection>>;
  /** Scoring version 2. */
  macro?: { rules: number; precision: number | null; recall: number | null; f1: number | null };
  expected?: number;
  predicted?: number;
  rules_by_status?: Record<RuleStatus, string[]>;
  missing?: MissedFinding[];
  unexpected?: UnexpectedFinding[];
  attribution?: { receipt_id: string; expected_rule: string; raised_rule: string; shared_line_ids: string[] }[];
  attribution_counts?: Record<string, number>;
}

export interface OperationalMetrics {
  receipts: number;
  not_run: number;
  run: number;
  evaluated: number;
  execution_errors: Rate;
  execution_error_codes: Record<string, number>;
  lines: number;
  coverage: Rate;
  classification_errors: Rate;
  uncertainty: { receipts: Rate; lines: Rate; by_reason: Record<string, number> };
  undetermined_receipts: Rate;
  dispositions: Record<Disposition, number>;
  latency: { receipts_timed: number; total_ms: number | null; mean_per_receipt_ms: number | null };
}

/** GET /v1/evaluations/{id}/metrics. provisional: over the receipts stored so far, never stored. */
export interface EvaluationMetrics {
  evaluation_id: string;
  status: EvaluationStatus;
  provisional: boolean;
  schema_version: string;
  result_context: ResultContext;
  context_complete: boolean | null;
  classification: ClassificationMetrics | Unavailable;
  disposition: DispositionMetrics | Unavailable;
  findings: FindingsMetrics | Unavailable;
  operational: OperationalMetrics;
  /** How these metrics were scored: as the run finished, rescored with a later version, or provisional. */
  scored?: { kind: "original" | "rescored" | "provisional"; original_schema_version?: string; current_schema_version?: string; rescored_at?: string; rescorings?: { schema_version: string; rescored_at: string }[] };
  /** Scoring version 2 onwards: absent from metrics scored before it. */
  scoring?: { baseline_version: string; definitions: Record<string, string> };
  evidence?: EvidenceMetrics | Unavailable;
  baseline?: BaselineMetrics | Unavailable;
  error_analysis?: ErrorAnalysis | Unavailable;
  coverage?: CoverageMetrics | Unavailable;
}

// --- Evaluation results and comparison: services/evaluations.py ---

export interface ReceiptScoring {
  lines: Record<string, { expected: string; predicted: string | null; outcome: LineOutcome }>;
  /** Null when the receipt has no labelled line. */
  classification_correct: boolean | null;
  expected_disposition: Disposition | null;
  disposition_correct: boolean | null;
}

export interface EvaluationResultItem {
  receipt_id: string;
  index: number;
  status: "ok" | "error";
  error: { code: string; message: string } | null;
  context: ResultContext;
  latency_ms: number | null;
  expected: ExpectedAnswers;
  scoring: ReceiptScoring;
  /** The core's ReceiptResult, unchanged; null when the receipt could not be evaluated. */
  result: ReceiptResult | null;
  spend: SpendTotals;
}

export interface EvaluationResultPage extends Page<EvaluationResultItem> {
  evaluation_id: string;
}

export interface EvaluationResultFilters {
  receipt_id?: string[];
  /** true: every labelled line correct; false: any not. Receipts without labelled lines match neither. */
  classification_correct?: boolean;
  category?: string[];
  disposition?: Disposition[];
  error?: boolean;
  uncertain?: boolean;
}

export interface EvaluationComparisonRow {
  evaluation_id: string;
  label: string | null;
  dataset: { dataset_id: string; name: string; version: number; canonical_sha256: string };
  ruleset: { ruleset_id: string; name: string; version: number; artifact_sha256: string };
  execution_mode: "replay" | "live";
  backend: string | null;
  setup?: ClassificationSetup;
  /** What the run classified with, by each adapter's own name: equal for runs before and after the adapters' rename. */
  classification_identity?: string | null;
  model_label: string | null;
  models: string[];
  /** The effective translator: null when translation was off, or when translation_known is false. */
  translation_model?: string | null;
  /** False for a run that never recorded its translator: it is never taken to match another's. */
  translation_known?: boolean;
  langgraph: Record<string, unknown> | null;
  result_context: ResultContext | null;
  /** Rates with their Wilson intervals; null when the block had nothing to score. */
  category_accuracy: Rate | null;
  macro_f1: number | null;
  disposition_accuracy: Rate | null;
  uncertainty_rate: Rate;
  total_latency_ms: number | null;
  mean_latency_per_receipt_ms: number | null;
  provider_calls: SpendTotals["calls"];
  new_spend_usd: string;
  cost_completeness: CostCompleteness;
  historical_cost_usd: SpendTotals["historical"];
  /** Only for a live run whose cost is complete. */
  cost_per_receipt_usd: string | null;
  cost_per_correct_prediction_usd: string | null;
}

/** POST /v1/evaluations/compare: a side-by-side view of completed runs, not a controlled benchmark. */
export interface EvaluationComparison {
  /** issue_codes names each issue: dataset, ruleset, catalog, core_version, translation_model, translation_model_unknown,
   * result_context, failed_receipts, execution_mode. Absent from an API that predates it. */
  comparability: { directly_comparable: boolean; issues: string[]; issue_codes?: string[]; note: string };
  evaluations: EvaluationComparisonRow[];
}

// --- Discovery: schemas/discovery.py ---

export interface ReplaySetInfo {
  name: string;
  present: boolean;
  answer_files: number | null;
  folder: string | null;
}

/** One model node of an orchestrator: its execution adapter (backend), that adapter's provider, and its model. */
export interface OrchestratorNode {
  name: string | null;
  backend: string | null;
  model: string | null;
  provider: string | null;
  protocol: string | null;
  /** The adapter's own name, whichever name is configured or recorded. */
  adapter?: string | null;
}

/**
 * A registered backend, which is an execution adapter (the protocol a model needs: chat completions or the Decisions
 * API), or the LangGraph orchestrator. Its id is what requests and records name; it is not a provider.
 */
export interface BackendInfo {
  id: string;
  name: string;
  kind?: "adapter" | "orchestrator";
  /** Earlier names of this adapter (openrouter, jev) that requests, records and replay sets may still use. */
  legacy_ids?: string[];
  provider?: string | null;
  protocol?: string | null;
  installed: boolean;
  enabled: boolean;
  /** Installed and enabled, for replay or deterministic use. */
  available: boolean;
  default: boolean;
  default_model: string | null;
  allowed_models: string[];
  capabilities: {
    review?: boolean;
    revise?: boolean;
    order?: OrchestratorNode[];
    routing?: Record<string, unknown>;
    reflection?: Record<string, unknown>;
    [key: string]: unknown;
  };
  replay: { supported: boolean; sets: ReplaySetInfo[] };
  live: { permitted: boolean; credential_configured: boolean; available: boolean };
}

/** What POST /v1/documents/extractions can use here. Sets are named, never located. */
export interface PolicyCompilerInfo {
  /** The only models a live extraction may name: chat models, never a decision model or a translator. */
  extraction_models?: string[];
  replay: {
    supported: boolean;
    sets: {
      name: string;
      /** The folder exists: not which documents it holds readings for. */
      present: boolean;
      /** Live extraction records its readings here. */
      live_target: boolean;
    }[];
  };
  live: {
    /** ALLOW_LIVE */
    permitted: boolean;
    /** A live recording set is named. */
    configured: boolean;
    /** A key is set: not proof the provider would answer. */
    credential_configured: boolean;
    available: boolean;
  };
}

/** Which ruleset versions requests may use here. */
export interface RulesetPermissions {
  /** Validate or evaluate a version not published for production, as a preview. */
  preview_allowed: boolean;
  /** The publications an approval may authorize: production, simulation. */
  publication_modes: PublishMode[];
}

export interface OrchestratorInfo {
  /** direct, or langgraph, which is also its backend id in requests. */
  id: string;
  name: string;
  installed: boolean;
  enabled: boolean;
  available: boolean;
  default: boolean;
  /** LangGraph's model nodes in order, each with its own provider and model; null for direct. */
  nodes: OrchestratorNode[] | null;
}

export interface ProviderInfo {
  id: string;
  name: string;
  /** The execution adapters (backend ids) that call this provider. */
  adapters: string[];
  credential_configured: boolean;
}

/** A model the deployment names for classification, with the one execution adapter its ID needs. */
export interface ClassificationModelInfo {
  model: string;
  provider: string;
  /** The execution adapter a request names for this model: chosen from the model, never by the user. */
  backend: string;
  protocol: string;
  default: boolean;
  allowed: boolean;
  sources: string[];
  capabilities: Record<string, unknown>;
}

export interface Backends {
  default_backend: string;
  execution_modes: { default?: string; replay?: boolean; live?: boolean; deterministic?: boolean; [key: string]: unknown };
  credential_configured: boolean;
  /** The policy compiler's extraction allowlist, and each role's where the deployment sets none. */
  allowed_models: string[];
  /** Models a request may choose for classification, and for translation: kept apart. Absent from an older API. */
  allowed_classification_models?: string[];
  allowed_translation_models?: string[];
  backends: BackendInfo[];
  orchestrators: OrchestratorInfo[];
  providers: ProviderInfo[];
  classification_models: ClassificationModelInfo[];
  policy_compiler: PolicyCompilerInfo;
  rulesets: RulesetPermissions;
  /** ALLOW_UNCAPPED_EVALUATIONS: whether a live evaluation may run with no limit. */
  evaluations: { uncapped_allowed: boolean };
  /** The translator: called only for non-English lines, but a model a live run may call whenever enabled. */
  translation: {
    enabled: boolean;
    default_model: string | null;
    /** The default, and every allowed model the core accepts for chat completions, which the translator sends. */
    models: { model: string; provider?: string; default: boolean; allowed: boolean }[];
  };
}

// --- Validation: schemas/validation.py, and the core's BatchResult (expense_validator/models/results.py) ---

export interface ValidationClassification {
  backend?: string | null;
  model?: string | null;
  /** Changes to config.yaml's classification.langgraph: a whole node list, and reflection fields that differ. */
  langgraph?: {
    backends?: { name: string; backend: string; model: string }[];
    reflection?: { enabled?: boolean; max_retries?: number; max_total_calls_per_line?: number; feedback_context?: string };
  } | null;
  replay_set?: string | null;
}

export interface LineResult {
  line_id: string;
  description_raw: string;
  category: string | null;
  restricted: boolean | null;
  /** The model's own coarse estimate, not a calibrated probability. */
  confidence: number | null;
  english_translation: string | null;
  attributes: Record<string, string | boolean> | null;
  classification_error: string | null;
  classification_details: Record<string, unknown> | null;
}

export interface ExceptionResult {
  rule_id: string;
  policy_doc: string;
  severity: string | null;
  line_ids: string[];
  evidence: string;
  source_quote: string;
  explanation: string;
}

export interface ReviewSignalResult {
  rule_id: string;
  reason: string;
}

export interface DispositionReason {
  code: string;
  basis: string;
  rule_ids: string[];
}

export interface OpenQuestionResult {
  rule_id: string;
  line_ids: string[];
  reasons: string[];
}

export interface UncertaintyResult {
  line_id: string;
  field: string;
  state: string;
  reason: string;
  confidence: number | null;
}

export interface ReceiptResult {
  receipt_id: string;
  disposition: Disposition;
  line_items: LineResult[];
  /** Active exceptions only: replaced, waived and excused findings are not in the result. */
  exceptions: ExceptionResult[];
  review_signals: ReviewSignalResult[];
  decided: boolean;
  disposition_reasons: DispositionReason[];
  open_questions: OpenQuestionResult[];
  uncertainty: UncertaintyResult[];
  metadata: {
    model: string | null;
    translation_model: string | null;
    retriever: string;
    prompt_sha256: string | null;
    ruleset_version: string;
    replayed: boolean;
    latency_ms: number | null;
  };
  provenance: {
    mode: ExecutionMode;
    context_complete: boolean;
    receipts_in_context: number;
    ruleset_sha256: string;
    model_overrides: string[];
    classification_backend: string | null;
  };
}

export interface BatchResult {
  mode: ExecutionMode;
  context_complete: boolean;
  results: ReceiptResult[];
}

export interface RunResponse {
  run_id: string;
  status: "completed";
  ruleset: RunRuleset;
  execution_mode: ExecutionMode;
  preview: boolean;
  context_complete: boolean;
  classification: ClassificationSelection;
  results: BatchResult;
  /** Orchestrator, provider, adapter and model, from the receipts' provenance and metadata; null when they differ. */
  setup?: ClassificationSetup | null;
  execution: {
    started_at: string;
    completed_at: string;
    duration_ms: number;
    receipt_count: number;
    core_version: string;
    api_version: string;
  };
  created_at: string;
}

// --- Findings: schemas/runs.py ---

export type FindingKind = "exception" | "review_signal" | "open_question" | "uncertainty";

export interface Finding {
  finding_id: string;
  kind: FindingKind;
  receipt_id: string;
  receipt_disposition: Disposition;
  receipt_decided: boolean;
  rule_id: string | null;
  severity: string | null;
  line_ids: string[] | null;
  disposition_reasons: DispositionReason[];
  record: Record<string, unknown>;
}

export interface FindingPage extends Page<Finding> {
  run_id: string;
}

export interface FindingFilters {
  kind?: FindingKind[];
  receipt_id?: string[];
  disposition?: Disposition[];
}

/** GET /v1/taxonomy, the parts a rule editor reads. */
export interface FieldCatalog {
  fields: Record<string, { type?: string; unit?: string }>;
  vocabularies: Record<string, string[]>;
}

/** POST /v1/rulesets/{id}/edits: reviewed changes to rules, made into a new draft version. */
export interface RuleEditBody {
  actor: string;
  rationale: string;
  changes: { rule_id: string; set: Record<string, unknown>; rationale?: string }[];
}

export interface FieldChange {
  rule_id: string;
  field: string;
  before: unknown;
  after: unknown;
}

export interface RuleEdits {
  based_on: string;
  based_on_version: number;
  based_on_artifact_sha256: string;
  actor: string;
  rationale: string;
  at: string;
  changes: FieldChange[];
}

/** GET /v1/rulesets/{id}/readiness: where a version stands on Edit, Resolve, Approve, Publish. */
export interface Readiness {
  status: RulesetStatus;
  next_step: "submit" | "review" | "approve" | "publish" | "none";
  rules_changed: number;
  review_remaining: number;
  validation_errors: number;
  approval: "not_approved" | "approved" | "published";
  approval_mode: string | null;
  can_publish: boolean;
  blockers: { message: string; rule_id: string | null; comparison_id: string | null }[];
}
