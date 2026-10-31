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
import type { LevelLine } from "../chart/chartCore";
import type { ChartBar, TimeframeId } from "../chart/timeframes";
import type { ChartMarker } from "../charting/markers";
import type { LayerData, LayerSegment, StudyLine } from "../charting/engine";

export type DrawableKind = "overlay" | "pane" | "box" | "band" | "bars";

export interface EvidenceSeries { name: string; values: (number | null)[]; style: "line" | "histogram" }
export interface EvidenceLevel { value: number; label: string; value_text: string }
export interface EvidenceDrawable {
  id: string; row: string; kind: DrawableKind; pane: "price" | "volume" | "oscillator"; label: string; failed: boolean;
  unit?: string | null; series?: EvidenceSeries[]; levels?: EvidenceLevel[]; marker?: { index: number; text: string };
  from?: number; to?: number; low?: number; high?: number; level?: number; index?: number[];
  role?: "demand" | "supply"; state?: string; touch_text?: string; formed_at?: string | null;
  /** EV3: the option chain whose OI walls / max pain these levels are (a chain is a snapshot of the newest bar's time). */
  chain?: { underlying: string; expiry: string };
}
export interface EvidenceTerm { text: string; value: number | string | boolean | null; value_text: string; unit: string | null; literal: boolean; note?: string }
export interface EvidenceRow {
  id: string; block: string; text: string; passed: boolean | null; terms: EvidenceTerm[]; verify: string; drawable: string | null;
  note?: string; negated?: boolean; zone_as_of?: string | null;
  /** SCR-FIX B5 (absent on older packs): what the row did against what the screen asks of it - "met" (✅), "failed"
   * (✗), "not_needed" (grey: an OR it sits in was made true by another branch) or "unknown". `raw` is the engine's own
   * truth; `required` false when the screen needs the condition FALSE (a NOT over its group - the text then reads
   * "NOT (...)"); `group` the innermost AND / OR it sits in, with the NOTs over it carried in. */
  state?: RowState; raw?: boolean | null; required?: boolean; group?: { id: string; op: "ALL" | "ANY"; negated: boolean } | null;
}
export type RowState = "met" | "failed" | "not_needed" | "unknown";
export interface EvidenceBlock { id: string; text: string; passed: boolean | null; rows: string[] }
/** EV3 (flag evidence_context): the engine's DowTrend on one timeframe as of the matched bar, and the rows reading it. */
export interface EvidenceContext {
  tf: string; tag: string | null; expr: string; bar_close: string | null; used_by: string[]; note: string | null; text: string;
}
/** EV3: present on a replayed pack - recomputed from the shared history as of `at`, never stored, never sent. */
export interface EvidenceReplay { at: string; source: string | null; note: string; stored_as_of: { bar_start: string; bar_close: string } }
/** SCR-FIX C4: why "Send to my Telegram" cannot go (the server's machine code; its sentence comes with it). */
export type TelegramReason = "not_linked" | "bot_not_configured" | "blocked_by_user" | "send_failed" | "not_owner";
export interface EvidencePack {
  schema: "evidence/1"; evidence_id?: number; pack_sha256?: string; can_send?: boolean;
  /** SCR-FIX C4: the caller's own Telegram chat is linked (the send can go), else why not. */
  telegram_linked?: boolean; telegram_reason?: TelegramReason | null; telegram_message?: string | null;
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
  context?: EvidenceContext[];
  replay?: EvidenceReplay;
  /** SCR-FIX B6: whether this evidence can be replayed at all (the replay controls show only then). */
  replay_available?: boolean;
}

