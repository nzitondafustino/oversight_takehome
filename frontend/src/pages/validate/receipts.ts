/**
 * Receipt input: parse JSON or JSONL, check the structure the API needs, remove ground-truth answers, and
 * describe each receipt for the preview. Values are never changed: numbers keep their exact text (losslessJson),
 * and everything beyond the shape checks below is left to the API, which validates receipts with the core.
 */
import { isJsonObject, JsonSyntaxError, parseJson, scalarText, stringifyJson, type JsonObject, type JsonValue } from "@/lib/losslessJson";

/** The core's answer fields (ingestion/loader.py ANSWER_FIELDS, LINE_ANSWER_FIELDS): never sent to the validator. */
export const RECEIPT_ANSWER_FIELDS = ["labels", "notes"] as const;
export const LINE_ANSWER_FIELDS = ["labels"] as const;

export type InputFormat = "json" | "jsonl";

export interface InputIssue {
  /** Where: "line 3" (JSONL), "receipts[1].line_items[0]", or "" for the whole input. */
  location: string;
  message: string;
}

export interface RemovedAnswers {
  receiptLabels: number;
  notes: number;
  lineLabels: number;
}

export interface ParsedInput {
  receipts: JsonObject[];
  issues: InputIssue[];
  removed: RemovedAnswers;
}

export interface ReceiptPreview {
  receiptId: string;
  merchant: string | null;
  datetime: string | null;
  currency: string | null;
  total: string | null;
  lineCount: number;
}

export interface InputLine {
  description: string | null;
  quantity: string | null;
  unitPrice: string | null;
  amount: string | null;
  language: string | null;
}

const MAX_ISSUES = 20;

function syntaxIssue(error: unknown, location: string): InputIssue {
  const message = error instanceof JsonSyntaxError || error instanceof Error ? error.message : String(error);
  return { location, message };
}

/** Parse the editor's text or a file's contents into receipt objects, without validating them. */
function parseValues(text: string, format: InputFormat): { values: { value: JsonValue; location: string }[]; issues: InputIssue[] } {
  const issues: InputIssue[] = [];
  if (!text.trim()) return { values: [], issues: [{ location: "", message: "The input is empty." }] };

  if (format === "jsonl") {
    const values: { value: JsonValue; location: string }[] = [];
    text.split(/\r?\n/).forEach((line, index) => {
      if (!line.trim()) return;
      try {
        values.push({ value: parseJson(line), location: `line ${index + 1}` });
      } catch (error) {
        if (issues.length < MAX_ISSUES) issues.push(syntaxIssue(error, `line ${index + 1}`));
      }
    });
    return { values, issues };
  }

  let parsed: JsonValue;
  try {
    parsed = parseJson(text);
  } catch (error) {
    return { values: [], issues: [syntaxIssue(error, "")] };
  }
  // A list of receipts, one receipt, or a dataset-style object {"receipts": [...]}.
  if (Array.isArray(parsed)) return { values: parsed.map((value, index) => ({ value, location: `receipts[${index}]` })), issues };
  if (isJsonObject(parsed) && !("receipt_id" in parsed) && Array.isArray(parsed.receipts)) {
    return { values: parsed.receipts.map((value, index) => ({ value, location: `receipts[${index}]` })), issues };
  }
  return { values: [{ value: parsed, location: "receipt" }], issues };
}

function withoutKeys(object: JsonObject, keys: readonly string[]): JsonObject {
  const out: JsonObject = Object.create(null);
  for (const key of Object.keys(object)) if (!keys.includes(key)) out[key] = object[key]!;
  return out;
}

/**
 * Parses and checks the input. Only these are checked here, so the problem can be shown before sending:
 * each receipt is an object with a receipt_id and a line_items list, each line an object with a line_id,
 * and no receipt or line ID repeats (the core keys predictions by line ID). Ground-truth answers are removed
 * and counted; nothing else is changed.
 */
export function parseReceiptInput(text: string, format: InputFormat): ParsedInput {
  const { values, issues } = parseValues(text, format);
  const removed: RemovedAnswers = { receiptLabels: 0, notes: 0, lineLabels: 0 };
  const receipts: JsonObject[] = [];
  const receiptIds = new Set<string>();
  const lineIds = new Set<string>();
  const add = (location: string, message: string) => {
    if (issues.length < MAX_ISSUES) issues.push({ location, message });
  };

  for (const { value, location } of values) {
    if (!isJsonObject(value)) {
      add(location, "A receipt must be a JSON object.");
      continue;
    }
    const receiptId = value.receipt_id;
    if (typeof receiptId !== "string" || !receiptId.trim()) {
      add(location, "receipt_id is required and must be text.");
      continue;
    }
    if (receiptIds.has(receiptId)) add(location, `receipt_id ${receiptId} appears more than once.`);
    receiptIds.add(receiptId);

    if ("labels" in value) removed.receiptLabels += 1;
    if ("notes" in value) removed.notes += 1;
    const receipt = withoutKeys(value, RECEIPT_ANSWER_FIELDS);

    if (!Array.isArray(value.line_items)) {
      add(`${location} (${receiptId})`, "line_items is required and must be a list.");
      continue;
    }
    const lines: JsonValue[] = [];
    value.line_items.forEach((line, index) => {
      const where = `${location} (${receiptId}).line_items[${index}]`;
      if (!isJsonObject(line)) return add(where, "A line item must be a JSON object.");
      if (typeof line.line_id !== "string" || !line.line_id.trim()) return add(where, "line_id is required and must be text.");
      if (lineIds.has(line.line_id)) add(where, `line_id ${line.line_id} appears more than once.`);
      lineIds.add(line.line_id);
      if ("labels" in line) removed.lineLabels += 1;
      lines.push(withoutKeys(line, LINE_ANSWER_FIELDS));
    });
    receipt.line_items = lines;
    receipts.push(receipt);
  }

  if (!issues.length && receipts.length === 0) issues.push({ location: "", message: "No receipts were found in the input." });
  return { receipts, issues, removed };
}

export function removedCount(removed: RemovedAnswers): number {
  return removed.receiptLabels + removed.notes + removed.lineLabels;
}

export function previewOf(receipt: JsonObject): ReceiptPreview {
  const lines = Array.isArray(receipt.line_items) ? receipt.line_items : [];
  return {
    receiptId: scalarText(receipt.receipt_id) ?? "",
    merchant: scalarText(receipt.merchant_name),
    // Local time at the merchant with no time zone: shown as written, never converted.
    datetime: scalarText(receipt.transaction_datetime),
    currency: scalarText(receipt.currency),
    total: scalarText(receipt.total_amount),
    lineCount: lines.length,
  };
}

/** Each input line by line_id, for showing amounts beside the results (the result itself holds none). */
export function inputLines(receipt: JsonObject): Map<string, InputLine> {
  const map = new Map<string, InputLine>();
  for (const line of Array.isArray(receipt.line_items) ? receipt.line_items : []) {
    if (!isJsonObject(line) || typeof line.line_id !== "string") continue;
    map.set(line.line_id, {
      description: scalarText(line.description_raw),
      quantity: scalarText(line.quantity),
      unitPrice: scalarText(line.unit_price),
      amount: scalarText(line.amount),
      language: scalarText(line.language),
    });
  }
  return map;
}

/** Re-indent the editor's JSON. Values, including number text, are unchanged. */
export function formatJsonText(text: string): string {
  return stringifyJson(parseJson(text), 2);
}

export { stringifyJson };
