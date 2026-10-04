import type { OHLCVBar } from "../types";

/**
 * Phase AN: indicator math for the Pro Chart, written to match the backend's pandas formulas
 * (`app/indicators`): EMA = ewm(span, adjust=False), Wilder smoothing = ewm(alpha=1/period,
 * adjust=False) for RSI / ATR / ADX, rolling SMA and sample-std Bollinger bands, the standard
 * Supertrend band flip, and a session-anchored VWAP (reset at each IST trading day). Every function
 * returns one value per candle, `null` while the indicator is still warming up, so a series lines
 * up with the candle it describes. These draw what the strategy engine sees; the signals themselves
 * still come from the backend.
 */

export type Series = (number | null)[];

const isNum = (v: number | null | undefined): v is number => v != null && Number.isFinite(v);

export function ema(values: Series, period: number): Series {
  const out: Series = new Array(values.length).fill(null);
  if (period <= 0) return out;
  const alpha = 2 / (period + 1);
  let y: number | null = null;
  let seen = 0;
  for (let i = 0; i < values.length; i++) {
    const x = values[i];
    if (!isNum(x)) { out[i] = null; continue; }
    y = y == null ? x : alpha * x + (1 - alpha) * y;
    seen += 1;
    out[i] = seen >= period ? y : null;
  }
  return out;
}

/** Wilder's smoothing: alpha = 1/period, seeded on the first value (pandas ewm adjust=False). */
export function wilder(values: Series, period: number): Series {
  const out: Series = new Array(values.length).fill(null);
  if (period <= 0) return out;
  const alpha = 1 / period;
  let y: number | null = null;
  let seen = 0;
  for (let i = 0; i < values.length; i++) {
    const x = values[i];
    if (!isNum(x)) { out[i] = null; continue; }
    y = y == null ? x : alpha * x + (1 - alpha) * y;
    seen += 1;
    out[i] = seen >= period ? y : null;
  }
  return out;
}

export function sma(values: Series, period: number): Series {
  const out: Series = new Array(values.length).fill(null);
  if (period <= 0) return out;
  let sum = 0;
  const window: number[] = [];
  for (let i = 0; i < values.length; i++) {
    const x = values[i];
    if (!isNum(x)) { out[i] = null; continue; }
    window.push(x); sum += x;
    if (window.length > period) sum -= window.shift() as number;
    out[i] = window.length === period ? sum / period : null;
  }
  return out;
}

export function trueRange(c: OHLCVBar[]): Series {
  return c.map((b, i) => {
    if (i === 0) return b.high - b.low;
    const pc = c[i - 1].close;
    return Math.max(b.high - b.low, Math.abs(b.high - pc), Math.abs(b.low - pc));
  });
}

export function atr(c: OHLCVBar[], period = 14): Series {
  return wilder(trueRange(c), period);
}

export function rsi(close: Series, period = 14): Series {
  const gains: Series = [null];
  const losses: Series = [null];
  for (let i = 1; i < close.length; i++) {
    const a = close[i], b = close[i - 1];
    if (!isNum(a) || !isNum(b)) { gains.push(null); losses.push(null); continue; }
    const d = a - b;
    gains.push(Math.max(d, 0)); losses.push(Math.max(-d, 0));
  }
  const ag = wilder(gains, period), al = wilder(losses, period);
  return close.map((_, i) => {
    const g = ag[i], l = al[i];
    if (!isNum(g) || !isNum(l)) return null;
    if (l === 0) return 100;
    return 100 - 100 / (1 + g / l);
  });
}

export function adx(c: OHLCVBar[], period = 14): { adx: Series; plusDi: Series; minusDi: Series } {
  const n = c.length;
  const plusDm: Series = [null], minusDm: Series = [null];
  for (let i = 1; i < n; i++) {
    const up = c[i].high - c[i - 1].high, down = c[i - 1].low - c[i].low;
    plusDm.push(up > down && up > 0 ? up : 0);
    minusDm.push(down > up && down > 0 ? down : 0);
  }
  const tr = trueRange(c); tr[0] = null;
  const sTr = wilder(tr, period), sP = wilder(plusDm, period), sM = wilder(minusDm, period);
  const plusDi: Series = [], minusDi: Series = [], dx: Series = [];
  for (let i = 0; i < n; i++) {
    const t = sTr[i], p = sP[i], m = sM[i];
    if (!isNum(t) || !isNum(p) || !isNum(m) || t === 0) { plusDi.push(null); minusDi.push(null); dx.push(null); continue; }
    const pd = 100 * (p / t), md = 100 * (m / t);
    plusDi.push(pd); minusDi.push(md);
    const sum = pd + md;
    dx.push(sum === 0 ? null : (100 * Math.abs(pd - md)) / sum);
  }
  return { adx: wilder(dx, period), plusDi, minusDi };
}

