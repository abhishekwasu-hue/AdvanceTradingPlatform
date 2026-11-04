import { formatNumber } from "../ui/format";
import type { AnalysisResult, Annotations } from "./types";

/**
 * Analyze a chart: what is drawn over the image, as plain shapes in the image's own pixel space - the on-screen SVG
 * overlay and the exported PNG draw the very same list, so the download always matches the screen.
 *
 * Colours are semantic tokens (UI v2 §8), never literal values: up = buyer areas and the next area (like a target
 * level), down = seller areas and the invalidation (like a stop level), fg / fg-muted for structure, warn for the RSI
 * divergence (a caution, not a direction).
 */

export type Token = "up" | "down" | "fg" | "fg-muted" | "warn" | "surface-1";

/** Every line, dot and label is drawn over a halo of the page surface, so it stays readable on a light or a dark chart
 * image in either theme. */
export const HALO: Token = "surface-1";
export const HALO_ALPHA = 0.85;

export type Shape =
  | { kind: "rect"; x: number; y: number; w: number; h: number; token: Token; fillAlpha: number; label?: string; dashed?: boolean }
  | { kind: "line"; x0: number; y0: number; x1: number; y1: number; token: Token; dashed?: boolean; label?: string; width?: number; labelAt?: "end" }
  | { kind: "dot"; x: number; y: number; token: Token; r: number; label?: string }
  | { kind: "text"; x: number; y: number; token: Token; text: string };

export const STATE_WORD: Record<string, string> = { fresh: "fresh", tested: "tested", broken: "broken", flipped: "flipped" };

/** Owner CA-Q3: "≈ 2,295" beside a drawn level (image-only prices are read from the axis, so always approximate). */
export function approxPrice(p: number | null | undefined): string {
  return p == null ? "" : ` ≈ ${formatNumber(p, p >= 1000 ? 0 : 2)}`;
}

/** Owner CA-Q3: the number an area carries on the chart (1, 2, ...) - the Key levels list shows the same number. */
export function areaNumber(a: Annotations, low: number | null, high: number | null): number | null {
  if (low == null && high == null) return null;
  const i = a.areas.findIndex((x) => x.price_low === low && x.price_high === high);
  return i < 0 ? null : i + 1;
}

/** Owner CA-Q3: how far a level is from the last price, in % of that price ("2.1% above"). */
export function distancePct(last: number | null | undefined, low: number | null, high: number | null): string | null {
  if (last == null || last <= 0 || (low == null && high == null)) return null;
  const lo = Math.min(low ?? high ?? 0, high ?? low ?? 0), hi = Math.max(low ?? high ?? 0, high ?? low ?? 0);
  if (last >= lo && last <= hi) return "at the price";
  const gap = last < lo ? lo - last : last - hi;
  return `${formatNumber((gap / last) * 100, 1)}% ${last < lo ? "above" : "below"}`;
}

