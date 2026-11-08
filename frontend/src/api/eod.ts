import { request } from "./client";

/**
 * M86 D2: the shared end-of-day reads (`GET /api/market-data/eod/*`, backend app/market_data/eod_routes.py) - the NSE
 * daily files only, shown to every user; never a broker's row and never an intraday bar.
 */
export interface EodClose {
  symbol: string;
  exchange: string;
  close: number | null;
  prev_close: number | null;
  change: number | null;
  /** In percent units (0.48 = +0.48%). */
  change_pct: number | null;
  /** The trade date of the close (YYYY-MM-DD). */
  date: string | null;
  missing: boolean;
}
export interface EodClosesReply { items: EodClose[]; as_of: string | null; source: "eod" }

export interface EodBar { timestamp: string; open: number; high: number; low: number; close: number; volume: number }
export interface EodBarsReply { symbol: string; exchange: string; timeframe: "day" | "week"; candles: EodBar[]; last_date: string | null; source: "eod" }

/** The server's limit per closes request. */
export const EOD_MAX_SYMBOLS = 60;

export const eodApi = {
  closes: (symbols: readonly string[], signal?: AbortSignal) =>
    request<EodClosesReply>(`/market-data/eod/closes?symbols=${encodeURIComponent(symbols.slice(0, EOD_MAX_SYMBOLS).join(","))}`, { signal }),
  bars: (symbol: string, timeframe: "day" | "week", lookbackDays = 730, signal?: AbortSignal) =>
    request<EodBarsReply>(`/market-data/eod/bars?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}&lookback_days=${lookbackDays}`, { signal }),
};
