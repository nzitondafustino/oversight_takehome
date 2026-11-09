/**
 * TanStack Query keys and hooks. Every key for a stored record starts with "records", so one
 * invalidation refreshes everything the API stores.
 */
import { keepPreviousData, QueryClient, replaceEqualDeep, useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef } from "react";
import { ApiError, requestText } from "./client";
import {
  cancelEvaluation,
  compareEvaluations,
  createDocument,
  createEvaluation,
  createExtraction,
  getExtractionJob,
  startExtractionJob,
  createRevision,
  createRuleset,
  getBackends,
  getDataset,
  getDocument,
  getEvaluation,
  getEvaluationMetrics,
  rescoreEvaluation,
  getExtraction,
  getExtractionBaseline,
  getRulesetChanges,
  getHealth,
  getRuleset,
  getRulesetComparisons,
  getRun,
  listDatasets,
  listDocuments,
  listEvaluationResults,
  listEvaluations,
  listExtractionComparisons,
  listExtractions,
  listFindings,
  listRulesets,
  listRuns,
  reviewExtraction,
  runExportUrl,
  transitionRuleset,
  uploadDataset,
  validateReceipts,
  createChangeRequest,
  getExtractionReview,
  listChangeRequests,
  resolveChangeRequest,
  updateDocumentProfile,
  actOnRevisionApproval,
  getComparisonSource,
  getRevisionApproval,
  editRules,
  getFieldCatalog,
  getReadiness,
} from "./endpoints";
import {
  isActiveEvaluation,
  type DatasetUpload,
  type DocumentCreate,
  type DocumentDetail,
  type EvaluationCreate,
  type EvaluationDetail,
  type EvaluationFilters,
  type EvaluationResultFilters,
  type EvaluationSummary,
  type ExtractionCreate,
  type ExtractionJob,
  type ExtractionReviewCreate,
  type ExtractionSummary,
  type FindingFilters,
  type PageParams,
  type RevisionCreate,
  type RulesetCreate,
  type RulesetDetail,
  type RulesetTransition,
  type RunFilters,
  type ChangeRequestCreate,
  type DocumentSummary,
  type ProfileUpdate,
  type ApprovalActionBody,
  type FieldComparison,
  type RuleEditBody,
} from "./types";

/** How often an active evaluation is polled. The worker heartbeats each receipt, so seconds are enough. */
export const ACTIVE_EVALUATION_POLL_MS = 2000;

/**
 * Each record kind has a "list" prefix for its pages and a "detail" prefix per record; what is read about one
 * record (an evaluation's metrics and results, an extraction's comparisons) sits under its detail key, so one
 * invalidation refreshes the record and everything read from it.
 */
export const queryKeys = {
  health: ["health"] as const,
  records: ["records"] as const,
  runs: (params: PageParams & RunFilters) => ["records", "runs", params] as const,
  evaluations: (params: PageParams & EvaluationFilters) => ["records", "evaluations", "list", params] as const,
  evaluationLists: () => ["records", "evaluations", "list"] as const,
  evaluation: (evaluationId: string) => ["records", "evaluations", "detail", evaluationId] as const,
  evaluationMetrics: (evaluationId: string) => ["records", "evaluations", "detail", evaluationId, "metrics"] as const,
  evaluationResults: (evaluationId: string, params: PageParams & EvaluationResultFilters) => ["records", "evaluations", "detail", evaluationId, "results", params] as const,
  evaluationComparison: (evaluationIds: readonly string[]) => ["records", "evaluations", "compare", [...evaluationIds]] as const,
  datasets: (params: PageParams & { name?: string; include_superseded?: boolean }) => ["records", "datasets", "list", params] as const,
  datasetLists: () => ["records", "datasets", "list"] as const,
  dataset: (datasetId: string, includeCases: boolean) => ["records", "datasets", "detail", datasetId, { includeCases }] as const,
  rulesets: (params: PageParams & { name?: string; include_superseded?: boolean }) => ["records", "rulesets", "list", params] as const,
  rulesetLists: () => ["records", "rulesets", "list"] as const,
  ruleset: (rulesetId: string) => ["records", "rulesets", "detail", rulesetId] as const,
  rulesetComparisons: (rulesetId: string, reviewRequired: boolean) => ["records", "rulesets", "detail", rulesetId, "comparisons", { reviewRequired }] as const,
  documents: (params: PageParams & { filename?: string }) => ["records", "documents", "list", params] as const,
  /** Every document page and revision: a new revision changes which one is the latest. */
  allDocuments: () => ["records", "documents"] as const,
  document: (documentId: string, revision?: number) => ["records", "documents", "detail", documentId, revision ?? "latest"] as const,
  extractions: (params: PageParams & { document_id?: string }) => ["records", "extractions", "list", params] as const,
  extractionLists: () => ["records", "extractions", "list"] as const,
  extraction: (extractionId: string) => ["records", "extractions", "detail", extractionId] as const,
  extractionReview: (extractionId: string, reviewId: string) => ["records", "extractions", "detail", extractionId, "review", reviewId] as const,
  changeRequests: (extractionId: string) => ["records", "extractions", "detail", extractionId, "change-requests"] as const,
  documentLibrary: () => ["records", "documents", "library"] as const,
  extractionComparisons: (extractionId: string, params: PageParams & { review_required?: boolean }) =>
    ["records", "extractions", "detail", extractionId, "comparisons", params] as const,
  runLists: () => ["records", "runs"] as const,
  run: (runId: string) => ["records", "run", runId] as const,
  findings: (runId: string, params: PageParams & FindingFilters) => ["records", "run", runId, "findings", params] as const,
  backends: ["backends"] as const,
};

