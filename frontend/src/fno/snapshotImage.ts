/**
 * FNO-8B-31 (owner decision 23 Oct): "Download image" on the journal's chart. The chart stays a redraw on his tap with
 * nothing stored on the server; this saves the chart as drawn on his screen as a PNG file on his own device (tested in
 * snapshotImage.test.ts). Nothing is uploaded: the SVG on the page is copied, its theme colours written in, drawn on a
 * canvas and handed to the browser as a download. The file name is plain - the symbol, "trade-chart" and the closing
 * day - never an internal id.
 */
import { istDay } from "./snapshot";

/** "NIFTY 50", closed 16 Oct 2026 -> "nifty-50-trade-chart-2026-10-16.png". */
export function snapshotFileName(symbol: string, closedMs: number): string {
  const name = symbol.toLowerCase().replace(/&/g, "and").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40) || "chart";
  const day = Number.isFinite(closedMs) ? `-${istDay(closedMs)}` : "";
  return `${name}-trade-chart${day}.png`;
}

/** Writes each `var(--token)` of the markup as its value (a stand-alone image has no page styles to read them from). */
export function inlineThemeColours(markup: string, resolve: (token: string) => string): string {
  return markup.replace(/var\(--([a-zA-Z0-9-]+)\)/g, (whole, token: string) => {
    const v = resolve(token).trim();
    return v || whole;
  });
}

/** The page's value of one theme token (for example "--surface-1" -> "255 255 255"). */
export const pageToken = (token: string): string =>
  typeof document === "undefined" ? "" : getComputedStyle(document.documentElement).getPropertyValue(`--${token}`);

export interface ImageOptions {
  fileName: string;
  /** A line written above the chart in the image (the symbol and when the trade opened and closed). */
  heading: string;
  /** Pixels per drawn unit (2 = sharp on most screens). */
  scale?: number;
}

const HEADING_PX = 28;

/** Saves the drawn chart `svg` as a PNG download. Resolves false when the browser cannot draw it (nothing is saved). */
export async function downloadSnapshot(svg: SVGSVGElement, { fileName, heading, scale = 2 }: ImageOptions): Promise<boolean> {
  const box = svg.viewBox.baseVal;
  const width = box && box.width > 0 ? box.width : svg.clientWidth;
  const height = box && box.height > 0 ? box.height : svg.clientHeight;
  if (!(width > 0) || !(height > 0)) return false;
  const copy = svg.cloneNode(true) as SVGSVGElement;
  copy.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  copy.setAttribute("width", String(width));
  copy.setAttribute("height", String(height));
  const font = getComputedStyle(svg).fontFamily;
  if (font) copy.setAttribute("font-family", font);
  const markup = inlineThemeColours(new XMLSerializer().serializeToString(copy), pageToken);
  const url = URL.createObjectURL(new Blob([markup], { type: "image/svg+xml;charset=utf-8" }));
  try {
    const img = new Image();
    await new Promise<void>((ok, fail) => { img.onload = () => ok(); img.onerror = () => fail(new Error("not drawn")); img.src = url; });
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(width * scale);
    canvas.height = Math.round((height + HEADING_PX) * scale);
    const ctx = canvas.getContext("2d");
    if (!ctx) return false;
    ctx.scale(scale, scale);
    ctx.fillStyle = `rgb(${pageToken("surface-1").trim() || "255 255 255"})`;
    ctx.fillRect(0, 0, width, height + HEADING_PX);
    ctx.fillStyle = `rgb(${pageToken("fg").trim() || "0 0 0"})`;
    ctx.font = `600 13px ${font || "sans-serif"}`;
    ctx.textBaseline = "middle";
    ctx.fillText(heading, 6, HEADING_PX / 2, width - 12);
    ctx.drawImage(img, 0, HEADING_PX, width, height);
    const blob = await new Promise<Blob | null>((ok) => canvas.toBlob(ok, "image/png"));
    if (!blob) return false;
    const href = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = href;
    a.download = fileName;
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(href), 1000);
    return true;
  } catch {
    return false;
  } finally {
    URL.revokeObjectURL(url);
  }
}
