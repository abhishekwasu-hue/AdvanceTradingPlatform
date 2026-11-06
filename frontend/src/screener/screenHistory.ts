/**
 * FN2-j (screener, behind `ui_v2`): "When did this screen hit" and the rolling "Backtest", on the existing history routes
 * (`POST /screener/history/asof | fitness | backtest`; flags `screener_history`, and `screener_backtest` for the last).
 * Information only: daily screens on end-of-day bars; nothing here places, changes or proposes an order.
 */
import { request } from "../api/client";
import { EMPTY, formatNumber, formatPct } from "../ui/format";

export const HISTORY_MAX_SYMBOLS = 300;
export const STEPS = [
  { value: "weekly", label: "Every week" },
  { value: "4-weekly", label: "Every 4 weeks" },
  { value: "monthly", label: "Every month" },
  { value: "daily", label: "Every day" },
] as const;

export interface HistoryScreen { source: string; universe: { symbols: string[] } }
export interface AsOfReply {
  date: string; text: string; universe: number; matched: string[]; not_evaluated: Record<string, string>;
  traded_that_day: number; notes: string[]; data: string; disclaimer: string;
}
export interface HorizonRow {
  horizon: number; dates: number; observations: number; passers_mean: number | null; universe_mean: number | null;
  excess_mean: number | null; hit_rate: number | null; t_stat: number | null; low_sample: boolean;
}
export interface FitnessReply {
  universe: string; start: string; end: string; every: string; dates: number; symbols: number; avg_passers: number | null;
  turnover: number | null; horizons: HorizonRow[]; confidence: string; main_horizon: number;
  deflation: { n_trials?: number; note?: string }; notes: string[]; disclaimer: string;
}
export interface BacktestMetrics {
  total_return: number | null; cagr: number | null; volatility: number | null; sharpe: number | null;
  max_drawdown: number | null; sessions: number;
}
export interface BacktestReply {
  strategy: BacktestMetrics; benchmarks: Record<string, BacktestMetrics>; excess_total_return: Record<string, number | null>;
  final_equity: number; capital: number; costs: number; rebalances: number; avg_holdings: number | null;
  turnover_per_rebalance: number | null; equity: { day: string; equity: number }[]; start: string; end: string; every: string;
  symbols: number; deflation: { n_trials?: number; note?: string }; notes: string[]; fills: string; disclaimer: string;
}

const post = <T,>(path: string, body: unknown) => request<T>(`/screener/history/${path}`, { method: "POST", body: JSON.stringify(body) });

export const screenHistoryApi = {
  asof: (s: HistoryScreen, date: string) => post<AsOfReply>("asof", { ...s, date }),
  fitness: (s: HistoryScreen, start: string, end: string, every: string) => post<FitnessReply>("fitness", { ...s, start, end, every }),
  backtest: (s: HistoryScreen, start: string, end: string, every: string) => post<BacktestReply>("backtest", { ...s, start, end, every }),
};
export type ScreenHistoryApi = typeof screenHistoryApi;

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object";
export const isAsOf = (v: unknown): v is AsOfReply => isObj(v) && Array.isArray(v.matched) && typeof v.universe === "number";
export const isFitness = (v: unknown): v is FitnessReply => isObj(v) && Array.isArray(v.horizons);
export const isBacktest = (v: unknown): v is BacktestReply => isObj(v) && isObj(v.strategy) && Array.isArray(v.equity);

/** A date range the server accepts: both set, the end not before the start. The reason in words when not. */
export function rangeProblem(start: string, end: string): string | null {
  if (!start || !end) return "Pick both dates.";
  if (end < start) return "The end date comes before the start date.";
  return null;
}

/** A return given as a fraction (0.0123) in percent units: "+1.23%". */
export const pctOf = (fraction: number | null | undefined): string => (fraction == null ? EMPTY : formatPct(fraction * 100));
/** A share given as a fraction (0.55): "55%". */
export const shareOf = (fraction: number | null | undefined): string => (fraction == null ? EMPTY : `${formatNumber(fraction * 100, 0)}%`);

export function benchmarkLabel(key: string): string {
  if (key === "nifty_50") return "NIFTY 50";
  if (key === "equal_weight") return "Equal-weight universe";
  return key.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

