import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";

/**
 * Every console.error a test causes fails it: a rendering error that an error boundary or React Router catches still
 * logs one, and would otherwise pass unnoticed. A test that means to cause one names it with allowConsoleErrors.
 */
let logged: string[] = [];
let allowed: RegExp[] = [];

export function allowConsoleErrors(...patterns: RegExp[]) {
  allowed.push(...patterns);
}

beforeEach(() => {
  logged = [];
  allowed = [];
  vi.spyOn(console, "error").mockImplementation((...args: unknown[]) => {
    const text = args.map((arg) => (arg instanceof Error ? `${arg.name}: ${arg.message}` : String(arg))).join(" ");
    if (!allowed.some((pattern) => pattern.test(text))) logged.push(text);
  });
});

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.mocked(console.error).mockRestore();
  if (logged.length) throw new Error(`Unexpected console.error during the test:\n${logged.map((text) => `- ${text.slice(0, 400)}`).join("\n")}`);
});

// Recharts' ResponsiveContainer measures its parent with ResizeObserver, which jsdom lacks.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver;
