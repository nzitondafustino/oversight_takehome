/**
 * The validation workspace's state, held above the router so that leaving the page and coming back keeps the
 * input, the configuration and the last result. It lives in memory only: receipts are never written to storage.
 */
import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import type { RunResponse } from "@/api/types";
import type { JsonObject } from "@/lib/losslessJson";
import { INITIAL_CONFIG, type ValidationConfig } from "./config";

export type InputSource = "editor" | "file";

export interface UploadedFile {
  name: string;
  size: number;
  format: "json" | "jsonl";
  text: string;
}

export interface LastValidation {
  run: RunResponse;
  /** The receipts exactly as sent, for merchant, totals and line amounts, which the result does not hold. */
  sent: JsonObject[];
}

interface Draft {
  source: InputSource;
  setSource: (source: InputSource) => void;
  text: string;
  setText: (text: string) => void;
  file: UploadedFile | null;
  setFile: (file: UploadedFile | null) => void;
  config: ValidationConfig;
  setConfig: (update: (config: ValidationConfig) => ValidationConfig) => void;
  last: LastValidation | null;
  setLast: (last: LastValidation | null) => void;
  /** Whether the intake (input and configuration) is expanded. It folds away once results are shown. */
  intakeOpen: boolean;
  setIntakeOpen: (open: boolean) => void;
}

const DraftContext = createContext<Draft | null>(null);

export function ValidationDraftProvider({ children }: { children: ReactNode }) {
  const [source, setSource] = useState<InputSource>("editor");
  const [text, setText] = useState("");
  const [file, setFile] = useState<UploadedFile | null>(null);
  const [config, setConfigState] = useState<ValidationConfig>(INITIAL_CONFIG);
  const [last, setLast] = useState<LastValidation | null>(null);
  const [intakeOpen, setIntakeOpen] = useState(true);
  const value = useMemo(
    () => ({ source, setSource, text, setText, file, setFile, config, setConfig: setConfigState, last, setLast, intakeOpen, setIntakeOpen }),
    [source, text, file, config, last, intakeOpen],
  );
  return <DraftContext.Provider value={value}>{children}</DraftContext.Provider>;
}

export function useValidationDraft(): Draft {
  const draft = useContext(DraftContext);
  if (!draft) throw new Error("useValidationDraft needs a ValidationDraftProvider");
  return draft;
}
