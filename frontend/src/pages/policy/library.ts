/**
 * The document library's logic, apart from its layout: categories, search, filters, sorting, grouping, telling
 * similar documents apart, and a line diff for comparing two texts. Nothing here guesses a category: a document
 * without one chosen by a person is unclassified.
 */
import type { DocumentApprovalStatus, DocumentCategory, DocumentSummary, ExtractionListItem } from "@/api/types";

export const CATEGORIES: { id: DocumentCategory; label: string }[] = [
  { id: "meals_entertainment", label: "Meals & Entertainment" },
  { id: "gifts_awards", label: "Gifts & Awards" },
  { id: "approvals_exceptions", label: "Approvals & Exceptions" },
  { id: "travel_regional", label: "Travel & Regional" },
  { id: "receipts_documentation", label: "Receipts & Documentation" },
  { id: "merchant_restrictions", label: "Merchant Restrictions" },
  { id: "technology_equipment", label: "Technology & Equipment" },
];
export const UNCLASSIFIED = "unclassified";
export type CategoryKey = DocumentCategory | typeof UNCLASSIFIED;

export function categoryKey(document: DocumentSummary): CategoryKey {
  return document.category ?? UNCLASSIFIED;
}

export function categoryLabel(key: CategoryKey | null | undefined): string {
  if (!key || key === UNCLASSIFIED) return "Unclassified";
  return CATEGORIES.find((item) => item.id === key)?.label ?? key;
}

/** The name a person reads: the title, else the filename without its extension, made readable. */
export function displayTitle(document: Pick<DocumentSummary, "title" | "filename">): string {
  if (document.title?.trim()) return document.title.trim();
  const stem = document.filename.replace(/\.md$/i, "").replace(/[-_.]+/g, " ").trim();
  return stem ? stem.charAt(0).toUpperCase() + stem.slice(1) : document.filename;
}

/** Every scope value, for filters and display. */
export function scopeValues(document: DocumentSummary): string[] {
  const scope = document.scope;
  return scope ? [...scope.regions, ...scope.entities, ...scope.expense_types] : [];
}

export function scopeText(document: DocumentSummary): string | null {
  const scope = document.scope;
  if (!scope) return null;
  const parts = [scope.regions.join(", "), scope.entities.join(", "), scope.expense_types.join(", ")].filter(Boolean);
  return parts.length ? parts.join(" · ") : null;
}

export type SortKey = "title" | "updated" | "revision";

export interface LibraryQuery {
  search: string;
  category: CategoryKey | "";
  scope: string;
  sort: SortKey;
  approval: DocumentApprovalStatus | "";
}

export const INITIAL_QUERY: LibraryQuery = { search: "", category: "", scope: "", sort: "title", approval: "" };

export const APPROVAL_TEXT: Record<DocumentApprovalStatus, string> = { draft: "Draft", needs_review: "Needs review", approved: "Approved" };

/** This exact revision's approval: one never acted on, or listed before approval existed, is a draft. */
export function approvalOf(document: DocumentSummary): DocumentApprovalStatus {
  return document.approval?.status ?? "draft";
}

/**
 * Documents in their order, with identical copies (equal content hashes) brought together where the first appears.
 * Each copy stays its own document with its own approval: a cluster has no status of its own.
 */
export function clusters(documents: readonly DocumentSummary[]): DocumentSummary[][] {
  const out: DocumentSummary[][] = [];
  const at = new Map<string, DocumentSummary[]>();
  for (const document of documents) {
    const existing = at.get(document.content_sha256);
    if (existing) existing.push(document);
    else {
      const cluster = [document];
      at.set(document.content_sha256, cluster);
      out.push(cluster);
    }
  }
  return out;
}

/** Hidden as a duplicate: marked a duplicate of a document that still has exactly its content. */
export function isHiddenDuplicate(document: DocumentSummary, byId: ReadonlyMap<string, DocumentSummary>): boolean {
  return !!document.duplicate_of && byId.get(document.duplicate_of)?.content_sha256 === document.content_sha256;
}

/** The copy to keep of identical ones: one already kept, else an approved one, else the oldest. */
export function keptCopy(copies: readonly DocumentSummary[]): DocumentSummary {
  const ids = new Set(copies.map((copy) => copy.document_id));
  return (
    copies.find((copy) => copies.some((other) => other.duplicate_of === copy.document_id) && !(copy.duplicate_of && ids.has(copy.duplicate_of))) ??
    copies.find((copy) => approvalOf(copy) === "approved") ??
    [...copies].sort((a, b) => a.created_at.localeCompare(b.created_at))[0]!
  );
}

/**
 * The profile changes that keep one copy of identical documents and hide every other, in an order the API accepts:
 * the kept copy's own mark cleared first, then each other copy pointed at it, a copy others pointed at last, once
 * they no longer do. A copy already hidden behind the kept one is left as it is.
 */
