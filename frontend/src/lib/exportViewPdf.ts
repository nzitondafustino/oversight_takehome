/** Capture the current UI as one continuous PDF page; never fetch or rebuild report data. */
export async function exportViewPdf(name: string): Promise<void> {
  const root = document.querySelector<HTMLElement>("[data-pdf-content]") ?? document.getElementById("main");
  if (!root) throw new Error("The current view is not ready to export.");
  if (document.fonts) await document.fonts.ready;
  const [{ toCanvas }, { jsPDF }] = await Promise.all([import("html-to-image"), import("jspdf")]);
  const width = Math.ceil(root.getBoundingClientRect().width);
  const height = Math.ceil(root.scrollHeight);
  if (!width || !height) throw new Error("The current view has no visible content to export.");
  const workspace = root.closest<HTMLElement>(".workspace-canvas");
  const background = getComputedStyle(workspace ?? root);
  const offset = workspace ? root.getBoundingClientRect().left - workspace.getBoundingClientRect().left : 0;
  // Preserve browser canvas/PDF limits without clipping content or adding pages.
  const pixelRatio = Math.min(2, 16384 / Math.max(width, height), Math.sqrt(16_000_000 / (width * height)));
  const canvas = await toCanvas(root, {
    width, height, pixelRatio, skipFonts: true,
    filter: node => !(node instanceof HTMLElement && node.hasAttribute("data-pdf-exclude")),
    style: {
      background: background.background,
      backgroundSize: `${workspace?.scrollWidth ?? width}px ${workspace?.scrollHeight ?? height}px`,
      backgroundPosition: `${-offset}px 0`,
    },
  });
  const scale = Math.min(1, 19000 / Math.max(width, height));
  const pageWidth = width * scale;
  const pageHeight = height * scale;
  const pdf = new jsPDF({ orientation: pageWidth > pageHeight ? "landscape" : "portrait", unit: "px", format: [pageWidth, pageHeight], hotfixes: ["px_scaling"], compress: true });
  pdf.setProperties({ title: `${name} — Northwind Global`, author: "Northwind Global" });
  pdf.addImage(canvas, "PNG", 0, 0, pageWidth, pageHeight, undefined, "FAST");
  pdf.save(`${name}.pdf`);
}
