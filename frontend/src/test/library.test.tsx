import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Providers, routes } from "@/App";
import { createQueryClient } from "@/api/queries";
import type { DocumentDetail, ExtractionListItem, ExtractionSummary, Page } from "@/api/types";
import { deduplication, displayTitle, documentUsage, filterAndSort, isHiddenDuplicate, oneOfEach, groupByCategory, identicalContent, INITIAL_QUERY, lineDiff, locateQuote, similarlyNamed } from "@/pages/policy/library";
import { approvalBlockers, workflowStatus } from "@/pages/policy/workflow";
import backendsJson from "./fixtures/backends.json";
import documentJson from "./fixtures/policy/document.json";
import extractionJson from "./fixtures/policy/extraction.json";
import extractionsJson from "./fixtures/policy/extractions.json";

const BASE = documentJson as unknown as DocumentDetail;
const HISTORY = extractionsJson as unknown as Page<ExtractionListItem>;

/** Documents that look alike: two named "Travel Policy", one of them identical in text to a third. */
function doc(id: string, overrides: Partial<DocumentDetail>): DocumentDetail {
  return { ...BASE, document_id: `doc_${id.padStart(24, "0")}`, revision: 1, based_on_revision: null, content_sha256: id.repeat(64).slice(0, 64), category: null, description: null, scope: null, ...overrides };
}
const TRAVEL_EU = doc("1", { approval: { status: "approved" }, title: "Travel Policy", filename: "travel-policy.md", category: "travel_regional", description: "Flights and hotels in Europe.", scope: { regions: ["EMEA"], entities: [], expense_types: [] }, content: "# Travel\n\nEconomy class.\n" });
const TRAVEL_US = doc("2", { approval: { status: "needs_review" }, title: "Travel Policy", filename: "travel-policy-us.md", category: "travel_regional", description: "Flights and hotels in the US.", scope: { regions: ["AMER"], entities: [], expense_types: [] }, revision: 3, content: "# Travel\n\nBusiness class over 6 hours.\n" });
const TRAVEL_COPY = doc("3", { title: "Travel (copy)", filename: "travel-copy.md", content_sha256: TRAVEL_EU.content_sha256, content: TRAVEL_EU.content });
const GIFTS = doc("4", { title: "Gifts", filename: "gifts.md", category: "gifts_awards", description: "Client gifts.", created_at: "2026-11-08T00:00:00Z" });
const LIBRARY = [TRAVEL_EU, TRAVEL_US, TRAVEL_COPY, GIFTS];

