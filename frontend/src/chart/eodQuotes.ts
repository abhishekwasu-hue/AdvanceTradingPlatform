import { ApiError } from "../api/client";
import { EOD_MAX_SYMBOLS, eodApi } from "../api/eod";
import type { QuoteRow, QuotesReply, WatchItem } from "./watchlistsV2";

/**
 * M86 D2: the watchlist's batch quotes for a user without a broker. The quotes route refuses (409: no broker session of
 * his own), so the NSE rows get their last close from NSE's end-of-day files (`GET /api/market-data/eod/closes`) and the
 * reply says the date (`eod_as_of`); other exchanges stay "–". Any other failure is the route's own, as before.
 */
export type EodQuotesReply = QuotesReply & { eod_as_of?: string | null };

export function withEodFallback(quotes: (items: WatchItem[], signal?: AbortSignal) => Promise<QuotesReply>) {
  return async (items: WatchItem[], signal?: AbortSignal): Promise<EodQuotesReply> => {
    try {
      return await quotes(items, signal);
    } catch (e) {
      if (!(e instanceof ApiError) || e.status !== 409) throw e;
      return eodQuotes(items, signal);
    }
  };
}

export async function eodQuotes(items: readonly WatchItem[], signal?: AbortSignal): Promise<EodQuotesReply> {
  const nse = [...new Set(items.filter((i) => i.exchange.toUpperCase() === "NSE").map((i) => i.symbol.toUpperCase()))].slice(0, EOD_MAX_SYMBOLS);
  const reply = nse.length ? await eodApi.closes(nse, signal) : { items: [], as_of: null };
  const bySymbol = new Map(reply.items.map((r) => [r.symbol, r]));
  const rows: QuoteRow[] = items.map((i) => {
    const r = i.exchange.toUpperCase() === "NSE" ? bySymbol.get(i.symbol.toUpperCase()) : undefined;
    return r && !r.missing && r.close !== null
      ? { symbol: i.symbol, exchange: i.exchange, ltp: r.close, change: r.change, change_pct: r.change_pct, missing: false, stale: false, error: null }
      : { symbol: i.symbol, exchange: i.exchange, ltp: null, change: null, change_pct: null, missing: true, error: null };
  });
  return { items: rows, eod_as_of: reply.as_of };
}
