/**
 * N4 CH6: the browser's study math - pure functions over plain arrays, run in a WebWorker (`studyWorker.ts`) so the
 * page never waits on them. The studies themselves are computed server-side by the screener's own runtime (one
 * computation library for screens, alerts, the backtest and the chart); the browser only turns those values into
 * chart series, and keeps three kernels - SMA, EMA, RSI with the backend's exact conventions (pandas `rolling` /
 * `ewm(adjust=False)`, Wilder's RSI) - checked value for value against the backend on `__fixtures__/study_parity.json`.
 * Every function is O(n) with no allocation per bar beyond its output: 50,000 bars in well under a second.
 */

export type Num = number | null;

/** Simple moving average over `period` values; null until `period` values exist (pandas rolling(min_periods=period)). */
export function sma(xs: readonly number[], period: number): Num[] {
  const out: Num[] = new Array(xs.length).fill(null);
  let sum = 0;
  for (let i = 0; i < xs.length; i++) {
    sum += xs[i];
    if (i >= period) sum -= xs[i - period];
    if (i >= period - 1) out[i] = sum / period;
  }
  return out;
}

/** Exponential moving average, alpha = 2 / (period + 1), seeded with the first value (pandas ewm(span, adjust=False,
 * min_periods=period)): null for the first period - 1 values. */
export function ema(xs: readonly number[], period: number): Num[] {
  const out: Num[] = new Array(xs.length).fill(null);
  const a = 2 / (period + 1);
  let y = NaN;
  for (let i = 0; i < xs.length; i++) {
    y = i === 0 ? xs[0] : (1 - a) * y + a * xs[i];
    if (i >= period - 1) out[i] = y;
  }
  return out;
}

/** Wilder's RSI over closes (alpha = 1 / period on gains and losses, the first change starts the averages; null until
 * `period` changes exist; 100 when there was no loss) - app/indicators/momentum.rsi. */
export function rsi(closes: readonly number[], period = 14): Num[] {
  const out: Num[] = new Array(closes.length).fill(null);
  const a = 1 / period;
  let g = NaN, l = NaN;
  for (let i = 1; i < closes.length; i++) {
    const d = closes[i] - closes[i - 1];
    const gain = d > 0 ? d : 0, loss = d < 0 ? -d : 0;
    if (i === 1) { g = gain; l = loss; } else { g = (1 - a) * g + a * gain; l = (1 - a) * l + a * loss; }
    if (i >= period) out[i] = l === 0 ? 100 : 100 - 100 / (1 + g / l);
  }
  return out;
}

export interface LinePoint { time: number; value: number }

/** Server times + values -> the chart's line data (a null value is a gap: no point). */
export function toLineData(times: readonly number[], values: readonly Num[]): LinePoint[] {
  const out: LinePoint[] = [];
  const n = Math.min(times.length, values.length);
  for (let i = 0; i < n; i++) {
    const v = values[i];
    if (v != null && Number.isFinite(v)) out.push({ time: times[i], value: v });
  }
  return out;
}

/** Fire times -> the close of the candle at each (for a mark on the price); a time with no candle is skipped. */
export function firesAt(fires: readonly number[], candleTimes: readonly number[], closes: readonly number[]): LinePoint[] {
  const at = new Map<number, number>();
  for (let i = 0; i < candleTimes.length; i++) at.set(candleTimes[i], closes[i]);
  const out: LinePoint[] = [];
  for (const t of fires) {
    const c = at.get(t);
    if (c != null) out.push({ time: t, value: c });
  }
  return out;
}

export type MathRequest =
  | { op: "line"; times: number[]; values: Num[] }
  | { op: "fires"; fires: number[]; candleTimes: number[]; closes: number[] }
  | { op: "sma" | "ema"; xs: number[]; period: number }
  | { op: "rsi"; xs: number[]; period: number };

/** One request, as the worker answers it (also the main-thread fallback). */
export function runMath(req: MathRequest): LinePoint[] | Num[] {
  switch (req.op) {
    case "line": return toLineData(req.times, req.values);
    case "fires": return firesAt(req.fires, req.candleTimes, req.closes);
    case "sma": return sma(req.xs, req.period);
    case "ema": return ema(req.xs, req.period);
    case "rsi": return rsi(req.xs, req.period);
  }
}
