import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Providers, routes } from "@/App";
import { LimitUses, logicOf } from "@/pages/policy/review/LimitUses";
import { createQueryClient } from "@/api/queries";
import type { Backends, DocumentDetail, ExtractionSummary, ExtractionJob, FieldComparison, Page, Readiness, RulesetComparisons, RulesetDetail, SourceLocation } from "@/api/types";
import backendsJson from "./fixtures/backends.json";
import comparisonsJson from "./fixtures/policy/comparisons.json";
import documentJson from "./fixtures/policy/document.json";
import documentsJson from "./fixtures/policy/documents.json";
import extractionJson from "./fixtures/policy/extraction.json";
import extractionsJson from "./fixtures/policy/extractions.json";
import notReadyJson from "./fixtures/policy/review-not-ready.json";
import readyJson from "./fixtures/policy/review-ready.json";
import rulesetAcceptedJson from "./fixtures/policy/ruleset-accepted.json";
import rulesetApprovedJson from "./fixtures/policy/ruleset-approved.json";
import rulesetComparisonsJson from "./fixtures/policy/ruleset-comparisons.json";
import rulesetDraftJson from "./fixtures/policy/ruleset-draft.json";
import rulesetInReviewJson from "./fixtures/policy/ruleset-in-review.json";
import rulesetPublishedJson from "./fixtures/policy/ruleset-published.json";
import rulesetUploadedJson from "./fixtures/policy/ruleset-uploaded.json";
import rulesetsJson from "./fixtures/policy/rulesets.json";

/**
 * Real API responses, recorded with the backend's own test helpers: the nine policy documents, their replay
 * extraction from model readings, an incomplete and a ready review, and one version taken through every step.
 */
const DOCUMENT = documentJson as unknown as DocumentDetail;
const DOCUMENTS = documentsJson as unknown as Page<DocumentDetail>;
const EXTRACTION = extractionJson as unknown as ExtractionSummary;
const DRAFT = rulesetDraftJson as unknown as RulesetDetail;
const IN_REVIEW = rulesetInReviewJson as unknown as RulesetDetail;
const ACCEPTED = rulesetAcceptedJson as unknown as RulesetDetail;
const APPROVED = rulesetApprovedJson as unknown as RulesetDetail;
const PUBLISHED = rulesetPublishedJson as unknown as RulesetDetail;
const UPLOADED = rulesetUploadedJson as unknown as RulesetDetail;
const BACKENDS = backendsJson as unknown as Backends;
const ID = DRAFT.ruleset_id;

interface Call {
  method: string;
  path: string;
  body: string | undefined;
}
type Answer = { status: number; body: unknown };

let calls: Call[];
let answer: (method: string, url: URL) => Answer | undefined;
let backends: Backends;

beforeEach(() => {
  calls = [];
  backends = BACKENDS;
  window.localStorage.setItem("epv.actor", "r.eviewer");
  window.localStorage.removeItem("epv.sourceView");  // the review's document view: Formatted unless chosen
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = new URL(String(input), "http://localhost");
      const method = init.method ?? "GET";
      calls.push({ method, path: `${url.pathname}${url.search}`, body: typeof init.body === "string" ? init.body : undefined });
      let reply = answer(method, url);
      if (!reply && url.pathname.endsWith("/duplicate-publication")) reply = { status: 200, body: { published: null } };
      if (!reply && url.pathname === "/api/v1/backends") reply = { status: 200, body: backends };
      if (!reply && url.pathname === "/api/v1/documents") reply = { status: 200, body: DOCUMENTS };
      if (!reply && url.pathname === "/api/v1/documents/extractions") reply = { status: 200, body: extractionsJson };
      if (!reply && url.pathname === "/api/v1/rulesets") reply = { status: 200, body: rulesetsJson };
      if (!reply && url.pathname === "/api/health") reply = { status: 200, body: { status: "ok", application: "running", database: "ok", version: "0.1.0" } };
      reply ??= { status: 404, body: { error: { code: "not_found", message: `no stub for ${url.pathname}` } } };
      return new Response(JSON.stringify(reply.body), { status: reply.status, headers: { "Content-Type": "application/json" } });
    }),
  );
});

afterEach(() => vi.unstubAllGlobals());

/** Categories start collapsed: open each one. */
async function openCategories() {
  const user = userEvent.setup();
  for (const button of await screen.findAllByRole("button", { expanded: false, name: /·\s*\d+$/ })) await user.click(button);
}

function renderAt(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(
    <Providers client={createQueryClient()}>
      <RouterProvider router={router} />
    </Providers>,
  );
  return router;
}

const posts = (path: string) => calls.filter((call) => call.method === "POST" && call.path === path);
const refusal = (status: number, code: string, message: string, details?: unknown) => ({ status, body: { error: { code, message, ...(details ? { details } : {}) } } });

