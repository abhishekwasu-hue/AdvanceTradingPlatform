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
  | { kind: "line"; x0: number; y0: number; x1: number; y1: number; token: Token; dashed?: boolean; label?: string; width?: number }
  | { kind: "dot"; x: number; y: number; token: Token; r: number; label?: string }
  | { kind: "text"; x: number; y: number; token: Token; text: string };

export const STATE_WORD: Record<string, string> = { fresh: "fresh", tested: "tested", broken: "broken", flipped: "flipped" };

export function shapesFor(a: Annotations, width: number): Shape[] {
  const out: Shape[] = [];
  for (const area of a.areas) {
    const x = Math.min(area.x0, area.x1), y = Math.min(area.y0, area.y1);
    out.push({ kind: "rect", x, y, w: Math.abs(area.x1 - area.x0), h: Math.max(1, Math.abs(area.y1 - area.y0)),
      token: area.side === "buyer" ? "up" : "down", fillAlpha: area.state === "broken" ? 0.06 : 0.16, dashed: area.state === "broken",
      label: `${area.side === "buyer" ? "Buyer" : "Seller"} area · ${STATE_WORD[area.state] ?? area.state}` });
  }
  for (const line of a.lines) {
    out.push({ kind: "line", x0: line.x0, y0: line.y0, x1: line.x1, y1: line.y1, token: "fg-muted", label: line.name || undefined });
    for (const t of line.touches) out.push({ kind: "dot", x: t.x, y: t.y, token: "fg-muted", r: 3 });
  }
  for (const s of a.setup_lines) {
    const token: Token = s.kind === "invalidation" ? "down" : s.kind === "next_area" ? "up" : "fg";
    const word = s.kind === "invalidation" ? "Invalidation" : s.kind === "next_area" ? "Next area" : "Trigger";
    out.push({ kind: "line", x0: 0, y0: s.y, x1: width, y1: s.y, token, dashed: true, label: word });
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
  if (a.commitment_candle) out.push({ kind: "dot", x: a.commitment_candle.x, y: a.commitment_candle.y, token: "fg", r: 9, label: "Commitment" });
  if (a.ltf_choch) out.push({ kind: "dot", x: a.ltf_choch.x, y: a.ltf_choch.y, token: "fg", r: 6, label: "Lower-timeframe change" });
  return out;
}

export function resultShapes(result: AnalysisResult): Shape[] {
  return shapesFor(result.annotations, result.image.width);
}

/** The legend rows the overlay shows (only what is on the chart). */
export function legendFor(a: Annotations): { token: Token; text: string }[] {
  const rows: { token: Token; text: string }[] = [];
  if (a.areas.some((x) => x.side === "buyer")) rows.push({ token: "up", text: "Buyer area" });
  if (a.areas.some((x) => x.side === "seller")) rows.push({ token: "down", text: "Seller area" });
  if (a.lines.length) rows.push({ token: "fg-muted", text: "Line and its touches" });
  if (a.setup_lines.some((s) => s.kind === "trigger")) rows.push({ token: "fg", text: "Trigger" });
  if (a.setup_lines.some((s) => s.kind === "invalidation")) rows.push({ token: "down", text: "Invalidation" });
  if (a.setup_lines.some((s) => s.kind === "next_area")) rows.push({ token: "up", text: "Next area" });
  if (a.rsi_divergence) rows.push({ token: "warn", text: "RSI divergence" });
  if (a.swings.length) rows.push({ token: "fg", text: "Swings: HH / HL / LH / LL" });
  return rows;
}
