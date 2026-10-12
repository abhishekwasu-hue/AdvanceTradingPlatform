/**
 * U8 EV1: the evidence pack (`evidence/1`) the backend stores with each matched symbol of a screen run, and the pure
 * geometry the evidence chart draws from it. Every number on the chart and in the verify strip comes from the pack - the
 * engine's own values at the matched bar - never from pixels or a second computation here.
 *
 * Owner rule 0 (minimal chart): only the pack's drawables are drawn - the screen's own conditions - plus the bars and
 * the matched bar. No user layers, no other indicators, no zones the screen did not read. A failed condition is drawn
 * only when "Show failed conditions" is on (the backend sends its drawing only with `debug`).
 */
import { request } from "../api/client";

export type DrawableKind = "overlay" | "pane" | "box" | "band" | "bars";

export interface EvidenceSeries { name: string; values: (number | null)[]; style: "line" | "histogram" }
export interface EvidenceLevel { value: number; label: string; value_text: string }
export interface EvidenceDrawable {
  id: string; row: string; kind: DrawableKind; pane: "price" | "volume" | "oscillator"; label: string; failed: boolean;
  unit?: string | null; series?: EvidenceSeries[]; levels?: EvidenceLevel[]; marker?: { index: number; text: string };
  from?: number; to?: number; low?: number; high?: number; level?: number; index?: number[];
  role?: "demand" | "supply"; state?: string; touch_text?: string; formed_at?: string | null;
}
export interface EvidenceTerm { text: string; value: number | string | boolean | null; value_text: string; unit: string | null; literal: boolean; note?: string }
export interface EvidenceRow {
  id: string; block: string; text: string; passed: boolean | null; terms: EvidenceTerm[]; verify: string; drawable: string | null;
  note?: string; negated?: boolean; zone_as_of?: string | null;
}
export interface EvidenceBlock { id: string; text: string; passed: boolean | null; rows: string[] }
export interface EvidencePack {
  schema: "evidence/1"; evidence_id?: number; pack_sha256?: string; can_send?: boolean;
  symbol: string; exchange: string; base_tf: string;
  screen: { text: string; ast_sha256: string; version: string; name: string | null };
  run_id: number | null;
  as_of: { bar_start: string; bar_close: string };
  data: { source: string | null };
  matched: boolean | null;
  bars: { t: string[]; o: (number | null)[]; h: (number | null)[]; l: (number | null)[]; c: (number | null)[]; v: (number | null)[] };
  matched_index: number;
  blocks: EvidenceBlock[];
  rows: EvidenceRow[];
  drawables: EvidenceDrawable[];
  summary: { conditions: number; shown: number; not_shown: number; drawn: number; debug: boolean };
  caption: string[];
  disclaimer: string;
}

export const evidenceApi = {
  get: (id: number, debug = false) => request<EvidencePack>(`/screener/evidence/${id}${debug ? "?debug=true" : ""}`),
  sendTelegram: (id: number) => request<{ sent: boolean; reason: string | null }>(`/screener/evidence/${id}/telegram`, { method: "POST" }),
};

/** Okabe-Ito (colour-blind safe): zone bands by role, the other drawables in order. Candles stay neutral. */
export const EVIDENCE_PALETTE = ["#E69F00", "#56B4E9", "#0072B2", "#D55E00"] as const;
export const ROLE_COLORS = { demand: "#009E73", supply: "#CC79A7" } as const;

/** A drawable's colour: its role for a zone band, else the palette by its place among the non-band drawables. */
export function drawableColor(d: EvidenceDrawable, all: readonly EvidenceDrawable[]): string {
  if (d.kind === "band") return ROLE_COLORS[d.role ?? "demand"];
  const order = all.filter((x) => x.kind !== "band").indexOf(d);
  return EVIDENCE_PALETTE[Math.max(0, order) % EVIDENCE_PALETTE.length];
}

const INTRADAY = new Set(["1m", "3m", "5m", "15m", "30m", "1h"]);

/** "bar closed 10 Oct 2026, 11:15 IST" for intraday bars, "bar of 19 Jul 2026" for daily and longer. */
export function barTimeText(pack: Pick<EvidencePack, "base_tf" | "as_of">): string {
  const intraday = INTRADAY.has(pack.base_tf);
  const d = new Date(intraday ? pack.as_of.bar_close : pack.as_of.bar_start);
  if (Number.isNaN(d.getTime())) return "";
  if (!intraday) return `bar of ${d.toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", year: "numeric" })}`;
  return `bar closed ${d.toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false })} IST`;
}

