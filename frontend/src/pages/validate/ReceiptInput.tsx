import { AlertTriangle, Braces, FileJson, FileUp, FlaskConical, RefreshCw, Trash2, Upload, WandSparkles } from "lucide-react";
import { useRef, useState, type ChangeEvent, type DragEvent } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { formatInteger } from "@/lib/format";
import { cn } from "@/lib/utils";
import { DEMO_RECEIPTS_JSON } from "./demo";
import type { InputSource, UploadedFile } from "./draft";
import { formatJsonText, removedCount, type InputIssue, type ParsedInput } from "./receipts";

/** The largest file read locally. The API's own limit on receipts per request still applies. */
const MAX_FILE_BYTES = 5 * 1024 * 1024;

/** A file's text as UTF-8, read locally. FileReader works wherever File.text() may not. */
function readText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(file, "utf-8");
  });
}

function IssueList({ issues, title }: { issues: InputIssue[]; title: string }) {
  if (!issues.length) return null;
  return (
    <div role="alert" className="mt-3 rounded-md border border-status-flag-border bg-status-flag-soft px-3 py-2.5">
      <p className="flex items-center gap-1.5 text-[13px] font-medium text-status-flag-text">
        <AlertTriangle className="size-3.5" aria-hidden />
        {title}
      </p>
      <ul className="mt-1.5 space-y-1 text-xs leading-5 text-foreground/85">
        {issues.map((issue, index) => (
          <li key={index} className="break-words">
            {issue.location ? <span className="font-mono text-[11px] text-muted-foreground">{issue.location}: </span> : null}
            {issue.message}
          </li>
        ))}
      </ul>
      {issues.length >= 20 ? <p className="mt-1 text-xs text-muted-foreground">Showing the first 20 problems.</p> : null}
    </div>
  );
}

function RemovedNotice({ parsed }: { parsed: ParsedInput }) {
  const total = removedCount(parsed.removed);
  if (!total) return null;
  const parts = [
    parsed.removed.receiptLabels && `labels on ${parsed.removed.receiptLabels} receipt${parsed.removed.receiptLabels === 1 ? "" : "s"}`,
    parsed.removed.notes && `notes on ${parsed.removed.notes} receipt${parsed.removed.notes === 1 ? "" : "s"}`,
    parsed.removed.lineLabels && `labels on ${parsed.removed.lineLabels} line${parsed.removed.lineLabels === 1 ? "" : "s"}`,
  ].filter(Boolean);
  return (
    <p className="mt-3 rounded-md border border-status-info-border bg-status-info-soft px-3 py-2 text-xs leading-5 text-foreground/85" role="status">
      <span className="font-medium text-primary">Ground-truth answers will not be sent.</span> Found {parts.join(", ")}. These evaluation answers are left out of
      the request; your input itself is unchanged.
    </p>
  );
}

