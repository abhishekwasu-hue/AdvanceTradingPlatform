/**
 * CH5-b: chart types as pure functions over bars (epoch-second START times, as the chart shows them), tested with
 * hand-checked cases and goldens - no chart library, no React.
 *
 * - Heikin Ashi: one bar per source bar, same time; bar i depends on bars 0..i only.
 * - Renko (close-based, fixed box) and range bars (fixed high-low span) are synthetic: their own bricks with their own
 *   times. They are built from CLOSED source bars only - a forming bar is dropped before they are computed - and only
 *   completed bricks are returned, so a brick, once shown, never changes or disappears as data arrives
 *   (truncation-invariant: the bricks of a prefix of the bars are a prefix of the bricks of all of them).
 * - Hollow candles: the candle's own data with colours (filled when it closed below its open, hollow otherwise; green
 *   or red by its close against the previous close).
 * - Line / area: the close.
 * Session breaks and the time axis: the first bar of each session in the venue's own time zone (IST for Indian venues,
 * UTC for 24x7 crypto venues), and tick labels in that zone.
 */
import type { ChartType } from "./workspace";

export interface OhlcBar { time: number; open: number; high: number; low: number; close: number }
/** A candle as the chart takes it; colours only for hollow candles. */
export interface CandlePoint extends OhlcBar { color?: string; borderColor?: string; wickColor?: string }

export const SYNTHETIC: ReadonlySet<ChartType> = new Set<ChartType>(["renko", "range"]);
export const LINE_TYPES: ReadonlySet<ChartType> = new Set<ChartType>(["line", "area"]);
export const CHART_TYPE_LABELS: Record<ChartType, string> = {
  candles: "Candles", hollow: "Hollow candles", heikin_ashi: "Heikin Ashi", renko: "Renko", range: "Range", line: "Line", area: "Area",
};

const round = (v: number) => Math.round(v * 1e8) / 1e8;

/** Heikin Ashi: close = (O+H+L+C)/4; open = (previous HA open + previous HA close)/2, the first (O+C)/2;
 * high / low include the HA open and close. */
export function heikinAshi(bars: readonly OhlcBar[]): OhlcBar[] {
  const out: OhlcBar[] = [];
  for (let i = 0; i < bars.length; i++) {
    const b = bars[i];
    const close = (b.open + b.high + b.low + b.close) / 4;
    const open = i === 0 ? (b.open + b.close) / 2 : (out[i - 1].open + out[i - 1].close) / 2;
    out.push({ time: b.time, open: round(open), high: round(Math.max(b.high, open, close)), low: round(Math.min(b.low, open, close)), close: round(close) });
  }
  return out;
}

/** Bars whose period has ended at `nowSec` (a bar starting at `time` closes at `time + periodSec`). Only trailing
 * bars can be forming, so the result is a prefix. */
export function closedBars<T extends { time: number }>(bars: readonly T[], periodSec: number, nowSec: number): T[] {
  let n = bars.length;
  while (n > 0 && bars[n - 1].time + periodSec > nowSec) n -= 1;
  return bars.slice(0, n);
}

/** Seconds per bar of a timeframe ("5min" -> 300, "day" -> 86400; unknown -> 60). */
export function periodSeconds(timeframe: string | undefined): number {
  if (timeframe === "day") return 86_400;
  const m = /^(\d+)\s*(?:m|min)$/.exec((timeframe ?? "").trim());
  return m ? Number(m[1]) * 60 : 60;
}

/** The largest 1 / 2 / 2.5 / 5 x 10^k step not above `value` (a round box size). */
export function niceStep(value: number): number {
  if (!(value > 0) || !Number.isFinite(value)) return 1;
  const p = 10 ** Math.floor(Math.log10(value));
  for (const m of [5, 2.5, 2, 1]) if (m * p <= value * (1 + 1e-12)) return round(m * p);
  return p;
}