/** The drawables to show: not those of hidden blocks, failed ones only with `showFailed`. */
export function visibleDrawables(pack: EvidencePack, hiddenBlocks: ReadonlySet<string>, showFailed: boolean): EvidenceDrawable[] {
  const blockOf = new Map(pack.rows.map((r) => [r.id, r.block]));
  return pack.drawables.filter((d) => (showFailed || !d.failed) && !hiddenBlocks.has(blockOf.get(d.row) ?? ""));
}

/** The verify strip rows: failed conditions only with `showFailed`. */
export function verifyRows(pack: EvidencePack, showFailed: boolean): EvidenceRow[] {
  return pack.rows.filter((r) => showFailed || r.passed !== false);
}

// --------------------------------------------------------------------------------------------------- geometry
export interface Candle { x: number; w: number; open: number; close: number; high: number; low: number; rising: boolean; index: number }
export interface Shape {
  kind: "rect" | "path" | "line" | "text";
  key: string; drawable?: string; color?: string; fill?: string; opacity?: number; dash?: string;
  x?: number; y?: number; w?: number; h?: number; d?: string; x1?: number; y1?: number; x2?: number; y2?: number; text?: string;
}
export interface Geometry {
  width: number; height: number; priceTop: number; priceBottom: number; left: number; right: number;
  candles: Candle[]; shapes: Shape[]; subShapes: Shape[]; sub: { top: number; bottom: number; label: string; color: string } | null;
  matchedX: number | null; lo: number; hi: number;
}

const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/**
 * Pixel geometry for an evidence pack (pure, for the SVG chart and for tests): candles, the drawables given (at most one
 * lower pane - the first pane drawable), the matched bar. Bars are slots of equal width; prices map linearly.
 */
