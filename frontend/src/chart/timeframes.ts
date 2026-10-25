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

// --- TF-1 (owner 25 Oct): one timeframe control everywhere ---------------------------------------------------------------
// A dropdown with every timeframe, grouped; a star per timeframe; the starred ones as one-click buttons beside it.

/** The starred timeframes a user starts with: 5m, 15m, 1H, D (backend app/me/prefs.py DEFAULT_TIMEFRAME_FAVOURITES). */
export const DEFAULT_FAVOURITES: readonly TimeframeId[] = ["5min", "15min", "60min", "day"];

/** How many favourites a phone shows beside the dropdown (the rest stay starred in the list). */
export const PHONE_FAVOURITES = 3;

export interface TimeframeGroup { label: "Minutes" | "Hours" | "Days" | "Weeks" | "Months"; ids: TimeframeId[] }

const GROUP_OF: Record<TimeframeId, TimeframeGroup["label"]> = {
  "1min": "Minutes", "3min": "Minutes", "5min": "Minutes", "15min": "Minutes", "30min": "Minutes",
  "60min": "Hours", day: "Days", week: "Weeks", month: "Months",
};
const GROUP_ORDER: readonly TimeframeGroup["label"][] = ["Minutes", "Hours", "Days", "Weeks", "Months"];

/** The offered timeframes, grouped as the dropdown shows them (an empty group is left out). Pure. */
export function groupTimeframes(offered: readonly TimeframeId[]): TimeframeGroup[] {
  return GROUP_ORDER.map((label) => ({ label, ids: TIMEFRAMES.filter((tf) => offered.includes(tf) && GROUP_OF[tf] === label) }))
    .filter((g) => g.ids.length > 0);
}

/** Any stored value as favourites: known timeframes only, each once, in the list's order; not a list = the defaults. */
export function cleanFavourites(raw: unknown): TimeframeId[] {
  if (!Array.isArray(raw)) return [...DEFAULT_FAVOURITES];
  return TIMEFRAMES.filter((tf) => raw.includes(tf));
}

/** Star or unstar one timeframe (the result keeps the list's order). Pure. */
export function toggleFavourite(favourites: readonly TimeframeId[], tf: TimeframeId): TimeframeId[] {
  const next = favourites.includes(tf) ? favourites.filter((f) => f !== tf) : [...favourites, tf];
  return TIMEFRAMES.filter((t) => next.includes(t));
}

/** The favourite buttons beside the dropdown: only what this chart offers; a phone shows at most three. Pure. */
export function shownFavourites(favourites: readonly TimeframeId[], offered: readonly TimeframeId[], phone: boolean): TimeframeId[] {
  const mine = TIMEFRAMES.filter((tf) => favourites.includes(tf) && offered.includes(tf));
  return phone ? mine.slice(0, PHONE_FAVOURITES) : mine;
}

export type IntervalUnit = "minutes" | "hours" | "days" | "weeks" | "months";
export const INTERVAL_UNITS: readonly IntervalUnit[] = ["minutes", "hours", "days", "weeks", "months"];

/** One custom interval in minutes (days, weeks and months as their own kind: they are built from daily bars). */
function intervalId(n: number, unit: IntervalUnit): TimeframeId | null {
  const minutes = unit === "minutes" ? n : unit === "hours" ? n * 60 : null;
  if (minutes !== null) {
    const id = minutes === 60 ? "60min" : `${minutes}min`;
    return isTimeframe(id) ? id : null;
  }
  if (n !== 1) return null;
  return unit === "days" ? "day" : unit === "weeks" ? "week" : "month";
}

/** The plain list of what a chart here can show ("1, 3, 5, 15 and 30 minutes, 1 hour, daily, weekly and monthly"). */
export function servedText(offered: readonly TimeframeId[]): string {
  const mins = offered.filter((tf) => GROUP_OF[tf] === "Minutes").map((tf) => String(timeframeSeconds(tf) / 60));
  const parts: string[] = [];
  if (mins.length) parts.push(`${mins.length > 1 ? `${mins.slice(0, -1).join(", ")} and ${mins[mins.length - 1]}` : mins[0]} minutes`);
  if (offered.includes("60min")) parts.push("1 hour");
  for (const [tf, word] of [["day", "daily"], ["week", "weekly"], ["month", "monthly"]] as const) if (offered.includes(tf)) parts.push(word);
  return parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}` : parts[0] ?? "";
}

export type CustomResult = { ok: true; id: TimeframeId } | { ok: false; message: string };

/** "Add custom interval": a number and a unit, checked against what the charts here can be given - the candle route
 * builds the timeframes in TIMEFRAMES only (week and month only when the server offers them), so any other interval is
 * refused in plain words naming what can be shown. Pure. */
export function parseCustomInterval(value: string, unit: IntervalUnit, offered: readonly TimeframeId[]): CustomResult {
  const text = value.trim();
  if (!/^\d{1,4}$/.test(text) || Number(text) < 1) return { ok: false, message: "Type a whole number, for example 15." };
  const n = Number(text);
  const id = intervalId(n, unit);
  if (id && offered.includes(id)) return { ok: true, id };
  const name = `${n} ${n === 1 ? unit.replace(/s$/, "") : unit}`;
  return { ok: false, message: `A ${name} chart is not available yet. Charts here can show ${servedText(offered)}.` };
}