/** Default box / range: a round 0.5 % of the FIRST bar's close - fixed by the start of the data, so appending bars
 * never changes it (a box sized on the latest ATR would redraw every brick). */
export function autoBoxSize(bars: readonly OhlcBar[], fraction = 0.005): number | null {
  const first = bars.find((b) => Number.isFinite(b.close) && b.close > 0);
  return first ? niceStep(first.close * fraction) : null;
}

/** Strictly increasing times for several bricks completed inside one source bar: the bar's time, then +1 s each. */
function stamp(out: OhlcBar[], time: number): number {
  const prev = out.length ? out[out.length - 1].time : -Infinity;
  return Math.max(time, prev + 1);
}

/** Close-based Renko: an up brick when the close is a full box above the last brick's top, a down brick a box below
 * its bottom; a reversal needs a full box beyond the last brick's other side (two boxes from its close). The first
 * brick is measured from the first close. Pass CLOSED bars only (`closedBars`); completed bricks only. */
export function renko(bars: readonly OhlcBar[], box: number): OhlcBar[] {
  const out: OhlcBar[] = [];
  if (!(box > 0) || bars.length === 0) return out;
  let top = bars[0].close, bottom = bars[0].close;          // the last brick's body (before any brick: the anchor)
  for (const b of bars) {
    const c = b.close;
    for (;;) {
      if (c >= top + box - 1e-9) {
        out.push({ time: stamp(out, b.time), open: round(top), high: round(top + box), low: round(top), close: round(top + box) });
        bottom = top; top = top + box;
      } else if (c <= bottom - box + 1e-9) {
        out.push({ time: stamp(out, b.time), open: round(bottom), high: round(bottom), low: round(bottom - box), close: round(bottom - box) });
        top = bottom; bottom = bottom - box;
      } else break;
    }
  }
  return out;
}

/** The price path assumed inside a bar from its OHLC: up bars go open -> low -> high -> close, down bars open -> high
 * -> low -> close (the conventional order; the real order needs ticks). */
export function barPath(b: OhlcBar): number[] {
  return b.close >= b.open ? [b.open, b.low, b.high, b.close] : [b.open, b.high, b.low, b.close];
}

/** Range bars: each spans exactly `range` from its high to its low; the next opens where the last closed. Built along
 * `barPath` of CLOSED bars; completed range bars only (the one still filling is never returned). */
export function rangeBars(bars: readonly OhlcBar[], range: number): OhlcBar[] {
  const out: OhlcBar[] = [];
  if (!(range > 0) || bars.length === 0) return out;
  let open = bars[0].open, high = open, low = open;
  for (const b of bars) {
    for (const p of barPath(b)) {
      for (;;) {
        if (p >= low + range - 1e-9) {                             // moving up through the top of the range
          const close = low + range;
          out.push({ time: stamp(out, b.time), open: round(open), high: round(close), low: round(low), close: round(close) });
          open = close; high = close; low = close;
        } else if (p <= high - range + 1e-9) {                     // moving down through the bottom
          const close = high - range;
          out.push({ time: stamp(out, b.time), open: round(open), high: round(high), low: round(close), close: round(close) });
          open = close; high = close; low = close;
        } else break;
      }
      high = Math.max(high, p);
      low = Math.min(low, p);
    }
  }
  return out;
}

/** Hollow candles: hollow (transparent body) when close >= open, filled otherwise; up colour when the close is at or
 * above the previous close, down colour below it (the first bar: against its own open). */
export function hollowCandles(bars: readonly OhlcBar[], up: string, down: string): CandlePoint[] {
  return bars.map((b, i) => {
    const ref = i === 0 ? b.open : bars[i - 1].close;
    const color = b.close >= ref ? up : down;
    return { ...b, color: b.close >= b.open ? "rgba(0,0,0,0)" : color, borderColor: color, wickColor: color };
  });
}

