import { ApiError, api } from "../api/client";
import { eodApi } from "../api/eod";
import { istDate } from "../components/chartHistory";
import type { CandlesResponse, OHLCVBar } from "../types";

/**
 * M86 D2: the chart's candle read for a user without a broker. The candle route answers Daily / Weekly from NSE's
 * end-of-day files itself where the store is on; where it refuses (409: no broker session), a Daily or Weekly NSE chart
 * reads the same files through `GET /api/market-data/eod/bars` instead, so the chart shows the last close rather than
 * an error. Intraday, Monthly and other exchanges keep the route's own answer (its reason is shown as before).
 */
const EOD_TIMEFRAMES: Readonly<Record<string, "day" | "week">> = { day: "day", week: "week" };
const MAX_DAYS = 3650;

export async function candlesWithEod(symbols: string[], timeframe: string, lookbackDays: number, exchange = "NSE", broker?: string,
  before?: string, v2?: { forming: boolean; volume_from_future: boolean }, signal?: AbortSignal): Promise<CandlesResponse> {
  try {
    return await api.marketDataCandles(symbols, timeframe, lookbackDays, exchange, broker, before, v2);
  } catch (e) {
    const kind = EOD_TIMEFRAMES[timeframe];
    if (!(e instanceof ApiError) || e.status !== 409 || !kind || exchange.toUpperCase() !== "NSE") throw e;
    return eodResponse(symbols, kind, lookbackDays, exchange, before, signal);
  }
}

async function eodResponse(symbols: string[], kind: "day" | "week", lookbackDays: number, exchange: string, before: string | undefined,
  signal: AbortSignal | undefined): Promise<CandlesResponse> {
  const out: CandlesResponse["symbols"] = {};
  for (const raw of symbols) {
    const sym = raw.trim().toUpperCase();
    const reply = await eodApi.bars(sym, kind, Math.max(1, Math.min(MAX_DAYS, Math.round(lookbackDays))), signal);
    const bars: OHLCVBar[] = reply.candles.filter((b) => !before || istDate(b) < before);
    out[sym] = { bars, count: bars.length, first: bars[0]?.timestamp ?? null, last: bars[bars.length - 1]?.timestamp ?? null, error: null,
      ...(bars.length ? {} : { empty_reason: "No end-of-day prices are stored for this symbol yet." }) };
  }
  return { source: { broker: "eod", account_label: "" }, exchange, timeframe: kind, base_interval: "day", lookback_days: lookbackDays,
    before: before ?? null, fetched_at: new Date().toISOString(), symbols: out, warnings: [], note: "" };
}