export function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        refetchOnWindowFocus: false,
        // Retry only what may pass on its own: no answer, a gateway error or a brief database outage.
        retry: (failureCount, error) => error instanceof ApiError && error.isTransient && failureCount < 2,
      },
    },
  });
}

export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: ({ signal }) => getHealth({ signal }),
    refetchInterval: 30_000,
    retry: false,
  });
}

export function useRuns(params: PageParams & RunFilters = {}) {
  return useQuery({
    queryKey: queryKeys.runs(params),
    queryFn: ({ signal }) => listRuns(params, { signal }),
    placeholderData: keepPreviousData,
  });
}

/** Evaluation lists are never polled: only the active jobs in them are (useActiveEvaluations). */
export function useEvaluations(params: PageParams & EvaluationFilters = {}) {
  return useQuery({
    queryKey: queryKeys.evaluations(params),
    queryFn: ({ signal }) => listEvaluations(params, { signal }),
    placeholderData: keepPreviousData,
  });
}

export function useRulesets(params: PageParams & { name?: string; include_superseded?: boolean } = {}) {
  return useQuery({
    queryKey: queryKeys.rulesets(params),
    queryFn: ({ signal }) => listRulesets(params, { signal }),
    placeholderData: keepPreviousData,
  });
}

export function useDocuments(params: PageParams & { filename?: string } = {}) {
  return useQuery({
    queryKey: queryKeys.documents(params),
    queryFn: ({ signal }) => listDocuments(params, { signal }),
    placeholderData: keepPreviousData,
  });
}

/**
 * Polls GET /v1/evaluations/{id} for each listed job that is queued, running or cancel_requested, and
 * for nothing else. Polling stops on its own once a job reaches a terminal status, and the lists are then
 * invalidated so they show the finished job with its metrics and spend.
 */
/**
 * The newer of two states of one job, by updated_at, which every write to a job sets. A poll sent before a
 * cancel can answer after it: its older state never replaces the newer one.
 */
export function newerEvaluation(cached: unknown, incoming: EvaluationDetail): EvaluationDetail {
  const current = cached as EvaluationDetail | undefined;
  if (current?.evaluation_id === incoming.evaluation_id && Date.parse(incoming.updated_at) < Date.parse(current.updated_at)) return current;
  return incoming;
}

/** structuralSharing for every evaluation detail query: keep the newer state, then share unchanged parts. */
function keepNewerEvaluation(cached: unknown, incoming: unknown): unknown {
  return replaceEqualDeep(cached, newerEvaluation(cached, incoming as EvaluationDetail));
}

