import { toJsonText } from "@/lib/losslessJson";
import type { ApiErrorBody } from "./types";

/**
 * The API's base URL as the browser sees it. The default, /api, is same-origin: the Vite dev server and
 * the container's nginx proxy it to the API, which has no CORS middleware.
 */
export const API_BASE_URL: string = (import.meta.env.VITE_API_BASE_URL || "/api").replace(/\/+$/, "");

/** An error the API reported in its one error shape, or a failure to reach it at all. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown;

  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }

  /** The request never got an HTTP answer: the API is down, or the proxy cannot reach it. */
  get isNetwork(): boolean {
    return this.status === 0;
  }

  /** Worth retrying: no answer, a gateway error, or the database being briefly unavailable. */
  get isTransient(): boolean {
    return this.status === 0 || this.status === 502 || this.status === 503 || this.status === 504;
  }
}

type QueryValue = string | number | boolean | null | undefined | readonly (string | number | boolean)[];

export function buildUrl(path: string, query?: Record<string, QueryValue>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value == null) continue;
    // A repeated filter means any of its values, as FastAPI reads list query parameters.
    for (const item of Array.isArray(value) ? value : [value]) params.append(key, String(item));
  }
  const search = params.toString();
  return `${API_BASE_URL}${path}${search ? `?${search}` : ""}`;
}

function isErrorBody(body: unknown): body is ApiErrorBody {
  if (typeof body !== "object" || body === null || !("error" in body)) return false;
  const error = (body as { error: unknown }).error;
  return typeof error === "object" && error !== null && "code" in error && "message" in error;
}

export interface RequestOptions {
  query?: Record<string, QueryValue>;
  signal?: AbortSignal;
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  /** A JSON body. A JsonNumber in it is written exactly as it was read (toJsonText). */
  body?: unknown;
  /** A body already serialized, sent byte for byte: used where JSON.stringify would round exact decimals. */
  rawBody?: string;
  /** A multipart body, such as a dataset upload. The browser sets its Content-Type with the boundary. */
  formData?: FormData;
  /** Statuses whose JSON body is the answer, such as /health's 503 with a degraded body. */
  acceptStatuses?: readonly number[];
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { query, signal, method = "GET", body, rawBody, formData, acceptStatuses = [] } = options;
  const json = rawBody ?? (body === undefined ? undefined : toJsonText(body));
  let response: Response;
  try {
    response = await fetch(buildUrl(path, query), {
      method,
      signal,
      headers: { Accept: "application/json", ...(json === undefined ? {} : { "Content-Type": "application/json" }) },
      body: formData ?? json,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "network_error", `The API at ${API_BASE_URL || "this origin"} could not be reached.`);
  }

  const text = await response.text();
  let parsed: unknown = undefined;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = undefined;
    }
  }

  if (response.ok || acceptStatuses.includes(response.status)) {
    if (parsed === undefined) throw new ApiError(response.status, "invalid_response", "The API returned a response that is not JSON.");
    return parsed as T;
  }
  if (isErrorBody(parsed)) {
    throw new ApiError(response.status, parsed.error.code, parsed.error.message, parsed.error.details);
  }
  if (response.status === 422 && typeof parsed === "object" && parsed !== null && "detail" in parsed) {
    throw new ApiError(422, "invalid_request", "The API refused the request parameters.", (parsed as { detail: unknown }).detail);
  }
  throw new ApiError(response.status, "http_error", `The API answered ${response.status} ${response.statusText}`.trim());
}

/**
 * Downloads a file the API serves as an attachment. It is fetched first, so an API error is reported as an
 * ApiError rather than saved as a file; the bytes are then saved exactly as received, under the API's filename.
 */
export async function downloadFile(url: string, fallbackName: string): Promise<void> {
  let response: Response;
  try {
    response = await fetch(url, { headers: { Accept: "application/json, text/csv, application/pdf" } });
  } catch {
    throw new ApiError(0, "network_error", `The API at ${API_BASE_URL || "this origin"} could not be reached.`);
  }
  if (!response.ok) {
    const text = await response.text();
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = undefined;
    }
    if (isErrorBody(parsed)) throw new ApiError(response.status, parsed.error.code, parsed.error.message, parsed.error.details);
    throw new ApiError(response.status, "http_error", `The API answered ${response.status}`);
  }
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const name = /filename="([^"]+)"/.exec(disposition)?.[1] ?? fallbackName;
  const blob = await response.blob();
  const href = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.download = name;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(href), 1000);
}

/** A response body as text, exactly as sent: for JSON shown to people, where parsing would round numbers. */
export async function requestText(url: string, { signal }: { signal?: AbortSignal } = {}): Promise<string> {
  let response: Response;
  try {
    response = await fetch(url, { signal, headers: { Accept: "application/json" } });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "network_error", `The API at ${API_BASE_URL || "this origin"} could not be reached.`);
  }
  const text = await response.text();
  if (response.ok) return text;
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    parsed = undefined;
  }
  if (isErrorBody(parsed)) throw new ApiError(response.status, parsed.error.code, parsed.error.message, parsed.error.details);
  throw new ApiError(response.status, "http_error", `The API answered ${response.status}`);
}

/** FastAPI's 422 detail entries: each names the field path (loc) it refuses. */
export function fieldErrors(details: unknown): { path: string; message: string }[] {
  if (!Array.isArray(details)) return [];
  return details.flatMap((item) => {
    if (typeof item !== "object" || item === null) return [];
    const { loc, msg } = item as { loc?: unknown; msg?: unknown };
    if (!Array.isArray(loc) || typeof msg !== "string") return [];
    return [{ path: loc.filter((part) => part !== "body").join("."), message: msg }];
  });
}