export function supertrend(c: OHLCVBar[], period = 10, multiplier = 3): { line: Series; direction: (1 | -1 | null)[] } {
  const n = c.length;
  const a = atr(c, period);
  const line: Series = new Array(n).fill(null);
  const direction: (1 | -1 | null)[] = new Array(n).fill(null);
  let finalUpper = NaN, finalLower = NaN, trend: 1 | -1 = 1;
  for (let i = 0; i < n; i++) {
    const at = a[i];
    if (!isNum(at)) continue;
    const hl2 = (c[i].high + c[i].low) / 2;
    const bu = hl2 + multiplier * at, bl = hl2 - multiplier * at;
    const prevClose = i > 0 ? c[i - 1].close : c[i].close;
    const fu = Number.isNaN(finalUpper) || bu < finalUpper || prevClose > finalUpper ? bu : finalUpper;
    const fl = Number.isNaN(finalLower) || bl > finalLower || prevClose < finalLower ? bl : finalLower;
    if (!Number.isNaN(finalUpper)) {
      if (trend === 1 && c[i].close < fl) trend = -1;
      else if (trend === -1 && c[i].close > fu) trend = 1;
    }
    finalUpper = fu; finalLower = fl;
    line[i] = trend === 1 ? fl : fu;
    direction[i] = trend;
  }
  return { line, direction };
}

/** Session VWAP: cumulative typical-price × volume over volume, restarted on each IST calendar day. */
export function vwap(c: OHLCVBar[]): Series {
  const out: Series = [];
  let pv = 0, vol = 0, day = "";
  for (const b of c) {
    const d = new Date(b.timestamp).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata" });
    if (d !== day) { day = d; pv = 0; vol = 0; }
    const tp = (b.high + b.low + b.close) / 3;
    pv += tp * (b.volume || 0); vol += b.volume || 0;
    out.push(vol > 0 ? pv / vol : null);
  }
  return out;
}

export function bollinger(close: Series, period = 20, k = 2): { mid: Series; upper: Series; lower: Series } {
  const mid = sma(close, period);
  const upper: Series = [], lower: Series = [];
  const window: number[] = [];
  for (let i = 0; i < close.length; i++) {
    const x = close[i];
    if (isNum(x)) { window.push(x); if (window.length > period) window.shift(); }
    const m = mid[i];
    if (!isNum(m) || window.length < period || period < 2) { upper.push(null); lower.push(null); continue; }
    const variance = window.reduce((s, v) => s + (v - m) * (v - m), 0) / (period - 1);
    const sd = Math.sqrt(variance);
    upper.push(m + k * sd); lower.push(m - k * sd);
  }
  return { mid, upper, lower };
}

export const closes = (c: OHLCVBar[]): Series => c.map((b) => b.close);

// ---- indicator catalogue -------------------------------------------------------------------------

export type IndicatorId = "ema_fast" | "ema_slow" | "sma" | "bollinger" | "vwap" | "supertrend" | "volume" | "rsi" | "adx";

export interface IndicatorSettings {
  emaFast: number; emaSlow: number; smaPeriod: number; bbPeriod: number; bbK: number;
  stPeriod: number; stMult: number; rsiPeriod: number; rsiMid: number; rsiHigh: number; rsiLow: number; adxPeriod: number; adxMin: number;
}

export const DEFAULT_SETTINGS: IndicatorSettings = {
  emaFast: 9, emaSlow: 21, smaPeriod: 50, bbPeriod: 20, bbK: 2, stPeriod: 10, stMult: 3,
  rsiPeriod: 14, rsiMid: 50, rsiHigh: 70, rsiLow: 30, adxPeriod: 14, adxMin: 20,
};

export const INDICATOR_LABELS: Record<IndicatorId, string> = {
  ema_fast: "EMA fast", ema_slow: "EMA slow", sma: "SMA", bollinger: "Bollinger", vwap: "VWAP", supertrend: "Supertrend",
  volume: "Volume", rsi: "RSI", adx: "ADX",
};