export function useActiveEvaluations(listed: readonly EvaluationSummary[]) {
  const queryClient = useQueryClient();
  const activeIds = useMemo(
    () => listed.filter((item) => isActiveEvaluation(item.status)).map((item) => item.evaluation_id),
    [listed],
  );

  const results = useQueries({
    queries: activeIds.map((evaluationId) => ({
      queryKey: queryKeys.evaluation(evaluationId),
      queryFn: ({ signal }: { signal: AbortSignal }) => getEvaluation(evaluationId, { signal }),
      staleTime: 0,
      structuralSharing: keepNewerEvaluation,
      refetchInterval: (query: { state: { data?: { status: EvaluationSummary["status"] } } }) =>
        query.state.data && !isActiveEvaluation(query.state.data.status) ? false : ACTIVE_EVALUATION_POLL_MS,
      refetchIntervalInBackground: false,
    })),
  });

  const finishedKey = results
    .map((result) => (result.data && !isActiveEvaluation(result.data.status) ? result.data.evaluation_id : ""))
    .filter(Boolean)
    .join(",");

  useEffect(() => {
    if (finishedKey) void queryClient.invalidateQueries({ queryKey: queryKeys.evaluationLists() });
  }, [finishedKey, queryClient]);

  return activeIds.map((evaluationId, index) => ({ evaluationId, query: results[index]! }));
}

export function useBackends() {
  return useQuery({ queryKey: queryKeys.backends, queryFn: ({ signal }) => getBackends({ signal }), staleTime: 5 * 60_000 });
}

/** The policy compiler's recording sets and whether live extraction could run here, from GET /v1/backends. */
export function usePolicyCompiler() {
  return useQuery({
    queryKey: queryKeys.backends,
    queryFn: ({ signal }) => getBackends({ signal }),
    staleTime: 5 * 60_000,
    select: (backends) => backends.policy_compiler,
  });
}

/** A stored run never changes, so it is never refetched once loaded. */
export function useRun(runId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.run(runId ?? ""),
    queryFn: ({ signal }) => getRun(runId!, { signal }),
    enabled: Boolean(runId),
    staleTime: Infinity,
  });
}

/**
 * The stored run as JSON text, from GET /v1/runs/{id}/export?format=json: the run exactly as GET /v1/runs/{id}
 * returns it, canonical and byte for byte. Loaded only when someone opens a JSON view; a run never changes.
 */
export function useRunJson(runId: string, enabled: boolean) {
  return useQuery({
    queryKey: [...queryKeys.run(runId), "json"],
    queryFn: ({ signal }) => requestText(runExportUrl(runId, "json"), { signal }),
    enabled,
    staleTime: Infinity,
  });
}

export function useFindings(runId: string, params: PageParams & FindingFilters) {
  return useQuery({
    queryKey: queryKeys.findings(runId, params),
    queryFn: ({ signal }) => listFindings(runId, params, { signal }),
    placeholderData: keepPreviousData,
    staleTime: Infinity,
  });
}

/**
 * POST /v1/validate. On success the new run is cached as a stored run, and run lists (Run History and the
 * dashboard) are invalidated. Nothing is retried: a validation is not idempotent, as each creates a run.
 */
export function useValidate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (serializedBody: string) => validateReceipts(serializedBody),
    retry: false,
    onSuccess: (run) => {
      queryClient.setQueryData(queryKeys.run(run.run_id), run);
      void queryClient.invalidateQueries({ queryKey: queryKeys.runLists() });
    },
  });
}

/** Refresh every stored record shown. */
export function useRefreshRecords() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: queryKeys.records });
}

// --- Evaluation Workbench (UI-3) ---

/**
 * One evaluation, polled every 2 s only while the API reports it queued, running or cancel_requested. When it
 * finishes, its metrics and results are refetched (they were provisional) and the lists show it finished.
 */
export function useEvaluation(evaluationId: string | undefined) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: queryKeys.evaluation(evaluationId ?? ""),
    queryFn: ({ signal }) => getEvaluation(evaluationId!, { signal }),
    enabled: Boolean(evaluationId),
    staleTime: 0,
    structuralSharing: keepNewerEvaluation,
    refetchInterval: (current) => (current.state.data && isActiveEvaluation(current.state.data.status) ? ACTIVE_EVALUATION_POLL_MS : false),
    refetchIntervalInBackground: false,
  });

  const status = query.data?.status;
  const previous = useRef(status);
  useEffect(() => {
    const was = previous.current;
    previous.current = status;
    if (!evaluationId || !was || !status || !isActiveEvaluation(was) || isActiveEvaluation(status)) return;
    void queryClient.invalidateQueries({ queryKey: queryKeys.evaluation(evaluationId) });
    void queryClient.invalidateQueries({ queryKey: queryKeys.evaluationLists() });
  }, [evaluationId, status, queryClient]);

  return query;
}