export function shapesFor(a: Annotations, width: number): Shape[] {
  const out: Shape[] = [];
  for (const [n, area] of a.areas.entries()) {
    const x = Math.min(area.x0, area.x1), y = Math.min(area.y0, area.y1);
    out.push({ kind: "rect", x, y, w: Math.abs(area.x1 - area.x0), h: Math.max(1, Math.abs(area.y1 - area.y0)),
      token: area.side === "buyer" ? "up" : "down", fillAlpha: area.state === "broken" ? 0.06 : 0.16, dashed: area.state === "broken",
      label: `${n + 1} · ${area.side === "buyer" ? "Buyer" : "Seller"} area · ${STATE_WORD[area.state] ?? area.state}` });
  }
  for (const line of a.lines) {
    out.push({ kind: "line", x0: line.x0, y0: line.y0, x1: line.x1, y1: line.y1, token: "fg-muted", label: line.name || undefined });
    for (const t of line.touches) out.push({ kind: "dot", x: t.x, y: t.y, token: "fg-muted", r: 3 });
  }
  for (const s of a.setup_lines) {
    const token: Token = s.kind === "invalidation" ? "down" : s.kind === "next_area" ? "up" : "fg";
    const word = s.kind === "invalidation" ? "Invalidation" : s.kind === "next_area" ? "Next area" : "Trigger";
    out.push({ kind: "line", x0: 0, y0: s.y, x1: width, y1: s.y, token, dashed: true, label: `${word}${approxPrice(s.price)}`, labelAt: "end" });
  }
  if (a.rsi_divergence) {
    const d = a.rsi_divergence;
    out.push({ kind: "line", ...d.price, token: "warn", width: 2, label: `RSI divergence (${d.kind})` });
    if (d.rsi) out.push({ kind: "line", ...d.rsi, token: "warn", width: 2 });
  }
  for (const s of a.swings) {
    out.push({ kind: "dot", x: s.x, y: s.y, token: s.label === "HH" || s.label === "HL" ? "up" : "down", r: 3 });
    out.push({ kind: "text", x: s.x, y: s.label === "HH" || s.label === "LH" ? s.y - 8 : s.y + 16, token: "fg", text: s.label });
  }
  if (a.choch) out.push({ kind: "line", x0: a.choch.x0, y0: a.choch.y0, x1: a.choch.x1, y1: a.choch.y1, token: "fg", width: 2, label: `CHoCH${approxPrice(a.choch.price)}`, labelAt: "end" });
  if (a.commitment_candle) out.push({ kind: "dot", x: a.commitment_candle.x, y: a.commitment_candle.y, token: "fg", r: 9, label: "Commitment" });
  if (a.ltf_choch) out.push({ kind: "dot", x: a.ltf_choch.x, y: a.ltf_choch.y, token: "fg", r: 6, label: "Lower-timeframe change" });
  return out;
}

export function resultShapes(result: AnalysisResult): Shape[] {
  return shapesFor(result.annotations, result.image.width);
}

/** The legend rows the overlay shows (only what is on the chart). */
export type Swatch = "box" | "dashed" | "line" | "dot";

/** The legend rows the overlay shows (only what is on the chart). Owner review (3 Nov): each row's swatch has the
 * drawn shape (a box for an area, a dashed line for a setup line), so a seller area and the invalidation - both red,
 * by meaning - are never told apart by colour alone. */
export function legendFor(a: Annotations): { token: Token; text: string; swatch: Swatch }[] {
  const rows: { token: Token; text: string; swatch: Swatch }[] = [];
  if (a.areas.some((x) => x.side === "buyer")) rows.push({ token: "up", text: "Buyer area", swatch: "box" });
  if (a.areas.some((x) => x.side === "seller")) rows.push({ token: "down", text: "Seller area", swatch: "box" });
  if (a.lines.length) rows.push({ token: "fg-muted", text: "Line and its touches", swatch: "line" });
  if (a.setup_lines.some((s) => s.kind === "trigger")) rows.push({ token: "fg", text: "Trigger", swatch: "dashed" });
  if (a.setup_lines.some((s) => s.kind === "invalidation")) rows.push({ token: "down", text: "Invalidation", swatch: "dashed" });
  if (a.setup_lines.some((s) => s.kind === "next_area")) rows.push({ token: "up", text: "Next area", swatch: "dashed" });
  if (a.rsi_divergence) rows.push({ token: "warn", text: "RSI divergence", swatch: "line" });
  if (a.choch) rows.push({ token: "fg", text: "CHoCH (change of character)", swatch: "line" });
  if (a.swings.length) rows.push({ token: "fg", text: "Swings: HH / HL / LH / LL", swatch: "dot" });
  return rows;
}

/** Owner review (3 Nov): the labels were drawn at 12 image pixels, unreadable once a wide screenshot is scaled to a
 * phone. The label size grows with the image's width (one size for the screen and the download). */
