/**
 * BT-DRAW (owner 18 Nov: "strategy nusar indicator sudha draw jhale pahije"): the lines a strategy TEMPLATE itself
 * reads, computed from the backtest's own bars with the template's own settings, so "Test a strategy" draws exactly
 * what the strategy uses - never a default EMA pair. A template that reads price structure only (areas, swings,
 * breaks) gets no lines. A line read on another chart (a context timeframe) or one the chart cannot compute is named
 * under the chart instead. Pure; reference only - nothing here places, changes or proposes an order.
 */
import type { StudyLine } from "../charting/engine";
import type { OHLCVBar } from "../types";
import { adx, bollinger, ema, rsi, sma, supertrend, vwap, type Series } from "../utils/indicators";

export interface StrategyLines {
  /** Lines to draw: on the price pane or in a pane of their own. */
  studies: { id: string; line: StudyLine }[];
  /** What the strategy reads that is not drawn, in plain words ("EMA 200 on the Daily chart"). */
  named: string[];
}

const COLORS = ["#2563eb", "#d97706", "#7c3aed", "#0891b2", "#be185d", "#4d7c0f"] as const;
const TF_WORDS: Record<string, string> = { "1d": "Daily", "1w": "Weekly", "1h": "1-hour", "60min": "1-hour", day: "Daily", week: "Weekly" };

const n = (v: unknown, fallback: number): number => (typeof v === "number" && Number.isFinite(v) && v > 0 ? v : fallback);
const s = (v: unknown, fallback: string): string => (typeof v === "string" && v ? v : fallback);
const on = (v: unknown): boolean => v === true;

function stochastic(c: OHLCVBar[], kPeriod: number, smooth: number, dPeriod: number): { k: Series; d: Series } {
  const raw: Series = c.map((_, i) => {
    if (i < kPeriod - 1) return null;
    const win = c.slice(i - kPeriod + 1, i + 1);
    const hi = Math.max(...win.map((b) => b.high));
    const lo = Math.min(...win.map((b) => b.low));
    return hi === lo ? 50 : ((c[i].close - lo) / (hi - lo)) * 100;
  });
  const k = smooth > 1 ? sma(raw, smooth) : raw;
  return { k, d: sma(k, dPeriod) };
}

function donchian(c: OHLCVBar[], period: number): { upper: Series; lower: Series } {
  const upper: Series = [];
  const lower: Series = [];
  c.forEach((_, i) => {
    if (i < period) { upper.push(null); lower.push(null); return; }
    const win = c.slice(i - period, i);                       // the channel of the bars before this one (a break closes beyond it)
    upper.push(Math.max(...win.map((b) => b.high)));
    lower.push(Math.min(...win.map((b) => b.low)));
  });
  return { upper, lower };
}

