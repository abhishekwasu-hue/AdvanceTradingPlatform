import { ApiError } from "../api/client";
import { EOD_MAX_SYMBOLS, type EodClose, eodApi } from "../api/eod";
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

/** M90 B4: a last close changes once a day, so a read one is kept a few minutes - a 500-row list asks for it in
 * requests of EOD_MAX_SYMBOLS once, not on every 10 s poll. */
export const EOD_CACHE_MS = 5 * 60_000;
const eodCache = new Map<string, { row: EodClose; asOf: string | null; at: number }>();
export function resetEodCache(): void { eodCache.clear(); }

export async function eodQuotes(items: readonly WatchItem[], signal?: AbortSignal, now: () => number = () => Date.now()): Promise<EodQuotesReply> {
  const nse = [...new Set(items.filter((i) => i.exchange.toUpperCase() === "NSE").map((i) => i.symbol.toUpperCase()))];
  const want = nse.filter((s) => { const hit = eodCache.get(s); return !hit || now() - hit.at > EOD_CACHE_MS; });
  for (let i = 0; i < want.length; i += EOD_MAX_SYMBOLS) {      // M90 B4: every NSE row, EOD_MAX_SYMBOLS a request
    const reply = await eodApi.closes(want.slice(i, i + EOD_MAX_SYMBOLS), signal);
    for (const r of reply.items) eodCache.set(r.symbol, { row: r, asOf: reply.as_of, at: now() });
  }
  const got = nse.map((s) => eodCache.get(s)).filter((h): h is { row: EodClose; asOf: string | null; at: number } => !!h);
  const dates = got.map((h) => h.asOf).filter((d): d is string => !!d).sort();
  const reply = { items: got.map((h) => h.row), as_of: dates.length ? dates[dates.length - 1] : null };
  const bySymbol = new Map(reply.items.map((r) => [r.symbol, r]));
  const rows: QuoteRow[] = items.map((i) => {
    const r = i.exchange.toUpperCase() === "NSE" ? bySymbol.get(i.symbol.toUpperCase()) : undefined;
    return r && !r.missing && r.close !== null
      ? { symbol: i.symbol, exchange: i.exchange, ltp: r.close, change: r.change, change_pct: r.change_pct, missing: false, stale: false, error: null }
      : { symbol: i.symbol, exchange: i.exchange, ltp: null, change: null, change_pct: null, missing: true, error: null };
  });
  return { items: rows, eod_as_of: reply.as_of };
}
