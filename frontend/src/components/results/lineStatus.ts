/**
 * Which findings concern which line, read only from the engine's result. A line's status comes from the findings
 * that name it, never from its category: a restricted category with no finding is not a violation, and a line
 * with no finding is not shown as compliant, only as having no item findings.
 */
import type { ExceptionResult, OpenQuestionResult, ReceiptResult, UncertaintyResult } from "@/api/types";
import { exceptionTone } from "./PolicyFindings";

export interface CitedException {
  /** The finding's key, as PolicyFindings anchors it: exception-<index in result.exceptions>. */
  key: string;
  exception: ExceptionResult;
  tone: "flag" | "review" | "neutral";
}

export interface CitedQuestion {
  key: string;
  question: OpenQuestionResult;
}

export interface LineFindings {
  exceptions: CitedException[];
  questions: CitedQuestion[];
  uncertainty: UncertaintyResult[];
  classificationError: string | null;
}

export type LineStatusKind = "flag" | "review" | "error" | "uncertain" | "open" | "none";

export interface LineStatus {
  kind: LineStatusKind;
  label: string;
}

export function exceptionsOf(result: ReceiptResult): CitedException[] {
  return result.exceptions.map((exception, index) => ({ key: `exception-${index}`, exception, tone: exceptionTone(result, exception.rule_id, exception.severity) }));
}

export function questionsOf(result: ReceiptResult): CitedQuestion[] {
  return result.open_questions.map((question, index) => ({ key: `question-${index}`, question }));
}

export function lineFindings(result: ReceiptResult, lineId: string): LineFindings {
  const line = result.line_items.find((item) => item.line_id === lineId);
  return {
    exceptions: exceptionsOf(result).filter((item) => item.exception.line_ids.includes(lineId)),
    questions: questionsOf(result).filter((item) => item.question.line_ids.includes(lineId)),
    uncertainty: result.uncertainty.filter((item) => item.line_id === lineId),
    classificationError: line?.classification_error ?? null,
  };
}

/** The most serious thing the findings say about one line. */
export function lineStatus(findings: LineFindings): LineStatus {
  if (findings.exceptions.some((item) => item.tone === "flag")) return { kind: "flag", label: "Exception" };
  if (findings.exceptions.length) return { kind: "review", label: "Exception · review" };
  if (findings.classificationError) return { kind: "error", label: "Classification error" };
  if (findings.uncertainty.length) return { kind: "uncertain", label: "Uncertain" };
  if (findings.questions.length) return { kind: "open", label: "Open question" };
  return { kind: "none", label: "No item findings" };
}

/** Findings that concern the receipt as a whole: they are never assigned to a line. */
export function receiptLevel(result: ReceiptResult) {
  return {
    exceptions: exceptionsOf(result).filter((item) => item.exception.line_ids.length === 0),
    questions: questionsOf(result).filter((item) => item.question.line_ids.length === 0),
    // A review signal names no lines (schemas/runs.py: line_ids is null for a review signal).
    signals: result.review_signals,
  };
}