describe("policy documents", () => {
  it("stores an uploaded document exactly, and opens it", async () => {
    answer = (method, url) => (method === "POST" && url.pathname === "/api/v1/documents" ? { status: 201, body: DOCUMENT } : url.pathname === `/api/v1/documents/${DOCUMENT.document_id}` ? { status: 200, body: DOCUMENT } : undefined);
    const router = renderAt("/documents");
    await openCategories();
    const user = userEvent.setup();
    expect(await screen.findByText(DOCUMENT.filename)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Upload document" }));
    const store = screen.getByRole("button", { name: "Store document" });
    await user.type(screen.getByLabelText("Filename"), "meals policy.txt");
    await user.type(screen.getByLabelText("Document text"), "# Meals\n\nUp to $75.50 a day.");
    expect(store).toBeDisabled();
    expect(screen.getByText(/A filename is a bare .md name/)).toBeInTheDocument();

    await user.clear(screen.getByLabelText("Filename"));
    await user.type(screen.getByLabelText("Filename"), "meals-policy.md");
    await user.click(store);
    await waitFor(() => expect(router.state.location.pathname).toBe(`/documents/${DOCUMENT.document_id}`));
    expect(JSON.parse(posts("/api/v1/documents")[0]!.body!)).toEqual({ filename: "meals-policy.md", content: "# Meals\n\nUp to $75.50 a day." });
  });

  it("shows a revision's identity and text, and a refused new revision as the API reports it", async () => {
    answer = (method, url) => {
      if (method === "POST") return refusal(409, "revision_conflict", "revision 1 is not the latest revision, 2: start again from the latest");
      if (url.pathname === `/api/v1/documents/${DOCUMENT.document_id}`) return { status: 200, body: DOCUMENT };
      return undefined;
    };
    renderAt(`/documents/${DOCUMENT.document_id}`);
    const user = userEvent.setup();
    expect(await screen.findByText(DOCUMENT.content_sha256)).toBeInTheDocument();
    // Formatted by default: headings as headings, no Markdown marks; the exact text under Source.
    const formatted = screen.getByLabelText("Formatted document");
    expect(within(formatted).getByRole("heading", { level: 1 })).toHaveTextContent("Meals");
    expect(formatted).not.toHaveTextContent("# ");
    await user.click(screen.getByRole("radio", { name: "Source" }));
    expect(screen.getByLabelText("Document text")).toHaveTextContent("Meals");

    await user.click(screen.getByRole("button", { name: "New revision" }));
    const store = screen.getByRole("button", { name: "Store revision 2" });
    expect(store).toBeDisabled();
    await user.type(screen.getByLabelText("Revision text"), "\nA new clause.");
    await user.click(store);
    expect(await screen.findByText("revision_conflict")).toBeInTheDocument();
    expect(JSON.parse(posts(`/api/v1/documents/${DOCUMENT.document_id}/revisions`)[0]!.body!)).toMatchObject({ based_on_revision: 1 });
  });
});

describe("Markdown uploads and stored documents", () => {
  async function openUpload() {
    renderAt("/documents");
    await openCategories();
    const user = userEvent.setup({ applyAccept: false });  // the file picker's filter is only a hint: the page checks
    await user.click(await screen.findByRole("button", { name: "Upload document" }));
    return user;
  }

  it("reads a chosen .md file exactly, shows its name and size, and sends its text", async () => {
    answer = (method, url) => (method === "POST" && url.pathname === "/api/v1/documents" ? { status: 201, body: DOCUMENT } : url.pathname === `/api/v1/documents/${DOCUMENT.document_id}` ? { status: 200, body: DOCUMENT } : undefined);
    const user = await openUpload();
    const text = "# Meals\r\n\nCafé \u{1F37D} up to $75.50.\n";
    await user.upload(screen.getByLabelText("Markdown file"), new File([text], "meals-policy.md", { type: "text/markdown" }));
    expect(await screen.findByLabelText("Chosen file")).toHaveTextContent(`meals-policy.md · ${new TextEncoder().encode(text).length} bytes · UTF-8 Markdown`);
    expect(screen.getByLabelText("Filename")).toHaveValue("meals-policy.md");
    await user.click(screen.getByRole("button", { name: "Store document" }));
    await waitFor(() => expect(posts("/api/v1/documents")).toHaveLength(1));
    expect(JSON.parse(posts("/api/v1/documents")[0]!.body!)).toEqual({ filename: "meals-policy.md", content: text });
  });

  it("refuses a file that is not Markdown, and sends nothing", async () => {
    answer = () => undefined;
    const user = await openUpload();
    await user.upload(screen.getByLabelText("Markdown file"), new File(["%PDF-1.7"], "travel-policy.pdf", { type: "application/pdf" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Only Markdown (.md) files can be uploaded.");
    expect(screen.getByRole("button", { name: "Store document" })).toBeDisabled();
    expect(posts("/api/v1/documents")).toHaveLength(0);
  });

  it("refuses a file that is not valid UTF-8, never storing it changed", async () => {
    answer = () => undefined;
    const user = await openUpload();
    await user.upload(screen.getByLabelText("Markdown file"), new File([new Uint8Array([0x23, 0x20, 0x43, 0x61, 0x66, 0xe9])], "latin1.md", { type: "text/markdown" }));  // "# Café" in Latin-1
    expect(await screen.findByText(/The file is not valid UTF-8 text/)).toBeInTheDocument();
    expect(screen.getByLabelText("Document text")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Store document" })).toBeDisabled();
  });

  it("explains a storage failure, which stored nothing", async () => {
    answer = (method) => (method === "POST" ? refusal(503, "document_storage_unavailable", "policy document storage could not write policy-documents/doc_x/revisions/1/document.md: S3 denied access") : undefined);
    const user = await openUpload();
    await user.type(screen.getByLabelText("Filename"), "meals-policy.md");
    await user.type(screen.getByLabelText("Document text"), "# Meals");
    await user.click(screen.getByRole("button", { name: "Store document" }));
    expect(await screen.findByText("document_storage_unavailable")).toBeInTheDocument();
    expect(screen.getByText(/S3 denied access/)).toBeInTheDocument();
    expect(screen.getByText("Document storage could not be reached or is not configured. Nothing was stored; try again later.")).toBeInTheDocument();
  });

  it("shows an earlier revision's stored text from the revision list", async () => {
    const second = { ...DOCUMENT, revision: 2, based_on_revision: 1, content: DOCUMENT.content + "\nA new clause.\n" };
    answer = (_, url) => {
      if (url.pathname !== `/api/v1/documents/${DOCUMENT.document_id}`) return undefined;
      if (url.searchParams.get("revision") === "1") return { status: 200, body: DOCUMENT };
      return { status: 200, body: second };
    };
    renderAt(`/documents/${DOCUMENT.document_id}`);
    const user = userEvent.setup();
    expect(await screen.findByLabelText("Formatted document")).toHaveTextContent("A new clause.");
    await user.click(within(screen.getByRole("list", { name: "Revisions" })).getByRole("button", { name: "Revision 1" }));
    await waitFor(() => expect(screen.getByLabelText("Formatted document")).not.toHaveTextContent("A new clause."));
    expect(screen.getByLabelText("Formatted document")).toHaveTextContent("Meals and Entertainment Policy");
    expect(calls.some((call) => call.path === `/api/v1/documents/${DOCUMENT.document_id}?revision=1`)).toBe(true);
  });

  it("shows why a revision whose stored text failed its check is not shown", async () => {
    answer = (_, url) =>
      url.pathname === `/api/v1/documents/${DOCUMENT.document_id}` ? refusal(500, "integrity_error", "the stored content of policy-documents/x does not match its recorded hash") : undefined;
    renderAt(`/documents/${DOCUMENT.document_id}`);
    expect(await screen.findByText("integrity_error")).toBeInTheDocument();
    expect(screen.getByText("The stored text of this revision does not match its recorded hash, so it is not shown.")).toBeInTheDocument();
  });
});

/** A document revision whose text holds the clause the compiler quoted, so the source view can find it. */
function sourceOf(url: URL): Answer | undefined {
  const match = /^\/api\/v1\/documents\/(doc_[0-9a-f]{24})$/.exec(url.pathname);
  if (!match) return undefined;
  const quoted = EXTRACTION.review_required.find((item) => item.document_id === match[1])?.evidence ?? "";
  return { status: 200, body: { ...DOCUMENT, document_id: match[1], revision: Number(url.searchParams.get("revision") ?? 1), content: `# Policy\n\nIntroduction.\n\n${quoted}\n\nThe end.\n` } };
}

function extractionAnswers(extraction: ExtractionSummary, extra: (method: string, url: URL) => Answer | undefined = () => undefined) {
  return (method: string, url: URL): Answer | undefined =>
    extra(method, url) ??
    (url.pathname === `/api/v1/documents/extractions/${extraction.extraction_id}`
      ? { status: 200, body: extraction }
      : url.pathname.endsWith("/change-requests")
        ? { status: 200, body: [] }
        : url.pathname.endsWith("/comparisons")
          ? { status: 200, body: comparisonsJson }
          : sourceOf(url));
}

/** A background extraction job as the API reports it. */
function job(status: ExtractionJob["status"], progress: ExtractionJob["progress"], mode: ExtractionJob["execution_mode"] = "replay"): ExtractionJob {
  return { job_id: "job_1", status, execution_mode: mode, extraction_model: null, documents: [], progress, extraction_id: status === "succeeded" ? EXTRACTION.extraction_id : null, error: null, created_at: "2026-11-08T12:00:00Z", started_at: "2026-11-08T12:00:00Z", finished_at: null, updated_at: "2026-11-08T12:00:00Z" };
}

/** Starting a job answers it queued; each read of it answers the next state given, then stays on the last. */
function jobAnswers(states: ExtractionJob[]) {
  let at = 0;
  return (method: string, url: URL): Answer | undefined => {
    if (method === "POST" && url.pathname === "/api/v1/documents/extraction-jobs") return { status: 202, body: job("queued", { stage: "queued", done: 0, total: 2, current: null }) };
    if (url.pathname === "/api/v1/documents/extraction-jobs/job_1") return { status: 200, body: states[Math.min(at++, states.length - 1)] };
    return undefined;
  };
}

describe("compilation", () => {
  const [first, second] = DOCUMENTS.items;
  const selection = `d=${first!.document_id}:${first!.revision},${second!.document_id}:${second!.revision}`;

  it("compiles the exact selected revisions by replay from recordings chosen explicitly, and never offers live when it is unavailable", async () => {
    answer = (method, url) => jobAnswers([job("succeeded", { stage: "done", done: 2, total: 2, current: null })])(method, url) ?? extractionAnswers(EXTRACTION)(method, url);
    const router = renderAt(`/documents/compile?${selection}`);
    const user = userEvent.setup();
    const selected = await screen.findByRole("list", { name: "Selected documents" });
    expect(within(selected).getAllByRole("listitem")).toHaveLength(2);
    expect(within(selected).getByLabelText(`Revision of ${first!.filename}`)).toHaveValue(String(first!.revision));

    expect(screen.getByRole("radio", { name: /Read with a model \(live\)/ })).toBeDisabled();
    const compile = screen.getByRole("button", { name: "Compile by replay" });
    expect(compile).toBeDisabled();
    expect(screen.getByText("Choose the recordings to reproduce from.")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Recordings"), "live");
    await user.click(compile);
    await waitFor(() => expect(router.state.location.pathname).toBe(`/documents/extractions/${EXTRACTION.extraction_id}`));
    expect(JSON.parse(posts("/api/v1/documents/extraction-jobs")[0]!.body!)).toEqual({
      documents: [first, second].map((item) => ({ document_id: item!.document_id, revision: item!.revision })),
      recording_set: "live",
    });
  });

  it("selects a document from the library and carries its exact revision to compilation", async () => {
    answer = () => undefined;
    const router = renderAt("/documents");
    await openCategories();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("checkbox", { name: `Select ${first!.title} (${first!.filename})` }));
    expect(screen.getByRole("region", { name: "Selection" })).toHaveTextContent("1 document selected");
    await user.click(screen.getByRole("button", { name: "Compile selected" }));
    expect(router.state.location.pathname).toBe("/documents/compile");
    expect(router.state.location.search).toBe(`?d=${first!.document_id}:${first!.revision}`);
  });

  it("offers live compilation only the policy extraction models, never a decision model or a translator", async () => {
    backends = {
      ...BACKENDS,
      allowed_models: ["anthropic/claude-haiku-5.5", "typesafe/jev-1.14", "tencent/hy-mt2-30b-a3b"],
      policy_compiler: { ...BACKENDS.policy_compiler, extraction_models: ["anthropic/claude-haiku-5.5"], live: { permitted: true, configured: true, credential_configured: true, available: true } },
    };
    answer = () => undefined;
    renderAt(`/documents/compile?d=${first!.document_id}:${first!.revision}`);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("radio", { name: /Read with a model \(live\)/ }));
    const model = screen.getByLabelText("Policy extraction model");
    expect(within(model).getAllByRole("option").map((option) => option.textContent)).toEqual(["Choose a model", "anthropic/claude-haiku-5.5"]);
    const paid = screen.getByRole("button", { name: "Compile live (paid)" });
    await user.selectOptions(model, "anthropic/claude-haiku-5.5");
    expect(paid).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: "I understand live compilation calls a paid model" }));
    await user.click(paid);
    await waitFor(() => expect(posts("/api/v1/documents/extraction-jobs")).toHaveLength(1));
    expect(JSON.parse(posts("/api/v1/documents/extraction-jobs")[0]!.body!)).toMatchObject({ execution_mode: "live", extraction_model: "anthropic/claude-haiku-5.5" });
    expect(JSON.parse(posts("/api/v1/documents/extraction-jobs")[0]!.body!)).not.toHaveProperty("fresh_reading");  // recorded readings are reused by default
  });

  it("reads every document again with the model only when asked", async () => {
    backends = { ...BACKENDS, policy_compiler: { ...BACKENDS.policy_compiler, extraction_models: ["anthropic/claude-haiku-5.5"], live: { permitted: true, configured: true, credential_configured: true, available: true } } };
    answer = () => undefined;
    renderAt(`/documents/compile?d=${first!.document_id}:${first!.revision}`);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("radio", { name: /Read with a model \(live\)/ }));
    await user.selectOptions(screen.getByLabelText("Policy extraction model"), "anthropic/claude-haiku-5.5");
    await user.click(screen.getByRole("checkbox", { name: "I understand live compilation calls a paid model" }));
    await user.click(screen.getByRole("checkbox", { name: /Read every document again/ }));
    await user.click(screen.getByRole("button", { name: "Compile live (paid)" }));
    await waitFor(() => expect(posts("/api/v1/documents/extraction-jobs")).toHaveLength(1));
    expect(JSON.parse(posts("/api/v1/documents/extraction-jobs")[0]!.body!)).toMatchObject({ execution_mode: "live", extraction_model: "anthropic/claude-haiku-5.5", fresh_reading: true });
  });

  it("follows a live compilation document by document, and explains a failure with a way to try again", async () => {
    backends = { ...BACKENDS, policy_compiler: { ...BACKENDS.policy_compiler, extraction_models: ["anthropic/claude-haiku-5.5"], live: { permitted: true, configured: true, credential_configured: true, available: true } } };
    answer = jobAnswers([
      job("running", { stage: "reading", done: 2, total: 9, current: "gifts-and-awards-policy.md" }, "live"),
      { ...job("failed", { stage: "reading", done: 4, total: 9, current: "regional-addendum.md" }, "live"), error: { code: "extraction_failed", message: "the extraction stopped on an unexpected error: see the API log" } },
    ]);
    renderAt(`/documents/compile?d=${first!.document_id}:${first!.revision}&job=job_1`);
    const progress = await screen.findByRole("region", { name: "Compilation progress" });
    expect(progress).toHaveTextContent("Reading gifts-and-awards-policy.md (3 of 9)");
    expect(progress).toHaveTextContent("2/9 documents · 20%");
    expect(screen.getByRole("button", { name: "Compiling…" })).toBeDisabled();
    const failure = await within(progress).findByRole("alert", {}, { timeout: 3000 });
    expect(failure).toHaveTextContent("Compilation failed");
    expect(failure).toHaveTextContent("compiling again reuses them, at no cost");
    await userEvent.setup().click(within(failure).getByRole("button", { name: "Try again" }));
    expect(screen.queryByRole("region", { name: "Compilation progress" })).toBeNull();
  });

  it("summarizes what the compiler produced, with one status label", async () => {
    answer = extractionAnswers(EXTRACTION);
    renderAt(`/documents/extractions/${EXTRACTION.extraction_id}`);
    const summary = await screen.findByLabelText("Compilation summary");
    expect(summary).toHaveTextContent(`Documents processed${EXTRACTION.documents.length}`);
    expect(summary).toHaveTextContent("Blocking issues4");
    expect(summary).toHaveTextContent("ReadinessNot ready");
    expect(screen.getAllByText("Ready for review")).toHaveLength(1);
    expect(screen.getByRole("list", { name: "Workflow" })).toHaveTextContent("Review differences");
  });
});

/** Every comparison the API reports for the extraction: the agreed ones and those needing review. */
const ALL_COMPARISONS = [...(comparisonsJson as { items: FieldComparison[] }).items, ...EXTRACTION.review_required];
const [MCC, , TECH, TIME] = EXTRACTION.review_required as [FieldComparison, FieldComparison, FieldComparison, FieldComparison];

/** The API's verified source: located for every clause but MCC-03's, which it cannot place word for word. */
function sourceFor(comparison: FieldComparison, owner: string): SourceLocation {
  const evidence = comparison.evidence ?? "";
  const content = `# Policy\n\nIntroduction.\n\n${evidence}\n\nThe end.\n`;
  const lines = evidence.split("\n").length;
  const located = comparison.rule_id !== "MCC-03";
  return {
    comparison_id: comparison.comparison_id,
    document: { document_id: comparison.document_id, revision: owner === "extraction" ? 1 : null, filename: comparison.policy_doc, content_sha256: "a".repeat(64), content },
    revision_verified: true,
    clause_verified: located,
    location: located ? { from_line: 5, to_line: 5 + lines - 1 } : null,
    section: null,
    heading_line: null,
    highlights: comparison.rule_id === "TIME-03" ? [{ line: 5, start: 0, end: 3, text: evidence.slice(0, 3), side: "regex" }] : [],
    reason: located ? null : "The clause is not found word for word in this revision.",
  };
}

function reviewAnswers(extraction: ExtractionSummary, extra: (method: string, url: URL) => Answer | undefined = () => undefined) {
  return extractionAnswers(extraction, (method, url) => {
    const special = extra(method, url);
    if (special) return special;
    const source = /\/comparisons\/(cmp-[0-9a-f]+)\/source$/.exec(url.pathname);
    if (source) {
      const comparison = ALL_COMPARISONS.find((item) => item.comparison_id === source[1])!;
      return { status: 200, body: sourceFor(comparison, url.pathname.includes("/extractions/") ? "extraction" : "ruleset") };
    }
    if (url.pathname.endsWith("/comparisons")) return { status: 200, body: { extraction_sha256: EXTRACTION.extraction_sha256, items: ALL_COMPARISONS, total: ALL_COMPARISONS.length, limit: 500, offset: 0 } };
    return undefined;
  });
}