export function evidenceGeometry(pack: EvidencePack, drawables: readonly EvidenceDrawable[], width = 720, height = 360): Geometry {
  const n = Math.min(pack.bars.o.length, pack.bars.h.length, pack.bars.l.length, pack.bars.c.length);
  const sub = drawables.find((d) => d.pane !== "price") ?? null;
  const left = 8, right = width - 64, priceTop = 12;
  const subH = sub ? Math.round(height * 0.24) : 0;
  const priceBottom = height - 8 - (sub ? subH + 10 : 0);
  const prices: number[] = [];
  for (let i = 0; i < n; i++) { if (finite(pack.bars.h[i])) prices.push(pack.bars.h[i] as number); if (finite(pack.bars.l[i])) prices.push(pack.bars.l[i] as number); }
  for (const d of drawables) {
    if (d.pane !== "price") continue;
    for (const s of d.series ?? []) for (const v of s.values) if (finite(v)) prices.push(v);
    for (const lv of d.levels ?? []) if (finite(lv.value)) prices.push(lv.value);
    for (const v of [d.low, d.high, d.level]) if (finite(v)) prices.push(v);
  }
  let lo = prices.length ? Math.min(...prices) : 0, hi = prices.length ? Math.max(...prices) : 1;
  if (hi <= lo) { hi = hi * 1.001 + 0.01; lo = lo * 0.999; }
  const pad = (hi - lo) * 0.05; lo -= pad; hi += pad;
  const slot = n ? (right - left) / n : 1;
  const xOf = (i: number) => left + slot * i + slot / 2;
  const yOf = (p: number, top = priceTop, bottom = priceBottom, a = lo, b = hi) => bottom - ((p - a) / (b - a)) * (bottom - top);
  const bodyW = Math.max(1, slot * 0.6);
  const candles: Candle[] = [];
  for (let i = 0; i < n; i++) {
    const [o, h, l, c] = [pack.bars.o[i], pack.bars.h[i], pack.bars.l[i], pack.bars.c[i]];
    if (!finite(o) || !finite(h) || !finite(l) || !finite(c)) continue;
    candles.push({ x: xOf(i), w: bodyW, open: yOf(o), close: yOf(c), high: yOf(h), low: yOf(l), rising: c >= o, index: i });
  }
  const shapes: Shape[] = [];
  const subShapes: Shape[] = [];
  const path = (values: readonly (number | null)[], y: (v: number) => number) => {
    let d = "", pen = false;
    values.slice(0, n).forEach((v, i) => {
      if (!finite(v)) { pen = false; return; }
      d += `${pen ? "L" : "M"}${xOf(i).toFixed(1)} ${y(v).toFixed(1)}`;
      pen = true;
    });
    return d;
  };
  drawables.forEach((d) => {
    const color = drawableColor(d, drawables);
    const base = { drawable: d.id, color };
    if (d.kind === "band" && finite(d.low) && finite(d.high)) {
      const x0 = xOf(d.from ?? 0) - slot / 2;
      shapes.push({ ...base, kind: "rect", key: `${d.id}-band`, x: x0, y: yOf(d.high), w: right - x0, h: Math.max(1, yOf(d.low) - yOf(d.high)), fill: color, opacity: 0.18 });
    } else if (d.kind === "box" && finite(d.low) && finite(d.high)) {
      const x0 = xOf(d.from ?? 0) - slot / 2, x1 = xOf(d.to ?? n - 1) + slot / 2;
      shapes.push({ ...base, kind: "rect", key: `${d.id}-box`, x: x0, y: yOf(d.high), w: x1 - x0, h: Math.max(1, yOf(d.low) - yOf(d.high)), fill: color, opacity: 0.16 });
      if (finite(d.level)) shapes.push({ ...base, kind: "line", key: `${d.id}-level`, x1: x0, x2: right, y1: yOf(d.level), y2: yOf(d.level), dash: "6 4" });
    } else if (d.kind === "bars") {
      for (const i of d.index ?? []) {
        if (i < 0 || i >= n || !finite(pack.bars.h[i]) || !finite(pack.bars.l[i])) continue;
        const top = yOf(pack.bars.h[i] as number) - 3, bottom = yOf(pack.bars.l[i] as number) + 3;
        shapes.push({ ...base, kind: "rect", key: `${d.id}-bar-${i}`, x: xOf(i) - bodyW / 2 - 3, y: top, w: bodyW + 6, h: bottom - top, fill: "none" });
      }
      if (finite(d.level)) shapes.push({ ...base, kind: "line", key: `${d.id}-level`, x1: left, x2: right, y1: yOf(d.level), y2: yOf(d.level), dash: "6 4" });
    } else if (d.kind === "overlay") {
      (d.series ?? []).forEach((s, si) => {
        const p = path(s.values, (v) => yOf(v));
        if (p) shapes.push({ ...base, kind: "path", key: `${d.id}-s${si}`, d: p, dash: si ? "4 3" : undefined });
      });
      (d.levels ?? []).forEach((lv, li) => shapes.push({ ...base, kind: "line", key: `${d.id}-l${li}`, x1: left, x2: right, y1: yOf(lv.value), y2: yOf(lv.value), dash: "6 4" }));
    }
  });
  let subInfo: Geometry["sub"] = null;
  if (sub) {
    const top = priceBottom + 12, bottom = height - 8;
    const color = drawableColor(sub, drawables);
    const vals: number[] = [];
    for (const s of sub.series ?? []) for (const v of s.values) if (finite(v)) vals.push(v);
    for (const lv of sub.levels ?? []) if (finite(lv.value)) vals.push(lv.value);
    let a = vals.length ? Math.min(...vals) : 0, b = vals.length ? Math.max(...vals) : 1;
    if ((sub.series ?? []).some((s) => s.style === "histogram")) a = Math.min(a, 0);
    if (b <= a) { b += 1; a -= 1; }
    const p2 = (b - a) * 0.08; a -= p2; b += p2;
    const y2 = (v: number) => yOf(v, top, bottom, a, b);
    (sub.series ?? []).forEach((s, si) => {
      if (s.style === "histogram") {
        s.values.slice(0, n).forEach((v, i) => {
          if (!finite(v)) return;
          subShapes.push({ drawable: sub.id, color, kind: "rect", key: `${sub.id}-h${i}`, x: xOf(i) - bodyW / 2, y: y2(v), w: bodyW, h: Math.max(0, y2(Math.max(a, 0)) - y2(v)), fill: color, opacity: si ? 0.5 : 0.7 });
        });
      } else {
        const p = path(s.values, y2);
        if (p) subShapes.push({ drawable: sub.id, color, kind: "path", key: `${sub.id}-s${si}`, d: p, dash: si ? "4 3" : undefined });
      }
    });
    (sub.levels ?? []).forEach((lv, li) => {
      subShapes.push({ drawable: sub.id, color: "currentColor", kind: "line", key: `${sub.id}-l${li}`, x1: left, x2: right, y1: y2(lv.value), y2: y2(lv.value), dash: "4 3" });
      subShapes.push({ drawable: sub.id, color: "currentColor", kind: "text", key: `${sub.id}-lt${li}`, x: right + 4, y: y2(lv.value) + 4, text: lv.value_text });
    });
    subInfo = { top, bottom, label: sub.label, color };
  }
  const j = pack.matched_index;
  return { width, height, priceTop, priceBottom, left, right, candles, shapes, subShapes, sub: subInfo,
           matchedX: j >= 0 && j < n ? xOf(j) : null, lo, hi };
}

/** One sentence for screen readers: what the chart shows (the matched bar, the conditions drawn). */
export function chartSummary(pack: EvidencePack, drawables: readonly EvidenceDrawable[]): string {
  const drawn = drawables.length ? drawables.map((d) => d.label).join("; ") : "no conditions drawn";
  return `${pack.symbol} ${pack.base_tf} candles up to the matched ${barTimeText(pack)}. Drawn: ${drawn}.`;
}
