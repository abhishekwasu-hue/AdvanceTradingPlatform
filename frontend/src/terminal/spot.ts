import { useMemo } from "react";
import { useLiveLtp } from "../components/chartHelpers";
import { tickKey, useLiveTicks, type LiveTick } from "../live/liveTicks";
import type { LiveChain } from "../optionChain/types";
import type { LtpResponse } from "../types";

/**
 * Owner fix T1-3 (24 Oct): the spot on the Terminal's option chain is the spot on its chart - ONE read, one value, one
 * time. The page reads the underlying's price once (the live tick of his own broker connection while `data_hub` is on,
 * else the same LTP read the chart makes) and hands that same value to the chart panel (as its live price) and to the
 * chain panel (its "spot" line). Only when neither has a price (market closed, no broker) does the chain's own spot -
 * the broker's underlying, or the last close - stand, with the chain's own "as of" time.
 */
export interface TerminalSpot { ltp: number; at: string | null; source: "tick" | "ltp" | "chain" }

/** The chart's live-price shape (ChartV2 `live`). */
export type ChartLive = Pick<LtpResponse, "ltp" | "timestamp" | "fetched_at">;

/** Pure: the one spot, newest source first. */
export function pickSpot(tick: LiveTick | undefined, ltp: Pick<LtpResponse, "ltp" | "timestamp" | "fetched_at"> | null, chain: LiveChain | null): TerminalSpot | null {
  if (tick && Number.isFinite(tick.ltp) && tick.ltp > 0) return { ltp: tick.ltp, at: tick.ts, source: "tick" };
  if (ltp && Number.isFinite(ltp.ltp) && ltp.ltp > 0) return { ltp: ltp.ltp, at: ltp.timestamp ?? ltp.fetched_at, source: "ltp" };
  const s = chain?.summary.spot;
  if (typeof s === "number" && s > 0) return { ltp: s, at: chain?.data_as_of ?? chain?.fetched_at ?? null, source: "chain" };
  return null;
}

/** The spot as the chart's live price (only a live source moves the chart; the chain's own spot never does). */
export function chartLiveOf(spot: TerminalSpot | null): ChartLive | null {
  if (!spot || spot.source === "chain") return null;
  return { ltp: spot.ltp, timestamp: spot.at, fetched_at: spot.at ?? "" };
}

export function useTerminalSpot({ symbol, exchange = "NSE", hub, enabled, chain }: {
  symbol: string; exchange?: string; hub: boolean; enabled: boolean; chain: LiveChain | null;
}): TerminalSpot | null {
  const items = useMemo(() => [{ symbol, exchange }], [symbol, exchange]);
  const ticks = useLiveTicks(items, enabled && hub);
  const tick = ticks.get(tickKey(symbol, exchange));
  // The same read the chart makes on its own (src/chart/EmbeddedChartV2.tsx): while the market is open.
  const { ltp } = useLiveLtp(enabled && !tick && chain?.market_open !== false, symbol, exchange);
  return useMemo(() => pickSpot(tick, ltp, chain), [tick, ltp, chain]);
}
