/**
 * F&O workspace 8b, slice 4 (owner order section 8b-k): the chart snapshot of a journal draft, drawn on the client
 * (tested in snapshot.test.tsx). The trade's journal entry already keeps the chart reference at entry and exit (the
 * symbol and the time, `chart_entry_json` / `chart_exit_json`); the snapshot is redrawn from the underlying's candles
 * (the same `POST /market-data/candles` read the chart page uses) with the moments it opened and closed marked. Nothing
 * is stored: no migration, no image upload. Also the cockpit's chart panel (8b-i) with his own strikes as lines.
 */
import type { OHLCVBar } from "../types";

export type SnapTimeframe = "5min" | "15min" | "60min" | "day";
export interface SnapWindow { timeframe: SnapTimeframe; lookbackDays: number; before: string | null }

const HOUR = 3_600_000;
const DAY = 24 * HOUR;
export const TF_MS: Record<SnapTimeframe, number> = { "5min": 5 * 60_000, "15min": 15 * 60_000, "60min": HOUR, day: DAY };
export const MAX_BARS = 160;
const INTRADAY_MAX_DAYS = 30;

/** The IST calendar date (yyyy-mm-dd) of an instant. */
export function istDay(ms: number): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(ms));
}

const nextDay = (day: string) => new Date(Date.parse(`${day}T00:00:00Z`) + DAY).toISOString().slice(0, 10);

/** Which candles to read for a trade open from `opened` to `closed`: a finer timeframe for a shorter trade, enough days
 * back to reach the entry, and - for a trade closed before today - the history up to its closing day only. */
export function snapshotWindow(opened: number, closed: number, now: number): SnapWindow {
  const span = Math.max(0, closed - opened);
  const timeframe: SnapTimeframe = span <= 6 * HOUR ? "5min" : span <= 3 * DAY ? "15min" : span <= 20 * DAY ? "60min" : "day";
  const days = Math.ceil(span / DAY) + 3;
  const lookbackDays = timeframe === "day" ? Math.min(730, days + 30) : Math.min(INTRADAY_MAX_DAYS, days);
  const closedDay = istDay(closed);
  return { timeframe, lookbackDays, before: closedDay < istDay(now) ? nextDay(closedDay) : null };
}

const ms = (b: OHLCVBar) => Date.parse(b.timestamp);

/** The bars around the trade: from a little before it opened to a little after it closed (at most MAX_BARS). */
export function cropBars(bars: readonly OHLCVBar[], opened: number, closed: number, tf: SnapTimeframe): OHLCVBar[] {
  const sorted = bars.filter((b) => Number.isFinite(ms(b))).sort((a, b) => ms(a) - ms(b));
  const pad = Math.max((closed - opened) * 0.25, 8 * TF_MS[tf]);
  let out = sorted.filter((b) => ms(b) >= opened - pad && ms(b) <= closed + pad);
  if (out.length > MAX_BARS) {
    const first = out.findIndex((b) => ms(b) >= opened - pad / 2);
    out = out.slice(Math.max(0, first), Math.max(0, first) + MAX_BARS);
  }
  return out;
}

export interface SnapCandle { x: number; w: number; open: number; close: number; high: number; low: number; rising: boolean }
export interface SnapMarker { x: number; label: string }
export interface SnapLevel { y: number; label: string }
export interface SnapGeometry {
  candles: SnapCandle[]; markers: SnapMarker[]; levels: SnapLevel[]; hi: number; lo: number;
  left: number; right: number; top: number; bottom: number;
  /** Levels that lie outside the price range drawn (named under the chart, never squeezed in). */
  outside: string[];
}

/** Where everything sits in a `width` x `height` box. `marks`: moments to mark (the bar at or after each one, else the
 * last bar); `levels`: prices to draw as lines when they lie inside the candles' range. */
export function snapshotGeometry(bars: readonly OHLCVBar[], marks: readonly { at: number; label: string }[], width: number, height: number,
                                 levels: readonly { price: number; label: string }[] = []): SnapGeometry | null {
  if (!bars.length) return null;
  const left = 4;
  const right = width - 56;
  const top = 8;
  const bottom = height - 16;
  let hi = Math.max(...bars.map((b) => b.high));
  let lo = Math.min(...bars.map((b) => b.low));
  if (!(hi > lo)) { hi += 1; lo -= 1; }
  const step = (right - left) / bars.length;
  const y = (v: number) => top + ((hi - v) / (hi - lo)) * (bottom - top);
  const candles = bars.map((b, i) => ({
    x: left + step * (i + 0.5), w: Math.max(1, Math.min(9, step * 0.6)), open: y(b.open), close: y(b.close), high: y(b.high), low: y(b.low),
    rising: b.close >= b.open,
  }));
  const markers = marks.map((m) => {
    let i = bars.findIndex((b) => ms(b) >= m.at);
    if (i < 0) i = bars.length - 1;
    return { x: candles[i].x, label: m.label };
  });
  const inside = levels.filter((l) => l.price >= lo && l.price <= hi);
  return {
    candles, markers, levels: inside.map((l) => ({ y: y(l.price), label: l.label })), hi, lo, left, right, top, bottom,
    outside: levels.filter((l) => !(l.price >= lo && l.price <= hi)).map((l) => l.label),
  };
}
