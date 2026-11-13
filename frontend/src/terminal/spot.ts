import { useMemo } from "react";
import { useLiveLtp } from "../components/chartHelpers";
import { liveTicks, tickKey, useLiveTicks, type LiveTick } from "../live/liveTicks";
import type { LiveChain } from "../optionChain/types";
import type { LtpResponse } from "../types";

/**
 * Owner fix T1-3 (24 Oct): the spot on the Terminal's option chain is the spot on its chart - ONE read, one value, one
 * time. The page reads the underlying's price once (the live tick of his own broker connection while `data_hub` is on,
 * else the same LTP read the chart makes) and hands that same value to the chart panel (as its live price) and to the
 * chain panel (its "spot" line). Owner bug 13 Nov: of the three reads (tick, LTP, the chain's own spot) the NEWEST by its
 * own time stands, so a stopped stream or an old quote never holds the spot while the chain moves; the context bar, the
 * chain's spot card and the chart's forming bar (every timeframe) take this one value.
 */
export interface TerminalSpot { ltp: number; at: string | null; source: "tick" | "ltp" | "chain" }

/** The chart's live-price shape (ChartV2 `live`). */
export type ChartLive = Pick<LtpResponse, "ltp" | "timestamp" | "fetched_at">;

/** A live tick counts as current only while this tab heard it this recently (browser clock); after that the LTP read
 * runs again. A hub stream that stops sending must never freeze the spot (owner bug 13 Nov: the strip stuck). */
export const TICK_FRESH_MS = 30_000;
/** The context bar's spot says "as of <time>" once its time is older than this (or the market is closed). */
export const SPOT_AS_OF_AFTER_MS = 60_000;

function timeOf(at: string | null | undefined): number {
  const t = at ? Date.parse(at) : NaN;
  return Number.isFinite(t) ? t : -Infinity;
}

/**
 * Pure: the one spot - the NEWEST of the three reads by its own time (owner bug 13 Nov: a tick or quote from minutes
 * ago was shown over the chain's newer spot, so the strip and every higher-timeframe forming bar stood still while the
 * chain moved). On equal time the live tick wins, then the LTP read, then the chain. An LTP the server flags stale is
 * not used; a read without any time loses to one with a time.
 */
export function pickSpot(tick: LiveTick | undefined, ltp: Pick<LtpResponse, "ltp" | "timestamp" | "fetched_at"> & Partial<Pick<LtpResponse, "stale">> | null,
  chain: LiveChain | null): TerminalSpot | null {
  const options: TerminalSpot[] = [];
  if (tick && Number.isFinite(tick.ltp) && tick.ltp > 0) options.push({ ltp: tick.ltp, at: tick.ts || null, source: "tick" });
  if (ltp && !ltp.stale && Number.isFinite(ltp.ltp) && ltp.ltp > 0) options.push({ ltp: ltp.ltp, at: ltp.timestamp ?? ltp.fetched_at ?? null, source: "ltp" });
  const s = chain?.summary.spot;
  if (typeof s === "number" && s > 0) options.push({ ltp: s, at: chain?.data_as_of ?? chain?.fetched_at ?? null, source: "chain" });
  let best: TerminalSpot | null = null;
  for (const o of options) if (best === null || timeOf(o.at) > timeOf(best.at)) best = o;   // strict: earlier entries win ties
  return best;
}

/** Pure: the "as of 12:05" note of a spot that is not current (older than SPOT_AS_OF_AFTER_MS, or the market closed). */
export function spotAsOf(spot: TerminalSpot | null, marketOpen: boolean | null | undefined, now: number): string | null {
  if (!spot?.at) return null;
  const t = timeOf(spot.at);
  if (marketOpen !== false && t !== -Infinity && now - t <= SPOT_AS_OF_AFTER_MS) return null;
  return spot.at;
}

/** The spot as the chart's live price: a live read, or the chain's spot while the market is open (it is the newest
 * read then). A closed market's chain spot (the last close) never moves the chart. */
export function chartLiveOf(spot: TerminalSpot | null, chainOpen = false): ChartLive | null {
  if (!spot || (spot.source === "chain" && !chainOpen)) return null;
  return { ltp: spot.ltp, timestamp: spot.at, fetched_at: spot.at ?? "" };
}

/** Pure: the tick only while this tab heard it within TICK_FRESH_MS. */
export function freshTick(tick: LiveTick | undefined, seenAt: number | undefined, now: number): LiveTick | undefined {
  return tick && seenAt !== undefined && now - seenAt <= TICK_FRESH_MS ? tick : undefined;
}

export function useTerminalSpot({ symbol, exchange = "NSE", hub, enabled, chain }: {
  symbol: string; exchange?: string; hub: boolean; enabled: boolean; chain: LiveChain | null;
}): TerminalSpot | null {
  const items = useMemo(() => [{ symbol, exchange }], [symbol, exchange]);
  const ticks = useLiveTicks(items, enabled && hub);
  const heard = ticks.get(tickKey(symbol, exchange));
  // Re-judged on every render (the chain's read re-renders the page every 15 s while open): a stopped stream's last tick
  // goes out and the LTP read below comes back.
  const tick = hub ? freshTick(heard, heard ? liveTicks().tickSeenAt(symbol, exchange) : undefined, Date.now()) : undefined;
  // The same read the chart makes on its own (src/chart/EmbeddedChartV2.tsx): while the market is open.
  const { ltp } = useLiveLtp(enabled && !tick && chain?.market_open !== false, symbol, exchange);
  return useMemo(() => pickSpot(tick, ltp, chain), [tick, ltp, chain]);
}
