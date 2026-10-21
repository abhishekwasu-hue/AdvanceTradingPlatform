/**
 * OC1 test data: a hand-made chain with round numbers (not market data). Seven strikes 24,200 to 24,800, the spot at
 * 24,480 (so the ATM strike is 24,500), the highest call OI at 24,800 and the highest put OI at 24,200.
 */
import type { ChainExpiries, ChainLeg, ChainRow, LiveChain } from "./types";

export const leg = (over: Partial<ChainLeg> = {}): ChainLeg => ({
  oi: null, oi_change: null, ltp: null, iv: null, volume: null, bid: null, ask: null,
  delta: null, gamma: null, theta: null, vega: null, ...over,
});

const STRIKES = [24200, 24300, 24400, 24500, 24600, 24700, 24800];

export function rows(): ChainRow[] {
  return STRIKES.map((k, i) => ({
    strike: k,
    call: leg({ oi: 100_000 * (i + 1), oi_change: 5_000 * (i - 3), ltp: 400 - 50 * i, iv: 12 + i / 10, volume: 20_000,
                bid: 399.5 - 50 * i, ask: 400.5 - 50 * i, delta: 0.8 - i / 10, gamma: 0.0012, theta: -9.5, vega: 11.25 }),
    put: leg({ oi: 100_000 * (7 - i), oi_change: -2_000 * i, ltp: 50 + 50 * i, iv: 13 - i / 10, volume: 30_000,
               bid: 49.5 + 50 * i, ask: 50.5 + 50 * i, delta: -0.2 - i / 10, gamma: 0.0011, theta: -8.25, vega: 10.5 }),
  }));
}

export const EXPIRIES: ChainExpiries = {
  underlying: "NIFTY", today: "2026-10-27",
  expiries: [{ date: "2026-10-27", kind: "monthly" }, { date: "2026-11-03", kind: "weekly" }, { date: "2026-11-10", kind: "weekly" }],
  default: "2026-11-03", source: "master",
};

export function liveChain(over: Partial<LiveChain> = {}): LiveChain {
  return {
    underlying: "NIFTY", expiry: "2026-11-03", rows: rows(), fetched_at: "2026-10-27T05:00:00+00:00", market_open: true,
    oi_change: { status: "ready", source: "broker", since: null },
    summary: {
      spot: 24480, futures: { symbol: "NIFTY FUT 24 NOV 26", expiry: "2026-11-24", ltp: 24530.5 }, atm_strike: 24500, pcr_oi: 0.88,
      max_pain: 24500, atm_iv: 12.5, days_to_expiry: 7, max_call_oi_strike: 24800, max_put_oi_strike: 24200,
      total_call_oi: 2_800_000, total_put_oi: 2_800_000,
    },
    ...over,
  };
}