/** Never polled: a running job's metrics are provisional and recomputed per request. Stored metrics never change. */
export function useEvaluationMetrics(evaluationId: string | undefined, scoring: "latest" | "original" = "latest") {
  return useQuery({
    queryKey: [...queryKeys.evaluationMetrics(evaluationId ?? ""), scoring] as const,
    queryFn: ({ signal }) => getEvaluationMetrics(evaluationId!, scoring, { signal }),
    enabled: Boolean(evaluationId),
    staleTime: (current) => (current.state.data?.provisional === false ? Infinity : 0),
  });
}

/** Several runs' stored metrics, each under the same key as one run's, so the comparison and the detail page share them. */
export function useEvaluationsMetrics(evaluationIds: readonly string[]) {
  return useQueries({
    queries: evaluationIds.map((evaluationId) => ({
      queryKey: [...queryKeys.evaluationMetrics(evaluationId), "latest"] as const,
      queryFn: ({ signal }: { signal: AbortSignal }) => getEvaluationMetrics(evaluationId, "latest", { signal }),
      // Stored metrics never change; provisional ones, cached while the run was watched, are read again.
      staleTime: (current: { state: { data?: { provisional: boolean } } }) => (current.state.data?.provisional === false ? Infinity : 0),
    })),
  });
}

/** Rescores a finished run; its metrics, original and latest, are read again. */
export function useRescoreEvaluation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (evaluationId: string) => rescoreEvaluation(evaluationId),
    retry: false,
    onSuccess: (_, evaluationId) => void queryClient.invalidateQueries({ queryKey: queryKeys.evaluationMetrics(evaluationId) }),
  });
}

export function useEvaluationResults(evaluationId: string | undefined, params: PageParams & EvaluationResultFilters = {}) {
  return useQuery({
    queryKey: queryKeys.evaluationResults(evaluationId ?? "", params),
    queryFn: ({ signal }) => listEvaluationResults(evaluationId!, params, { signal }),
    enabled: Boolean(evaluationId),
    placeholderData: keepPreviousData,
  });
}

/** POST /v1/evaluations/compare is a read of completed runs, which never change: fetched once per selection. */
export function useEvaluationComparison(evaluationIds: readonly string[]) {
  return useQuery({
    queryKey: queryKeys.evaluationComparison(evaluationIds),
    queryFn: ({ signal }) => compareEvaluations(evaluationIds, { signal }),
    enabled: evaluationIds.length >= 2,
    staleTime: Infinity,
  });
}

export function useDatasets(params: PageParams & { name?: string; include_superseded?: boolean } = {}) {
  return useQuery({
    queryKey: queryKeys.datasets(params),
    queryFn: ({ signal }) => listDatasets(params, { signal }),
    placeholderData: keepPreviousData,
  });
}

/** A dataset version never changes once stored. */
export function useDataset(datasetId: string | undefined, includeCases = false) {
  return useQuery({
    queryKey: queryKeys.dataset(datasetId ?? "", includeCases),
    queryFn: ({ signal }) => getDataset(datasetId!, includeCases, { signal }),
    enabled: Boolean(datasetId),
    staleTime: Infinity,
  });
}

/** Uploading the same bytes again returns the stored version, so a repeat is harmless; it is still never retried. */
export function useUploadDataset() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (upload: DatasetUpload) => uploadDataset(upload),
    retry: false,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.datasetLists() }),
  });
}

/** Queues a job, which then shows up polled by useEvaluation. Never retried: send an idempotency_key to repeat safely. */
export function useCreateEvaluation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: EvaluationCreate) => createEvaluation(body),
    retry: false,
    onSuccess: (job) => cacheEvaluation(queryClient, job),
  });
}

