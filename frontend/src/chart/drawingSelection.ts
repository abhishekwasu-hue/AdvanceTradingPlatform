/**
 * Owner 21 Nov (drawings like a pro charting app): what the selection bar offers for a selected drawing, without
 * React. Each kind gets only the settings its picture honours (geometry.ts): a line's colour, width and style; the
 * extension for a trend line and a channel (a ray already runs past its second point); a label's colour and text; a
 * position (a planning drawing in its own profit / loss tints) only lock, copy and delete.
 */
import type { DrawingKind, DrawingStyle, DrawingV1 } from "../charting/drawings";
import { DEFAULT_COLOR } from "../charting/geometry";
import type { ChartPalette } from "./chartTheme";

export interface DrawingSettings { colour: boolean; line: boolean; extend: boolean; text: boolean }

const LINES: readonly DrawingKind[] = ["trendline", "ray", "hline", "vline", "measure", "channel"];
const COLOURED: readonly DrawingKind[] = [...LINES, "rectangle", "fib_retracement", "fib_extension", "text"];
const EXTENDS: readonly DrawingKind[] = ["trendline", "channel"];

export function drawingSettings(kind: DrawingKind): DrawingSettings {
  return { colour: COLOURED.includes(kind), line: LINES.includes(kind), extend: EXTENDS.includes(kind), text: kind === "text" };
}

export const WIDTHS = [1, 2, 3, 4] as const;
export const LINE_STYLES: readonly { id: NonNullable<DrawingStyle["line"]>; label: string }[] = [
  { id: "solid", label: "Solid" }, { id: "dashed", label: "Dashed" }, { id: "dotted", label: "Dotted" },
];
export const EXTEND_OPTIONS: readonly { id: NonNullable<DrawingStyle["extend"]>; label: string }[] = [
  { id: "none", label: "No extension" }, { id: "left", label: "Extend left" }, { id: "right", label: "Extend right" }, { id: "both", label: "Extend both ways" },
];

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
