import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Providers, routes } from "@/App";
import { createQueryClient } from "@/api/queries";
import type { Backends, DocumentDetail, ExtractionSummary, Page, RulesetComparisons, RulesetDetail } from "@/api/types";
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
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = new URL(String(input), "http://localhost");
      const method = init.method ?? "GET";
      calls.push({ method, path: `${url.pathname}${url.search}`, body: typeof init.body === "string" ? init.body : undefined });
      let reply = answer(method, url);
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
    const user = userEvent.setup();
    expect(await screen.findByRole("link", { name: DOCUMENT.filename })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Upload" }));
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

describe("extraction", () => {
  it("extracts by replay from a recording set chosen explicitly, and never offers live when it is unavailable", async () => {
    answer = (method, url) => (method === "POST" && url.pathname === "/api/v1/documents/extractions" ? { status: 201, body: EXTRACTION } : url.pathname === `/api/v1/documents/extractions/${EXTRACTION.extraction_id}` ? { status: 200, body: EXTRACTION } : undefined);
    const router = renderAt("/documents");
    const user = userEvent.setup();
    const [first, second] = DOCUMENTS.items;
    await user.click(await screen.findByRole("checkbox", { name: `Select ${first!.filename}` }));
    await user.click(screen.getByRole("checkbox", { name: `Select ${second!.filename}` }));

    expect(screen.getByRole("radio", { name: "live" })).toBeDisabled();
    const extract = screen.getByRole("button", { name: "Extract by replay" });
    expect(extract).toBeDisabled();
    await user.selectOptions(screen.getByLabelText("Recording set"), "live");
    await user.click(extract);
    await waitFor(() => expect(router.state.location.pathname).toBe(`/documents/extractions/${EXTRACTION.extraction_id}`));
    expect(JSON.parse(posts("/api/v1/documents/extractions")[0]!.body!)).toEqual({
      documents: [first, second].map((item) => ({ document_id: item!.document_id, revision: item!.revision })),
      recording_set: "live",
    });
  });

  it("sends a live extraction only with a model and an explicit paid-call confirmation", async () => {
    backends = { ...BACKENDS, allowed_models: ["vendor/model-a"], policy_compiler: { ...BACKENDS.policy_compiler, live: { permitted: true, configured: true, credential_configured: true, available: true } } };
    answer = () => undefined;
    renderAt("/documents");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("checkbox", { name: `Select ${DOCUMENTS.items[0]!.filename}` }));
    await user.click(screen.getByRole("radio", { name: "live" }));
    const paid = screen.getByRole("button", { name: "Extract live (paid)" });
    expect(paid).toBeDisabled();
    await user.selectOptions(screen.getByLabelText("Model"), "vendor/model-a");
    expect(paid).toBeDisabled();
    expect(posts("/api/v1/documents/extractions")).toHaveLength(0);
    await user.click(screen.getByRole("checkbox", { name: "I understand live extraction calls a paid model" }));
    await user.click(paid);
    await waitFor(() => expect(posts("/api/v1/documents/extractions")).toHaveLength(1));
    expect(JSON.parse(posts("/api/v1/documents/extractions")[0]!.body!)).toMatchObject({ execution_mode: "live", extraction_model: "vendor/model-a" });
  });

  it("shows unresolved fields with their source evidence and both readings, never filled in", async () => {
    answer = (_, url) => (url.pathname === `/api/v1/documents/extractions/${EXTRACTION.extraction_id}` ? { status: 200, body: EXTRACTION } : url.pathname.endsWith("/comparisons") ? { status: 200, body: comparisonsJson } : undefined);
    renderAt(`/documents/extractions/${EXTRACTION.extraction_id}`);
    const tech = await screen.findByRole("listitem", { name: "TECH-01 quote" });
    expect(within(tech).getByText("disagree")).toBeInTheDocument();
    expect(within(tech).getByText("“Personal electronics are not reimbursable.”")).toBeInTheDocument();
    expect(within(tech).getAllByText("missing").length).toBeGreaterThan(0);
    const evidence = EXTRACTION.review_required.find((item) => item.rule_id === "TECH-01")!.evidence!;
    expect(within(tech).getByText((_, element) => element?.tagName === "BLOCKQUOTE" && element.textContent === evidence)).toBeInTheDocument();
    expect(screen.getAllByText(/unresolved: .* TIME-03 thresholds is disagree/).length).toBe(1);
  });
});