export function ReceiptInput({
  source,
  onSourceChange,
  text,
  onTextChange,
  file,
  onFileChange,
  parsed,
}: {
  source: InputSource;
  onSourceChange: (source: InputSource) => void;
  text: string;
  onTextChange: (text: string) => void;
  file: UploadedFile | null;
  onFileChange: (file: UploadedFile | null) => void;
  parsed: ParsedInput | null;
}) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [formatError, setFormatError] = useState<string | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);

  function format() {
    try {
      onTextChange(formatJsonText(text));
      setFormatError(null);
    } catch (error) {
      setFormatError(error instanceof Error ? error.message : String(error));
    }
  }

  async function readFile(chosen: File | undefined) {
    if (!chosen) return;
    const extension = chosen.name.toLowerCase().split(".").pop();
    if (extension !== "json" && extension !== "jsonl") {
      setFileError(`${chosen.name} is not a .json or .jsonl file. Images, PDFs and OCR are not supported.`);
      return;
    }
    if (chosen.size > MAX_FILE_BYTES) {
      setFileError(`${chosen.name} is ${formatInteger(chosen.size)} bytes; files up to ${formatInteger(MAX_FILE_BYTES)} bytes are read here.`);
      return;
    }
    try {
      const content = await readText(chosen);
      setFileError(null);
      onFileChange({ name: chosen.name, size: chosen.size, format: extension, text: content });
    } catch {
      setFileError(`${chosen.name} could not be read.`);
    }
  }

  function onPick(event: ChangeEvent<HTMLInputElement>) {
    void readFile(event.target.files?.[0]);
    event.target.value = ""; // choosing the same file again still triggers a change
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    void readFile(event.dataTransfer.files[0]);
  }

  const lineCount = text ? text.split("\n").length : 0;

  return (
    <div>
      <p className="mb-3 text-xs leading-4 text-muted-foreground">
        Structured receipts in the core's raw format (<span className="font-mono">data/README.md</span>): one receipt, a list, or JSONL. Evaluation answers (
        <span className="font-mono">labels</span>, <span className="font-mono">notes</span>) are never sent.
      </p>
        <Tabs value={source} onValueChange={(value) => onSourceChange(value as InputSource)}>
          <TabsList aria-label="Input method">
            <TabsTrigger value="editor">
              <Braces aria-hidden />
              JSON editor
            </TabsTrigger>
            <TabsTrigger value="file">
              <FileUp aria-hidden />
              Upload file
            </TabsTrigger>
          </TabsList>

          <TabsContent value="editor">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap gap-2">
                <Button type="button" variant="outline" size="sm" onClick={format} disabled={!text.trim()}>
                  <WandSparkles aria-hidden />
                  Format JSON
                </Button>
                <Button type="button" variant="ghost" size="sm" onClick={() => onTextChange("")} disabled={!text}>
                  <Trash2 aria-hidden />
                  Clear
                </Button>
              </div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => {
                  onTextChange(DEMO_RECEIPTS_JSON);
                  setFormatError(null);
                }}
                title="RCP-013 and RCP-014 from data/receipts.jsonl, answers removed; covered by the openrouter replay set"
              >
                <FlaskConical aria-hidden />
                Load demo receipts
              </Button>
            </div>
            <label htmlFor="receipt-json" className="sr-only">
              Receipt JSON
            </label>
            <textarea
              id="receipt-json"
              value={text}
              onChange={(event) => {
                onTextChange(event.target.value);
                setFormatError(null);
              }}
              spellCheck={false}
              autoCapitalize="off"
              autoCorrect="off"
              placeholder={'[\n  {\n    "receipt_id": "RCP-001",\n    "line_items": [{ "line_id": "RCP-001-L1", ... }],\n    ...\n  }\n]'}
              aria-describedby="receipt-json-hint"
              aria-invalid={source === "editor" && Boolean(parsed?.issues.length) ? true : undefined}
              className="block h-64 w-full resize-y rounded-md border border-input bg-background/60 px-3 py-2.5 font-mono text-[12.5px] leading-5 text-foreground shadow-inner placeholder:text-subtle-foreground focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring aria-invalid:border-status-flag-border"
            />
            <p id="receipt-json-hint" className="mt-1.5 flex justify-between text-xs text-subtle-foreground">
              <span>Numbers are sent exactly as written. Format only re-indents.</span>
              <span className="tabular">{formatInteger(lineCount)} lines</span>
            </p>
            {formatError ? <IssueList issues={[{ location: "", message: `Cannot format: ${formatError}` }]} title="Not formatted" /> : null}
            {source === "editor" && text.trim() && parsed ? (
              <>
                <IssueList issues={parsed.issues} title="Fix the input before validating" />
                <RemovedNotice parsed={parsed} />
              </>
            ) : null}
          </TabsContent>

          <TabsContent value="file">
            <input ref={fileInput} type="file" accept=".json,.jsonl,application/json" className="sr-only" onChange={onPick} aria-label="Choose a JSON or JSONL file" tabIndex={-1} />
            {file ? (
              <div className="rounded-md border border-border px-4 py-3">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex min-w-0 items-center gap-3">
                    <div className="flex size-9 shrink-0 items-center justify-center rounded-md bg-primary-soft text-primary">
                      <FileJson className="size-4.5" aria-hidden />
                    </div>
                    <div className="min-w-0">
                      <p className="truncate font-medium text-foreground">{file.name}</p>
                      <p className="text-xs text-subtle-foreground">
                        {file.format.toUpperCase()} · {formatInteger(file.size)} bytes ·{" "}
                        {parsed ? `${formatInteger(parsed.receipts.length)} receipt${parsed.receipts.length === 1 ? "" : "s"} read` : "reading"}
                        {parsed?.issues.length ? (
                          <span className="text-status-flag-text">
                            {" "}
                            · {parsed.issues.length} problem{parsed.issues.length === 1 ? "" : "s"}
                          </span>
                        ) : null}
                      </p>
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <Button type="button" variant="outline" size="sm" onClick={() => fileInput.current?.click()}>
                      <RefreshCw aria-hidden />
                      Replace
                    </Button>
                    <Button type="button" variant="ghost" size="sm" onClick={() => onFileChange(null)}>
                      <Trash2 aria-hidden />
                      Remove
                    </Button>
                  </div>
                </div>
                {parsed ? (
                  <>
                    <IssueList issues={parsed.issues} title={`${file.name} cannot be validated as it is`} />
                    <RemovedNotice parsed={parsed} />
                  </>
                ) : null}
              </div>
            ) : (
              <div
                onDragOver={(event) => {
                  event.preventDefault();
                  setDragging(true);
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={onDrop}
                className={cn(
                  "flex flex-col items-center justify-center rounded-md border border-dashed px-6 py-12 text-center",
                  dragging ? "border-primary bg-primary-soft" : "border-input bg-background/60",
                )}
              >
                <div className="mb-3 flex size-10 items-center justify-center rounded-full bg-muted text-subtle-foreground">
                  <Upload className="size-5" aria-hidden />
                </div>
                <p className="text-sm font-medium text-foreground">Drop a .json or .jsonl file here</p>
                <p className="mt-1 max-w-sm text-[13px] leading-5 text-muted-foreground">
                  Read in your browser and checked before anything is sent. A JSON file holds one receipt, a list, or{" "}
                  <span className="font-mono">{'{"receipts": [...]}'}</span>; a JSONL file holds one receipt per line.
                </p>
                <Button type="button" variant="outline" size="sm" className="mt-4" onClick={() => fileInput.current?.click()}>
                  Choose file
                </Button>
              </div>
            )}
            {fileError ? <IssueList issues={[{ location: "", message: fileError }]} title="File not read" /> : null}
            <p className="mt-2 flex items-center gap-1.5 text-xs text-subtle-foreground">
              <Badge variant="outline">No OCR</Badge> Images and PDFs are not read: upload structured receipts only.
            </p>
          </TabsContent>
        </Tabs>
    </div>
  );
}