export function keepOnly(copies: readonly DocumentSummary[], kept: DocumentSummary): { document_id: string; duplicate_of: string | null }[] {
  const restore = kept.duplicate_of ? [{ document_id: kept.document_id, duplicate_of: null }] : [];
  const others = copies.filter((copy) => copy.document_id !== kept.document_id && copy.duplicate_of !== kept.document_id);
  const pointedAt = (copy: DocumentSummary) => copies.some((other) => other.duplicate_of === copy.document_id);
  const ordered = [...others.filter((copy) => !pointedAt(copy)), ...others.filter(pointedAt)];
  return [...restore, ...ordered.map((copy) => ({ document_id: copy.document_id, duplicate_of: kept.document_id }))];
}

/** For each set of identical documents, every copy not yet hidden, marked a duplicate of the one kept. */
export function deduplication(documents: readonly DocumentSummary[]): { document_id: string; duplicate_of: string | null }[] {
  return clusters(documents).flatMap((copies) => (copies.length < 2 ? [] : keepOnly(copies, keptCopy(copies))));
}

/** One document per identical set, for selecting: copies of one text would compile it twice. */
export function oneOfEach(documents: readonly DocumentSummary[]): DocumentSummary[] {
  return clusters(documents).map((copies) => (copies.length === 1 ? copies[0]! : keptCopy(copies)));
}

/** Matches title, filename and description, each word anywhere, ignoring case and accents. */
export function matches(document: DocumentSummary, search: string): boolean {
  const words = fold(search).split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const haystack = fold([displayTitle(document), document.filename, document.description ?? ""].join(" "));
  return words.every((word) => haystack.includes(word));
}

function fold(text: string): string {
  return text.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();
}

export function filterAndSort(documents: readonly DocumentSummary[], query: LibraryQuery): DocumentSummary[] {
  const found = documents.filter(
    (document) =>
      matches(document, query.search) &&
      (!query.category || categoryKey(document) === query.category) &&
      (!query.scope || scopeValues(document).includes(query.scope)) &&
      (!query.approval || approvalOf(document) === query.approval),
  );
  const byTitle = (a: DocumentSummary, b: DocumentSummary) => displayTitle(a).localeCompare(displayTitle(b)) || a.filename.localeCompare(b.filename);
  const order: Record<SortKey, (a: DocumentSummary, b: DocumentSummary) => number> = {
    title: byTitle,
    updated: (a, b) => b.created_at.localeCompare(a.created_at) || byTitle(a, b),
    revision: (a, b) => b.revision - a.revision || byTitle(a, b),
  };
  return [...found].sort(order[query.sort]);
}

/** Documents by category, in the listed order, unclassified last; empty groups left out. */
export function groupByCategory(documents: readonly DocumentSummary[]): { key: CategoryKey; label: string; documents: DocumentSummary[] }[] {
  const keys: CategoryKey[] = [...CATEGORIES.map((item) => item.id), UNCLASSIFIED];
  return keys
    .map((key) => ({ key, label: categoryLabel(key), documents: documents.filter((document) => categoryKey(document) === key) }))
    .filter((group) => group.documents.length > 0);
}

export function allScopes(documents: readonly DocumentSummary[]): string[] {
  return [...new Set(documents.flatMap(scopeValues))].sort((a, b) => a.localeCompare(b));
}

/**
 * Documents whose latest revisions hold exactly the same text, by content hash: only these are "identical". Similar
 * names say nothing about the text.
 */
export function identicalContent(documents: readonly DocumentSummary[]): Map<string, DocumentSummary[]> {
  const byHash = new Map<string, DocumentSummary[]>();
  for (const document of documents) byHash.set(document.content_sha256, [...(byHash.get(document.content_sha256) ?? []), document]);
  const twins = new Map<string, DocumentSummary[]>();
  for (const group of byHash.values()) if (group.length > 1) for (const document of group) twins.set(document.document_id, group.filter((other) => other.document_id !== document.document_id));
  return twins;
}

/** Documents another document could be mistaken for: the same displayed title, or the same filename. */
export function similarlyNamed(documents: readonly DocumentSummary[]): Map<string, DocumentSummary[]> {
  const key = (document: DocumentSummary) => fold(displayTitle(document)).replace(/\s+/g, " ");
  const similar = new Map<string, DocumentSummary[]>();
  for (const document of documents) {
    // Exclude identical content; only differing documents can be confused.
    const others = documents.filter((other) => other.document_id !== document.document_id && other.content_sha256 !== document.content_sha256 && (key(other) === key(document) || other.filename === document.filename));
    if (others.length) similar.set(document.document_id, others);
  }
  return similar;
}

// --- Line diff ---

export type DiffLine = { kind: "same" | "added" | "removed"; text: string; left: number | null; right: number | null };

/**
 * Line-by-line differences between two texts, by longest common subsequence. Texts beyond MAX_DIFF_LINES each are
 * compared by their common prefix and suffix only, so a very large pair stays responsive; the result says so.
 */
export const MAX_DIFF_LINES = 4000;

