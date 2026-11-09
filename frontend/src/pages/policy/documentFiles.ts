/**
 * Reading a Markdown policy document from a chosen file, and what a document error means for the person who met it.
 * The API checks all of this again (schemas/documents.py, services/documents.py); these checks only stop a request it
 * would certainly refuse, and say why.
 */
import { ApiError } from "@/api/client";

/** schemas/documents.py Filename: rules cite a document by this bare .md name. */
export const FILENAME = /^[A-Za-z0-9][A-Za-z0-9._-]*\.md$/;

export type ReadFile = { ok: true; name: string; size: number; content: string } | { ok: false; name: string; error: string };

/**
 * A chosen file's text, only when it is a .md file of valid UTF-8. A browser's file.text() would replace invalid bytes
 * without a word, so a document in another encoding would be stored changed: it is refused instead.
 */
export async function readMarkdownFile(file: File): Promise<ReadFile> {
  if (!file.name.toLowerCase().endsWith(".md")) return { ok: false, name: file.name, error: "Only Markdown (.md) files can be uploaded." };
  const bytes = await bytesOf(file);
  try {
    const content = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    return { ok: true, name: file.name, size: bytes.byteLength, content };
  } catch {
    return { ok: false, name: file.name, error: "The file is not valid UTF-8 text. Save it as UTF-8 and choose it again." };
  }
}

/** The file's bytes, through FileReader, which every browser supports. */
function bytesOf(file: File): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () => reject(reader.error ?? new Error("the file could not be read"));
    reader.readAsArrayBuffer(file);
  });
}

const HINTS: Record<string, string> = {
  document_storage_unavailable: "Document storage could not be reached or is not configured. Nothing was stored; try again later.",
  document_too_large: "The document is larger than this deployment accepts.",
  invalid_document: "The text is not a policy document the compiler can read.",
  revision_conflict: "Another revision was stored meanwhile. Reload the latest revision and make the change again.",
  revision_unchanged: "The text is the same as the latest revision.",
  document_content_missing: "The stored text of this revision is missing from document storage.",
  integrity_error: "The stored text of this revision does not match its recorded hash, so it is not shown.",
};

/** A plain explanation of a document error, beside the API's own message and code, or null for any other error. */
export function documentErrorHint(error: unknown): string | null {
  return error instanceof ApiError ? HINTS[error.code] ?? null : null;
}