describe("extraction review", () => {
  it("sends every decision exactly, an override value without rounding, and shows a review the compiler does not accept", async () => {
    answer = (method, url) => (method === "POST" ? { status: 201, body: notReadyJson } : url.pathname === `/api/v1/documents/extractions/${EXTRACTION.extraction_id}` ? { status: 200, body: EXTRACTION } : url.pathname.endsWith("/comparisons") ? { status: 200, body: comparisonsJson } : undefined);
    renderAt(`/documents/extractions/${EXTRACTION.extraction_id}`);
    const user = userEvent.setup();
    const send = await screen.findByRole("button", { name: "Send review" });
    expect(send).toBeDisabled();

    for (const comparison of EXTRACTION.review_required) {
      const card = screen.getByRole("listitem", { name: `${comparison.rule_id} ${comparison.field}` });
      await user.selectOptions(within(card).getByLabelText("Decision"), comparison.rule_id === "TIME-03" ? "override" : "accept_regex");
    }
    const time = screen.getByRole("listitem", { name: "TIME-03 thresholds" });
    await user.type(within(time).getByLabelText("Value (JSON)"), "not json");
    expect(screen.getByText(/TIME-03 thresholds: the value must be JSON/)).toBeInTheDocument();
    await user.clear(within(time).getByLabelText("Value (JSON)"));
    await user.click(within(time).getByLabelText("Value (JSON)"));
    await user.paste('[{"unit": "usd", "value": 75.50}]');
    await user.type(within(time).getByLabelText("Rationale"), "the policy says 75.50");
    await user.click(send);

    await waitFor(() => expect(posts(`/api/v1/documents/extractions/${EXTRACTION.extraction_id}/review`)).toHaveLength(1));
    const body = posts(`/api/v1/documents/extractions/${EXTRACTION.extraction_id}/review`)[0]!.body!;
    expect(body).toContain('"value":[{"unit":"usd","value":75.50}]');
    expect(JSON.parse(body)).toMatchObject({ actor: "r.eviewer", extraction_sha256: EXTRACTION.extraction_sha256 });
    expect(JSON.parse(body).decisions).toHaveLength(4);
    expect(await screen.findByText(/Not ready: the compiler does not accept this review yet/)).toBeInTheDocument();
    expect(screen.getByText(/Still undecided: REG-01 thresholds, TECH-01 quote, TIME-03 thresholds/)).toBeInTheDocument();
  });

  it("makes a draft version from a ready review", async () => {
    const reviewed = { ...EXTRACTION, reviews: [{ review_id: readyJson.review_id, review_sha256: readyJson.review_sha256, policy: "exceptions_only", ready_for_approval: true, remaining: 0, error_issues: 0, created_at: readyJson.created_at }] };
    answer = (method, url) => {
      if (method === "POST" && url.pathname === "/api/v1/rulesets") return { status: 201, body: DRAFT };
      if (url.pathname === `/api/v1/documents/extractions/${EXTRACTION.extraction_id}`) return { status: 200, body: reviewed };
      if (url.pathname.endsWith("/comparisons")) return { status: 200, body: comparisonsJson };
      if (url.pathname === `/api/v1/rulesets/${ID}`) return { status: 200, body: DRAFT };
      return undefined;
    };
    const router = renderAt(`/documents/extractions/${EXTRACTION.extraction_id}`);
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Ruleset name"), "policies");
    await user.click(screen.getByRole("button", { name: "Create draft version" }));
    await waitFor(() => expect(router.state.location.pathname).toBe(`/policies/${ID}`));
    expect(JSON.parse(posts("/api/v1/rulesets")[0]!.body!)).toEqual({ name: "policies", extraction: { extraction_id: EXTRACTION.extraction_id, review_id: readyJson.review_id } });
  });
});

describe("ruleset lifecycle", () => {
  function lifecycle(overrides: Partial<Record<"submit" | "review" | "approve" | "publish", Answer>> = {}) {
    const next: Record<string, RulesetDetail> = { submit: IN_REVIEW, review: ACCEPTED, approve: APPROVED, publish: PUBLISHED };
    answer = (method, url) => {
      const step = url.pathname.split("/").pop()!;
      if (method === "POST") return overrides[step as keyof typeof overrides] ?? { status: 200, body: next[step] };
      if (url.pathname === `/api/v1/rulesets/${ID}`) return { status: 200, body: DRAFT };
      if (url.pathname === `/api/v1/rulesets/${ID}/comparisons`) return { status: 200, body: rulesetComparisonsJson };
      return undefined;
    };
  }

  it("takes a version through submit, review, approval and a confirmed publication", async () => {
    lifecycle();
    renderAt(`/policies/${ID}`);
    const user = userEvent.setup();
    const panel = await screen.findByRole("region", { name: "Lifecycle" });

    await user.click(within(panel).getByRole("button", { name: "Submit for review" }));
    expect(await within(panel).findByText(/reuses the decisions of extraction review/)).toBeInTheDocument();
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
    const panel = await screen.findByRole("region", { name: "Lifecycle" });
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
    const events = await screen.findByRole("list", { name: "Lifecycle events" });
    expect(within(events).getAllByRole("listitem")).toHaveLength(PUBLISHED.transitions.length);
    expect(events).toHaveTextContent("Approved → Published");
    expect(events).toHaveTextContent("p.ublisher (claimed)");
    const decisions = await screen.findByRole("list", { name: "Review decisions" });
    expect(decisions).toHaveTextContent("TECH-01 quote: Override → “is a **high** exception, regardless of stated business purpose”");
    expect(screen.queryByRole("button", { name: /Submit|Approve|Publish/ })).not.toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Comparison" }));
    const tech = await screen.findByRole("region", { name: "Rule TECH-01" });
    expect(within(tech).getByText("In this version (executable)")).toBeInTheDocument();
    expect(within(tech).getByText("disagree")).toBeInTheDocument();
  });
});
