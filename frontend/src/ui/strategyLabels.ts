import type { IndicatorName } from "../types";

/**
 * SB-UX SU7 / SU8 (owner order 30 Oct): the ONE label map for strategy words. An engine id ("PLUS_DI", "OR_HIGH",
 * "PDH", "ema_fast", "net_pnl") is never printed: every screen that shows an indicator, a strategy setting, an
 * optimiser score or an exit rule reads its name here (the step-by-step builder, the full builder under Advanced,
 * the Library, Test a strategy).
 */

/** The no-code engine's indicators, in plain names. */
export const INDICATOR_LABEL: Record<IndicatorName, string> = {
  EMA: "EMA", SMA: "SMA", RSI: "RSI", ADX: "ADX", PLUS_DI: "+DI", MINUS_DI: "-DI", ATR: "ATR", SUPERTREND: "Supertrend",
  CLOSE: "Close", OPEN: "Open", HIGH: "High", LOW: "Low", VWAP: "VWAP", DAY_OPEN: "Today's open", OR_HIGH: "Opening range high",
  OR_LOW: "Opening range low", PDH: "Previous day high", PDL: "Previous day low", PDC: "Previous day close", BB_UPPER: "Bollinger upper",
  BB_MID: "Bollinger middle", BB_LOWER: "Bollinger lower", VOLUME: "Volume", VOLUME_SMA: "Average volume",
};

/** A built-in strategy's settings (backend/app/strategy_engine/*: `default_params`), in plain names. */
export const PARAM_LABEL: Readonly<Record<string, string>> = {
  adx_min: "Minimum ADX", adx_period: "ADX length", atr_buffer: "ATR buffer", atr_mult_sl: "Stop (x ATR)", atr_period: "ATR length",
  bb_k: "Bollinger width (std. dev.)", bb_period: "Bollinger length", ema_fast: "Fast EMA", ema_slow: "Slow EMA", ema_trend: "Trend EMA",
  htf_ema_period: "Higher-timeframe EMA", last_entry: "Last entry at", lookback: "Bars looked at", macd_fast: "MACD fast",
  macd_signal: "MACD signal", macd_slow: "MACD slow", max_stop_atr: "Widest stop (x ATR)", min_rr: "Minimum R:R",
  min_stop_atr: "Tightest stop (x ATR)", pullback_atr_mult: "Pullback depth (x ATR)", range_minutes: "Opening range (minutes)",
  rsi_high: "RSI upper level", rsi_long_min: "RSI floor for longs", rsi_long_trigger: "RSI long trigger", rsi_low: "RSI lower level",
  rsi_mid: "RSI middle level", rsi_period: "RSI length", rsi_short_max: "RSI ceiling for shorts", rsi_short_trigger: "RSI short trigger",
  st_mult: "Supertrend multiple", st_period: "Supertrend length", stop_atr_inside: "Stop inside the area (x ATR)", target_rr: "Targets (R)",
  tf: "Timeframe", touch_pct: "Touch distance (%)", volume_lookback: "Bars for average volume", volume_mult: "Volume multiple",
};

const TIMEFRAME_VALUE: Readonly<Record<string, string>> = {
  "1min": "1-minute", "3min": "3-minute", "5min": "5-minute", "15min": "15-minute", "30min": "30-minute", "60min": "1-hour", day: "Daily",
};

/** A timeframe by name ("5min" -> "5-minute"). */
export const timeframeWords = (tf: string): string => TIMEFRAME_VALUE[tf] ?? tf;

/** A key no map knows, in words ("pullback_bars" -> "Pullback bars"); never the raw key. */
export function wordsOf(key: string): string {
  const text = key.replace(/[_.]+/g, " ").trim().toLowerCase();
  return text ? text[0].toUpperCase() + text.slice(1) : "";
}

export function paramLabel(key: string): string {
  return PARAM_LABEL[key] ?? wordsOf(key);
}

/** A setting's value in words: a list as "1.5 / 2", a timeframe by name, on / off for a switch. */
export function paramValue(key: string, value: unknown): string {
  if (Array.isArray(value)) return value.map((v) => paramValue(key, v)).join(" / ");
  if (typeof value === "boolean") return value ? "On" : "Off";
  if (value === null || value === undefined || value === "") return "Off";
  if (typeof value === "number") return String(Math.round(value * 1000) / 1000);
  if (typeof value === "string") return TIMEFRAME_VALUE[value] ?? value;
  return "Set";   // a nested setting: named, never printed as data
}

/** One chip per setting: "Fast EMA 9", "Targets (R) 1.5 / 2". */
export function paramChips(params: Record<string, unknown> | null | undefined): string[] {
  if (!params) return [];
  return Object.entries(params).map(([k, v]) => `${paramLabel(k)} ${paramValue(k, v)}`);
}

/** REV-B2 (owner order 30 Oct): the Strategy Library's chips for a built-in. A built-in has no reward:risk filter of its
 * own (its old `min_rr` compared two of its fixed numbers and never acted), so one is never shown there, even from an
 * older server; the trader's own minimum lives in his risk limits. */
export function libraryChips(params: Record<string, unknown> | null | undefined): string[] {
  if (!params) return [];
  return paramChips(Object.fromEntries(Object.entries(params).filter(([k]) => k !== "min_rr")));
}