export function labelSize(width: number): number {
  return Math.max(12, Math.round(width / 55));
}

/** One label as drawn: the text, its anchor point (x at the start or the end of the text, y on the baseline), and
 * the box it is estimated to cover in image pixels. */
export interface PlacedLabel {
  text: string; token: Token; bold: boolean; anchor: "start" | "end"; x: number; y: number;
  box: { x: number; y: number; w: number; h: number };
}

/** The estimated box of a text: width 0.6 x font size per character, height one font size (baseline at 0.8 of it). */
function boxAt(text: string, x: number, y: number, anchor: "start" | "end", fs: number): PlacedLabel["box"] {
  const w = 0.6 * fs * text.length;
  return { x: anchor === "end" ? x - w : x, y: y - 0.8 * fs, w, h: fs };
}

export function boxOverlap(a: PlacedLabel["box"], b: PlacedLabel["box"]): number {
  const dx = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
  const dy = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
  return dx > 0 && dy > 0 ? dx * dy : 0;
}

/** How many steps a label may be moved up, and down, to clear the labels placed before it. */
export const LABEL_STEPS = 4;
const OUTSIDE = 1e9;     // a spot out of the image is taken only when no spot inside it is left

/**
 * Owner CA-Q3: on a crowded chart the labels were drawn over each other. Every label (an area's, a line's, a mark's
 * and the swing words) gets its usual spot; a label that would cover one placed before it is moved up or down -
 * alternately, one step (fs + 2) at a time, at most LABEL_STEPS each way, never out of the image - to the first free
 * spot (if none is free, the spot that covers the least). Lines, boxes and dots never move. Deterministic: the
 * on-screen SVG and the exported PNG both draw exactly these positions.
 */
export function layoutLabels(shapes: Shape[], height: number, fs: number): PlacedLabel[] {
  const k = fs / 12;
  const want: Omit<PlacedLabel, "box">[] = [];
  for (const s of shapes) {
    if (s.kind === "text") want.push({ text: s.text, token: s.token, bold: true, anchor: "start", x: s.x - 8 * k, y: s.y });
    else if (!s.label) continue;
    else if (s.kind === "rect") want.push({ text: s.label, token: s.token, bold: false, anchor: "start", x: s.x + 4 * k, y: s.y + 13 * k });
    else if (s.kind === "dot") want.push({ text: s.label, token: s.token, bold: false, anchor: "start", x: s.x + s.r + 3 * k, y: s.y - s.r });
    else if (s.labelAt === "end") want.push({ text: s.label, token: s.token, bold: false, anchor: "end", x: Math.max(s.x0, s.x1) - 4 * k, y: Math.min(s.y0, s.y1) - 4 * k });
    else want.push({ text: s.label, token: s.token, bold: false, anchor: "start", x: Math.min(s.x0, s.x1) + 4 * k, y: Math.min(s.y0, s.y1) - 4 * k });
  }
  const step = fs + 2;
  const placed: PlacedLabel[] = [];
  for (const l of want) {
    let best: PlacedLabel = { ...l, box: boxAt(l.text, l.x, l.y, l.anchor, fs) };
    let bestCost = Infinity;
    for (let i = 0; i <= 2 * LABEL_STEPS; i++) {
      const dy = i === 0 ? 0 : (i % 2 ? -1 : 1) * Math.ceil(i / 2) * step;
      const box = boxAt(l.text, l.x, l.y + dy, l.anchor, fs);
      const inside = box.y >= 0 && box.y + box.h <= height;
      if (!inside && i > 0) continue;
      const cost = placed.reduce((sum, p) => sum + boxOverlap(box, p.box), 0) + (inside ? 0 : OUTSIDE);
      if (cost < bestCost) { best = { ...l, y: l.y + dy, box }; bestCost = cost; }
      if (cost === 0 && inside) break;
    }
    placed.push(best);
  }
  return placed;
}
