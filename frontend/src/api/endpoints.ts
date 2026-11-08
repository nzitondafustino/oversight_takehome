/** One function per backend route the frontend reads. Paths and parameters are the FastAPI routes' own. */
import { buildUrl, request } from "./client";
import type {
  Backends,
  DatasetDetail,
  DatasetSummary,
  DatasetUpload,
  DocumentCreate,
  DocumentDetail,
  DocumentSummary,
  EvaluationComparison,
  EvaluationCreate,
  EvaluationDetail,
  EvaluationFilters,
  EvaluationMetrics,
  EvaluationResultFilters,
  EvaluationResultPage,
  EvaluationSummary,
  ExtractionComparisonPage,
  ExtractionCreate,
  ExtractionListItem,
  ExtractionReviewCreate,
  ExtractionReviewDetail,
  ExtractionSummary,
  FindingFilters,
  FindingPage,
  Health,
  Page,
  PageParams,
  RevisionCreate,
  RulesetComparisons,
  RulesetCreate,
  RulesetDetail,
  RulesetSummary,
  RulesetTransition,
  RunFilters,
  RunResponse,
  RunSummary,
} from "./types";

type Signal = { signal?: AbortSignal };

/** GET /health. Its 503 carries a degraded Health body, which is an answer, not an error. */
export function getHealth({ signal }: Signal = {}) {
  return request<Health>("/health", { signal, acceptStatuses: [503] });
}

/** GET /v1/runs: runs, newest first, without their results. */
export function listRuns(page: PageParams & RunFilters = {}, { signal }: Signal = {}) {
  return request<Page<RunSummary>>("/v1/runs", { signal, query: { ...page } });
}

/** GET /v1/evaluations: summaries, newest first, with key metrics and new spend. */
export function listEvaluations(page: PageParams & EvaluationFilters = {}, { signal }: Signal = {}) {
  return request<Page<EvaluationSummary>>("/v1/evaluations", { signal, query: { ...page } });
}

/** GET /v1/evaluations/{id}: status, progress and spending; polled while a job is active. */
export function getEvaluation(evaluationId: string, { signal }: Signal = {}) {
  return request<EvaluationDetail>(`/v1/evaluations/${encodeURIComponent(evaluationId)}`, { signal });
}

/** GET /v1/rulesets: versions with their lifecycle state, newest first. */
export function listRulesets(page: PageParams & { name?: string } = {}, { signal }: Signal = {}) {
  return request<Page<RulesetSummary>>("/v1/rulesets", { signal, query: { ...page } });
}

/** GET /v1/documents: each document's latest revision, without its text. */
export function listDocuments(page: PageParams & { filename?: string } = {}, { signal }: Signal = {}) {
  return request<Page<DocumentSummary>>("/v1/documents", { signal, query: { ...page } });
}

/** GET /v1/backends: which classification backends and modes this deployment supports. */
export function getBackends({ signal }: Signal = {}) {
  return request<Backends>("/v1/backends", { signal });
}

/** GET /v1/runs/{id}: the stored run, its full result read back exactly. Nothing is validated again. */
export function getRun(runId: string, { signal }: Signal = {}) {
  return request<RunResponse>(`/v1/runs/${encodeURIComponent(runId)}`, { signal });
}

/** GET /v1/runs/{id}/findings: one page of the stored run's findings, in the API's fixed order. */
export function listFindings(runId: string, params: PageParams & FindingFilters = {}, { signal }: Signal = {}) {
  return request<FindingPage>(`/v1/runs/${encodeURIComponent(runId)}/findings`, { signal, query: { ...params } });
}

/**
 * POST /v1/validate. The body is serialized by the caller (losslessJson), so receipt amounts reach the API
 * exactly as written: see pages/validate/request.ts.
 */
export function validateReceipts(serializedBody: string) {
  return request<RunResponse>("/v1/validate", { method: "POST", rawBody: serializedBody });
}

// --- Evaluation datasets ---

/** POST /v1/evaluations/datasets (multipart). 201 for a new version, 200 when these exact bytes were uploaded before. */
export function uploadDataset({ file, name, description, context_complete }: DatasetUpload) {
  const form = new FormData();
  form.append("file", file);
  if (name !== undefined) form.append("name", name);
  if (description !== undefined) form.append("description", description);
  if (context_complete !== undefined) form.append("context_complete", String(context_complete));
  return request<DatasetSummary>("/v1/evaluations/datasets", { method: "POST", formData: form });
}

/** GET /v1/evaluations/datasets: versions, newest first, without their content. */
export function listDatasets(params: PageParams & { name?: string } = {}, { signal }: Signal = {}) {
  return request<Page<DatasetSummary>>("/v1/evaluations/datasets", { signal, query: { ...params } });
}

export function getDataset(datasetId: string, includeCases = false, { signal }: Signal = {}) {
  return request<DatasetDetail>(`/v1/evaluations/datasets/${encodeURIComponent(datasetId)}`, { signal, query: { include_cases: includeCases || undefined } });
}

// --- Evaluations ---

/** POST /v1/evaluations: queues a job. Replay unless the body asks for live, which the API gates. */
export function createEvaluation(body: EvaluationCreate) {
  return request<EvaluationDetail>("/v1/evaluations", { method: "POST", body });
}