/** STRAT-X (owner decision 30 Oct, D2): a built-in's stop and targets are suggested values for the trader's own settings,
 * never a rule of the strategy - the Library shows them apart, under this note. */
export const SUGGESTED_KEYS: readonly string[] = ["atr_mult_sl", "target_rr"];
export const SUGGESTED_NOTE = "Suggested — change in settings";

/** The Library's chips for a built-in, split: its own settings, and the suggested stop / targets (SUGGESTED_KEYS). */
export function librarySplit(params: Record<string, unknown> | null | undefined): { settings: string[]; suggested: string[] } {
  const own = Object.fromEntries(Object.entries(params ?? {}).filter(([k]) => k !== "min_rr"));
  const pick = (want: boolean) => Object.fromEntries(Object.entries(own).filter(([k]) => SUGGESTED_KEYS.includes(k) === want));
  return { settings: paramChips(pick(false)), suggested: paramChips(pick(true)) };
}

/** M49 U3 (owner-approved mockup docs/ui/U3_LIBRARY.html): one setting as a label and a value in words. */
export interface SettingRow { key: string; label: string; value: string }

/** The most settings a Library card shows; the rest sit under "More settings". */
export const LIBRARY_MAIN_MAX = 3;

const rowsOf = (params: Record<string, unknown>): SettingRow[] =>
  Object.entries(params).map(([key, v]) => ({ key, label: paramLabel(key), value: paramValue(key, v) }));

/**
 * U3: a Library card's settings, split. `main` is at most three: the chart (one timeframe: "Chart 5-minute"; two: the
 * bigger and the smaller chart) and the first of its own settings; `more` is the rest of its own settings; `suggested`
 * is the built-in's suggested stop / targets (STRAT-X, SUGGESTED_KEYS), counted under "More settings". The `tf` setting
 * is the chart itself, so it is never listed twice; a minimum R:R is never shown (REV-B2).
 */
export function libraryCardSettings(params: Record<string, unknown> | null | undefined, timeframes: readonly string[] = []):
  { main: SettingRow[]; more: SettingRow[]; suggested: SettingRow[] } {
  const own = Object.fromEntries(Object.entries(params ?? {}).filter(([k]) => k !== "min_rr"));
  const tf = typeof own.tf === "string" ? own.tf : null;
  delete own.tf;
  const frames = timeframes.length ? timeframes : tf ? [tf] : [];
  const chart: SettingRow[] = frames.length >= 2
    ? [{ key: "chart_big", label: "Big chart", value: timeframeWords(frames[frames.length - 1]) },
       { key: "chart_small", label: "Small chart", value: timeframeWords(frames[0]) }]
    : frames.length === 1 ? [{ key: "chart", label: "Chart", value: timeframeWords(frames[0]) }] : [];
  const settings = rowsOf(Object.fromEntries(Object.entries(own).filter(([k]) => !SUGGESTED_KEYS.includes(k))));
  const suggested = rowsOf(Object.fromEntries(Object.entries(own).filter(([k]) => SUGGESTED_KEYS.includes(k))));
  const room = Math.max(0, LIBRARY_MAIN_MAX - chart.length);
  const first = settings.slice(0, room);
  // The mockup's order: one chart after the settings ("Fast average 9, Slow average 21, Chart 15-minute"); two charts first.
  const main = (chart.length >= 2 ? [...chart, ...first] : [...first, ...chart]).slice(0, LIBRARY_MAIN_MAX);
  return { main, more: settings.slice(room), suggested };
}

/** U3: the Library's category in a word a trader uses (never "Indicator-based" or the engine's value). */
export const CATEGORY_LABEL: Readonly<Record<string, string>> = { indicator_based: "Indicators", multi_timeframe: "Multi-timeframe" };
export const categoryLabel = (c: string): string => CATEGORY_LABEL[c] ?? wordsOf(c);

/** U3: the first sentence of a strategy's description - the card's one plain line (the whole text sits in the details). */
export function firstSentence(text: string): string {
  const t = text.trim();
  const m = /^(.+?[.!?])(\s|$)/.exec(t);
  return m ? m[1] : t;
}

/** The optimiser's scores (backend/app/backtest/optimizer.py METRICS). */
export const METRIC_LABEL: Readonly<Record<string, string>> = {
  net_pnl: "Net P&L", expectancy: "Average per trade", profit_factor: "Profit factor", win_rate: "Win %",
};
export const metricLabel = (m: string): string => METRIC_LABEL[m] ?? wordsOf(m);

/** A run's dynamic exits (ExitRules), in words. */
export function exitRuleWords(rules: Record<string, unknown> | null | undefined): string {
  if (!rules) return "None";
  const words: string[] = [];
  for (const [k, v] of Object.entries(rules)) {
    if (v === null || v === undefined || v === "") continue;
    if (k === "trailing_stop_pct") words.push(`Trailing stop ${v}%`);
    else if (k === "break_even_at_r") words.push(`Break-even at ${v} R`);
    else if (k === "time_exit_minutes") words.push(`Exit after ${v} min`);
    else if (k === "time_exit_at") words.push(`Flat at ${v}`);
    else words.push(`${wordsOf(k)} ${paramValue(k, v)}`);
  }
  return words.length ? words.join(", ") : "None";
}
