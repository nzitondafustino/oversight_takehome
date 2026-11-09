import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { exportViewPdf } from "@/lib/exportViewPdf";

const state = vi.hoisted(() => ({ capture: vi.fn(), create: vi.fn(), image: vi.fn(), save: vi.fn(), properties: vi.fn() }));
vi.mock("html-to-image", () => ({ toCanvas: state.capture }));
vi.mock("jspdf", () => ({ jsPDF: class {
  constructor(options: unknown) { state.create(options); }
  addImage = state.image;
  save = state.save;
  setProperties = state.properties;
} }));

beforeEach(() => {
  Object.values(state).forEach(mock => mock.mockReset());
  state.capture.mockResolvedValue(document.createElement("canvas"));
});
afterEach(() => { document.body.innerHTML = ""; });

function view(width: number, height: number) {
  document.body.innerHTML = '<div class="workspace-canvas"><main id="main" data-pdf-content><details><summary>Collapsed details</summary>Hidden data</details><div data-pdf-exclude>Export PDF</div><svg><text>Category accuracy</text></svg></main></div>';
  const root = document.getElementById("main")!;
  vi.spyOn(root, "getBoundingClientRect").mockReturnValue({ width, height: 800, left: 240 } as DOMRect);
  Object.defineProperty(root, "scrollHeight", { value: height });
  return root;
}

it("captures the rendered charts and current disclosure state on one continuous page", async () => {
  const root = view(1200, 5000);
  await exportViewPdf("evaluation-123");
  const [node, options] = state.capture.mock.calls[0]!;
  expect(node).toBe(root);
  expect(options).toMatchObject({ width: 1200, height: 5000, skipFonts: true });
  expect(options.filter(root.querySelector("[data-pdf-exclude]"))).toBe(false);
  expect(options.filter(root.querySelector("svg"))).toBe(true);
  expect(root.querySelector("details")!.open).toBe(false);
  expect(state.create).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ format: [1200, 5000], unit: "px" }));
  expect(state.image).toHaveBeenCalledTimes(1);
  expect(state.save).toHaveBeenCalledWith("evaluation-123.pdf");
});

it("scales oversized views within image and PDF limits without clipping or pagination", async () => {
  view(1400, 40000);
  await exportViewPdf("large-view");
  const options = state.capture.mock.calls[0]![1];
  expect(options.height * options.pixelRatio).toBeLessThanOrEqual(16384);
  const page = state.create.mock.calls[0]![0];
  expect(Math.max(...page.format)).toBeLessThanOrEqual(19000);
  expect(page.format[0] / page.format[1]).toBeCloseTo(1400 / 40000);
  expect(state.image).toHaveBeenCalledTimes(1);
});

it("reports capture failures without saving an empty PDF", async () => {
  view(1200, 2000);
  state.capture.mockRejectedValue(new Error("Capture failed"));
  await expect(exportViewPdf("failed")).rejects.toThrow("Capture failed");
  expect(state.save).not.toHaveBeenCalled();
});