export function lineDiff(left: string, right: string): { lines: DiffLine[]; approximate: boolean } {
  const a = left.split("\n");
  const b = right.split("\n");
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start++;
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
    endA--;
    endB--;
  }
  const head: DiffLine[] = a.slice(0, start).map((text, index) => ({ kind: "same", text, left: index + 1, right: index + 1 }));
  const tail: DiffLine[] = a.slice(endA).map((text, index) => ({ kind: "same", text, left: endA + index + 1, right: endB + index + 1 }));
  const midA = a.slice(start, endA);
  const midB = b.slice(start, endB);
  const approximate = midA.length > MAX_DIFF_LINES || midB.length > MAX_DIFF_LINES;
  const middle = approximate
    ? [
        ...midA.map((text, index): DiffLine => ({ kind: "removed", text, left: start + index + 1, right: null })),
        ...midB.map((text, index): DiffLine => ({ kind: "added", text, left: null, right: start + index + 1 })),
      ]
    : lcsDiff(midA, midB, start);
  return { lines: [...head, ...middle, ...tail], approximate };
}

function lcsDiff(a: string[], b: string[], offset: number): DiffLine[] {
  const table: number[][] = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i--) for (let j = b.length - 1; j >= 0; j--) table[i]![j] = a[i] === b[j] ? table[i + 1]![j + 1]! + 1 : Math.max(table[i + 1]![j]!, table[i]![j + 1]!);
  const lines: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      lines.push({ kind: "same", text: a[i]!, left: offset + i + 1, right: offset + j + 1 });
      i++;
      j++;
    } else if (table[i + 1]![j]! >= table[i]![j + 1]!) {
      lines.push({ kind: "removed", text: a[i]!, left: offset + i + 1, right: null });
      i++;
    } else {
      lines.push({ kind: "added", text: b[j]!, left: null, right: offset + j + 1 });
      j++;
    }
  }
  for (; i < a.length; i++) lines.push({ kind: "removed", text: a[i]!, left: offset + i + 1, right: null });
  for (; j < b.length; j++) lines.push({ kind: "added", text: b[j]!, left: null, right: offset + j + 1 });
  return lines;
}

/**
 * Where a quoted clause sits in a document: the lines it covers, found by exact text, then ignoring differences in
 * whitespace only. null when it is not in the text: no position is ever guessed.
 */
export function locateQuote(content: string, quote: string | null | undefined): { from: number; to: number } | null {
  if (!quote?.trim()) return null;
  let index = content.indexOf(quote);
  let length = quote.length;
  if (index < 0) {
    const words = quote.trim().split(/\s+/).map((word) => word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    const match = new RegExp(words.join("\\s+")).exec(content);
    if (!match) return null;
    index = match.index;
    length = match[0].length;
  }
  const from = content.slice(0, index).split("\n").length;
  const to = from + content.slice(index, index + length).split("\n").length - 1;
  return { from, to };
}

// --- Where a document stands in compilation ---

export interface DocumentUsage {
  label: "In a published ruleset" | "Revised since its ruleset was published" | "In a ruleset version" | "Compiled" | "Not compiled";
  tone: "pass" | "review" | "info" | "neutral";
  detail: string;
}

/**
 * Where each document's latest revision stands, from the compilation history given (the newest extractions): in a
 * published version, published at an earlier revision, in a version being prepared, compiled only, or never compiled.
 * Versions compiled from text sent inline name no stored document, so they are not counted.
 */
export function documentUsage(documents: readonly DocumentSummary[], extractions: readonly ExtractionListItem[]): Map<string, DocumentUsage> {
  const usage = new Map<string, DocumentUsage>();
  for (const document of documents) {
    const using = extractions.filter((item) => item.documents.some((read) => read.document_id === document.document_id));
    const revisionIn = (item: (typeof using)[number]) => item.documents.find((read) => read.document_id === document.document_id)!.revision;
    const published = using.filter((item) => item.versions?.some((version) => version.status === "published"));
    if (published.some((item) => revisionIn(item) === document.revision)) {
      usage.set(document.document_id, { label: "In a published ruleset", tone: "pass", detail: `Revision ${document.revision} is in a published ruleset. That is not document approval.` });
    } else if (published.length) {
      const revisions = [...new Set(published.map(revisionIn))].sort((a, b) => a - b);
      usage.set(document.document_id, { label: "Revised since its ruleset was published", tone: "review", detail: `Published at revision ${revisions.join(", ")}; the latest is ${document.revision}.` });
    } else if (using.some((item) => item.versions?.length)) {
      usage.set(document.document_id, { label: "In a ruleset version", tone: "info", detail: "In a ruleset version not yet published." });
    } else if (using.length) {
      usage.set(document.document_id, { label: "Compiled", tone: "neutral", detail: "Compiled, with no ruleset version made yet." });
    } else {
      usage.set(document.document_id, { label: "Not compiled", tone: "neutral", detail: "Not in any recent compilation." });
    }
  }
  return usage;
}