export function useCancelEvaluation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (evaluationId: string) => cancelEvaluation(evaluationId),
    retry: false,
    onSuccess: (job) => cacheEvaluation(queryClient, job),
  });
}

/** A poll still in flight is cancelled first, and an older state never replaces this answer (newerEvaluation). */
async function cacheEvaluation(queryClient: QueryClient, job: EvaluationDetail) {
  const key = queryKeys.evaluation(job.evaluation_id);
  await queryClient.cancelQueries({ queryKey: key, exact: true });
  queryClient.setQueryData<EvaluationDetail>(key, (cached) => newerEvaluation(cached, job));
  void queryClient.invalidateQueries({ queryKey: queryKeys.evaluationLists() });
}

// --- Policy Management (UI-4) ---

/** With a revision, an immutable record; without one, the latest, which a new revision replaces. */
export function useDocument(documentId: string | undefined, revision?: number) {
  return useQuery({
    queryKey: queryKeys.document(documentId ?? "", revision),
    queryFn: ({ signal }) => getDocument(documentId!, revision, { signal }),
    enabled: Boolean(documentId),
    staleTime: revision === undefined ? 30_000 : Infinity,
  });
}

export function useCreateDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: DocumentCreate) => createDocument(body),
    retry: false,
    onSuccess: (document) => cacheDocument(queryClient, document),
  });
}

export function useCreateRevision() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ documentId, body }: { documentId: string; body: RevisionCreate }) => createRevision(documentId, body),
    retry: false,
    onSuccess: (document) => cacheDocument(queryClient, document),
  });
}

function cacheDocument(queryClient: QueryClient, document: DocumentDetail) {
  void queryClient.invalidateQueries({ queryKey: queryKeys.allDocuments() });
  queryClient.setQueryData(queryKeys.document(document.document_id, document.revision), document);
}

export function useExtractions(params: PageParams & { document_id?: string } = {}) {
  return useQuery({
    queryKey: queryKeys.extractions(params),
    queryFn: ({ signal }) => listExtractions(params, { signal }),
    placeholderData: keepPreviousData,
  });
}

export function useExtraction(extractionId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.extraction(extractionId ?? ""),
    queryFn: ({ signal }) => getExtraction(extractionId!, { signal }),
    enabled: Boolean(extractionId),
  });
}

/** A version beside the version published before it. */
export function useRulesetChanges(rulesetId: string | undefined) {
  return useQuery({
    queryKey: ["records", "rulesets", "changes", rulesetId ?? ""] as const,
    queryFn: ({ signal }) => getRulesetChanges(rulesetId!, { signal }),
    enabled: Boolean(rulesetId),
  });
}

/** The compilation beside the latest published version, refreshed when something is published. */
export function useExtractionBaseline(extractionId: string | undefined) {
  return useQuery({
    queryKey: ["records", "extractions", "baseline", extractionId ?? ""] as const,
    queryFn: ({ signal }) => getExtractionBaseline(extractionId!, { signal }),
    enabled: Boolean(extractionId),
  });
}

/** An extraction's comparisons are a stored snapshot: they never change. */
export function useExtractionComparisons(extractionId: string | undefined, params: PageParams & { review_required?: boolean } = {}) {
  return useQuery({
    queryKey: queryKeys.extractionComparisons(extractionId ?? "", params),
    queryFn: ({ signal }) => listExtractionComparisons(extractionId!, params, { signal }),
    enabled: Boolean(extractionId),
    placeholderData: keepPreviousData,
    staleTime: Infinity,
  });
}

/** Starts an extraction in the background: a slow live one is never cut off by a request timeout. */
export function useStartExtractionJob() {
  return useMutation({ mutationFn: (body: ExtractionCreate) => startExtractionJob(body), retry: false });
}

/** A background extraction, refreshed every second until it finishes; then its extraction lists are refreshed. */
export function useExtractionJob(jobId: string | null) {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: ["records", "extraction-jobs", jobId ?? ""],
    queryFn: async ({ signal }) => {
      const job = await getExtractionJob(jobId!, { signal });
      if (job.status === "succeeded") void queryClient.invalidateQueries({ queryKey: queryKeys.extractionLists() });
      return job;
    },
    enabled: !!jobId,
    refetchInterval: (query: { state: { data?: ExtractionJob } }) => (query.state.data && ["succeeded", "failed"].includes(query.state.data.status) ? false : 1000),
    refetchIntervalInBackground: true,
  });
}