export const evidenceApi = {
  get: (id: number, debug = false) => request<EvidencePack>(`/screener/evidence/${id}${debug ? "?debug=true" : ""}`),
  /** EV3: the stored pack's screen as of an earlier bar of its window (`at` = that bar's close, with its zone). */
  replay: (id: number, at: string, debug = false) =>
    request<EvidencePack>(`/screener/evidence/${id}/replay?at=${encodeURIComponent(at)}${debug ? "&debug=true" : ""}`),
  sendTelegram: (id: number) => request<{ sent: boolean; reason: TelegramReason | null; message: string | null }>(`/screener/evidence/${id}/telegram`, { method: "POST" }),
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

/** EV3: the timeframes a replay reads from the shared history (the backend's REPLAY_TFS). */
export const REPLAY_TFS: ReadonlySet<string> = new Set(["1m", "3m", "5m", "15m", "30m", "1h", "1d"]);
const TF_MS: Record<string, number> = { "1m": 60e3, "3m": 180e3, "5m": 300e3, "15m": 900e3, "30m": 1800e3, "1h": 3600e3, "1d": 86400e3 };

/**
 * EV3: the replay steps of a stored pack - each bar's close (the runtime's bar_close: start + the timeframe) from the
 * window's first bar up to the pack's own bar, oldest first. Empty for a timeframe that is not replayed. The last step is
 * the stored pack itself.
 */
export function replaySteps(pack: Pick<EvidencePack, "base_tf" | "bars" | "as_of" | "matched_index">): string[] {
  const ms = TF_MS[pack.base_tf];
  if (!ms || !REPLAY_TFS.has(pack.base_tf)) return [];
  const last = Date.parse(pack.as_of.bar_close);
  const out: string[] = [];
  for (const t of pack.bars.t) {
    const close = Date.parse(t) + ms;
    if (Number.isFinite(close) && close <= last) out.push(new Date(close).toISOString());
  }
  return out;
}

/** EV3: the replay step after moving `delta` bars from `current` (null = the stored pack, the last step); null again at the match. */
export function stepReplay(current: number | null, delta: number, count: number): number | null {
  if (count < 2) return null;
  const next = Math.min(count - 1, Math.max(0, (current ?? count - 1) + delta));
  return next >= count - 1 ? null : next;
}

/** EV3: a trend chip's tone - up / down / neither (the tag itself is always shown as text, never by colour alone). */
export function trendTone(tag: string | null): "up" | "down" | "flat" {
  const t = (tag ?? "").toUpperCase();
  return t === "UP" ? "up" : t === "DOWN" ? "down" : "flat";
}

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

/** The verify strip rows: failed conditions only with `showFailed`. SCR-FIX B5: a row of an OR group says so ("One of:"),
 * so the table reads like the screen's text; the text already carries a NOT the screen puts over its group. */
export function verifyRows(pack: EvidencePack, showFailed: boolean): EvidenceRow[] {
  return pack.rows.filter((r) => showFailed || r.passed !== false)
    .map((r) => (r.group?.op === "ANY" ? { ...r, text: `One of: ${r.text}` } : r));
}

/** The comparison and logic signs a condition is written with, and the words a trader reads (longest sign first). */
const SIGN_WORDS: [RegExp, string][] = [
  [/\s*>=\s*/g, " at or above "], [/\s*<=\s*/g, " at or below "], [/\s*==\s*/g, " is "], [/\s*!=\s*/g, " is not "],
  [/\s*>\s*/g, " above "], [/\s*<\s*/g, " below "],
  [/\bAND\b/g, "and"], [/\bOR\b/g, "or"], [/\bNOT\b/g, "not"],
];

/**
 * SCR-FIX D6 / D7: a condition or a verify line in plain words for the default view - "RSI(14) 63.6 above 50", not
 * "RSI(14) 63.6 > 50"; the quotes around a choice go ("NearZone(demand, 20)"); CrossAbove(a, b) reads "a crosses above b".
 * The numbers and names are kept exactly as the engine wrote them. The code itself stays one tap away, under "Advanced".
 */
export function plainCondition(text: string): string {
  let t = text.replace(/"([^"]*)"/g, "$1").trim();
  const cross = /^Cross(Above|Below)\((.*)\)$/.exec(t);
  if (cross) {
    // split the two arguments at the comma outside any brackets: CrossAbove(EMA(close, 20), SMA(close, 50))
    let depth = 0;
    const at = [...cross[2]].findIndex((c) => { depth += c === "(" ? 1 : c === ")" ? -1 : 0; return c === "," && depth === 0; });
    if (at > 0) t = `${cross[2].slice(0, at).trim()} crosses ${cross[1].toLowerCase()} ${cross[2].slice(at + 1).trim()}`;
  }
  for (const [sign, words] of SIGN_WORDS) t = t.replace(sign, words);
  return t.replace(/\s+/g, " ").trim();
}


/** SCR-FIX B6: the replay controls show only when the evidence can be replayed - the server says so up front
 * (`replay_available`; absent on an older server) and no replay request was refused - and there is a bar to step to. */