const issue = (rule: string, field: string) => screen.findByRole("article", { name: `${rule} ${field}` });

/** What the API sends as rule_logic for the rules under review and those they replace: the candidate's own rules. */
const split = (measure: string, operator: string, applies_to: string, threshold_of: string | null) => ({ in_conditions: [], helper: { measure, operator, applies_to, threshold_of } });
const alcohol = { op: "all", conditions: [{ op: "eq", field: "item.category", value: "alcohol" }, { op: "eq", field: "item.is_purchase", value: true }] };
const LOGIC = {
  "REG-01": { kind: "check", scope: "item", outcome: "exception", severity: "high", status: "active", applies_when: { op: "eq", field: "receipt.entity", value: "APAC" }, select: alcohol, params: {}, param_uses: {},
    replaces: [{ rule_id: "ALC-01", level: "receipt" }, { rule_id: "ALC-02", level: "receipt" }, { rule_id: "ALC-03", level: "receipt" }] },
  "ALC-01": { kind: "check", scope: "item", outcome: "exception", severity: "high", status: "active", select: alcohol, params: {}, param_uses: {}, replaces: [] },
  "MCC-03": { kind: "process", directive: "guidance", status: "active", params: {}, param_uses: {} },
  "TIME-03": {
    kind: "check", scope: "receipt", helper: "split_transactions", outcome: "exception", severity: "high", status: "active",
    params: { min_receipts: { type: "number", value: "2", unit: "count", source: "policy" }, itemization_usd: { type: "number", value: "75", unit: "usd", source: "policy" }, gift_cap_usd: { type: "number", value: "50", unit: "usd", source: "policy" } },
    param_uses: {
      min_receipts: split("receipts by the same employee, at the same merchant, on the same date", "at least", "every split this rule looks for", null),
      itemization_usd: split("each receipt's total in USD", "each at or below, together above", "receipts that are not itemized", "DOC-01"),
      gift_cap_usd: split("each receipt's client-gift total for one named recipient, in USD", "each at or below, together above", "client gifts that name their recipient, not gifts to government officials", "GIFT-01"),
    },
    replaces: [{ rule_id: "GIFT-01", level: "finding" }],
  },
};
const WITH_LOGIC = { ...EXTRACTION, rule_logic: LOGIC, rule_titles: { ...EXTRACTION.rule_titles, "ALC-01": "Alcohol requires a documented entertainment context", "ALC-02": "Alcohol per-attendee cap", "ALC-03": "Retail alcohol", "MCC-01": "High-risk merchant category", "GIFT-01": "Client gift cap", "TIME-03": "Split transactions" } } as unknown as ExtractionSummary;
const REG = EXTRACTION.review_required[1]!;
const saving = (body: unknown) => (method: string, url: URL) => (method === "POST" && url.pathname.endsWith("/review") ? { status: 201, body } : undefined);
const reviewPosts = () => posts(`/api/v1/documents/extractions/${EXTRACTION.extraction_id}/review`);