/** POST /v1/evaluations/{id}/cancel: cancels a queued job, or asks a running one to stop. */
export function cancelEvaluation(evaluationId: string) {
  return request<EvaluationDetail>(`/v1/evaluations/${encodeURIComponent(evaluationId)}/cancel`, { method: "POST", body: {} });
}

/** GET /v1/evaluations/{id}/metrics: stored once finished; provisional, never stored, while it runs. */
export function getEvaluationMetrics(evaluationId: string, { signal }: Signal = {}) {
  return request<EvaluationMetrics>(`/v1/evaluations/${encodeURIComponent(evaluationId)}/metrics`, { signal });
}

/** GET /v1/evaluations/{id}/results: each receipt's result with its answers, scoring and spend, in dataset order. */
export function listEvaluationResults(evaluationId: string, params: PageParams & EvaluationResultFilters = {}, { signal }: Signal = {}) {
  return request<EvaluationResultPage>(`/v1/evaluations/${encodeURIComponent(evaluationId)}/results`, { signal, query: { ...params } });
}

/** POST /v1/evaluations/compare: 2 to 10 completed runs side by side. A read: it stores nothing. */
export function compareEvaluations(evaluationIds: readonly string[], { signal }: Signal = {}) {
  return request<EvaluationComparison>("/v1/evaluations/compare", { method: "POST", signal, body: { evaluation_ids: evaluationIds } });
}

// --- Policy documents and extractions ---

export function createDocument(body: DocumentCreate) {
  return request<DocumentDetail>("/v1/documents", { method: "POST", body });
}

/** GET /v1/documents/{id}: one revision with its content, the latest when revision is left out. */
export function getDocument(documentId: string, revision?: number, { signal }: Signal = {}) {
  return request<DocumentDetail>(`/v1/documents/${encodeURIComponent(documentId)}`, { signal, query: { revision } });
}

export function createRevision(documentId: string, body: RevisionCreate) {
  return request<DocumentDetail>(`/v1/documents/${encodeURIComponent(documentId)}/revisions`, { method: "POST", body });
}

/** POST /v1/documents/extractions: the core's extraction of exact revisions, stored as a snapshot. */
export function createExtraction(body: ExtractionCreate) {
  return request<ExtractionSummary>("/v1/documents/extractions", { method: "POST", body });
}

/** GET /v1/documents/extractions: newest first, each with its reviews, without the fields read from the core's result. */
export function listExtractions(params: PageParams & { document_id?: string } = {}, { signal }: Signal = {}) {
  return request<Page<ExtractionListItem>>("/v1/documents/extractions", { signal, query: { ...params } });
}

export function getExtraction(extractionId: string, { signal }: Signal = {}) {
  return request<ExtractionSummary>(`/v1/documents/extractions/${encodeURIComponent(extractionId)}`, { signal });
}

export function listExtractionComparisons(extractionId: string, params: PageParams & { review_required?: boolean } = {}, { signal }: Signal = {}) {
  return request<ExtractionComparisonPage>(`/v1/documents/extractions/${encodeURIComponent(extractionId)}/comparisons`, { signal, query: { ...params } });
}

/** POST /v1/documents/extractions/{id}/review: stored whether ready or not; only a ready one makes a candidate. */
export function reviewExtraction(extractionId: string, body: ExtractionReviewCreate) {
  return request<ExtractionReviewDetail>(`/v1/documents/extractions/${encodeURIComponent(extractionId)}/review`, { method: "POST", body });
}

// --- Ruleset versions and their lifecycle ---

/** POST /v1/rulesets: the next draft version of a name. An artifact's numbers are sent exactly (JsonNumber). */
export function createRuleset(body: RulesetCreate) {
  return request<RulesetDetail>("/v1/rulesets", { method: "POST", body });
}

export function getRuleset(rulesetId: string, { signal }: Signal = {}) {
  return request<RulesetDetail>(`/v1/rulesets/${encodeURIComponent(rulesetId)}`, { signal });
}

/** GET /v1/rulesets/{id}/comparisons. An uploaded artifact has none: 409 not_compiled. */
export function getRulesetComparisons(rulesetId: string, reviewRequired = false, { signal }: Signal = {}) {
  return request<RulesetComparisons>(`/v1/rulesets/${encodeURIComponent(rulesetId)}/comparisons`, { signal, query: { review_required: reviewRequired || undefined } });
}

/** POST /v1/rulesets/{id}/submit | review | approve | publish. */
export function transitionRuleset(rulesetId: string, { step, body }: RulesetTransition) {
  return request<RulesetDetail>(`/v1/rulesets/${encodeURIComponent(rulesetId)}/${step}`, { method: "POST", body });
}

/** Export URLs: fetched by downloadFile, which reports API errors and saves the bytes under the API's filename. */
export function runExportUrl(runId: string, format: "json" | "csv") {
  return buildUrl(`/v1/runs/${encodeURIComponent(runId)}/export`, { format });
}

export function evaluationExportUrl(evaluationId: string, format: "json" | "csv") {
  return buildUrl(`/v1/evaluations/${encodeURIComponent(evaluationId)}/export`, { format });
}