export function canReplay(pack: Pick<EvidencePack, "replay_available">, steps: number, refused: boolean): boolean {
  return steps > 1 && pack.replay_available !== false && !refused;
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
      (d.levels ?? []).forEach((lv, li) => {
        shapes.push({ ...base, kind: "line", key: `${d.id}-l${li}`, x1: left, x2: right, y1: yOf(lv.value), y2: yOf(lv.value), dash: "6 4" });
        if (d.chain) shapes.push({ ...base, kind: "text", key: `${d.id}-lt${li}`, x: left + 4, y: yOf(lv.value) - 3, text: `${lv.label} ${lv.value_text}` });
      });
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

// ------------------------------------------------------------------------------------------- SCR-FIX C1 / C2 / C4
/** The Charts page's timeframe for a screen's timeframe (null: the Charts page has no such timeframe). */
const CHART_TF: Record<string, TimeframeId> = {
  "1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "60min", "1d": "day", "1w": "week", "1M": "month",
};
export function chartTimeframe(baseTf: string): TimeframeId | null {
  return CHART_TF[baseTf] ?? null;
}

/** The pack's bars as the chart's bars (a bar with a missing price is left out; a missing volume is 0). */
export function packCandles(pack: Pick<EvidencePack, "bars">): ChartBar[] {
  const { t, o, h, l, c, v } = pack.bars;
  const out: ChartBar[] = [];
  for (let i = 0; i < t.length; i++) {
    const [op, hi, lo, cl] = [o[i], h[i], l[i], c[i]];
    if (!finite(op) || !finite(hi) || !finite(lo) || !finite(cl)) continue;
    const vol = v?.[i];
    out.push({ timestamp: t[i], open: op, high: hi, low: lo, close: cl, volume: finite(vol) ? vol : 0 });
  }
  return out;
}

/** C2: the matched bar's own time and prices, exactly as the pack has them (null when the index is outside the bars). */
export interface MatchedBar { time: string; open: number; high: number; low: number; close: number; volume: number | null }
export function matchedBar(pack: Pick<EvidencePack, "bars" | "matched_index">): MatchedBar | null {
  const i = pack.matched_index;
  const { t, o, h, l, c, v } = pack.bars;
  const [op, hi, lo, cl, vol] = [o[i], h[i], l[i], c[i], v?.[i]];
  if (i < 0 || i >= t.length || !finite(op) || !finite(hi) || !finite(lo) || !finite(cl)) return null;
  return { time: t[i], open: op, high: hi, low: lo, close: cl, volume: finite(vol) ? vol : null };
}

/** A price with two decimals and Indian grouping ("1,23,456.70"). */
export function priceText(p: number): string {
  return p.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/**
 * C1: the pack's drawables in the shapes the Charts page's chart takes - the screen's thresholds as level lines, its
 * series as study lines (price overlays on the price pane, an oscillator in its own pane with its thresholds as flat
 * lines), zone bands as bands, a breakout box as its four edges (segments: the chart has no box primitive), highlighted
 * bars as markers, and the matched bar marked. Only the pack's own drawables (owner rule 0).
 */
export interface ChartLayers { levels: LevelLine[]; studies: { id: string; line: StudyLine }[]; layer: LayerData; markers: ChartMarker[] }
export function evidenceChartLayers(pack: EvidencePack, drawables: readonly EvidenceDrawable[], highlight: string | null = null): ChartLayers {
  const sec = (iso: string | undefined) => (iso ? Math.floor(Date.parse(iso) / 1000) : NaN);
  const t = pack.bars.t;
  const n = t.length;
  const at = (i: number | undefined, fallback: number) => sec(t[Math.max(0, Math.min(n - 1, i ?? fallback))]);
  const levels: LevelLine[] = [];
  const studies: { id: string; line: StudyLine }[] = [];
  const bands: NonNullable<LayerData["bands"]> = [];
  const segments: LayerSegment[] = [];
  const markers: ChartMarker[] = [];
  const fade = (id: string, color: string) => (highlight && id !== highlight ? `${color}55` : color);
  for (const d of drawables) {
    const color = fade(d.id, drawableColor(d, drawables));
    const own = d.pane !== "price";
    (d.series ?? []).forEach((s, si) => {
      const data = s.values.slice(0, n).flatMap((v, i) => (finite(v) && Number.isFinite(sec(t[i])) ? [{ time: sec(t[i]), value: v }] : []));
      if (data.length) studies.push({ id: `ev-${d.id}-s${si}`, line: { data, color, pane: own ? "own" : "price", title: si === 0 ? d.label : s.name } });
    });
    (d.levels ?? []).forEach((lv, li) => {
      if (!finite(lv.value)) return;
      const title = `${lv.label} ${lv.value_text}`.trim();
      if (own) {
        // an oscillator's threshold: a flat line in its pane across the window
        const data = t.flatMap((x) => (Number.isFinite(sec(x)) ? [{ time: sec(x), value: lv.value }] : []));
        if (data.length) studies.push({ id: `ev-${d.id}-l${li}`, line: { data, color, pane: "own", title } });
      } else levels.push({ price: lv.value, title, tone: "neutral" });
    });
    if (d.kind === "band" && finite(d.low) && finite(d.high)) {
      bands.push({ low: d.low, high: d.high, color, opacity: 0.18, label: d.label });
    } else if (d.kind === "box" && finite(d.low) && finite(d.high)) {
      const [t1, t2, hi, lo] = [at(d.from, 0), at(d.to, n - 1), d.high, d.low];
      const edges: [number, number, number, number][] = [[t1, hi, t2, hi], [t1, lo, t2, lo], [t1, lo, t1, hi], [t2, lo, t2, hi]];
      for (const [a1, p1, a2, p2] of edges) segments.push({ t1: a1, p1, t2: a2, p2, color, width: 1, style: "solid" });
    }
    if ((d.kind === "box" || d.kind === "bars") && finite(d.level)) levels.push({ price: d.level, title: d.label, tone: "neutral" });
    if (d.kind === "bars") {
      for (const i of d.index ?? []) if (i >= 0 && i < n) markers.push({ timestamp: t[i], position: "belowBar", color, shape: "circle", text: "" });
    }
  }
  const m = pack.matched_index;
  if (m >= 0 && m < n) markers.push({ timestamp: t[m], position: "aboveBar", color: "fg", shape: "arrowDown", text: "Matched" });
  return { levels, studies, layer: { bands, segments }, markers: markers.sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp)) };
}

