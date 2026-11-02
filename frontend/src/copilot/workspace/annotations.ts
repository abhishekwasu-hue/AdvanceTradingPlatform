import type { LayerBand, LayerData, LayerLabel, LayerSegment } from "../../charting/engine";
import { areaLabelText } from "../../charting/geometry";
import { ZONE_COLORS } from "../../charting/marketZones";
import { PA_COLORS } from "../../charting/priceAction";
import { readLayer, type ReadChart } from "../../chart/readChart";

/**
 * M75 W-3 (owner order M75 CW-2, flag `copilot_workspace`): the workspace's annotations - ONE reading from ONE source
 * (ATP's chart readers, or the signal engine when it reads this chart for him), drawn by code: areas as filled bands with
 * their side, timeframe and state; the swings HH / HL / LH / LL; BOS / CHoCH; an RSI divergence when present; the
 * structure level. Every number comes from the server's response (`GET /api/charts/read` -> `annotations`); nothing here
 * reads the candles or the picture. Pure: the component wires it.
 */

export type AnnotationSource = "atp" | "engine";
export type AreaRole = "demand" | "supply";

export interface AnnotationArea {
  role: AreaRole; side: "buyer" | "seller"; low: number; high: number; low_text: string; high_text: string;
  timeframe: string; state: string | null; state_words: string | null; tests: number | null; words: string;
}
export interface AnnotationSwing { label: "HH" | "HL" | "LH" | "LL"; kind: "H" | "L"; time: number; price: number; price_text: string; timeframe: string }
export interface AnnotationEvent {
  type: "BOS" | "CHOCH"; name: string; direction: "up" | "down" | "flat" | null; time: number; swing_time: number | null;
  price: number; price_text: string; timeframe: string; words: string;
}
export interface AnnotationTrend { timeframe: string; direction: string; words: string }
export interface AnnotationDivergence {
  side: "bullish" | "bearish"; role: AreaRole; timeframe: string; first: { time: number; price: number }; second: { time: number; price: number }; words: string;
}
export interface AnnotationLevel { price: number; price_text: string; side: "above" | "below"; words: string }
export interface Annotations {
  source: AnnotationSource; source_words: string; timeframe: string; as_of: string | null; last_close: number | null;
  areas: AnnotationArea[]; swings: AnnotationSwing[]; events: AnnotationEvent[]; trend: AnnotationTrend[];
  next_areas: { above: AnnotationArea | null; below: AnnotationArea | null; inside: AnnotationArea | null };
  divergence: AnnotationDivergence | null; level: AnnotationLevel | null; summary: string[]; not_given: string[];
}

const STATE_SHORT: Record<string, string> = { fresh: "fresh", tested: "tested", flipped: "flipped", broken: "broken" };
const TREND_TONE: Record<string, "up" | "down" | "flat"> = { up: "up", "up (weak)": "up", down: "down", "down (weak)": "down" };

/** The tone of a trend direction (green up / red down / neutral otherwise). */
export function trendTone(direction: string): "up" | "down" | "flat" {
  return TREND_TONE[direction] ?? "flat";
}

/** The area band's label: "Buyer area · 15m · fresh · 2 tests" (parts the data lacks are dropped). */
export function annotationAreaLabel(a: AnnotationArea): string {
  const base = areaLabelText({ role: a.role, timeframe: a.timeframe, state: a.state ? STATE_SHORT[a.state] ?? null : null });
  return a.tests ? `${base} · ${a.tests} test${a.tests === 1 ? "" : "s"}` : base;
}

/** The annotations as chart layers: bands (areas; the structure level as a thin band so its label is placed clear of
 * the axis and the controls), labels (swings, BOS / CHoCH) and segments (the divergence, an event's broken level). */