/** Lines for one template id and its settings (the template's defaults merged with the settings the test ran with). */
export function templateLines(id: string, params: Record<string, unknown>, candles: OHLCVBar[]): StrategyLines {
  const times = candles.map((b) => Math.floor(Date.parse(b.timestamp) / 1000));
  const close: Series = candles.map((b) => b.close);
  const studies: StrategyLines["studies"] = [];
  const named: string[] = [];
  let colour = 0;
  const add = (key: string, title: string, values: Series, pane: "price" | "own" = "price") => {
    const data = values.flatMap((v, i) => (v != null && Number.isFinite(v) && Number.isFinite(times[i]) ? [{ time: times[i], value: v }] : []));
    if (data.length) studies.push({ id: `bt-${key}`, line: { data, color: COLORS[colour++ % COLORS.length], pane, title } });
  };
  const ma = (key: string, type: string, period: number) => {
    const t = type.toUpperCase();
    if (t === "EMA") add(key, `EMA ${period}`, ema(close, period));
    else if (t === "SMA") add(key, `SMA ${period}`, sma(close, period));
    else named.push(`${t} ${period}`);
  };
  const context = (what: string) => {
    const tf = s(params.context_timeframe, "");
    named.push(`${what} on the ${TF_WORDS[tf] ?? tf} chart`);
  };
  const maType = s(params.ma_type, "EMA");

  switch (id) {
    case "D1":
      add("st", `Supertrend ${n(params.period, 10)} / ${n(params.multiplier, 3)}`, supertrend(candles, n(params.period, 10), n(params.multiplier, 3)).line);
      if (on(params.use_context)) context("Supertrend");
      break;
    case "F1":
      if (s(params.source, "supertrend_flip").includes("supertrend")) {
        add("st", `Supertrend ${n(params.period, 10)} / ${n(params.multiplier, 3)}`, supertrend(candles, n(params.period, 10), n(params.multiplier, 3)).line);
      } else {
        ma("fast", maType, n(params.fast, 20));
        ma("slow", maType, n(params.slow, 50));
      }
      if (on(params.use_context)) context("Supertrend");
      break;
    case "D2":
      ma("fast", maType, n(params.fast, 9));
      ma("slow", maType, n(params.slow, 21));
      if (on(params.use_trend)) ma("trend", maType, n(params.trend_period, 200));
      break;
    case "D3":
      ma("fast", maType, n(params.fast, 20));
      ma("slow", maType, n(params.slow, 50));
      break;
    case "D4":
      ma("ma", s(params.ma_type, "sma"), n(params.ma_n, 50));
      break;
    case "D6": {
      const fast = n(params.fast, 12), slow = n(params.slow, 26), sig = n(params.signal, 9);
      const f = ema(close, fast), sl = ema(close, slow);
      const macd: Series = f.map((v, i) => (v != null && sl[i] != null ? v - (sl[i] as number) : null));
      add("macd", `MACD ${fast} / ${slow}`, macd, "own");
      add("macd-signal", `Signal ${sig}`, ema(macd, sig), "own");
      break;
    }
    case "D7":
      named.push(`Parabolic SAR ${n(params.start, 0.02)} / ${n(params.step, 0.02)} / ${n(params.max_step, 0.2)}`);
      break;
    case "D9":
      if (s(params.trend_by, "average") === "average") context(`${maType.toUpperCase()} ${n(params.trend_period, 30)}`);
      ma("pullback", maType, n(params.pullback_period, 20));
      break;
    case "E1":
      add("rsi", `RSI ${n(params.rsi_period, 14)}`, rsi(close, n(params.rsi_period, 14)), "own");
      if (s(params.trend_filter, "average") === "average") context(`${maType.toUpperCase()} ${n(params.trend_period, 50)}`);
      else if (s(params.trend_filter, "") === "supertrend") context(`Supertrend ${n(params.st_period, 10)} / ${n(params.st_mult, 3)}`);
      break;
    case "E3":
    case "F2": {
      const bb = bollinger(close, n(params.bb_n, 20), n(params.bb_k, 2));
      const label = `Bollinger ${n(params.bb_n, 20)} / ${n(params.bb_k, 2)}`;
      add("bb-upper", label, bb.upper);
      add("bb-mid", "Middle", bb.mid);
      add("bb-lower", "Lower", bb.lower);
      if (s(params.regime, "adx") === "adx") add("adx", `ADX ${n(params.adx_period, 14)}`, adx(candles, n(params.adx_period, 14)).adx, "own");
      break;
    }
    case "D5":
      add("adx", `ADX ${n(params.adx_period, 14)}`, adx(candles, n(params.adx_period, 14)).adx, "own");
      break;
    case "E4": {
      const st = stochastic(candles, n(params.k_period, 14), n(params.smooth, 3), n(params.d_period, 3));
      add("stoch-k", `Stochastic %K ${n(params.k_period, 14)}`, st.k, "own");
      add("stoch-d", `%D ${n(params.d_period, 3)}`, st.d, "own");
      if (on(params.use_trend)) context(`${maType.toUpperCase()} ${n(params.trend_period, 50)}`);
      break;
    }
    case "E5":
      add("vwap", "Session VWAP", vwap(candles));
      if (on(params.use_trend)) context(`${maType.toUpperCase()} ${n(params.trend_period, 50)}`);
      break;
    case "H10":
      ma("ema", "EMA", n(params.ema_n, 9));
      break;
    case "B5":
    case "C5": {
      const ch = donchian(candles, n(params.n, 20));
      add("dc-upper", `Donchian ${n(params.n, 20)} high`, ch.upper);
      add("dc-lower", `Donchian ${n(params.n, 20)} low`, ch.lower);
      break;
    }
    case "B7": {
      const bb = bollinger(close, n(params.n, 20), 2);
      add("bb-upper", `Bollinger ${n(params.n, 20)} upper`, bb.upper);
      add("bb-lower", `Bollinger ${n(params.n, 20)} lower`, bb.lower);
      break;
    }
    default:
      if (/^H\d+$/.test(id) && on(params.vwap)) add("vwap", "Session VWAP", vwap(candles));
      if (/^G\d+$/.test(id) && "rsi_period" in params) add("rsi", `RSI ${n(params.rsi_period, 14)}`, rsi(close, n(params.rsi_period, 14)), "own");
  }
  return { studies, named: [...new Set(named)] };
}

/** The settings a template test ran with: the template's defaults, then the ones the trader set. */
export function templateParams(defaults: readonly { key: string; default?: unknown }[], set: Record<string, unknown> = {}): Record<string, unknown> {
  return { ...Object.fromEntries(defaults.map((p) => [p.key, p.default])), ...set };
}

/** BT-DRAW: an exit marker names why the trade closed (the engine's own reason, shortened) and its result. */
export function exitMarkerText(reason: string | null | undefined, pnl: string): string {
  const why = (reason ?? "").replace(/\s+/g, " ").trim();
  const short = why.length > 22 ? `${why.slice(0, 21)}…` : why;
  return [short || "Exit", pnl].filter(Boolean).join(" ");
}