/** Replay unless the body asks for live, which calls a paid model: the API refuses it unless the deployment allows it. */
export function useCreateExtraction() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ExtractionCreate) => createExtraction(body),
    retry: false,
    onSuccess: (extraction: ExtractionSummary) => {
      queryClient.setQueryData(queryKeys.extraction(extraction.extraction_id), extraction);
      void queryClient.invalidateQueries({ queryKey: queryKeys.extractionLists() });
    },
  });
}

/** Every review is stored, ready or not, so the extraction's review list changes. */
export function useReviewExtraction() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ extractionId, body }: { extractionId: string; body: ExtractionReviewCreate }) => reviewExtraction(extractionId, body),
    retry: false,
    onSuccess: (review) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.extraction(review.extraction_id), exact: true });
      void queryClient.invalidateQueries({ queryKey: queryKeys.extractionLists() });
    },
  });
}

export function useRuleset(rulesetId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.ruleset(rulesetId ?? ""),
    queryFn: ({ signal }) => getRuleset(rulesetId!, { signal }),
    enabled: Boolean(rulesetId),
  });
}

/** A version's comparisons are written once with it. */
export function useRulesetComparisons(rulesetId: string | undefined, reviewRequired = false) {
  return useQuery({
    queryKey: queryKeys.rulesetComparisons(rulesetId ?? "", reviewRequired),
    queryFn: ({ signal }) => getRulesetComparisons(rulesetId!, reviewRequired, { signal }),
    enabled: Boolean(rulesetId),
    staleTime: Infinity,
  });
}

export function useCreateRuleset() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: RulesetCreate) => createRuleset(body),
    retry: false,
    onSuccess: (version) => cacheRuleset(queryClient, version),
  });
}

/**
 * submit, review, approve or publish. Never retried: each step is checked against the version's current state,
 * and the API's refusal (such as approval_does_not_hold) is the answer to show.
 */
export function useRulesetTransition() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ rulesetId, transition }: { rulesetId: string; transition: RulesetTransition }) => transitionRuleset(rulesetId, transition),
    retry: false,
    onSuccess: (version) => cacheRuleset(queryClient, version),
  });
}

/** Lists are refreshed too: a status change decides which versions validation and evaluation may use. */
function cacheRuleset(queryClient: QueryClient, version: RulesetDetail) {
  queryClient.setQueryData(queryKeys.ruleset(version.ruleset_id), version);
  void queryClient.invalidateQueries({ queryKey: queryKeys.rulesetLists() });
}

/** Every document's latest revision, read a page of 100 at a time: the library searches, groups and filters them here. */
export function useDocumentLibrary() {
  return useQuery({
    queryKey: queryKeys.documentLibrary(),
    queryFn: async ({ signal }) => {
      const items: DocumentSummary[] = [];
      for (let offset = 0; ; offset += 100) {
        const page = await listDocuments({ limit: 100, offset }, { signal });
        items.push(...page.items);
        if (items.length >= page.total || !page.items.length) return items;
      }
    },
    staleTime: 15_000,
  });
}

export function useUpdateDocumentProfile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ documentId, body }: { documentId: string; body: ProfileUpdate }) => updateDocumentProfile(documentId, body),
    retry: false,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.allDocuments() }),
  });
}

/** Marks (or, with null, restores) documents as duplicates, one profile change each. Nothing is deleted. */
export function useMarkDuplicates() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ actor, changes }: { actor: string; changes: { document_id: string; duplicate_of: string | null }[] }) => {
      for (const change of changes) await updateDocumentProfile(change.document_id, { actor, duplicate_of: change.duplicate_of });
    },
    retry: false,
    onSettled: () => void queryClient.invalidateQueries({ queryKey: queryKeys.allDocuments() }),
  });
}