describe("library logic", () => {
  it("plans to hide each identical copy behind one kept copy, the approved one, and never a merely similar one", () => {
    expect(deduplication(LIBRARY)).toEqual([{ document_id: TRAVEL_COPY.document_id, duplicate_of: TRAVEL_EU.document_id }]);
    const hidden = { ...TRAVEL_COPY, duplicate_of: TRAVEL_EU.document_id };
    expect(deduplication([TRAVEL_EU, TRAVEL_US, hidden, GIFTS])).toEqual([]);
    expect(isHiddenDuplicate(hidden, new Map(LIBRARY.map((item) => [item.document_id, item])))).toBe(true);
    // Its text changed since: no longer identical, so no longer hidden.
    expect(isHiddenDuplicate({ ...hidden, content_sha256: "e".repeat(64) }, new Map(LIBRARY.map((item) => [item.document_id, item])))).toBe(false);
    expect(oneOfEach(LIBRARY).map((item) => item.document_id)).toEqual([TRAVEL_EU, TRAVEL_US, GIFTS].map((item) => item.document_id));
  });

  it("tells identical content apart from merely similar names, by content hash only", () => {
    const twins = identicalContent(LIBRARY);
    expect(twins.get(TRAVEL_EU.document_id)!.map((item) => item.filename)).toEqual(["travel-copy.md"]);
    expect(twins.has(TRAVEL_US.document_id)).toBe(false);  // same title, other text: not identical
    const similar = similarlyNamed(LIBRARY);
    expect(similar.get(TRAVEL_EU.document_id)!.map((item) => item.filename)).toEqual(["travel-policy-us.md"]);
    expect(similar.has(TRAVEL_COPY.document_id)).toBe(false);
    // Same name and same text is an identical copy, never a look-alike to be told apart.
    const twin = { ...TRAVEL_EU, document_id: "doc_" + "7".repeat(24) };
    expect(similarlyNamed([TRAVEL_EU, twin]).size).toBe(0);
    expect(identicalContent([TRAVEL_EU, twin]).size).toBe(2);
  });

  it("groups by the chosen category, unclassified last, and never guesses one from a filename", () => {
    const groups = groupByCategory(LIBRARY);
    expect(groups.map((group) => [group.label, group.documents.length])).toEqual([["Gifts & Awards", 1], ["Travel & Regional", 2], ["Unclassified", 1]]);
    expect(groupByCategory([doc("9", { filename: "meals-and-entertainment-policy.md", category: null })])[0]!.label).toBe("Unclassified");
  });

  it("searches title, filename and description, filters by category and scope, and sorts", () => {
    const find = (change: Partial<typeof INITIAL_QUERY>) => filterAndSort(LIBRARY, { ...INITIAL_QUERY, ...change }).map((item) => item.filename);
    expect(find({ search: "europe" })).toEqual(["travel-policy.md"]);
    expect(find({ search: "travel-policy-us" })).toEqual(["travel-policy-us.md"]);
    expect(find({ search: "travel hotels" }).sort()).toEqual(["travel-policy-us.md", "travel-policy.md"]);
    expect(find({ category: "unclassified" })).toEqual(["travel-copy.md"]);
    expect(find({ scope: "AMER" })).toEqual(["travel-policy-us.md"]);
    expect(find({ sort: "revision" })[0]).toBe("travel-policy-us.md");
    expect(find({ sort: "updated" })[0]).toBe("gifts.md");
    expect(displayTitle({ title: null, filename: "meals-and-entertainment.md" })).toBe("Meals and entertainment");
  });

  it("diffs two texts line by line", () => {
    const { lines } = lineDiff("a\nb\nc\n", "a\nB\nc\nd\n");
    expect(lines.map((line) => `${line.kind[0]}${line.text}`)).toEqual(["sa", "rb", "aB", "sc", "ad", "s"]);
  });

  it("locates a quoted clause exactly or by whitespace, and never guesses", () => {
    const text = "# Policy\n\nMeals are capped\nat 75 USD.\n";
    expect(locateQuote(text, "Meals are capped\nat 75 USD.")).toEqual({ from: 3, to: 4 });
    expect(locateQuote(text, "Meals are   capped at 75 USD.")).toEqual({ from: 3, to: 4 });
    expect(locateQuote(text, "Meals are capped at 80 USD.")).toBeNull();
  });

  it("reads a historical extraction, with no versions or change requests recorded, as before", () => {
    const old = HISTORY.items[0]!;
    expect(old.versions).toBeUndefined();
    // This recorded extraction has a review the compiler accepted, and no version made from it yet.
    expect(old.reviews.some((review) => review.ready_for_approval)).toBe(true);
    expect(workflowStatus(old)).toBe("ready_for_approval");
    expect(workflowStatus({ ...old, reviews: [] })).toBe(old.ready_for_approval ? "ready_for_approval" : "ready_for_review");
    expect(approvalBlockers({ ...(extractionJson as unknown as ExtractionSummary), ready_for_approval: true, review_required: [] }, undefined, []).length).toBe(0);
    const usage = documentUsage([{ ...BASE, document_id: old.documents[0]!.document_id }], [old]);
    expect(usage.get(old.documents[0]!.document_id)!.label).toBe("Compiled");
  });

  it("says when a published document has been revised since", () => {
    const published = { ...HISTORY.items[0]!, documents: [{ ...HISTORY.items[0]!.documents[0]!, document_id: TRAVEL_US.document_id, revision: 2 }], versions: [{ ruleset_id: "rs_1", name: "policies", version: 1, status: "published" as const }] };
    expect(documentUsage([TRAVEL_US], [published]).get(TRAVEL_US.document_id)).toMatchObject({ label: "Revised since its ruleset was published", detail: "Published at revision 2; the latest is 3." });
    expect(documentUsage([{ ...TRAVEL_US, revision: 2 }], [published]).get(TRAVEL_US.document_id)!.label).toBe("In a published ruleset");
  });
});