/**
 * C4: what the page offers by "Send to my Telegram": nothing when the server has no bot (or the evidence is not the
 * caller's, or it is a replay), "connect" when the caller's chat is not linked yet (the connect row in place), else
 * "send".
 */
export type TelegramAction = "hidden" | "connect" | "send";
export function telegramAction(pack: Pick<EvidencePack, "can_send" | "telegram_linked" | "telegram_reason" | "replay">): TelegramAction {
  if (!pack.can_send || pack.replay || pack.telegram_reason === "bot_not_configured" || pack.telegram_reason === "not_owner") return "hidden";
  if (pack.telegram_linked === false && pack.telegram_reason === "not_linked") return "connect";
  return "send";
}

/**
 * C4: "connect, then send by itself, exactly once". Armed when the trader linked Telegram from the evidence panel; the
 * first reload that shows the chat linked sends once for that evidence, and never again (a second link, a re-render or
 * a reload does not send twice).
 */
export class SendAfterLink {
  private armed = false;
  private readonly sent = new Set<number>();
  arm(): void { this.armed = true; }
  disarm(): void { this.armed = false; }
  /** True exactly once per evidence: armed and the pack now says the chat is linked. */
  take(evidenceId: number | null, pack: Pick<EvidencePack, "telegram_linked" | "can_send" | "replay"> | null): boolean {
    if (!this.armed || evidenceId == null || !pack || !pack.can_send || pack.replay || pack.telegram_linked !== true || this.sent.has(evidenceId)) return false;
    this.armed = false;
    this.sent.add(evidenceId);
    return true;
  }
}

const REASONS: readonly TelegramReason[] =["not_linked", "bot_not_configured", "blocked_by_user", "send_failed", "not_owner"];

/** C4: the reason code and sentence of a refused send (the 409's `detail`), from the response body. */
export function sendRefusal(body: string): { reason: TelegramReason | null; message: string | null } {
  try {
    const detail = (JSON.parse(body) as { detail?: unknown }).detail as { reason?: unknown; message?: unknown } | undefined;
    if (detail && typeof detail === "object") {
      return { reason: REASONS.find((r) => r === detail.reason) ?? null, message: typeof detail.message === "string" ? detail.message : null };
    }
  } catch { /* intentional: not JSON */ }
  return { reason: null, message: null };
}
