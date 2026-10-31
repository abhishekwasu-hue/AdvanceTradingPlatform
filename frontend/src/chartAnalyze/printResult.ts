import { formatDateIST } from "../ui/format";
/**
 * Analyze a chart: "Download PDF" through the browser's own print dialog ("Save as PDF"), so no PDF library is added
 * to the bundle. The result block is copied into a hidden same-origin frame with the page's own stylesheets, in the
 * light theme (paper), without the buttons (`data-print-hide`), and that frame alone is printed: the app's sidebar,
 * bars and dialogs never reach the paper. The image is the user's own stored image (a same-origin blob: URL), so
 * nothing leaves the browser. The label and the disclaimer are inside the result block and print with it.
 */

export const PRINT_HIDE = "data-print-hide";

/** The printable copy of `node`: a deep clone without the elements marked `data-print-hide`. */
export function printableClone(node: HTMLElement, doc: Document = document): HTMLElement {
  const copy = doc.importNode(node, true) as HTMLElement;
  copy.querySelectorAll(`[${PRINT_HIDE}]`).forEach((el) => el.remove());
  return copy;
}

const PRINT_CSS = `
  @page { margin: 12mm; }
  html, body { background: rgb(var(--surface-1)); }
  body { margin: 0; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  section, li, tr, figure { break-inside: avoid; }
`;

function loaded(link: HTMLLinkElement): Promise<void> {
  return new Promise((resolve) => {
    if (link.sheet) { resolve(); return; }
    link.addEventListener("load", () => resolve(), { once: true });
    link.addEventListener("error", () => resolve(), { once: true });
  });
}

/** Opens the print dialog for the result block. Resolves once the dialog was opened (or the browser refused). */
export async function printResult(node: HTMLElement, title: string): Promise<void> {
  const frame = document.createElement("iframe");
  frame.setAttribute("aria-hidden", "true");
  frame.tabIndex = -1;
  frame.title = "Print";
  Object.assign(frame.style, { position: "fixed", right: "0", bottom: "0", width: "0", height: "0", border: "0", visibility: "hidden" });
  document.body.appendChild(frame);
  const doc = frame.contentDocument;
  const win = frame.contentWindow;
  if (!doc || !win) { frame.remove(); throw new Error("This browser cannot print the result."); }
  doc.open();
  doc.write("<!doctype html><html><head><meta charset=\"utf-8\"></head><body></body></html>");
  doc.close();
  doc.documentElement.dataset.theme = "light";
  doc.documentElement.lang = "en";
  doc.title = title;
  const waits: Promise<void>[] = [];
  document.querySelectorAll<HTMLLinkElement | HTMLStyleElement>("link[rel=stylesheet], style").forEach((el) => {
    const copy = doc.importNode(el, true) as HTMLLinkElement | HTMLStyleElement;
    doc.head.appendChild(copy);
    if (copy.tagName === "LINK") waits.push(loaded(copy as HTMLLinkElement));
  });
  const style = doc.createElement("style");
  style.textContent = PRINT_CSS;
  doc.head.appendChild(style);
  const copy = printableClone(node, doc);
  copy.style.padding = "0";
  doc.body.appendChild(copy);
  await Promise.all(waits);
  await Promise.all(Array.from(doc.images).map((img) => (img.complete ? Promise.resolve() : img.decode().catch(() => undefined))));  // intentional: an image that cannot be decoded prints without it
  const done = () => setTimeout(() => frame.remove(), 500);
  win.addEventListener("afterprint", done, { once: true });
  win.focus();
  win.print();
  setTimeout(() => frame.remove(), 60_000);   // a browser without afterprint
}

/** "Chart analysis - DEMO 15m - 20 Oct 2026": the file name the browser suggests for the PDF. */
export function printTitle(symbol: string | null, timeframe: string | null, when: Date = new Date()): string {
  const date = formatDateIST(when);                         // REV-1 M3: the IST day
  return ["Chart analysis", [symbol, timeframe].filter(Boolean).join(" "), date].filter(Boolean).join(" - ");
}