// --- The pages ---

let calls: { method: string; path: string; body?: string }[];
let answer: (method: string, url: URL) => { status: number; body: unknown } | undefined;

beforeEach(() => {
  calls = [];
  answer = () => undefined;
  window.localStorage.setItem("epv.actor", "e.ditor");
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = new URL(String(input), "http://localhost");
      const method = init.method ?? "GET";
      calls.push({ method, path: `${url.pathname}${url.search}`, body: typeof init.body === "string" ? init.body : undefined });
      let reply = answer(method, url);
      const byId = new Map(LIBRARY.map((item) => [item.document_id, item]));
      const match = /^\/api\/v1\/documents\/(doc_[0-9a-f]{24})$/.exec(url.pathname);
      if (!reply && match && byId.has(match[1]!)) {
        const found = byId.get(match[1]!)!;
        const revision = Number(url.searchParams.get("revision") ?? found.revision);
        const latest = revision === found.revision;
        reply = { status: 200, body: { ...found, revision, content: latest ? found.content : found.content.replace("Business class over 6 hours", "Economy class"), content_sha256: latest ? found.content_sha256 : "f".repeat(64) } };
      }
      if (!reply && url.pathname === "/api/v1/documents") reply = { status: 200, body: { items: LIBRARY.map(({ content: _, ...rest }) => rest), total: LIBRARY.length, limit: 100, offset: 0 } };
      if (!reply && url.pathname === "/api/v1/documents/extractions") reply = { status: 200, body: HISTORY };
      if (!reply && url.pathname === "/api/v1/rulesets") reply = { status: 200, body: { items: [], total: 0, limit: 100, offset: 0 } };
      if (!reply && url.pathname === "/api/v1/backends") reply = { status: 200, body: backendsJson };
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

describe("document library", () => {
  it("groups documents by category and tells similar ones apart by what they are", async () => {
    renderAt("/documents");
    await openCategories();
    const travel = await screen.findByRole("region", { name: "Travel & Regional" });
    expect(within(travel).getAllByRole("listitem")).toHaveLength(2);
    const row = (filename: string) => within(travel).getAllByRole("listitem").find((item) => item.textContent!.includes(filename + "Flights"))!;
    // Two documents titled "Travel Policy", told apart by filename, description, scope and revision.
    expect(row("travel-policy.md")).toHaveTextContent("Travel PolicyApprovedtravel-policy.mdFlights and hotels in Europe.EMEA");
    expect(row("travel-policy-us.md")).toHaveTextContent("Travel PolicyNeeds reviewtravel-policy-us.mdFlights and hotels in the US.AMER");
    expect(row("travel-policy-us.md")).toHaveTextContent("Revision 3");
    expect(within(row("travel-policy.md")).getByText("Similar name")).toBeInTheDocument();
    // Its identical copy is unclassified, so in another group: there it is still marked identical.
    expect(within(row("travel-policy.md")).getByText("Identical content")).toBeInTheDocument();
    expect(within(row("travel-policy-us.md")).queryByText("Identical content")).not.toBeInTheDocument();
    // Internal identifiers stay out of the library.
    expect(screen.queryByText(TRAVEL_EU.document_id)).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Unclassified" })).toHaveTextContent("Travel (copy)");
  });

  it("searches, filters, collapses groups, and selects a whole category", async () => {
    renderAt("/documents");
    await openCategories();
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Search documents"), "hotels us");
    expect(screen.getByText("1 of 4 documents")).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Search documents"));
    await user.selectOptions(screen.getByLabelText("Scope"), "EMEA");
    expect(screen.getByText("1 of 4 documents")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Scope"), "");
    await user.selectOptions(screen.getByLabelText("Category"), "gifts_awards");
    expect(screen.queryByRole("region", { name: "Travel & Regional" })).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Category"), "");

    const header = screen.getByRole("button", { name: /Travel & Regional/ });
    await user.click(header);
    expect(header).toHaveAttribute("aria-expanded", "false");
    expect(within(screen.getByRole("region", { name: "Travel & Regional" })).queryAllByRole("listitem")).toHaveLength(0);
    await user.click(screen.getByRole("checkbox", { name: "Select all in Travel & Regional" }));
    expect(screen.getByRole("region", { name: "Selection" })).toHaveTextContent("2 documents selected");
    expect(screen.getByRole("link", { name: "Compare" }).getAttribute("href")).toMatch(/^\/documents\/compare\?a=doc_.*:\d&b=doc_.*:\d$/);
    await user.click(screen.getByRole("button", { name: "Clear selection" }));
    expect(screen.queryByRole("region", { name: "Selection" })).not.toBeInTheDocument();
  });

  it("compares two similarly named documents side by side, saying they differ", async () => {
    renderAt(`/documents/compare?a=${TRAVEL_EU.document_id}:1&b=${TRAVEL_US.document_id}:3`);
    expect(await screen.findByText("These documents share a name but not their text: they are not duplicates.")).toBeInTheDocument();
    const table = screen.getByRole("table", { name: "Line differences" });
    expect(table.querySelector('[data-change="removed"]')).toHaveTextContent("Economy class.");
    expect(table.querySelector('[data-change="added"]')).toHaveTextContent("Business class over 6 hours.");
  });

  it("calls two separate documents with the same text identical", async () => {
    renderAt(`/documents/compare?a=${TRAVEL_EU.document_id}:1&b=${TRAVEL_COPY.document_id}:1`);
    expect(await screen.findByText("Identical content")).toBeInTheDocument();
    expect(screen.getByText("No differences.")).toBeInTheDocument();
  });

  it("compares two revisions of one document", async () => {
    renderAt(`/documents/compare?a=${TRAVEL_US.document_id}:2&b=${TRAVEL_US.document_id}:3`);
    expect(await screen.findByRole("heading", { name: "Revision 2 and 3 of Travel Policy" })).toBeInTheDocument();
    expect(screen.getByText("1 line differs")).toBeInTheDocument();
  });

  it("opens a document's drawer with its business details, revision history and hidden technical details, and describes it without a new revision", async () => {
    answer = (method) => (method === "PATCH" ? { status: 200, body: { ...TRAVEL_COPY, category: "travel_regional" } } : undefined);
    renderAt("/documents");
    await openCategories();
    const user = userEvent.setup();
    const travel = await screen.findByRole("region", { name: "Travel & Regional" });
    const us = within(travel).getAllByRole("listitem").find((item) => item.textContent!.includes("travel-policy-us.md"))!;
    await user.click(within(us).getByRole("button", { name: "Travel Policy" }));
    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByText("Revision history")).toBeInTheDocument();
    expect(within(drawer).getAllByRole("link", { name: /^Revision \d$/ })).toHaveLength(3);
    expect(within(drawer).getByText(TRAVEL_US.document_id)).not.toBeVisible();  // inside closed technical details
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Travel (copy)" }));
    const copy = await screen.findByRole("dialog");
    expect(within(copy).getByText("Identical content")).toBeInTheDocument();
    await user.click(within(copy).getByRole("button", { name: "Edit description" }));
    await user.selectOptions(within(copy).getByLabelText("Policy category"), "travel_regional");
    await user.click(within(copy).getByRole("button", { name: "Save description" }));
    await waitFor(() => expect(calls.filter((call) => call.method === "PATCH")).toHaveLength(1));
    const patch = calls.find((call) => call.method === "PATCH")!;
    expect(patch.path).toBe(`/api/v1/documents/${TRAVEL_COPY.document_id}/profile`);
    expect(JSON.parse(patch.body!)).toEqual({ actor: "e.ditor", category: "travel_regional", description: null, scope: { regions: [], entities: [], expense_types: [] } });
    expect(calls.some((call) => call.method === "POST")).toBe(false);  // no revision
  });

  it("opens a document at the line a mismatch points to", async () => {
    renderAt(`/documents/${TRAVEL_US.document_id}?revision=3&line=3`);
    const source = await screen.findByRole("list", { name: "Document source" });
    expect(within(source).getAllByRole("listitem")[2]).toHaveClass("bg-primary-soft");
  });

  it("keeps compilation history on its own tab, reading historical extractions as before", async () => {
    renderAt("/documents?tab=history");
    await userEvent.setup().click(await screen.findByText("Compilation history", { selector: "summary" }));
    const table = await screen.findByRole("table", { name: "Compilation history" });
    const row = within(table).getAllByRole("row")[1]!;
    expect(row).toHaveTextContent("Replay");
    expect(row).toHaveTextContent("None yet");
    expect(within(row).getByRole("link", { name: "Provenance" })).toHaveAttribute("href", `/documents/extractions/${HISTORY.items[0]!.extraction_id}?tab=provenance`);
    expect(screen.queryByRole("region", { name: "Travel & Regional" })).not.toBeInTheDocument();
  });
});

describe("document approval", () => {
  it("filters by the approval of each exact revision", async () => {
    renderAt("/policies?tab=documents");
    await openCategories();
    const user = userEvent.setup();
    expect(await screen.findByRole("radio", { name: "Approved · 1" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Needs review · 1" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Draft · 2" })).toBeInTheDocument();  // never acted on: drafts, never inherited
    await user.click(screen.getByRole("radio", { name: "Approved · 1" }));
    expect(screen.getByText("1 of 4 documents")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Travel & Regional" })).toHaveTextContent("travel-policy.md");
    await user.click(screen.getByRole("radio", { name: "Draft · 2" }));
    expect(screen.queryByRole("region", { name: "Travel & Regional" })).not.toBeInTheDocument();
  });

  it("groups identical copies together, each with its own approval, and says when they differ", async () => {
    const copy = { ...TRAVEL_COPY, category: "travel_regional" as const };
    answer = (_, url) => (url.pathname === "/api/v1/documents" ? { status: 200, body: { items: [TRAVEL_EU, TRAVEL_US, copy, GIFTS].map(({ content: _, ...rest }) => rest), total: 4, limit: 100, offset: 0 } } : undefined);
    renderAt("/policies?tab=documents");
    await openCategories();
    const copies = await screen.findByRole("listitem", { name: /, 2 identical copies$/ });
    expect(copies).toHaveTextContent("Approvals differ: 1 approved, 1 draft");
    await userEvent.setup().click(within(copies).getByRole("button", { name: /2 identical copies/ }));
    const rows = within(copies).getAllByRole("listitem");
    const status = (filename: string) => rows.find((item) => item.textContent!.includes(filename))!.textContent!.match(/Approved|Needs review|Draft/)![0];
    expect([status("travel-policy.md"), status("travel-copy.md")]).toEqual(["Approved", "Draft"]);  // each its own, no group status
  });

  it("submits and approves one exact revision, naming its hash, with its history", async () => {
    const history: unknown[] = [];
    let status = "draft";
    answer = (method, url) => {
      if (!url.pathname.endsWith("/revisions/3/approval")) return undefined;
      if (method === "POST") {
        status = status === "draft" ? "needs_review" : "approved";
        history.push({ action: status === "approved" ? "approve" : "submit", from_status: "draft", to_status: status, actor: { claimed: "e.ditor", verified: false }, note: null, content_sha256: TRAVEL_US.content_sha256, at: "2026-11-08T12:00:00Z" });
      }
      return { status: 200, body: { document_id: TRAVEL_US.document_id, revision: 3, status, content_sha256: TRAVEL_US.content_sha256, history } };
    };
    renderAt("/policies?tab=documents");
    await openCategories();
    const user = userEvent.setup();
    const travel = await screen.findByRole("region", { name: "Travel & Regional" });
    const us = within(travel).getAllByRole("listitem").find((item) => item.textContent!.includes("travel-policy-us.md"))!;
    await user.click(within(us).getByRole("button", { name: "Travel Policy" }));
    const panel = await screen.findByRole("region", { name: "Approval of this revision" });
    expect(within(panel).getByText("Approval of revision 3")).toBeInTheDocument();
    await user.click(await within(panel).findByRole("button", { name: "Submit for review" }));
    await user.click(await within(panel).findByRole("button", { name: "Approve this revision" }));
    await waitFor(() => expect(calls.filter((call) => call.method === "POST")).toHaveLength(2));
    const bodies = calls.filter((call) => call.method === "POST").map((call) => [call.path, JSON.parse(call.body!)]);
    expect(bodies).toEqual([
      [`/api/v1/documents/${TRAVEL_US.document_id}/revisions/3/approval`, { action: "submit", actor: "e.ditor", content_sha256: TRAVEL_US.content_sha256 }],
      [`/api/v1/documents/${TRAVEL_US.document_id}/revisions/3/approval`, { action: "approve", actor: "e.ditor", content_sha256: TRAVEL_US.content_sha256 }],
    ]);
    expect(await within(panel).findByRole("list", { name: "Approval history" })).toHaveTextContent("Approved by e.ditor");
  });
});

describe("collapsed categories, duplicates and select all", () => {
  it("keeps the copy a person chooses of identical documents, hiding the others after a confirmation", async () => {
    const copies = [TRAVEL_EU, { ...TRAVEL_COPY, category: "travel_regional" as const }, doc("5", { title: "Travel (copy 2)", filename: "travel-copy-2.md", category: "travel_regional", content_sha256: TRAVEL_EU.content_sha256 })];
    let library = copies.map(({ content: _, ...rest }) => rest) as Omit<DocumentDetail, "content">[];
    answer = (method, url) => {
      if (method === "PATCH") {
        const id = url.pathname.split("/")[4]!;
        library = library.map((item) => (item.document_id === id ? { ...item, duplicate_of: JSON.parse(calls.at(-1)!.body!).duplicate_of } : item));
        return { status: 200, body: library.find((item) => item.document_id === id) };
      }
      return url.pathname === "/api/v1/documents" ? { status: 200, body: { items: library, total: library.length, limit: 100, offset: 0 } } : undefined;
    };
    renderAt("/policies?tab=documents");
    const user = userEvent.setup();
    await openCategories();
    const entry = await screen.findByRole("listitem", { name: /, 3 identical copies$/ });
    await user.click(within(entry).getByRole("button", { name: "3 identical copies" }));
    const second = within(entry).getAllByRole("listitem").find((item) => item.textContent!.includes("travel-copy-2.md"))!;
    await user.click(within(second).getByRole("button", { name: "Keep this one" }));
    const confirm = within(entry).getByRole("region", { name: /^Keep one copy of/ });
    expect(confirm).toHaveTextContent("Nothing is deleted");
    expect(calls.filter((call) => call.method === "PATCH")).toEqual([]);
    await user.click(within(confirm).getByRole("button", { name: "Keep this copy, hide 2" }));
    await waitFor(() => expect(calls.filter((call) => call.method === "PATCH")).toHaveLength(2));
    const kept = copies[2]!.document_id;
    expect(calls.filter((call) => call.method === "PATCH").map((call) => [call.path.split("/")[4], JSON.parse(call.body!).duplicate_of])).toEqual([[TRAVEL_EU.document_id, kept], [TRAVEL_COPY.document_id, kept]]);
    // One entry remains, saying what it hides.
    expect(await screen.findByText("Kept copy: 2 identical duplicates hidden")).toBeInTheDocument();
    expect(screen.queryByRole("listitem", { name: /identical copies$/ })).toBeNull();
  });

  it("starts with every category collapsed, and a search opens them", async () => {
    renderAt("/policies?tab=documents");
    const user = userEvent.setup();
    const travel = await screen.findByRole("region", { name: "Travel & Regional" });
    expect(within(travel).getByRole("button", { name: /Travel & Regional/ })).toHaveAttribute("aria-expanded", "false");
    expect(within(travel).queryByRole("list")).toBeNull();
    await user.type(screen.getByLabelText("Search documents"), "gifts");
    expect(await screen.findByRole("list", { name: "Gifts & Awards documents" })).toHaveTextContent("gifts.md");
  });

  it("selects one copy of each document shown at once", async () => {
    renderAt("/policies?tab=documents");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("checkbox", { name: "Select all" }));
    expect(screen.getByRole("region", { name: "Selection" })).toHaveTextContent("3 documents selected");  // the identical copy is not selected twice
    await user.click(screen.getByRole("checkbox", { name: "Select all" }));
    expect(screen.queryByRole("region", { name: "Selection" })).toBeNull();
  });

  it("hides identical copies after a confirmation, deleting nothing, and can show and restore them", async () => {
    let library = LIBRARY.map(({ content: _, ...rest }) => rest) as Omit<DocumentDetail, "content">[];
    answer = (method, url) => {
      if (method === "PATCH") {
        const id = url.pathname.split("/")[4]!;
        const body = JSON.parse(calls.at(-1)!.body!);
        library = library.map((item) => (item.document_id === id ? { ...item, duplicate_of: body.duplicate_of } : item));
        return { status: 200, body: library.find((item) => item.document_id === id) };
      }
      return url.pathname === "/api/v1/documents" ? { status: 200, body: { items: library, total: library.length, limit: 100, offset: 0 } } : undefined;
    };
    renderAt("/policies?tab=documents");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /^Deduplicate all: keep one of each \(1 to hide\)/ }));
    const confirm = screen.getByRole("region", { name: "Hide duplicates" });
    expect(confirm).toHaveTextContent("Nothing is deleted");
    expect(calls.filter((call) => call.method === "PATCH")).toEqual([]);  // not before confirming
    await user.click(within(confirm).getByRole("button", { name: "Hide duplicates" }));
    await waitFor(() => expect(calls.filter((call) => call.method === "PATCH")).toHaveLength(1));
    const patch = calls.find((call) => call.method === "PATCH")!;
    expect([patch.path, JSON.parse(patch.body!)]).toEqual([`/api/v1/documents/${TRAVEL_COPY.document_id}/profile`, { actor: "e.ditor", duplicate_of: TRAVEL_EU.document_id }]);
    expect(calls.some((call) => call.method === "DELETE")).toBe(false);

    expect(await screen.findByText("3 documents")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "1 duplicate hidden · show" }));
    expect(await screen.findByText("4 documents")).toBeInTheDocument();
    await openCategories();
    const hidden = screen.getAllByRole("listitem").find((item) => item.textContent!.includes("travel-copy.md") && item.textContent!.includes("Hidden duplicate"))!;
    await user.click(within(hidden).getByRole("button", { name: "Restore" }));
    await waitFor(() => expect(JSON.parse(calls.filter((call) => call.method === "PATCH").at(-1)!.body!)).toEqual({ actor: "e.ditor", duplicate_of: null }));
  });
});