describe("issue review", () => {
  it("lays each issue out as what needs attention, why, where it comes from, where it applies and what it affects", async () => {
    answer = reviewAnswers(WITH_LOGIC);
    renderAt(`/documents/extractions/${EXTRACTION.extraction_id}?tab=review&item=${TIME.comparison_id}`);
    const time = await issue("TIME-03", "Limits");
    for (const step of ["What needs attention", "Why it was flagged", "Where it comes from", "Where it applies", "What TIME-03 does", "Your decision"]) expect(within(time).getByRole("region", { name: step })).toBeInTheDocument();
    expect(within(time).getByText("Check the limits this rule applies")).toBeInTheDocument();
    expect(within(time).getByText(/two readings give different limits: only the text-match reading found 2 \(count\)/)).toBeInTheDocument();
    const readings = within(time).getByRole("region", { name: "Interpretations" });
    expect(within(readings).getByText("2 (count)")).toHaveAttribute("data-differs");  // only the text-match reading has it
    expect(within(readings).getAllByText("50 USD").every((item) => !item.hasAttribute("data-differs"))).toBe(true);
    expect(within(time).getByText("Executable check")).toBeInTheDocument();
    expect(within(time).getByText(/Replaces the finding of GIFT-01 \(Client gift cap\) that it names as split around/)).toBeInTheDocument();
    // The compiler's names stay out of the way, in Technical details.
    expect(within(time).getAllByText(/regex|LLM/).every((item) => item.closest("details:not([open])"))).toBe(true);
  });

  it("shows only what is left to decide by default, with every field and the decided ones on demand", async () => {
    answer = reviewAnswers(EXTRACTION);
    renderAt(`/documents/extractions/${EXTRACTION.extraction_id}?tab=review`);
    const user = userEvent.setup();
    await issue("MCC-03", "Limits");
    expect(screen.getByRole("radio", { name: "To decide · 4" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("radio", { name: "Decided · 0" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: `Every field · ${ALL_COMPARISONS.length}` })).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Review status" })).getByText("0 of 4")).toBeInTheDocument();
    const list = screen.getByRole("navigation", { name: "Issues" });
    expect(within(list).getAllByRole("button").map((item) => item.textContent)).toEqual(["MCC-03 · Limits", "REG-01 · Limits", "TECH-01 · Quoted policy text", "TIME-03 · Limits"]);
    await user.click(within(list).getByRole("button", { name: "TECH-01 · Quoted policy text" }));
    const tech = await issue("TECH-01", "Quoted policy text");
    within(tech).getByRole("button", { name: /Previous/ }).focus();
    await user.keyboard("{ArrowLeft}");  // the arrow keys move between issues wherever focus is in the review
    expect(await issue("REG-01", "Limits")).toBeInTheDocument();
    await user.selectOptions(screen.getByRole("combobox", { name: "Document" }), "technology-and-equipment-policy.md");
    expect(await issue("TECH-01", "Quoted policy text")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "To decide · 1" })).toBeInTheDocument();
  });

  it("makes a regional override explicit: where it applies, the rules it replaces, and those it leaves in force", async () => {
    // REG-01's agreed references, as the API reports that field: the rules its text names.
    const references = { ...REG, comparison_id: "cmp-reg01-references", field: "references", status: "agreed", regex_value: ["ALC-01", "ALC-02", "ALC-03", "MCC-01"], llm_value: ["ALC-01", "ALC-02", "ALC-03", "MCC-01"], agreed_value: ["ALC-01", "ALC-02", "ALC-03", "MCC-01"], review_required: false };
    answer = reviewAnswers(WITH_LOGIC, (_, url) => (url.pathname.endsWith("/comparisons") ? { status: 200, body: { extraction_sha256: EXTRACTION.extraction_sha256, items: [...ALL_COMPARISONS, references], total: ALL_COMPARISONS.length + 1, limit: 500, offset: 0 } } : undefined));
    renderAt(`/documents/extractions/${EXTRACTION.extraction_id}?tab=review&item=${REG.comparison_id}`);
    const reg = await issue("REG-01", "Limits");
    expect(within(within(reg).getByRole("region", { name: "Where it applies" })).getByText("When the entity is “APAC”.")).toBeInTheDocument();
    const related = within(reg).getByRole("list", { name: "Related rules" });
    expect(related).toHaveTextContent("Replaces ALC-01 (Alcohol requires a documented entertainment context), ALC-02 (Alcohol per-attendee cap) and ALC-03 (Retail alcohol) on every receipt it applies to: those rules are not reported there, and REG-01 is reported instead.");
    expect(related).toHaveTextContent("Mentions MCC-01 (High-risk merchant category) without replacing it: it still applies.");
    expect(within(reg).getByText(/Raises a high exception\.$/)).toBeInTheDocument();  // the replacement is said once, below
  });

  it("offers only the decisions that fit the issue: a number that may be a limit is confirmed or entered, never read", async () => {
    answer = reviewAnswers(WITH_LOGIC, saving(notReadyJson));
    renderAt(`/documents/extractions/${EXTRACTION.extraction_id}?tab=review&item=${REG.comparison_id}`);
    const user = userEvent.setup();
    const reg = await issue("REG-01", "Limits");
    expect(within(reg).getByText("Confirm that this rule sets no limit")).toBeInTheDocument();
    const decision = within(reg).getByRole("radiogroup", { name: "Decision" });
    expect(within(decision).getAllByRole("radio").map((item) => item.closest("label")!.querySelector(".font-medium")!.textContent)).toEqual(["No limit", "It is a limit: enter it", "Ask for clarification"]);
    await user.click(within(decision).getByRole("radio", { name: /No limit/ }));
    expect(within(reg).getByLabelText("What will change")).toHaveTextContent("The compiled rule REG-01 has no limit either: no rule change is needed.");
    await user.click(within(reg).getByRole("button", { name: "Save decision" }));
    await waitFor(() => expect(reviewPosts()).toHaveLength(1));
    const sent = JSON.parse(reviewPosts()[0]!.body!).decisions.find((item: { comparison_id: string }) => item.comparison_id === REG.comparison_id);
    expect(sent).toEqual({ comparison_id: REG.comparison_id, extraction_sha256: REG.extraction_sha256, reviewer: "r.eviewer", action: "accept_regex", rationale: "Not a limit: the number in the clause is not an amount or count the rule checks." });
  });

  it("warns before saving a reading that leaves out a limit the compiled rule checks, allows it, and saves only when asked", async () => {
    answer = reviewAnswers(WITH_LOGIC, saving(notReadyJson));
    renderAt(`/documents/extractions/${EXTRACTION.extraction_id}?tab=review&item=${TIME.comparison_id}`);
    const user = userEvent.setup();
    const time = await issue("TIME-03", "Limits");
    const decision = within(time).getByRole("radiogroup", { name: "Decision" });
    expect(within(decision).getByRole("radio", { name: /Use: 50 USD, 75 USD/ }).closest("label")).toHaveTextContent("This leaves out 2 (min receipts), which the policy text states. That is your call: it does not block approval, and TIME-03 still checks it as the text states.");
    expect(within(decision).getByRole("radio", { name: /Use: 2 \(count\), 50 USD, 75 USD/ }).closest("label")).not.toHaveTextContent("leaves out");
    await user.click(within(decision).getByRole("radio", { name: /Use: 50 USD, 75 USD/ }));
    expect(reviewPosts()).toHaveLength(0);  // choosing saves nothing
    expect(within(time).getByLabelText("What will change")).toHaveTextContent("Will record for TIME-03 · Limits: 50 USD, 75 USD");
    await user.click(within(decision).getByRole("radio", { name: /Use: 2 \(count\), 50 USD, 75 USD/ }));
    expect(within(time).getByLabelText("What will change")).toHaveTextContent("The compiled rule TIME-03 already uses 2 (min receipts), 75 USD (itemization) and 50 USD (gift cap): no rule change is needed.");
    await user.click(within(time).getByRole("button", { name: "Save decision" }));
    await waitFor(() => expect(reviewPosts()).toHaveLength(1));
    expect(JSON.parse(reviewPosts()[0]!.body!).decisions.at(-1)).toMatchObject({ comparison_id: TIME.comparison_id, action: "accept_regex" });
  });

  it("confirms a saved decision, says what changed and what is left, and moves on to the next issue", async () => {
    const saved = { ...notReadyJson, decisions: [...notReadyJson.decisions, { comparison_id: TIME.comparison_id, extraction_sha256: EXTRACTION.extraction_sha256, action: "accept_regex", reviewer: "r.eviewer" }], remaining: notReadyJson.remaining.filter((id) => id !== TIME.comparison_id), issues: [] };
    answer = reviewAnswers(WITH_LOGIC, saving(saved));
    renderAt(`/documents/extractions/${EXTRACTION.extraction_id}?tab=review&item=${TIME.comparison_id}`);
    const user = userEvent.setup();
    const time = await issue("TIME-03", "Limits");
    await user.click(within(time).getByRole("radio", { name: /Use: 2 \(count\), 50 USD, 75 USD/ }));
    await user.click(within(time).getByRole("button", { name: "Save decision" }));
    const done = (await within(time).findByText("Decision recorded: Used the text-match reading.")).closest("[role=status]") as HTMLElement;
    expect(done).toHaveTextContent("Decision recorded: Used the text-match reading.");
    expect(done).toHaveTextContent("2 issues still need a decision.");
    expect(done).toHaveTextContent("no rule change is needed");
    expect(within(screen.getByRole("region", { name: "Review status" })).getByText("2 of 4")).toBeInTheDocument();
    expect(within(time).getByLabelText("Saved decision")).toHaveTextContent("Used the text-match reading");
    await user.click(within(done).getByRole("button", { name: /Next issue: REG-01 · Limits/ }));
    expect(await issue("REG-01", "Limits")).toBeInTheDocument();
  });

  it("shows a decision that leaves out what the policy states as an interpretation mismatch on its issue, even from an older review", async () => {
    const message = "the reviewed interpretation disagrees with the executable rules: error faithful: TIME-03: min_receipts is 2 count, not in its clause. Update rules/rule_logic.yaml and validate again, or change the decision";
    const saved = { ...notReadyJson, decisions: [{ comparison_id: TIME.comparison_id, extraction_sha256: EXTRACTION.extraction_sha256, action: "accept_llm", reviewer: "r.eviewer" }], remaining: [], issues: [{ level: "error", code: "executable_conflict", comparison_id: null, message }] };
    const reviewed = { ...WITH_LOGIC, reviews: [{ review_id: "rev_1", review_sha256: "b".repeat(64), policy: "exceptions_only", ready_for_approval: false, remaining: 0, error_issues: 1, created_at: "2026-11-08T12:00:00Z" }] } as unknown as ExtractionSummary;
    answer = reviewAnswers(reviewed, (method, url) => (url.pathname.endsWith("/reviews/rev_1") ? { status: 200, body: saved } : saving(notReadyJson)(method, url)));
    renderAt(`/documents/extractions/${EXTRACTION.extraction_id}?tab=review&item=${TIME.comparison_id}`);
    const user = userEvent.setup();
    const time = await issue("TIME-03", "Limits");
    expect((await within(time).findAllByText("Interpretation mismatch")).length).toBeGreaterThan(0);  // the issue's state, and its explanation's title
    const alert = within(time).getByRole("alert");
    // What the decision leaves out, named with the part of the rule that checks it.
    expect(alert).toHaveTextContent("This decision leaves out 2 (min receipts), which the policy states and TIME-03 checks. The policy states 2 (count), 50 USD, 75 USD; TIME-03 follows it.");
    expect(screen.queryByText("Problems not tied to one issue")).not.toBeInTheDocument();
    // The fix the policy settles is one click: the reading that states what the policy does.
    await user.click(within(alert).getByRole("button", { name: "Use 2 (count), 50 USD, 75 USD: what the policy states" }));
    await waitFor(() => expect(reviewPosts()).toHaveLength(1));
    const sent = JSON.parse(reviewPosts()[0]!.body!).decisions.find((item: { comparison_id: string }) => item.comparison_id === TIME.comparison_id);
    expect(sent).toEqual({ comparison_id: TIME.comparison_id, extraction_sha256: TIME.extraction_sha256, reviewer: "r.eviewer", action: "accept_regex", rationale: "As the policy text states" });
  });

  it("shows a decision that leaves out a stated limit as allowed, with what the rule still checks, never as a problem", async () => {
    const message = "TIME-03 thresholds: this decision leaves out 2 count, which the clause states; the compiled rule still checks it as the text states";
    const saved = { ...notReadyJson, ready_for_approval: true, decisions: [{ comparison_id: TIME.comparison_id, extraction_sha256: EXTRACTION.extraction_sha256, action: "accept_llm", reviewer: "r.eviewer", rationale: "2 is not a threshold" }], remaining: [], issues: [{ level: "note", code: "limit_left_out", comparison_id: TIME.comparison_id, message }] };
    const reviewed = { ...WITH_LOGIC, reviews: [{ review_id: "rev_1", review_sha256: "b".repeat(64), policy: "exceptions_only", ready_for_approval: true, remaining: 0, error_issues: 0, created_at: "2026-11-08T12:00:00Z" }] } as unknown as ExtractionSummary;
    answer = reviewAnswers(reviewed, (method, url) => (url.pathname.endsWith("/reviews/rev_1") ? { status: 200, body: saved } : saving(notReadyJson)(method, url)));
    renderAt(`/documents/extractions/${EXTRACTION.extraction_id}?tab=review&item=${TIME.comparison_id}`);
    const time = await issue("TIME-03", "Limits");
    expect(await within(time).findByText(/^Your decision leaves out/)).toHaveTextContent("Your decision leaves out 2 (min receipts), which the policy text states. It does not block approval; TIME-03 still checks it as the text states.");
    expect(within(time).queryByRole("alert")).not.toBeInTheDocument();
    expect(within(time).queryByText("Interpretation mismatch")).not.toBeInTheDocument();
  });

  it("offers a correction of the value from a mismatch, with the stated value filled in", async () => {
    const saved = { ...notReadyJson, decisions: [{ comparison_id: TIME.comparison_id, extraction_sha256: EXTRACTION.extraction_sha256, action: "accept_llm", reviewer: "r.eviewer" }], remaining: [], issues: [{ level: "error", code: "interpretation_mismatch", comparison_id: TIME.comparison_id, message: "TIME-03 thresholds: this decision departs from what the clause states" }] };
    const reviewed = { ...WITH_LOGIC, reviews: [{ review_id: "rev_1", review_sha256: "b".repeat(64), policy: "exceptions_only", ready_for_approval: false, remaining: 0, error_issues: 1, created_at: "2026-11-08T12:00:00Z" }] } as unknown as ExtractionSummary;
    answer = reviewAnswers(reviewed, (_, url) => (url.pathname.endsWith("/reviews/rev_1") ? { status: 200, body: saved } : undefined));
    renderAt(`/documents/extractions/${EXTRACTION.extraction_id}?tab=review&item=${TIME.comparison_id}`);
    const user = userEvent.setup();
    const time = await issue("TIME-03", "Limits");
    await user.click(within(await within(time).findByRole("alert")).getByRole("button", { name: "Enter the correct value" }));
    expect(within(time).getByRole("radio", { name: /Neither: enter the correct value/ })).toBeChecked();
    expect(within(time).getByLabelText("Amount 1")).toBeInTheDocument();
  });

  it("corrects a limit with field controls, keeps amounts exact, and requires a reason", async () => {
    answer = reviewAnswers(EXTRACTION, saving(notReadyJson));
    renderAt(`/documents/extractions/${EXTRACTION.extraction_id}?tab=review&item=${TIME.comparison_id}`);
    const user = userEvent.setup();
    const time = await issue("TIME-03", "Limits");
    await user.click(within(time).getByRole("radio", { name: /Neither: enter the correct value/ }));
    expect(within(time).getByLabelText("Amount 1")).toHaveValue("50");
    await user.clear(within(time).getByLabelText("Amount 2"));
    await user.type(within(time).getByLabelText("Amount 2"), "75.50");
    await user.click(within(time).getByRole("button", { name: "Save decision" }));
    expect(within(time).getByRole("alert")).toHaveTextContent("Say why this is the correct value: a correction needs a reason.");
    expect(reviewPosts()).toHaveLength(0);
    await user.type(within(time).getByLabelText("Reason (required)"), "The policy says 75.50");
    expect(within(time).getByLabelText("What will change")).toHaveTextContent("Will record for TIME-03 · Limits: 50 USD, 75.50 USD");
    await user.click(within(time).getByRole("button", { name: "Save decision" }));
    await waitFor(() => expect(reviewPosts()).toHaveLength(1));
    const body = reviewPosts()[0]!.body!;
    expect(body).toContain('"value":[{"unit":"usd","value":"50"},{"unit":"usd","value":"75.50"}]');
    expect(JSON.parse(body).decisions.at(-1)).toMatchObject({ action: "override", rationale: "The policy says 75.50" });
  });

  it("asks for clarification on one issue", async () => {
    answer = reviewAnswers(EXTRACTION, (method, url) => (method === "POST" && url.pathname.endsWith("/change-requests") ? { status: 201, body: { change_request_id: "chg_1" } } : undefined));
    renderAt(`/documents/extractions/${EXTRACTION.extraction_id}?tab=review&item=${TECH.comparison_id}`);
    const user = userEvent.setup();
    const tech = await issue("TECH-01", "Quoted policy text");
    await user.click(within(tech).getByRole("radio", { name: /Ask for clarification/ }));
    const form = within(tech).getByRole("form", { name: "Request changes" });
    await user.type(within(form).getByLabelText("Requested correction"), "Quote the clause exactly.");
    await user.click(within(form).getByRole("button", { name: "Send request" }));
    await waitFor(() => expect(posts(`/api/v1/documents/extractions/${EXTRACTION.extraction_id}/change-requests`)).toHaveLength(1));
    expect(JSON.parse(posts(`/api/v1/documents/extractions/${EXTRACTION.extraction_id}/change-requests`)[0]!.body!)).toEqual({ actor: "r.eviewer", comparison_id: TECH.comparison_id, requested_correction: "Quote the clause exactly." });
  });

  it("highlights the verified clause in the source, and says plainly when its position is unavailable", async () => {
    window.localStorage.setItem("epv.sourceView", "source");  // exact lines and marks
    answer = reviewAnswers(EXTRACTION);
    renderAt(`/documents/extractions/${EXTRACTION.extraction_id}?tab=review&item=${TECH.comparison_id}`);
    const tech = await issue("TECH-01", "Quoted policy text");
    const source = await within(tech).findByRole("list", { name: "Policy document" });
    const lit = source.querySelectorAll("[data-clause]");
    expect(lit).toHaveLength(TECH.evidence!.split("\n").length);
    expect(within(tech).getByText(/verified in this revision/)).toHaveTextContent(`Lines 5–${4 + TECH.evidence!.split("\n").length}, verified in this revision`);

    renderAt(`/documents/extractions/${EXTRACTION.extraction_id}?tab=review&item=${MCC.comparison_id}`);
    const mcc = await issue("MCC-03", "Limits");
    expect(await within(mcc).findByText("Precise location unavailable")).toBeInTheDocument();
    expect(within(mcc).getByRole("list", { name: "Policy document" }).querySelectorAll("[data-clause]")).toHaveLength(0);  // nothing marked on a guess
  });

  it("marks the disputed value inside the clause", async () => {
    window.localStorage.setItem("epv.sourceView", "source");
    answer = reviewAnswers(EXTRACTION);
    renderAt(`/documents/extractions/${EXTRACTION.extraction_id}?tab=review&item=${TIME.comparison_id}`);
    const time = await issue("TIME-03", "Limits");
    const mark = (await within(time).findByRole("list", { name: "Policy document" })).querySelector("mark[data-side=regex]");
    expect(mark).toHaveTextContent(TIME.evidence!.slice(0, 3));
    expect(within(time).getByText(/only in the text-match reading/)).toBeInTheDocument();
  });

  it("ties each limit to the parameter that holds it", async () => {
    answer = reviewAnswers(WITH_LOGIC);
    renderAt(`/documents/extractions/${EXTRACTION.extraction_id}?tab=review&item=${TIME.comparison_id}`);
    const time = await issue("TIME-03", "Limits");
    const where = within(time).getByLabelText("How each limit is applied");
    const row = (value: string) => within(where).getAllByRole("listitem").find((item) => item.textContent!.startsWith(value))!;
    expect(row("50 USD")).toHaveTextContent("50 USD → Gift cap gift_cap_usdthreshold of GIFT-01");
    expect(row("50 USD")).toHaveTextContent("Stated: “or the $50 gift cap for one recipient (GIFT-01).”");
    expect(row("2")).toHaveTextContent("Min receipts");
  });

  it("shows a published version's decisions read-only, with their source", async () => {
    window.localStorage.setItem("epv.sourceView", "source");
    answer = reviewAnswers(EXTRACTION, (_, url) =>
      url.pathname === `/api/v1/rulesets/${ID}` ? { status: 200, body: PUBLISHED } : url.pathname === `/api/v1/rulesets/${ID}/comparisons` ? { status: 200, body: rulesetComparisonsJson } : undefined,
    );
    renderAt(`/policies/${ID}`);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Review" }));
    await user.click(await screen.findByRole("button", { name: "TECH-01 · Quoted policy text" }));  // the decisions taken, listed
    const tech = await issue("TECH-01", "Quoted policy text");
    expect(screen.getByText("Read only")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Review status" })).getByText("4 of 4")).toBeInTheDocument();
    expect(within(tech).getByText(/^Corrected to “is a \*\*high\*\* exception, regardless of stated business purpose”$/)).toBeInTheDocument();
    expect(within(tech).queryByRole("radiogroup", { name: "Decision" })).not.toBeInTheDocument();
    expect(await within(tech).findByRole("list", { name: "Policy document" })).toBeInTheDocument();
  });
});

/** The compilation beside the published version it would replace, as GET .../baseline answers. */
const V1 = { ruleset_id: "rs_v1", name: "policies", version: 1, published_at: "2026-11-08T12:00:00Z" };
const AMENDED = {
  published: V1,
  rule_changes: [{ rule_id: "TIME-03", field: "params.min_receipts", before: { type: "number", value: "2", unit: "count", source: "policy" }, after: { type: "number", value: "3", unit: "count", source: "policy" } }],
  changed_clauses: [{ policy_doc: "transaction-pattern-rules.md", rule_id: "TIME-03" }, { policy_doc: "regional-addendum.md", rule_id: "REG-01" }],
  decisions: [
    { comparison_id: MCC.comparison_id, state: "unchanged", previous: { comparison_id: "cmp-old-mcc", status: "unconfirmed", regex_value: [], llm_value: [], evidence: MCC.evidence, clause_hash: MCC.clause_hash }, decision: { comparison_id: "cmp-old-mcc", extraction_sha256: "c".repeat(64), action: "accept_regex", reviewer: "a.nother", rationale: "5813 is a merchant code" } },
    { comparison_id: REG.comparison_id, state: "changed", previous: { comparison_id: "cmp-old-reg", status: "unconfirmed", regex_value: [], llm_value: [], evidence: "an earlier wording", clause_hash: "d".repeat(64) }, decision: { comparison_id: "cmp-old-reg", extraction_sha256: "c".repeat(64), action: "accept_regex", reviewer: "a.nother", rationale: "three counts rules" } },
  ],
};
const withBaseline = (baseline: unknown, extra: (method: string, url: URL) => Answer | undefined = () => undefined) => (method: string, url: URL) => (url.pathname.endsWith("/baseline") ? { status: 200, body: baseline } : extra(method, url));

describe("policy versions", () => {
  it("shows a changed limit as a proposed amendment beside the published version, not as an error", async () => {
    answer = reviewAnswers(WITH_LOGIC, withBaseline(AMENDED));
    renderAt(`/documents/extractions/${EXTRACTION.extraction_id}?tab=review&item=${TIME.comparison_id}`);
    const time = await issue("TIME-03", "Limits");
    expect(await within(time).findByText("Policy change detected", { selector: "span" })).toBeInTheDocument();  // the issue's state
    const change = within(time).getByRole("region", { name: "Policy change detected" });
    expect(change).toHaveTextContent("Previous published version, policies v1");
    expect(change).toHaveTextContent("Min receipts: 2");
    expect(change).toHaveTextContent("Current draft");
    expect(change).toHaveTextContent("Min receipts: 3");
    expect(change).toHaveTextContent("Reason for review: The current draft changes the min receipts of TIME-03, and the policy text of its clause changed too.");
    expect(change).toHaveTextContent("What to do: Verify that the current policy document supports the new value, then confirm or correct it.");
    expect(within(time).queryByRole("alert")).not.toBeInTheDocument();
    const summary = screen.getByRole("region", { name: "Compared with the published version" });
    expect(within(summary).getByRole("list", { name: "Policy changes" })).toHaveTextContent("TIME-03 Split transactions: Min receipts 2 → 3");
  });

  it("keeps the reviewer's place and shows the outcome when a save makes the compilation's first review", async () => {
    answer = reviewAnswers(WITH_LOGIC, withBaseline(AMENDED, (method, url) => {
      if (method === "POST" && url.pathname.endsWith("/review")) return { status: 201, body: { ...notReadyJson, review_id: "rev_first" } };
      if (url.pathname === `/api/v1/documents/extractions/${EXTRACTION.extraction_id}` && posts(`/api/v1/documents/extractions/${EXTRACTION.extraction_id}/review`).length) return { status: 200, body: { ...WITH_LOGIC, reviews: [{ review_id: "rev_first", review_sha256: "e".repeat(64), policy: "exceptions_only", ready_for_approval: false, remaining: 3, error_issues: 0, created_at: "2026-11-08T12:00:00Z" }] } };
      if (url.pathname.endsWith("/reviews/rev_first")) return new Promise<Answer>(() => {}) as unknown as Answer;  // still loading
      return undefined;
    }));
    renderAt(`/documents/extractions/${EXTRACTION.extraction_id}?tab=review&item=${MCC.comparison_id}`);
    const user = userEvent.setup();
    const mcc = await issue("MCC-03", "Limits");
    await user.click(await within(mcc).findByRole("button", { name: "Reuse this decision" }));
    expect(await screen.findByText("The earlier decision was saved again for this compilation, under your name.")).toBeInTheDocument();
    expect(screen.getByRole("article", { name: "MCC-03 Limits" })).toBeInTheDocument();
  });

  it("offers an unchanged earlier decision again, saved under the reviewer's name, and never a changed one", async () => {
    answer = reviewAnswers(WITH_LOGIC, withBaseline(AMENDED, saving(notReadyJson)));
    renderAt(`/documents/extractions/${EXTRACTION.extraction_id}?tab=review&item=${REG.comparison_id}`);
    const user = userEvent.setup();
    const reg = await issue("REG-01", "Limits");
    expect(await within(reg).findByText("Previous decision needs revalidation", { selector: "span" })).toBeInTheDocument();
    const earlier = within(reg).getByRole("region", { name: "Previous decision" });
    expect(earlier).toHaveTextContent("policies v1 was approved with: Used the text-match reading · “three counts rules”.");
    expect(within(earlier).queryByRole("button")).not.toBeInTheDocument();  // a changed question is decided again
    const summary = screen.getByRole("region", { name: "Compared with the published version" });
    expect(summary).toHaveTextContent("1 earlier decision needs revalidation");
    await user.click(within(summary).getByRole("button", { name: "Reuse 1 unchanged decision from policies v1" }));
    await waitFor(() => expect(reviewPosts()).toHaveLength(1));
    const sent = JSON.parse(reviewPosts()[0]!.body!).decisions.find((item: { comparison_id: string }) => item.comparison_id === MCC.comparison_id);
    expect(sent).toEqual({ comparison_id: MCC.comparison_id, extraction_sha256: MCC.extraction_sha256, reviewer: "r.eviewer", action: "accept_regex", rationale: "Reused from policies v1: same clause and readings. 5813 is a merchant code" });
  });

  it("says a rule that quotes words its clause no longer has needs the rule logic changed, offers no reviewer action, and is never ready", async () => {
    const stale = { ...WITH_LOGIC, status: "no_candidate", candidate_ruleset_sha256: null, discrepancies: [{ rule_id: "REG-01", policy_doc: "regional-addendum.md", field: "source_quote", stated: "report REG-01 in place of those three.", compiled: "report REG-01 instead of those three" }] } as unknown as ExtractionSummary;
    answer = reviewAnswers(stale, withBaseline({ published: V1 }));
    renderAt(`/documents/extractions/${EXTRACTION.extraction_id}?tab=review`);
    const panel = await screen.findByRole("region", { name: "REG-01 needs correction" });
    expect(panel).toHaveTextContent("The compiled rule cites the policy as saying “report REG-01 instead of those three”, which the current clause no longer contains");
    expect(panel).toHaveTextContent("No review decision can resolve this.");
    expect(within(panel).queryByRole("button")).not.toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Review status" })).getByText("No: a compiled rule needs correction first")).toBeInTheDocument();
  });

  it("asks for the rule a new clause needs, and adds it with the clause's own limit, who and why", async () => {
    const stated = { title: "Large weekend purchase", text: "A receipt over $500 dated on a weekend raises a **review signal**.", severities: [], review_signal: true, thresholds: [{ value: "500", unit: "usd" }] };
    const added = { ...WITH_LOGIC, status: "no_candidate", candidate_ruleset_sha256: null, rule_overrides: [], discrepancies: [{ rule_id: "TIME-05", policy_doc: "transaction-pattern-rules.md", field: "rule", stated, compiled: null }] } as unknown as ExtractionSummary;
    answer = reviewAnswers(added, withBaseline({ published: V1 }, (method, url) => {
      if (url.pathname === "/api/v1/taxonomy") return { status: 200, body: { fields: { "receipt.total_amount_usd": { type: "number" }, "receipt.entity": { type: "string" } }, vocabularies: {} } };
      return method === "POST" && url.pathname === "/api/v1/documents/extractions" ? { status: 201, body: { ...EXTRACTION, extraction_id: "ext_added" } } : undefined;
    }));
    const router = renderAt(`/documents/extractions/${EXTRACTION.extraction_id}?tab=review`);
    const user = userEvent.setup();
    expect(await screen.findByText("A new clause needs a rule")).toBeInTheDocument();
    const panel = screen.getByRole("region", { name: "TIME-05 needs a rule" });
    expect(panel).toHaveTextContent("A receipt over $500 dated on a weekend raises a review signal.");
    expect(within(panel).getByLabelText("limit usd")).toHaveValue("500");  // the clause's own limit, filled in
    const button = within(panel).getByRole("button", { name: "Add TIME-05 and compile again" });
    expect(button).toBeDisabled();
    expect(within(panel).getByLabelText("Still needed")).toHaveTextContent("Say when it applies");
    await user.click(within(panel).getByRole("button", { name: "Add a condition" }));
    await user.selectOptions(await within(panel).findByLabelText("When it applies 1: field"), "receipt.total_amount_usd");
    await user.selectOptions(within(panel).getByLabelText("When it applies 1: operator"), "gt");
    await user.selectOptions(within(panel).getByLabelText("When it applies 1: compared with"), "limit_usd");
    await user.type(within(panel).getByLabelText("Why this rule is added"), "Revision 2 adds TIME-05");
    expect(within(panel).queryByLabelText("Still needed")).not.toBeInTheDocument();
    await user.click(button);
    await waitFor(() => expect(posts("/api/v1/documents/extractions")).toHaveLength(1));
    const body = JSON.parse(posts("/api/v1/documents/extractions")[0]!.body!);
    expect(body.rule_overrides).toEqual([{
      rule_id: "TIME-05", add: true, rationale: "Revision 2 adds TIME-05",
      set: { title: "Large weekend purchase", outcome: "review_signal", scope: "receipt", source_quote: "A receipt over $500 dated on a weekend raises a review signal", params: { limit_usd: { type: "number", value: "500", unit: "usd" } }, applies_when: { op: "gt", field: "receipt.total_amount_usd", value: { param: "limit_usd" } } },
    }]);
    await waitFor(() => expect(router.state.location.pathname).toBe("/documents/extractions/ext_added"));
  });

  it("corrects the draft rule from an issue whose decision matches the policy but not the rule", async () => {
    const saved = { ...notReadyJson, decisions: [{ comparison_id: TIME.comparison_id, extraction_sha256: EXTRACTION.extraction_sha256, action: "accept_regex", reviewer: "r.eviewer" }], remaining: [], issues: [{ level: "error", code: "executable_conflict", comparison_id: TIME.comparison_id, message: "TIME-03 thresholds: this decision matches the clause, but the compiled rule does not" }] };
    const reviewed = { ...WITH_LOGIC, rule_overrides: [], reviews: [{ review_id: "rev_1", review_sha256: "b".repeat(64), policy: "exceptions_only", ready_for_approval: false, remaining: 0, error_issues: 1, created_at: "2026-11-08T12:00:00Z" }] } as unknown as ExtractionSummary;
    answer = reviewAnswers(reviewed, (method, url) => {
      if (url.pathname.endsWith("/reviews/rev_1")) return { status: 200, body: saved };
      if (url.pathname === "/api/v1/taxonomy") return { status: 200, body: { fields: {}, vocabularies: {} } };
      return method === "POST" && url.pathname === "/api/v1/documents/extractions" ? { status: 201, body: { ...EXTRACTION, extraction_id: "ext_corrected" } } : undefined;
    });
    renderAt(`/documents/extractions/${EXTRACTION.extraction_id}?tab=review&item=${TIME.comparison_id}`);
    const user = userEvent.setup();
    const time = await issue("TIME-03", "Limits");
    await user.click(within(await within(time).findByRole("alert")).getByRole("button", { name: "Correct the draft rule TIME-03" }));
    const correction = within(time).getByRole("region", { name: "Correct TIME-03" });
    await user.clear(within(correction).getByLabelText("min receipts"));
    await user.type(within(correction).getByLabelText("min receipts"), "3");
    await user.type(within(correction).getByLabelText("Why the rule changes"), "The policy asks for three");
    await user.click(within(correction).getByRole("button", { name: "Correct TIME-03 and compile again" }));
    await waitFor(() => expect(posts("/api/v1/documents/extractions")).toHaveLength(1));
    expect(JSON.parse(posts("/api/v1/documents/extractions")[0]!.body!).rule_overrides).toEqual([{ rule_id: "TIME-03", set: { params: { min_receipts: { type: "number", value: "3", unit: "count", source: "policy" } } }, rationale: "The policy asks for three" }]);
  });

  it("explains a compiled rule that no longer does what the policy states, and corrects the draft rule with who and why", async () => {
    const discrepant = { ...WITH_LOGIC, status: "no_candidate", candidate_ruleset_sha256: null, rule_overrides: [], discrepancies: [{ rule_id: "TIME-03", policy_doc: "transaction-pattern-rules.md", field: "thresholds", stated: [{ value: "3", unit: "count" }, { value: "50", unit: "usd" }, { value: "75", unit: "usd" }], compiled: { min_receipts: { value: "2", unit: "count" }, itemization_usd: { value: "75", unit: "usd" }, gift_cap_usd: { value: "50", unit: "usd" } } }] } as unknown as ExtractionSummary;
    answer = reviewAnswers(discrepant, withBaseline({ published: V1 }, (method, url) => (method === "POST" && url.pathname === "/api/v1/documents/extractions" ? { status: 201, body: { ...EXTRACTION, extraction_id: "ext_corrected" } } : undefined)));
    const router = renderAt(`/documents/extractions/${EXTRACTION.extraction_id}?tab=review`);
    const user = userEvent.setup();
    const panel = await screen.findByRole("region", { name: "TIME-03 needs correction" });
    expect(panel).toHaveTextContent("The policy states3 (count), 50 USD, 75 USD");
    expect(panel).toHaveTextContent("The draft rule checksmin receipts 2 (count), itemization 75 USD, gift cap 50 USD");
    expect(within(panel).getByLabelText("New min_receipts")).toHaveValue("3");  // the stated value no param holds
    const button = within(panel).getByRole("button", { name: "Correct the draft rule and compile again" });
    expect(button).toBeDisabled();  // a correction needs a reason
    await user.type(within(panel).getByLabelText("Why the draft rule changes"), "Revision 2 asks for three receipts");
    await user.click(button);
    await waitFor(() => expect(posts("/api/v1/documents/extractions")).toHaveLength(1));
    const body = JSON.parse(posts("/api/v1/documents/extractions")[0]!.body!);
    expect(body.actor).toBe("r.eviewer");
    expect(body.rule_overrides).toEqual([{ rule_id: "TIME-03", set: { params: { min_receipts: { type: "number", value: "3", unit: "count" } } }, rationale: "Revision 2 asks for three receipts" }]);
    expect(body.documents).toEqual(EXTRACTION.documents.map((document) => ({ document_id: document.document_id, revision: document.revision })));
    await waitFor(() => expect(router.state.location.pathname).toBe("/documents/extractions/ext_corrected"));
  });
});

describe("approval readiness", () => {
  it("lists exactly what blocks approval and opens each field from there", async () => {
    answer = reviewAnswers(EXTRACTION);
    renderAt(`/documents/extractions/${EXTRACTION.extraction_id}?tab=approval`);
    const user = userEvent.setup();
    const blocked = await screen.findByRole("list", { name: "What blocks approval" });
    expect(within(blocked).getAllByRole("listitem")).toHaveLength(EXTRACTION.review_required.length);
    expect(screen.getByRole("button", { name: "Create draft version for approval" })).toBeDisabled();
    await user.click(within(blocked).getAllByRole("button", { name: "Open this field" })[0]!);
    expect(await issue("MCC-03", "Limits")).toBeInTheDocument();  // the field the blocker names, among those to decide
    expect(screen.getByRole("radio", { name: "To decide · 4" })).toHaveAttribute("aria-checked", "true");
  });

  it("makes a draft version from a ready review", async () => {
    const reviewed = { ...EXTRACTION, reviews: [{ review_id: readyJson.review_id, review_sha256: readyJson.review_sha256, policy: "exceptions_only", ready_for_approval: true, remaining: 0, error_issues: 0, created_at: readyJson.created_at }] };
    answer = extractionAnswers(reviewed, (method, url) => {
      if (method === "POST" && url.pathname === "/api/v1/rulesets") return { status: 201, body: DRAFT };
      if (url.pathname.endsWith(`/reviews/${readyJson.review_id}`)) return { status: 200, body: readyJson };
      if (url.pathname === `/api/v1/rulesets/${ID}`) return { status: 200, body: DRAFT };
      return undefined;
    });
    const router = renderAt(`/documents/extractions/${EXTRACTION.extraction_id}?tab=approval`);
    const user = userEvent.setup();
    expect(await screen.findByText(/Ready for approval\./)).toBeInTheDocument();
    await user.type(await screen.findByLabelText("Ruleset name"), "policies");
    await user.click(screen.getByRole("button", { name: "Create draft version for approval" }));
    await waitFor(() => expect(router.state.location.pathname).toBe(`/policies/${ID}`));
    expect(JSON.parse(posts("/api/v1/rulesets")[0]!.body!)).toEqual({ name: "policies", extraction: { extraction_id: EXTRACTION.extraction_id, review_id: readyJson.review_id } });
  });
});

describe("plain-English rules", () => {
  it("shows what each rule does, with its technical form on demand", async () => {
    answer = (_, url) => (url.pathname === `/api/v1/rulesets/${ID}` ? { status: 200, body: PUBLISHED } : undefined);
    renderAt(`/policies/${ID}`);
    const user = userEvent.setup();
    const rules = await screen.findByRole("list", { name: "Rules" });
    await user.click(within(rules).getByRole("button", { name: /^REG-01/ }));
    expect(rules).toHaveTextContent("When it appliesWhen the entity is “APAC”.");
    expect(rules).toHaveTextContent("What it checksItems where the item's category is “alcohol” and the item is a purchase.");
    expect(rules).toHaveTextContent("Raises a high exception. Takes the place of ALC-01, ALC-02, ALC-03.");
    expect(within(rules).getByText("Technical details")).toBeInTheDocument();
  });
});

function readinessOf(status: RulesetDetail["status"], blockers: Readiness["blockers"] = []): Readiness {
  const next = ({ draft: "submit", in_review: "review", approved: "publish", published: "none" } as const)[status];
  return { status, next_step: next, rules_changed: 0, review_remaining: 0, validation_errors: 0, approval: status === "published" ? "published" : status === "approved" ? "approved" : "not_approved", approval_mode: status === "approved" ? "production" : null, can_publish: status === "approved" && !blockers.length, blockers };
}

describe("ruleset lifecycle", () => {
  function lifecycle(overrides: Partial<Record<"submit" | "review" | "approve" | "publish", Answer>> = {}) {
    const next: Record<string, RulesetDetail> = { submit: IN_REVIEW, review: ACCEPTED, approve: APPROVED, publish: PUBLISHED };
    let current: RulesetDetail = DRAFT;
    answer = (method, url) => {
      const step = url.pathname.split("/").pop()!;
      if (method === "POST") {
        const reply = overrides[step as keyof typeof overrides] ?? { status: 200, body: next[step] };
        if (reply.status === 200) current = next[step]!;
        return reply;
      }
      if (url.pathname === `/api/v1/rulesets/${ID}/readiness`) return { status: 200, body: readinessOf(current.status) };
      if (url.pathname === `/api/v1/rulesets/${ID}`) return { status: 200, body: DRAFT };
      if (url.pathname === `/api/v1/rulesets/${ID}/comparisons`) return { status: 200, body: rulesetComparisonsJson };
      return undefined;
    };
  }

  it("takes a version through submit, review, approval and a confirmed publication", async () => {
    lifecycle();
    renderAt(`/policies/${ID}`);
    const user = userEvent.setup();
    const panel = await screen.findByRole("region", { name: "Next step" });

    await user.click(within(panel).getByRole("button", { name: "Submit for review" }));
    expect(await within(panel).findByText(/recorded in this compilation's review/)).toBeInTheDocument();
    await user.click(within(panel).getByRole("button", { name: "Accept" }));
    const mode = await within(panel).findByLabelText("Publication the approval authorizes");
    expect(within(mode).getAllByRole("option").map((option) => option.textContent)).toEqual(["production"]);
    await user.click(within(panel).getByRole("button", { name: "Approve for production" }));

    const publish = await within(panel).findByRole("button", { name: "Publish" });
    expect(publish).toBeDisabled();
    await user.click(within(panel).getByRole("checkbox"));
    await user.click(publish);
    expect(await within(panel).findByText(/No step changes it now/)).toBeInTheDocument();

    const bodies = ["submit", "review", "approve", "publish"].map((step) => JSON.parse(posts(`/api/v1/rulesets/${ID}/${step}`)[0]!.body!));
    expect(bodies).toEqual([{ actor: "r.eviewer" }, { actor: "r.eviewer", decision: "accepted" }, { actor: "r.eviewer", mode: "production" }, { actor: "r.eviewer", approval_reference: APPROVED.approval_reference }]);
  });

  it("needs a reason to reject, and shows the compiler's refusal of an approval with what it reported", async () => {
    lifecycle({ approve: refusal(422, "approval_does_not_hold", "the compiler's publication safeguards refuse this approval for this version", { reasons: ["simulation readings cannot authorize production"] }) });
    answer = ((base) => (method: string, url: URL) => (method === "GET" && url.pathname === `/api/v1/rulesets/${ID}` ? { status: 200, body: IN_REVIEW } : base(method, url)))(answer);
    renderAt(`/policies/${ID}`);
    const user = userEvent.setup();
    const panel = await screen.findByRole("region", { name: "Next step" });
    const reject = await within(panel).findByRole("button", { name: "Reject" });
    expect(reject).toBeDisabled();
    await user.type(within(panel).getByLabelText("Reason"), "thresholds need a second look");
    expect(reject).toBeEnabled();

    await user.click(within(panel).getByRole("button", { name: "Accept" }));
    await user.click(await within(panel).findByRole("button", { name: "Approve for production" }));
    expect(await within(panel).findByText("approval_does_not_hold")).toBeInTheDocument();
    await user.click(within(panel).getByText("What the API reported"));
    expect(within(panel).getByText(/simulation readings cannot authorize production/)).toBeInTheDocument();
  });

  it("offers only the deployment's publication modes, and never production for simulation readings", async () => {
    backends = { ...BACKENDS, rulesets: { preview_allowed: false, publication_modes: ["production", "simulation"] } };
    const simulated = { ...ACCEPTED, compilation: { ...ACCEPTED.compilation!, reading_kind: "simulation" as const } };
    answer = (method, url) => (method === "GET" && url.pathname === `/api/v1/rulesets/${ID}` ? { status: 200, body: simulated } : url.pathname.endsWith("/comparisons") ? { status: 200, body: rulesetComparisonsJson } : undefined);
    renderAt(`/policies/${ID}`);
    const mode = await screen.findByLabelText("Publication the approval authorizes");
    const options = within(mode).getAllByRole("option") as HTMLOptionElement[];
    expect(options.map((option) => [option.value, option.disabled])).toEqual([["production", true], ["simulation", false]]);
    expect(screen.getByRole("button", { name: "Approve for simulation" })).toBeEnabled();
  });

  it("says why simulation readings cannot be approved where only production is allowed", async () => {
    const simulated = { ...ACCEPTED, compilation: { ...ACCEPTED.compilation!, reading_kind: "simulation" as const } };
    answer = (method, url) => (method === "GET" && url.pathname === `/api/v1/rulesets/${ID}` ? { status: 200, body: simulated } : url.pathname.endsWith("/comparisons") ? { status: 200, body: rulesetComparisonsJson } : undefined);
    renderAt(`/policies/${ID}`);
    expect(await screen.findByText(/simulation readings, which never authorize production/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Approve for/ })).not.toBeInTheDocument();
  });

  it("never offers approval of an uploaded artifact", async () => {
    const uploaded = { ...UPLOADED, status: "in_review" as const, review: ACCEPTED.review };
    answer = (_, url) => (url.pathname === `/api/v1/rulesets/${UPLOADED.ruleset_id}` ? { status: 200, body: uploaded } : undefined);
    renderAt(`/policies/${UPLOADED.ruleset_id}`);
    expect(await screen.findByText(/the compiler's approval covers only a version it compiled from policy documents/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Approve for/ })).not.toBeInTheDocument();
    const guidance = screen.getByRole("region", { name: "How to approve and publish" });
    expect(within(guidance).getByRole("link", { name: "Select policy documents" })).toHaveAttribute("href", "/policies?tab=documents");
    expect(within(guidance).getByRole("link", { name: "Find a compiled version" })).toHaveAttribute("href", "/policies?tab=rules");
    expect(screen.getByRole("link", { name: "Compile from policy documents" })).toHaveAttribute("href", "/policies?tab=documents");
  });
});

describe("history and comparison", () => {
  it("shows every lifecycle event, the review's decisions and the extracted fields against the executable rules", async () => {
    answer = (_, url) => {
      if (url.pathname === `/api/v1/rulesets/${ID}`) return { status: 200, body: PUBLISHED };
      if (url.pathname === `/api/v1/rulesets/${ID}/comparisons`) return { status: 200, body: rulesetComparisonsJson as unknown as RulesetComparisons };
      return undefined;
    };
    renderAt(`/policies/${ID}`);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "History" }));
    const events = await screen.findByRole("list", { name: "Lifecycle events" });
    expect(within(events).getAllByRole("listitem")).toHaveLength(PUBLISHED.transitions.length);
    expect(events).toHaveTextContent("Approved → Published");
    expect(events).toHaveTextContent("p.ublisher (claimed)");
    const decisions = await screen.findByRole("list", { name: "Review decisions" });
    expect(decisions).toHaveTextContent("TECH-01 quote: Override → “is a **high** exception, regardless of stated business purpose”");
    expect(screen.queryByRole("button", { name: /Submit|Approve|Publish/ })).not.toBeInTheDocument();

  });
});

describe("limit parameters", () => {
  it("finds where a stored rule's conditions compare each param, and says when no param holds a value", () => {
    const doc01 = { rule_id: "DOC-01", params: { total_usd: { type: "number" as const, value: "75", unit: "usd" } }, applies_when: { op: "all", conditions: [{ op: "gt", field: "receipt.total_amount_usd", value: { param: "total_usd" } }, { op: "eq", field: "receipt.itemized", value: false }] } };
    expect(logicOf(doc01).param_uses).toEqual({ total_usd: { in_conditions: ["applies_when"], helper: null } });
    render(<LimitUses ruleId="DOC-01" logic={logicOf(doc01)} readings={[[{ value: "75", unit: "usd" }, { value: "60", unit: "usd" }]]} clause="Receipts over $75 must be itemized." />);
    const list = screen.getByLabelText("How each limit is applied");
    expect(within(list).getAllByRole("listitem")[0]).toHaveTextContent("75 USD → Total total_usd");
    expect(within(list).getAllByRole("listitem")[0]).toHaveTextContent("Applies when");
    expect(within(list).getAllByRole("listitem")[1]).toHaveTextContent("no parameter of DOC-01 holds this value: the rule would not use it");
    expect(within(list).getAllByRole("listitem")[1]).toHaveTextContent("Not stated in this clause");
  });
});

describe("rule editing", () => {
  const NEW = { ...DRAFT, ruleset_id: "rs_new", version: 2 } as RulesetDetail;
  function editing(edit: Answer = { status: 201, body: NEW }) {
    answer = (method, url) => {
      if (method === "POST" && url.pathname === `/api/v1/rulesets/${PUBLISHED.ruleset_id}/edits`) return edit;
      if (url.pathname === `/api/v1/rulesets/${PUBLISHED.ruleset_id}`) return { status: 200, body: PUBLISHED };
      if (url.pathname === `/api/v1/rulesets/${PUBLISHED.ruleset_id}/readiness`) return { status: 200, body: readinessOf("published") };
      if (url.pathname === "/api/v1/rulesets/rs_new") return { status: 200, body: NEW };
      if (url.pathname === "/api/v1/taxonomy") return { status: 200, body: { fields: { "receipt.entity": { type: "string" } }, vocabularies: {} } };
      return undefined;
    };
  }

  it("shows each rule as ID, name and severity", async () => {
    editing();
    renderAt(`/policies/${PUBLISHED.ruleset_id}?tab=rules`);
    const rules = await screen.findByRole("list", { name: "Rules" });
    expect(within(rules).getByRole("button", { name: /^ALC-02/ })).toHaveTextContent(/^ALC-02Alcohol per-attendee cap.*Medium/);
  });

  it("shows a rule's technical details as readable JSON", async () => {
    editing();
    renderAt(`/policies/${PUBLISHED.ruleset_id}?tab=rules`);
    const user = userEvent.setup();
    const rules = await screen.findByRole("list", { name: "Rules" });
    await user.click(within(rules).getByRole("button", { name: /^ALC-02/ }));
    await user.click(within(rules).getByText("Technical details"));
    const details = within(rules).getByLabelText("Technical details of ALC-02");
    expect(details).toHaveTextContent('"rule_id": "ALC-02"');
    expect(details).not.toHaveTextContent("[object Object]");
  });

  it("requires confirmation before staging a rule removal, and cancellation changes nothing", async () => {
    editing();
    renderAt(`/policies/${PUBLISHED.ruleset_id}?tab=rules`);
    const user = userEvent.setup();
    const rules = await screen.findByRole("list", { name: "Rules" });
    await user.click(within(rules).getByRole("button", { name: /^ALC-02/ }));
    await user.click(within(rules).getByRole("button", { name: "Remove rule in new draft" }));
    expect(screen.getByRole("dialog", { name: "Remove ALC-02?" })).toHaveTextContent("Are you sure");
    expect(screen.queryByRole("region", { name: "Changes to save" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("region", { name: "Changes to save" })).not.toBeInTheDocument();
    await user.click(within(rules).getByRole("button", { name: "Remove rule in new draft" }));
    await user.click(screen.getByRole("button", { name: "Confirm removal" }));
    expect(screen.getByRole("region", { name: "Changes to save" })).toHaveTextContent("Inactive (removed from evaluation)");
    expect(calls.filter((call) => call.method !== "GET")).toHaveLength(0);
  });

  it("edits a published rule into a new draft version, listing before and after, and never changes the published one", async () => {
    editing();
    const router = renderAt(`/policies/${PUBLISHED.ruleset_id}?tab=rules`);
    const user = userEvent.setup();
    const rules = await screen.findByRole("list", { name: "Rules" });
    await user.click(within(rules).getByRole("button", { name: /^ALC-02/ }));
    await user.click(within(rules).getByRole("button", { name: "Edit" }));
    const name = within(rules).getByLabelText("Rule name");
    await user.clear(name);
    await user.type(name, "Alcohol cap per attendee");
    await user.click(within(rules).getByRole("button", { name: "Done" }));

    const changes = screen.getByRole("region", { name: "Changes to save" });
    expect(changes).toHaveTextContent("1 rule changed: before and after");
    expect(changes).toHaveTextContent(/ALC-02.*Alcohol per-attendee cap → Alcohol cap per attendee/);
    expect(changes).toHaveTextContent("This published version never changes.");
    const saveButton = within(changes).getByRole("button", { name: "Save as a new draft version" });
    expect(saveButton).toBeDisabled();  // a reason is required
    await user.type(within(changes).getByLabelText("Why these rules change (required)"), "Clearer name");
    await user.click(saveButton);
    await waitFor(() => expect(router.state.location.pathname).toBe("/policies/rs_new"));
    expect(JSON.parse(posts(`/api/v1/rulesets/${PUBLISHED.ruleset_id}/edits`)[0]!.body!)).toEqual({
      actor: "r.eviewer", rationale: "Clearer name", changes: [{ rule_id: "ALC-02", set: { title: "Alcohol cap per attendee" } }],
    });
    expect(calls.filter((call) => call.method !== "GET" && call.path !== `/api/v1/rulesets/${PUBLISHED.ruleset_id}/edits`)).toEqual([]);
  });

  it("shows the compiler's refusal of an edit, linked to the rule", async () => {
    editing(refusal(422, "rule_edit_invalid", "the compiler refuses these rule changes", { blockers: [{ message: "ALC-02: cap_usd is 40 usd, not in its clause", rule_id: "ALC-02" }] }));
    renderAt(`/policies/${PUBLISHED.ruleset_id}?tab=rules`);
    const user = userEvent.setup();
    const rules = await screen.findByRole("list", { name: "Rules" });
    await user.click(within(rules).getByRole("button", { name: /^ALC-02/ }));
    await user.click(within(rules).getByRole("button", { name: "Edit" }));
    const cap = within(rules).getByLabelText(/cap usd/i);
    await user.clear(cap);
    await user.type(cap, "40");
    const changes = screen.getByRole("region", { name: "Changes to save" });
    await user.type(within(changes).getByLabelText("Why these rules change (required)"), "Lower cap");
    await user.click(within(changes).getByRole("button", { name: "Save as a new draft version" }));
    const refused = await within(changes).findByRole("list", { name: "What the compiler refused" });
    expect(refused).toHaveTextContent("cap_usd is 40 usd, not in its clause");
    expect(within(refused).getByRole("button", { name: "ALC-02" })).toBeInTheDocument();
  });

  it("offers no editing for an uploaded artifact", async () => {
    answer = (_, url) => (url.pathname === `/api/v1/rulesets/${UPLOADED.ruleset_id}` ? { status: 200, body: UPLOADED } : url.pathname.endsWith("/readiness") ? { status: 200, body: readinessOf(UPLOADED.status) } : undefined);
    renderAt(`/policies/${UPLOADED.ruleset_id}?tab=rules`);
    const user = userEvent.setup();
    const rules = await screen.findByRole("list", { name: "Rules" });
    await user.click(within(rules).getAllByRole("button")[0]!);
    expect(within(rules).queryByRole("button", { name: "Edit" })).toBeNull();
  });
});

describe("publication workflow", () => {
  function at(version: RulesetDetail, readiness: Readiness) {
    answer = (_, url) => {
      if (url.pathname === `/api/v1/rulesets/${version.ruleset_id}`) return { status: 200, body: version };
      if (url.pathname === `/api/v1/rulesets/${version.ruleset_id}/readiness`) return { status: 200, body: readiness };
      if (url.pathname === `/api/v1/rulesets/${version.ruleset_id}/comparisons`) return { status: 200, body: rulesetComparisonsJson };
      return undefined;
    };
  }

  it("summarizes where a version stands and offers Publish only when the API says it may", async () => {
    at(APPROVED, { ...readinessOf("approved"), rules_changed: 2 });
    renderAt(`/policies/${ID}`);
    const panel = await screen.findByRole("region", { name: "Next step" });
    const summary = await within(panel).findByLabelText("Publication summary");
    expect(summary).toHaveTextContent(/Rules changed\s*2/);
    expect(summary).toHaveTextContent(/Approval\s*Approved/);
    expect(within(panel).getByRole("list", { name: "Publication steps" }).querySelector("[aria-current=step]")).toHaveTextContent("Publish");
    expect(await within(panel).findByRole("button", { name: "Publish" })).toBeDisabled();  // until confirmed
  });

  it("explains in plain words what blocks publication, linked to the rule, and offers no Publish", async () => {
    at(APPROVED, readinessOf("approved", [{ message: "A change request on ALC-02 is open: The cap is 30 USD.", rule_id: "ALC-02", comparison_id: null }]));
    renderAt(`/policies/${ID}`);
    const user = userEvent.setup();
    const panel = await screen.findByRole("region", { name: "Next step" });
    const blockers = await within(panel).findByRole("list", { name: "What blocks publication" });
    expect(blockers).toHaveTextContent("A change request on ALC-02 is open");
    expect(within(panel).queryByRole("button", { name: "Publish" })).toBeNull();
    await user.click(within(blockers).getByRole("button", { name: "Open ALC-02" }));
    const rules = await screen.findByRole("list", { name: "Rules" });
    expect(within(rules).getByRole("button", { name: /^ALC-02/ })).toHaveAttribute("aria-expanded", "true");
  });

  it("shows a version made by editing with each change, before and after", async () => {
    const edited = { ...DRAFT, based_on_version: 1, edits: { based_on: ID, based_on_version: 1, based_on_artifact_sha256: "0", at: "2026-11-08T12:00:00Z", actor: "e.ditor", rationale: "Clearer name", changes: [{ rule_id: "ALC-02", field: "title", before: "Alcohol per-attendee cap", after: "Alcohol cap per attendee" }] } } as unknown as RulesetDetail;
    at(edited, readinessOf("draft"));
    renderAt(`/policies/${ID}?tab=rules`);
    const changes = await screen.findByLabelText("Changes in this version");
    expect(changes).toHaveTextContent(/Changed from version 1.*Clearer name/);
    expect(changes).toHaveTextContent("Alcohol per-attendee cap → Alcohol cap per attendee");
  });
});


describe("policy analytics", () => {
  it("shows measured totals, unknown billing and old-record coverage, and filters replay", async () => {
    const live = { ...EXTRACTION, extraction_id: "ext_live", provenance: { ...EXTRACTION.provenance, execution_mode: "live", extraction_model: "test/model" }, analytics: {
      duration_ms: 1500, provider_latency_ms: 1200, live_calls: 2, failed_calls: 1, reused_documents: 0, documents: 9,
      new_spend_usd: "0.0003", cost_completeness: "incomplete", unpriced_calls: 1, input_tokens: null, output_tokens: null, calls: [],
    } };
    const replay = { ...EXTRACTION, extraction_id: "ext_replay", provenance: { ...EXTRACTION.provenance, execution_mode: "replay" }, analytics: {
      duration_ms: 250, provider_latency_ms: 0, live_calls: 0, failed_calls: 0, reused_documents: 9, documents: 9,
      new_spend_usd: "0", cost_completeness: "complete", unpriced_calls: 0, input_tokens: 0, output_tokens: 0, calls: [],
    } };
    answer = (_, url) => url.pathname === "/api/v1/documents/extractions" ? { status: 200, body: { items: [live, replay, EXTRACTION], total: 3, limit: 100, offset: 0 } } : undefined;
    renderAt("/policies?tab=analytics");
    const totals = await screen.findByLabelText("Policy analytics totals");
    expect(within(totals).getByText("At least $0.0003")).toBeInTheDocument();
    expect(within(totals).getByText("1.75 s")).toBeInTheDocument();
    expect(within(totals).getByText("875 ms")).toBeInTheDocument();
    expect(within(totals).getByText("1.20 s")).toBeInTheDocument();
    expect(screen.getByText(/1 older compilations have no execution metrics/)).toBeInTheDocument();
    expect(screen.getByText(/1 provider calls have no reported cost/)).toBeInTheDocument();
    const table = screen.getByRole("table", { name: "Compilation spending and latency" });
    expect(within(table).getByRole("link", { name: "ext_live" })).toHaveAttribute("href", "/documents/extractions/ext_live?tab=provenance");
    await userEvent.selectOptions(screen.getByLabelText("Execution mode"), "replay");
    expect(within(totals).getByText("$0.00")).toBeInTheDocument();
    expect(within(table).queryByRole("link", { name: "ext_live" })).not.toBeInTheDocument();
    expect(calls.filter((call) => call.method === "POST")).toHaveLength(0);
  });

  it("paginates compilation metrics without treating missing history as zero", async () => {
    const items = Array.from({ length: 12 }, (_, index) => ({ ...EXTRACTION, extraction_id: `ext_page_${index}` }));
    answer = (_, url) => url.pathname === "/api/v1/documents/extractions" ? { status: 200, body: { items, total: 12, limit: 100, offset: 0 } } : undefined;
    renderAt("/policies?tab=analytics");
    const table = await screen.findByRole("table", { name: "Compilation spending and latency" });
    expect(within(table).getAllByRole("link")).toHaveLength(10);
    expect(screen.getByText("1–10 of 12")).toBeInTheDocument();
    expect(within(screen.getByLabelText("Policy analytics totals")).getAllByText("Not recorded").length).toBeGreaterThan(0);
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(within(table).getAllByRole("link")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  });
});

it("shows every lifecycle status in version history and requires confirmation to delete a draft", async () => {
  answer = (method, url) => {
    if (url.pathname === "/api/v1/rulesets") return { status: 200, body: { items: [DRAFT, { ...APPROVED, ruleset_id: "approved-history" }, { ...PUBLISHED, ruleset_id: "published-history" }], total: 3, limit: 10, offset: 0 } };
    if (url.pathname === `/api/v1/rulesets/${ID}` && method === "DELETE") return { status: 200, body: { deleted: true, ruleset_id: ID } };
    if (url.pathname === `/api/v1/rulesets/${ID}`) return { status: 200, body: DRAFT };
    if (url.pathname === `/api/v1/rulesets/${PUBLISHED.ruleset_id}`) return { status: 200, body: PUBLISHED };
    return undefined;
  };
  renderAt("/policies?tab=rules");
  const user = userEvent.setup();
  const history = await screen.findByRole("region", { name: "All ruleset versions" });
  await waitFor(() => expect(within(history).getAllByRole("row")).toHaveLength(4));
  const deleteDraft = within(history).getByRole("button", { name: `Delete draft version: ${DRAFT.name} v${DRAFT.version}` });
  expect(within(history).getAllByRole("button", { name: /^Delete draft version:/ })).toHaveLength(1);
  await user.click(deleteDraft);
  expect(calls.filter(call => call.method === "DELETE")).toHaveLength(0);
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  expect(calls.filter(call => call.method === "DELETE")).toHaveLength(0);
  await user.click(deleteDraft);
  await user.click(screen.getByRole("button", { name: "Confirm deletion" }));
  await waitFor(() => expect(calls.filter(call => call.method === "DELETE")).toHaveLength(1));
});

it("confirms publication of identical rules and requires a reason before making the request", async () => {
  answer = (method, url) => {
    if (url.pathname.endsWith("/duplicate-publication")) return method === "POST"
      ? { status: 201, body: PUBLISHED }
      : { status: 200, body: { published: { name: "policies", version: 2, mode: "production" } } };
    if (url.pathname === `/api/v1/rulesets/${UPLOADED.ruleset_id}`) return { status: 200, body: UPLOADED };
    return undefined;
  };
  renderAt(`/policies/${UPLOADED.ruleset_id}`);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Publish anyway" }));
  expect(screen.getByRole("button", { name: "Confirm publication" })).toBeDisabled();
  expect(calls.filter(call => call.method === "POST")).toHaveLength(0);
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  expect(calls.filter(call => call.method === "POST")).toHaveLength(0);
  await user.click(screen.getByRole("button", { name: "Publish anyway" }));
  await user.type(screen.getByLabelText("Reason for publishing again"), "Reissue identical rules");
  await user.click(screen.getByRole("button", { name: "Confirm publication" }));
  await waitFor(() => expect(posts(`/api/v1/rulesets/${UPLOADED.ruleset_id}/duplicate-publication`)).toHaveLength(1));
  expect(JSON.parse(posts(`/api/v1/rulesets/${UPLOADED.ruleset_id}/duplicate-publication`)[0]!.body!)).toEqual({ actor: "r.eviewer", rationale: "Reissue identical rules" });
});

it("shows superseded originals only in history and links to their published successor", async () => {
  const successor = { ...PUBLISHED, ruleset_id: "published-successor", name: "default", version: 4 };
  const original = { ...UPLOADED, superseded_by: { ruleset_id: successor.ruleset_id, name: successor.name, version: successor.version } };
  answer = (_, url) => {
    if (url.pathname === "/api/v1/rulesets") return { status: 200, body: { items: url.searchParams.get("include_superseded") === "true" ? [successor, original] : [successor], total: url.searchParams.get("include_superseded") === "true" ? 2 : 1, limit: 10, offset: 0 } };
    if (url.pathname === `/api/v1/rulesets/${successor.ruleset_id}`) return { status: 200, body: successor };
    if (url.pathname === `/api/v1/rulesets/${original.ruleset_id}`) return { status: 200, body: original };
    return undefined;
  };
  renderAt("/policies?tab=rules");
  const user = userEvent.setup();
  const history = await screen.findByRole("region", { name: "All ruleset versions" });
  await waitFor(() => expect(within(history).getAllByRole("row")).toHaveLength(2));
  await user.click(screen.getByLabelText("Show superseded versions"));
  expect(await within(history).findByText("Superseded by default v4")).toBeInTheDocument();
  const row = within(history).getByText("Superseded by default v4").closest("tr")!;
  expect(within(row).queryByRole("button", { name: /Delete/ })).not.toBeInTheDocument();
  await user.click(within(row).getByRole("link"));
  expect(await screen.findByRole("link", { name: "View published successor" })).toHaveAttribute("href", "/policies/published-successor");
  expect(screen.queryByRole("region", { name: "How to approve and publish" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Publish anyway" })).not.toBeInTheDocument();
});

it("lists compiled versions when no separate extraction runs were recorded", async () => {
  answer = (_, url) => {
    if (url.pathname === "/api/v1/documents/extractions") return { status: 200, body: { items: [], total: 0, limit: 100, offset: 0 } };
    if (url.pathname === "/api/v1/rulesets") return { status: 200, body: { items: [PUBLISHED], total: 1, limit: 100, offset: 0 } };
    if (url.pathname === `/api/v1/rulesets/${PUBLISHED.ruleset_id}`) return { status: 200, body: PUBLISHED };
    if (url.pathname.endsWith("/comparisons")) return { status: 200, body: { extraction_sha256: PUBLISHED.compilation!.extraction_sha256, comparisons: [] } };
    return undefined;
  };
  renderAt("/policies?tab=review");
  await userEvent.setup().click(await screen.findByText("Compilation history", { selector: "summary" }));
  expect(await screen.findByRole("heading", { name: "Compiled ruleset versions" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: `${PUBLISHED.name} v${PUBLISHED.version} · Published` })).toHaveAttribute("href", `/policies/${PUBLISHED.ruleset_id}?tab=history`);
  expect(screen.queryByText("No compilations yet")).not.toBeInTheDocument();
});