/** One stored review with its decisions: never changes, so it is never refetched. */
export function useExtractionReview(extractionId: string | undefined, reviewId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.extractionReview(extractionId ?? "", reviewId ?? ""),
    queryFn: ({ signal }) => getExtractionReview(extractionId!, reviewId!, { signal }),
    enabled: Boolean(extractionId && reviewId),
    staleTime: Infinity,
    // Saving a decision makes a new review: the previous one stays in view while it loads, so the reviewer keeps
    // their place and sees the outcome of what they saved.
    placeholderData: keepPreviousData,
  });
}

export function useChangeRequests(extractionId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.changeRequests(extractionId ?? ""),
    queryFn: ({ signal }) => listChangeRequests(extractionId!, { signal }),
    enabled: Boolean(extractionId),
  });
}

function refreshChangeRequests(queryClient: QueryClient, extractionId: string) {
  void queryClient.invalidateQueries({ queryKey: queryKeys.changeRequests(extractionId) });
  void queryClient.invalidateQueries({ queryKey: queryKeys.extraction(extractionId), exact: true });
  void queryClient.invalidateQueries({ queryKey: queryKeys.extractionLists() });
}

export function useCreateChangeRequest() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ extractionId, body }: { extractionId: string; body: ChangeRequestCreate }) => createChangeRequest(extractionId, body),
    retry: false,
    onSuccess: (request) => refreshChangeRequests(queryClient, request.extraction_id),
  });
}

export function useResolveChangeRequest() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ extractionId, changeRequestId, body }: { extractionId: string; changeRequestId: string; body: { actor: string; note: string } }) => resolveChangeRequest(extractionId, changeRequestId, body),
    retry: false,
    onSuccess: (request) => refreshChangeRequests(queryClient, request.extraction_id),
  });
}

export function useRevisionApproval(documentId: string | undefined, revision: number | undefined) {
  return useQuery({
    queryKey: ["records", "documents", "approval", documentId ?? "", revision ?? 0] as const,
    queryFn: ({ signal }) => getRevisionApproval(documentId!, revision!, { signal }),
    enabled: Boolean(documentId && revision),
  });
}

export function useRevisionApprovalAction() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ documentId, revision, body }: { documentId: string; revision: number; body: ApprovalActionBody }) => actOnRevisionApproval(documentId, revision, body),
    retry: false,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.allDocuments() }),
  });
}

/** Every comparison of an extraction, read a page of 500 at a time: the review filters them here. Never changes. */
export function useAllExtractionComparisons(extractionId: string | undefined) {
  return useQuery({
    queryKey: ["records", "extractions", "detail", extractionId ?? "", "all-comparisons"] as const,
    queryFn: async ({ signal }) => {
      const items: FieldComparison[] = [];
      for (let offset = 0; ; offset += 500) {
        const page = await listExtractionComparisons(extractionId!, { limit: 500, offset }, { signal });
        items.push(...page.items);
        if (items.length >= page.total || !page.items.length) return items;
      }
    },
    enabled: Boolean(extractionId),
    staleTime: Infinity,
  });
}

export function useComparisonSource(owner: { extractionId: string } | { rulesetId: string } | undefined, comparisonId: string | undefined) {
  const id = owner ? ("extractionId" in owner ? `ext:${owner.extractionId}` : `rs:${owner.rulesetId}`) : "";
  return useQuery({
    queryKey: ["records", "sources", id, comparisonId ?? ""] as const,
    queryFn: ({ signal }) => getComparisonSource(owner!, comparisonId!, { signal }),
    enabled: Boolean(owner && comparisonId),
    staleTime: Infinity,
  });
}

export function useFieldCatalog() {
  return useQuery({ queryKey: ["catalog"] as const, queryFn: ({ signal }) => getFieldCatalog({ signal }), staleTime: Infinity });
}

/** What stands between a version and publication now: refetched whenever the version changes, including when its review is accepted. */
export function useReadiness(rulesetId: string | undefined, status?: string, review?: string | null) {
  return useQuery({
    queryKey: ["records", "rulesets", "readiness", rulesetId ?? "", status ?? "", review ?? ""] as const,
    queryFn: ({ signal }) => getReadiness(rulesetId!, { signal }),
    enabled: Boolean(rulesetId),
  });
}

export function useEditRules() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ rulesetId, body }: { rulesetId: string; body: RuleEditBody }) => editRules(rulesetId, body),
    retry: false,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["records", "rulesets"] }),
  });
}
