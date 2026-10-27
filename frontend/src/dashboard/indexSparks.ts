import { useEffect, useState } from "react";
import { api } from "../api/client";
import { istDate } from "../components/chartHistory";
import type { OHLCVBar } from "../types";
import type { MarketStrip } from "./types";

/**
 * Colour v2 (owner, 23 Oct: "a small sparkline on the NIFTY / BANK NIFTY / VIX cards"): the strip's indices drawn as a
 * line of their real 5-minute closes in the last session - the same broker candles the old dashboard's Market pulse
 * read (POST /api/market-data/candles, "5min", 5 calendar days so a weekend or holiday still has the last session).
 * Read only while colour v2 is on and the strip comes from a live broker session; re-read once a minute while the
 * market is open. No session, an error or fewer than two closes: no line at all - never sample data.
 */

/** Closes of the last session (the IST date of the newest bar) in time order. */
export function lastSessionCloses(bars: readonly OHLCVBar[]): number[] {
  if (bars.length === 0) return [];
  const sorted = [...bars].sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
  const day = istDate(sorted[sorted.length - 1]);
  return sorted.filter((b) => istDate(b) === day).map((b) => b.close).filter((c) => Number.isFinite(c));
}

/** The broker to read candles through: the strip's own live broker, else none (market memory has no candles). */
export function sparkBroker(strip: MarketStrip | null): string | null {
  if (!strip || !strip.broker || strip.source === "memory" || strip.source === "none") return null;
  return strip.broker.broker;
}

export const SPARK_REFRESH_MS = 60_000;

export function useIndexSparks(enabled: boolean, strip: MarketStrip | null): Record<string, number[]> {
  const [sparks, setSparks] = useState<Record<string, number[]>>({});
  const broker = enabled ? sparkBroker(strip) : null;
  const keys = strip ? strip.indices.map((i) => i.key).join("|") : "";
  const open = !!strip?.market.is_open;
  useEffect(() => {
    if (!broker || !keys) { setSparks({}); return undefined; }
    let live = true;
    const symbols = keys.split("|");
    const load = () => {
      api.marketDataCandles(symbols, "5min", 5, "NSE", broker).then((r) => {
        if (!live) return;
        const next: Record<string, number[]> = {};
        for (const s of symbols) {
          const row = r.symbols[s.toUpperCase()] ?? r.symbols[s];
          if (row && !row.error) next[s] = lastSessionCloses(row.bars);
        }
        setSparks(next);
      }, () => { if (live) setSparks({}); });
    };
    load();
    const id = open ? window.setInterval(load, SPARK_REFRESH_MS) : undefined;
    return () => { live = false; if (id !== undefined) window.clearInterval(id); };
  }, [broker, keys, open]);
  return sparks;
}
