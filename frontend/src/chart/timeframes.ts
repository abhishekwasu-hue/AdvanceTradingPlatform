/**
 * UI2-4b1 (Charts page plan §3): the one timeframe list every v2 chart uses - the Chart tab, the Multi-chart tab and the
 * charts on other pages - so no page keeps its own (today there are four, two without 3m). The ids are the candle
 * route's (`POST /api/market-data/candles`); week and month are aggregated in IST from daily bars on the server (UI2-4a2).
 */
import type { OHLCVBar } from "../types";
import { applyLivePrice } from "../utils/indicators";

export const TIMEFRAMES = ["1min", "3min", "5min", "15min", "30min", "60min", "day", "week", "month"] as const;
export type TimeframeId = (typeof TIMEFRAMES)[number];

interface TimeframeSpec { label: string; seconds: number; historyDays: number; olderDays: number }

/** Labels as the trader reads them (day-one fix 8: words, "Daily" / "1 hour", not "D" / "1H"); history = calendar days on first load (a few hundred bars), older = per "load older"
 * page. Intraday is built from one-minute bars, 30 days at most per request; week / month follow the server's defaults. */
const SPEC: Record<TimeframeId, TimeframeSpec> = {
  "1min": { label: "1 min", seconds: 60, historyDays: 5, olderDays: 5 },
  "3min": { label: "3 min", seconds: 180, historyDays: 7, olderDays: 7 },
  "5min": { label: "5 min", seconds: 300, historyDays: 10, olderDays: 10 },
  "15min": { label: "15 min", seconds: 900, historyDays: 20, olderDays: 20 },
  "30min": { label: "30 min", seconds: 1_800, historyDays: 30, olderDays: 30 },
  "60min": { label: "1 hour", seconds: 3_600, historyDays: 30, olderDays: 30 },
  day: { label: "Daily", seconds: 86_400, historyDays: 730, olderDays: 730 },
  week: { label: "Weekly", seconds: 7 * 86_400, historyDays: 1_826, olderDays: 1_826 },
  month: { label: "Monthly", seconds: 30 * 86_400, historyDays: 7_300, olderDays: 7_300 },
};

/** Day, week and month: the bars the daily route builds (live only moves a bar the server marked `forming`). */
export const DAILY_OR_LONGER: ReadonlySet<TimeframeId> = new Set<TimeframeId>(["day", "week", "month"]);

export const DEFAULT_TIMEFRAME: TimeframeId = "5min";

export function isTimeframe(value: unknown): value is TimeframeId {
  return typeof value === "string" && (TIMEFRAMES as readonly string[]).includes(value);
}

export const timeframeLabel = (tf: TimeframeId): string => SPEC[tf].label;

/** UI-H W0-1: the short names a phone's timeframe row shows ("1m 5m 15m 1H D"); the full label stays the accessible name. */
const SHORT: Record<TimeframeId, string> = { "1min": "1m", "3min": "3m", "5min": "5m", "15min": "15m", "30min": "30m", "60min": "1H", day: "D", week: "W", month: "M" };
export const timeframeShort = (tf: TimeframeId): string => SHORT[tf];
/** Seconds per bar (a month counts as 30 days: only used to tell a closed bar from a forming one). */
export const timeframeSeconds = (tf: TimeframeId): number => SPEC[tf].seconds;
export const historyDaysFor = (tf: TimeframeId): number => SPEC[tf].historyDays;
export const olderPageDaysFor = (tf: TimeframeId): number => SPEC[tf].olderDays;

/** The next shorter (delta < 0) or longer (delta > 0) timeframe, stopping at 1m and M. */
export function stepTimeframe(tf: TimeframeId, delta: number): TimeframeId {
  const i = TIMEFRAMES.indexOf(tf);
  return TIMEFRAMES[Math.max(0, Math.min(TIMEFRAMES.length - 1, (i < 0 ? TIMEFRAMES.indexOf(DEFAULT_TIMEFRAME) : i) + delta))];
}

/** A bar as the v2 chart receives it: the daily route marks today's bar `forming` while the session is open. */
export type ChartBar = OHLCVBar & { forming?: boolean };

/** Folds a live last price into the bars. Intraday: as every chart does today (`applyLivePrice`: the last bar moves or
 * a new one starts). Day / week / month: only a bar the server marked `forming` moves, and no bar is ever started here,
 * so a completed bar (yesterday's, last week's) never takes today's price. Pure. */
export function foldLive(bars: ChartBar[], ltp: number | null | undefined, at: string | null | undefined, tf: TimeframeId): ChartBar[] {
  if (!DAILY_OR_LONGER.has(tf)) return applyLivePrice(bars, ltp, at, tf);
  const last = bars[bars.length - 1];
  if (!last?.forming || ltp == null || !Number.isFinite(ltp)) return bars;
  return [...bars.slice(0, -1), { ...last, close: ltp, high: Math.max(last.high, ltp), low: Math.min(last.low, ltp) }];
}