export const OVERLAYS: IndicatorId[] = ["ema_fast", "ema_slow", "sma", "bollinger", "vwap", "supertrend"];
export const PANES: IndicatorId[] = ["volume", "rsi", "adx"];

const num = (v: unknown, fallback: number): number => (typeof v === "number" && Number.isFinite(v) ? v : fallback);

/** The indicators a strategy actually reads, from its default parameters (`StrategyInfo.default_params`),
 * so the chart shows the same lines the engine decided on. Unknown strategies get EMA 9/21 + volume. */
export function indicatorsForStrategy(params?: Record<string, unknown> | null): { ids: IndicatorId[]; settings: IndicatorSettings } {
  const s: IndicatorSettings = { ...DEFAULT_SETTINGS };
  const ids: IndicatorId[] = [];
  if (!params) return { ids: ["ema_fast", "ema_slow", "volume"], settings: s };
  if ("ema_fast" in params || "ema_slow" in params) {
    s.emaFast = num(params.ema_fast, s.emaFast); s.emaSlow = num(params.ema_slow, s.emaSlow);
    ids.push("ema_fast", "ema_slow");
  }
  if ("ema_trend" in params) {
    // Phase AO MACD + EMA trend: the trend EMA is the line the strategy filters on.
    s.emaSlow = num(params.ema_trend, s.emaSlow);
    if (!ids.includes("ema_slow")) ids.push("ema_slow");
  }
  if ("bb_period" in params) {
    s.bbPeriod = num(params.bb_period, s.bbPeriod); s.bbK = num(params.bb_k, s.bbK);
    ids.push("bollinger");
  }
  if (typeof params.__id === "string" && (params.__id.includes("vwap") || params.__id.startsWith("orb"))) ids.push("vwap");
  if ("st_period" in params || "st_mult" in params) {
    s.stPeriod = num(params.st_period, s.stPeriod); s.stMult = num(params.st_mult, s.stMult);
    ids.push("supertrend");
  }
  ids.push("volume");
  if ("rsi_period" in params) {
    s.rsiPeriod = num(params.rsi_period, s.rsiPeriod); s.rsiMid = num(params.rsi_mid, s.rsiMid);
    s.rsiLow = num(params.rsi_long_trigger ?? params.rsi_low, s.rsiLow); s.rsiHigh = num(params.rsi_short_trigger ?? params.rsi_high, s.rsiHigh);
    ids.push("rsi");
  }
  if ("adx_period" in params) {
    s.adxPeriod = num(params.adx_period, s.adxPeriod); s.adxMin = num(params.adx_min, s.adxMin);
    ids.push("adx");
  }
  if (ids.length === 1) ids.unshift("ema_fast", "ema_slow");
  return { ids, settings: s };
}

/** Minutes per bar for the platform's timeframe labels; `day` is a trading day. */
export function timeframeMinutes(tf: string | undefined): number {
  if (!tf) return 1;
  if (tf === "day" || tf === "1d") return 24 * 60;
  const m = /^(\d+)\s*(min|m)$/i.exec(tf.trim());
  return m ? Number(m[1]) : 1;
}

/** Fold a live last price into the candle series: a new bar when the price falls in a later
 * timeframe bucket than the last candle, else the last candle's close/high/low move. Pure. */
export function applyLivePrice(candles: OHLCVBar[], ltp: number | null | undefined, at: string | null | undefined, timeframe: string | undefined): OHLCVBar[] {
  if (candles.length === 0 || !isNum(ltp ?? null)) return candles;
  const price = ltp as number;
  const last = candles[candles.length - 1];
  const lastMs = new Date(last.timestamp).getTime();
  const bucketMs = timeframeMinutes(timeframe) * 60_000;
  const atMs = at ? new Date(at).getTime() : Date.now();
  if (atMs < lastMs) return candles;
  if (atMs >= lastMs + bucketMs && bucketMs < 24 * 60 * 60_000) {
    const start = lastMs + Math.floor((atMs - lastMs) / bucketMs) * bucketMs;
    return [...candles, { timestamp: new Date(start).toISOString(), open: price, high: price, low: price, close: price, volume: 0 }];
  }
  const updated: OHLCVBar = { ...last, close: price, high: Math.max(last.high, price), low: Math.min(last.low, price) };
  return [...candles.slice(0, -1), updated];
}
