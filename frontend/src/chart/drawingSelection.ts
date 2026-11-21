/**
 * Owner 21 Nov (drawings like a pro charting app): what the selection bar offers for a selected drawing, without
 * React. Each kind gets only the settings its picture honours (geometry.ts): colour, width and style for lines, boxes
 * and Fibonacci levels; the extension for a trend line and a channel (a ray already runs past its second point); a
 * label's colour and text; a position (a planning drawing in its own profit / loss tints) only lock, copy and delete.
 * Widths stop at 4, what the chart draws (geometry.ts), although the store takes up to WIDTH_MAX.
 */
import { EXTENDS as EXTEND_IDS, LINE_STYLES as LINE_IDS, type DrawingKind, type DrawingStyle, type DrawingV1 } from "../charting/drawings";
import { DEFAULT_COLOR } from "../charting/geometry";
import type { ChartPalette } from "./chartTheme";

export interface DrawingSettings { colour: boolean; line: boolean; extend: boolean; text: boolean }

const LINES: readonly DrawingKind[] = ["trendline", "ray", "hline", "vline", "measure", "channel", "rectangle", "fib_retracement", "fib_extension"];
const COLOURED: readonly DrawingKind[] = [...LINES, "text"];
const EXTENDS: readonly DrawingKind[] = ["trendline", "channel"];

export function drawingSettings(kind: DrawingKind): DrawingSettings {
  return { colour: COLOURED.includes(kind), line: LINES.includes(kind), extend: EXTENDS.includes(kind), text: kind === "text" };
}

export const WIDTHS = [1, 2, 3, 4] as const;
const LINE_LABELS: Record<NonNullable<DrawingStyle["line"]>, string> = { solid: "Solid", dashed: "Dashed", dotted: "Dotted" };
const EXTEND_LABELS: Record<NonNullable<DrawingStyle["extend"]>, string> = {
  none: "No extension", left: "Extend left", right: "Extend right", both: "Extend both ways",
};
export const LINE_STYLES = LINE_IDS.map((id) => ({ id, label: LINE_LABELS[id] }));
export const EXTEND_OPTIONS = EXTEND_IDS.map((id) => ({ id, label: EXTEND_LABELS[id] }));

/** `rgb(r, g, b)`, `rgba(...)` or `#rrggbb` as lower-case `#rrggbb` (the stored form); null for anything else. */
export function toHex(css: string): string | null {
  const s = css.trim().toLowerCase();
  if (/^#[0-9a-f]{6}$/.test(s)) return s;
  const m = /^rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)/.exec(s);
  if (!m) return null;
  return `#${m.slice(1, 4).map((v) => Math.max(0, Math.min(255, Number(v))).toString(16).padStart(2, "0")).join("")}`;
}

/** The swatches, from the chart theme: the drawing colour first, then up, down, the four price-line colours and the
 * chart text; each once. */
export function swatchColours(p: ChartPalette): string[] {
  const out: string[] = [];
  for (const c of [p.drawing, p.up, p.down, ...p.ov, p.text]) {
    const hex = toHex(c);
    if (hex && !out.includes(hex)) out.push(hex);
  }
  return out;
}

/** The colour the drawing is shown in: its own, or the chart's drawing colour for the default one (lightweight.ts). */
export function shownColour(d: DrawingV1, p: ChartPalette): string {
  const own = d.style?.color?.toLowerCase();
  if (own && own !== DEFAULT_COLOR.toLowerCase()) return own;
  return toHex(p.drawing) ?? DEFAULT_COLOR;
}

/** A label's text as the store takes it: trimmed, not empty, no < or >. */
export function textProblem(text: string): string | null {
  if (!text.trim()) return "Type the label's text.";
  if (/[<>]/.test(text)) return "The text may not contain < or >.";
  return null;
}
