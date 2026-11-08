import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "../api/client";
import { eodApi } from "../api/eod";
import { noBrokerEodFor } from "../ui/dataPages";
import { lastCloseText } from "../ui/lastClose";
import { candlesWithEod } from "./eodCandles";
import { eodQuotes, withEodFallback } from "./eodQuotes";

/** M86 D2: a user without a broker sees the last end-of-day close (NSE's files) on charts and watchlists. */
const NO_BROKER = new ApiError(409, "No broker session with a valid token.");
const bar = (day: string, close: number) => ({ timestamp: `${day}T00:00:00+05:30`, open: close - 1, high: close + 2, low: close - 3, close, volume: 10 });

afterEach(() => vi.restoreAllMocks());

describe("M86 words", () => {
  it("Last close with its date, or alone", () => {
    expect(lastCloseText("2026-11-06")).toBe("Last close · 6 Nov");
    expect(lastCloseText(null)).toBe("Last close");
    expect(lastCloseText("not a date")).toBe("Last close");
  });

  it("Charts, Option chain, Find setups and Signals (and their sub-paths) show the last close without a broker", () => {
    for (const path of ["/charts", "/charts/RELIANCE", "/option-chain", "/screener", "/screener/custom", "/signals"]) {
      expect(noBrokerEodFor(path), path).toMatch(/last close|Last close/);
    }
    // Their own no-broker layout: Today, My trades and the Copilot (not gated by the shell).
    expect(noBrokerEodFor("/positions")).toBeNull();
    expect(noBrokerEodFor("/")).toBeNull();
  });
});

describe("candlesWithEod", () => {
  it("passes the candle route's answer through untouched", async () => {
    const reply = { symbols: {} } as Awaited<ReturnType<typeof api.marketDataCandles>>;
    vi.spyOn(api, "marketDataCandles").mockResolvedValue(reply);
    const eod = vi.spyOn(eodApi, "bars");
    expect(await candlesWithEod(["RELIANCE"], "day", 400)).toBe(reply);
    expect(eod).not.toHaveBeenCalled();
  });

  it("a Daily / Weekly NSE chart the route refuses for want of a broker reads NSE's end-of-day files", async () => {
    vi.spyOn(api, "marketDataCandles").mockRejectedValue(NO_BROKER);
    const eod = vi.spyOn(eodApi, "bars").mockResolvedValue({ symbol: "RELIANCE", exchange: "NSE", timeframe: "week", last_date: "2026-11-06", source: "eod",
      candles: [bar("2026-10-26", 100), bar("2026-11-02", 104)] });
    const out = await candlesWithEod(["reliance"], "week", 900);
    expect(eod).toHaveBeenCalledWith("RELIANCE", "week", 900, undefined);
    expect(out.symbols.RELIANCE.bars.map((b) => b.close)).toEqual([100, 104]);
    expect(out.symbols.RELIANCE.error).toBeNull();
    // An older page: only bars before the date asked for.
    const older = await candlesWithEod(["RELIANCE"], "week", 900, "NSE", undefined, "2026-11-01");
    expect(older.symbols.RELIANCE.bars.map((b) => b.close)).toEqual([100]);
  });

  it("nothing stored: an empty chart with its plain reason", async () => {
    vi.spyOn(api, "marketDataCandles").mockRejectedValue(NO_BROKER);
    vi.spyOn(eodApi, "bars").mockResolvedValue({ symbol: "X", exchange: "NSE", timeframe: "day", last_date: null, source: "eod", candles: [] });
    const out = await candlesWithEod(["X"], "day", 400);
    expect(out.symbols.X.bars).toEqual([]);
    expect(out.symbols.X.empty_reason).toMatch(/No end-of-day prices/);
  });

  it("intraday, Monthly, another exchange or another failure keep the route's own answer", async () => {
    vi.spyOn(api, "marketDataCandles").mockRejectedValue(NO_BROKER);
    const eod = vi.spyOn(eodApi, "bars");
    await expect(candlesWithEod(["RELIANCE"], "5min", 5)).rejects.toBe(NO_BROKER);
    await expect(candlesWithEod(["RELIANCE"], "month", 900)).rejects.toBe(NO_BROKER);
    await expect(candlesWithEod(["SENSEX"], "day", 400, "BSE")).rejects.toBe(NO_BROKER);
    const down = new ApiError(503, "Broker down");
    vi.spyOn(api, "marketDataCandles").mockRejectedValue(down);
    await expect(candlesWithEod(["RELIANCE"], "day", 400)).rejects.toBe(down);
    expect(eod).not.toHaveBeenCalled();
  });
});

describe("watchlist quotes without a broker", () => {
  const items = [{ symbol: "RELIANCE", exchange: "NSE" }, { symbol: "TCS", exchange: "NSE" }, { symbol: "SENSEX", exchange: "BSE" }];

  it("the NSE rows get their last close and the reply its date; other exchanges stay unpriced", async () => {
    vi.spyOn(eodApi, "closes").mockResolvedValue({ as_of: "2026-11-06", source: "eod", items: [
      { symbol: "RELIANCE", exchange: "NSE", close: 1400.5, prev_close: 1390.5, change: 10, change_pct: 0.72, date: "2026-11-06", missing: false },
      { symbol: "TCS", exchange: "NSE", close: null, prev_close: null, change: null, change_pct: null, date: null, missing: true },
    ] });
    const quotes = vi.fn().mockRejectedValue(NO_BROKER);
    const out = await withEodFallback(quotes)(items);
    expect(out.eod_as_of).toBe("2026-11-06");
    expect(out.items[0]).toMatchObject({ symbol: "RELIANCE", ltp: 1400.5, change: 10, change_pct: 0.72, missing: false });
    expect(out.items[1]).toMatchObject({ symbol: "TCS", ltp: null, missing: true });
    expect(out.items[2]).toMatchObject({ symbol: "SENSEX", ltp: null, missing: true });
  });

  it("asks only for NSE symbols, and nothing when there are none", async () => {
    const closes = vi.spyOn(eodApi, "closes").mockResolvedValue({ as_of: null, source: "eod", items: [] });
    await eodQuotes(items);
    expect(closes.mock.calls[0][0]).toEqual(["RELIANCE", "TCS"]);
    closes.mockClear();
    const out = await eodQuotes([{ symbol: "SENSEX", exchange: "BSE" }]);
    expect(closes).not.toHaveBeenCalled();
    expect(out.eod_as_of).toBeNull();
  });

  it("a live answer or another failure is the route's own", async () => {
    const live = { items: [] };
    expect(await withEodFallback(vi.fn().mockResolvedValue(live))(items)).toBe(live);
    const down = new ApiError(503, "Broker down");
    await expect(withEodFallback(vi.fn().mockRejectedValue(down))(items)).rejects.toBe(down);
  });
});