export function annotationLayer(a: Annotations): LayerData {
  const bands: LayerBand[] = a.areas.map((x) => ({ low: x.low, high: x.high, role: x.role, color: ZONE_COLORS[x.role], opacity: 0.16,
    style: "solid" as const, label: annotationAreaLabel(x) }));
  if (a.level) {
    bands.push({ low: a.level.price, high: a.level.price, role: "neutral", color: PA_COLORS.structure, style: "dashed",
      label: `Structure changes beyond ${a.level.price_text}` });
  }
  const labels: LayerLabel[] = a.swings.map((s) => ({ time: s.time, price: s.price, text: s.label,
    color: s.kind === "H" ? PA_COLORS.high : PA_COLORS.low, position: s.kind === "H" ? "above" : "below" }));
  const segments: LayerSegment[] = [];
  for (const e of a.events) {
    const color = e.type === "CHOCH" ? PA_COLORS.structure : e.direction === "down" ? PA_COLORS.down : PA_COLORS.up;
    if (e.swing_time != null && e.swing_time < e.time) segments.push({ t1: e.swing_time, p1: e.price, t2: e.time, p2: e.price, color, style: "dotted" });
    labels.push({ time: e.time, price: e.price, text: `${e.name} ${e.direction === "down" ? "↓" : "↑"}`, color, position: e.direction === "down" ? "above" : "below" });
  }
  const d = a.divergence;
  if (d) {
    segments.push({ t1: d.first.time, p1: d.first.price, t2: d.second.time, p2: d.second.price, color: ZONE_COLORS[d.role], width: 2,
      style: "dashed", label: "RSI divergence" });
  }
  return { asOf: a.as_of ?? undefined, bands, labels, segments };
}

/** The workspace's chart layer for a reading: its annotations when the server gave them, else the reading's own layer. */
export function workspaceLayer(r: ReadChart): LayerData {
  return r.annotations ? annotationLayer(r.annotations) : readLayer(r);
}

/** The table rows of the analysis panel (label, value, tone), in reading order. */
export interface AnnotationRow { key: string; label: string; value: string; tone?: "up" | "down" | "flat" }
export function annotationRows(a: Annotations): AnnotationRow[] {
  const rows: AnnotationRow[] = a.trend.map((t) => ({ key: `trend-${t.timeframe}`, label: `Trend ${t.timeframe}`,
    value: t.words.split(": ").slice(1).join(": ") || t.direction, tone: trendTone(t.direction) }));
  const last = a.events[a.events.length - 1];
  rows.push({ key: "event", label: "Last BOS / CHoCH", value: last ? `${last.name} ${last.direction ?? ""} at ${last.price_text}`.replace("  ", " ") : a.not_given.includes("events") ? "Not given by this source" : "None on this chart",
    tone: last ? (last.direction === "down" ? "down" : last.direction === "up" ? "up" : "flat") : undefined });
  const range = (x: AnnotationArea) => `${x.low_text} - ${x.high_text} (${x.timeframe}${x.state ? `, ${x.state}` : ""}${x.tests ? `, ${x.tests} test${x.tests === 1 ? "" : "s"}` : ""})`;
  if (a.next_areas.inside) rows.push({ key: "inside", label: "Price is inside", value: `${a.next_areas.inside.side} area ${range(a.next_areas.inside)}` });
  rows.push({ key: "above", label: "Next area above", value: a.next_areas.above ? `${a.next_areas.above.side} area ${range(a.next_areas.above)}` : "None near the price",
    tone: a.next_areas.above ? (a.next_areas.above.role === "supply" ? "down" : "up") : undefined });
  rows.push({ key: "below", label: "Next area below", value: a.next_areas.below ? `${a.next_areas.below.side} area ${range(a.next_areas.below)}` : "None near the price",
    tone: a.next_areas.below ? (a.next_areas.below.role === "supply" ? "down" : "up") : undefined });
  rows.push({ key: "divergence", label: "RSI divergence", value: a.divergence ? `${a.divergence.side} (${a.divergence.timeframe})` : a.not_given.includes("divergence") ? "Not given by this source" : "None now",
    tone: a.divergence ? (a.divergence.side === "bullish" ? "up" : "down") : undefined });
  rows.push({ key: "level", label: "Structure level", value: a.level ? `changes beyond ${a.level.price_text}` : "Not shown for this reading" });
  rows.push({ key: "swings", label: "Latest swings", value: a.swings.length ? a.swings.slice(-4).map((s) => s.label).join(" · ") : "None confirmed yet" });
  return rows;
}
