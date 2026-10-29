import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { MarketStrip } from "../dashboard/types";

/**
 * UI-28 U7.1 (owner, 28 Oct; Kite shows it): the option chain's spot card with its signed change and % against the
 * previous close. The chain read has no previous close, so the index's previous close comes from the Today strip
 * (GET /api/market/strip - NIFTY 50 and BANK NIFTY); the change is the spot ON SCREEN minus that close, so the card's
 * number and its change always agree. Other underlyings show the spot without a change (never a guess).
 */
const STRIP_KEY: Record<string, string> = { NIFTY: "NIFTY 50", BANKNIFTY: "NIFTY BANK" };

/** The previous close of the chain's underlying from the strip, or null. Pure. */
export function prevCloseOf(strip: MarketStrip | null, symbol: string): number | null {
  const key = STRIP_KEY[symbol.trim().toUpperCase()];
  const row = key ? strip?.indices.find((i) => i.key === key) : undefined;
  const v = row?.prev_close;
  return typeof v === "number" && Number.isFinite(v) && v > 0 ? v : null;
}

/** The signed change and % of `spot` against `prev`, or null when either is missing. Pure. */
export function spotChange(spot: number | null | undefined, prev: number | null): { change: number; pct: number } | null {
  if (prev === null || typeof spot !== "number" || !Number.isFinite(spot) || spot <= 0) return null;
  const change = spot - prev;
  return { change, pct: (change / prev) * 100 };
}

/** The previous close for the page's underlying, read once per symbol (it does not move during the session). */
export function usePrevClose(symbol: string): number | null {
  const [prev, setPrev] = useState<number | null>(null);
  useEffect(() => {
    if (!STRIP_KEY[symbol.trim().toUpperCase()]) { setPrev(null); return undefined; }
    let alive = true;
    api.marketStrip().then((s) => { if (alive) setPrev(prevCloseOf(s, symbol)); }, () => { if (alive) setPrev(null); });
    return () => { alive = false; };
  }, [symbol]);
  return prev;
}

/** UI-28 U7.1: the strike-range chips above the chain (Sensibull users: clutter on a phone). 0 = every strike. */
export const STRIKE_RANGES = [0, 5, 10, 15] as const;

/** The rows within `n` listed strikes either side of the ATM strike (all rows for 0, or without an ATM). Pure. */
export function rowsAround<T extends { strike: number }>(rows: readonly T[], atm: number | null | undefined, n: number): T[] {
  if (!n || atm === null || atm === undefined || rows.length === 0) return [...rows];
  let at = 0;
  rows.forEach((r, i) => { if (Math.abs(r.strike - atm) < Math.abs(rows[at].strike - atm)) at = i; });
  return rows.slice(Math.max(0, at - n), at + n + 1);
}