export interface ChartView {
  /** The bars the price series shows (source times, or the bricks' own times for synthetic types). */
  bars: CandlePoint[];
  /** Renko / range: the times are the bricks', not the source bars' - indicators and volume do not apply. */
  synthetic: boolean;
  /** Line / area: drawn from the closes. */
  line: boolean;
  /** Renko box / range size used (null for other types). */
  size: number | null;
}

/** What the chart shows for a chart type. `liveBars` may carry a forming last bar (candles, hollow, Heikin Ashi, line
 * and area show it as it forms); `sourceBars` are the bars as received, from which Renko and range drop every bar not
 * closed at `nowSec` before they are built. */
export function chartView(type: ChartType, liveBars: readonly OhlcBar[], sourceBars: readonly OhlcBar[], opts: {
  periodSec: number; nowSec: number; size?: number | null; up: string; down: string;
}): ChartView {
  if (SYNTHETIC.has(type)) {
    const closed = closedBars(sourceBars, opts.periodSec, opts.nowSec);
    const size = opts.size && opts.size > 0 ? opts.size : autoBoxSize(closed);
    const bars = size == null ? [] : type === "renko" ? renko(closed, size) : rangeBars(closed, size);
    return { bars, synthetic: true, line: false, size };
  }
  const plain = liveBars.map((b) => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close }));
  if (type === "heikin_ashi") return { bars: heikinAshi(plain), synthetic: false, line: false, size: null };
  if (type === "hollow") return { bars: hollowCandles(plain, opts.up, opts.down), synthetic: false, line: false, size: null };
  return { bars: plain, synthetic: false, line: LINE_TYPES.has(type), size: null };
}

// --- sessions and the time axis --------------------------------------------------------------------------------------
const UTC_VENUES = new Set(["CRYPTO", "BINANCE", "COINDCX", "WAZIRX"]);

/** The zone a venue's sessions and axis are read in: UTC for 24x7 crypto venues, IST for Indian venues. */
export function venueZone(exchange: string | undefined): "UTC" | "Asia/Kolkata" {
  return UTC_VENUES.has((exchange ?? "").toUpperCase()) ? "UTC" : "Asia/Kolkata";
}

export function is24x7(exchange: string | undefined): boolean {
  return venueZone(exchange) === "UTC";
}

const dayKey = (t: number, zone: string) => new Date(t * 1000).toLocaleDateString("en-CA", { timeZone: zone });

/** The time of the first bar of every session after the first: where the calendar date (in the venue's zone) changes
 * between two bars. Daily and longer bars have no breaks. */
export function sessionBreaks(times: readonly number[], exchange: string | undefined, periodSec: number): number[] {
  if (periodSec >= 86_400 || times.length < 2) return [];
  const zone = venueZone(exchange);
  const out: number[] = [];
  let prev = dayKey(times[0], zone);
  for (let i = 1; i < times.length; i++) {
    const k = dayKey(times[i], zone);
    if (k !== prev) out.push(times[i]);
    prev = k;
  }
  return out;
}

/** Tick label in the venue's zone: the date (with the year at a year mark) for day / month / year marks, the time
 * otherwise. `tickType` is lightweight-charts' TickMarkType (0 year, 1 month, 2 day of month, 3 time, 4 time + seconds). */
export function formatTick(t: number, tickType: number, zone: string): string {
  const d = new Date(t * 1000);
  if (tickType === 0) return d.toLocaleDateString("en-IN", { timeZone: zone, year: "numeric" });
  if (tickType === 1) return d.toLocaleDateString("en-IN", { timeZone: zone, month: "short", year: "2-digit" });
  if (tickType === 2) return d.toLocaleDateString("en-IN", { timeZone: zone, day: "2-digit", month: "short" });
  return d.toLocaleTimeString("en-IN", { timeZone: zone, hour: "2-digit", minute: "2-digit", hour12: false, ...(tickType === 4 ? { second: "2-digit" } : {}) });
}
