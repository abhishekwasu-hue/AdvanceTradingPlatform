/**
 * N4 CH4b: option / future contract charts and synthetic spreads - daily bars and OI from the shared market-data history
 * (`GET /api/charts/contracts/series`), contract search from the instrument master (`/search`), and the links from the
 * option chain to a contract's chart. Flag `chart_contracts`; off -> 503 and no link is shown. Every number is the
 * server's: a spread's close is the sum of its legs' closes x quantity (no OHLC is invented for it), and the IV is
 * COMPUTED (Black-76 from the close and the same-expiry future), always labelled so. Reference only - no order here.
 */
import { ApiError, request } from "../api/client";
import type { OHLCVBar } from "../types";

export interface ContractRow { key: string; tradingsymbol: string; underlying: string; kind: "CE" | "PE" | "FUT"; expiry: string; strike: number | null; lot_size: number }
export interface ContractSearch { underlying: string; contracts: ContractRow[]; total: number; note: string }
export interface SeriesLeg {
  key: string; qty: number; underlying: string; kind: "CE" | "PE" | "FUT"; expiry: string; strike: number | null; bars: number;
  oi: (number | null)[]; close: number[]; sources: string[]; iv?: (number | null)[];
}
export interface ContractSeries {
  schema: "contract/1"; at: string; start: string; days: string[]; times: number[]; legs: SeriesLeg[]; note: string;
  ohlc?: { o: number[]; h: number[]; l: number[]; c: number[]; v: number[] }; spread: number[]; iv_note?: string; reason?: string;
}

export const contractsApi = {
  search: (underlying: string, opts: { kind?: string; expiry?: string; near?: number; limit?: number } = {}) => {
    const q = new URLSearchParams({ underlying });
    for (const [k, v] of Object.entries(opts)) if (v != null && v !== "") q.set(k, String(v));
    return request<ContractSearch>(`/charts/contracts/search?${q.toString()}`);
  },
  series: (legs: string[], iv = true) => {
    const q = new URLSearchParams();
    for (const leg of legs) q.append("leg", leg);
    if (iv) q.set("iv", "true");
    return request<ContractSeries>(`/charts/contracts/series?${q.toString()}`);
  },
};

/** The backend's strike text (the bhavcopy's): 25000 -> "25000", 112.5 -> "112.5". */
export function strikeText(strike: number): string {
  return strike.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
}

/** The shared lake's key of a contract: NFO:<SYM>:FUT:<expiry> or NFO:<SYM>:<CE|PE>:<expiry>:<strike>. */
export function contractKey(underlying: string, kind: "CE" | "PE" | "FUT", expiry: string, strike?: number | null): string {
  const base = `NFO:${underlying.trim().toUpperCase()}:${kind}:${expiry}`;
  return kind === "FUT" ? base : `${base}:${strikeText(Number(strike ?? 0))}`;
}

/** The full-window chart of one contract or a spread: `/?contract=<leg>&contract=<leg>` (a leg is `[qty*]KEY`). */
export function contractChartUrl(legs: string[]): string {
  const q = new URLSearchParams();
  for (const leg of legs) q.append("contract", leg);
  return `/?${q.toString()}`;
}

/** A short name: "CH4BIDX 27 Oct 25000 CE", "-2 x ..." for a leg with a quantity. */
export function legName(leg: Pick<SeriesLeg, "underlying" | "kind" | "expiry" | "strike" | "qty">): string {
  const d = new Date(`${leg.expiry}T00:00:00Z`);
  const exp = Number.isNaN(d.getTime()) ? leg.expiry : d.toLocaleDateString("en-IN", { timeZone: "UTC", day: "2-digit", month: "short" });
  const body = `${leg.underlying} ${exp}${leg.kind === "FUT" ? " FUT" : ` ${strikeText(leg.strike ?? 0)} ${leg.kind}`}`;
  return leg.qty === 1 ? body : `${leg.qty > 0 ? "+" : ""}${leg.qty} x ${body}`;
}

/** The chart's candles: a single contract's own daily OHLC; a spread's close only (open = high = low = close, said so). */
export function toCandles(s: ContractSeries): OHLCVBar[] {
  return s.times.map((t, i) => {
    const ts = new Date(t * 1000).toISOString();
    if (s.ohlc) return { timestamp: ts, open: s.ohlc.o[i], high: s.ohlc.h[i], low: s.ohlc.l[i], close: s.ohlc.c[i], volume: s.ohlc.v[i] };
    const c = s.spread[i];
    return { timestamp: ts, open: c, high: c, low: c, close: c, volume: 0 };
  });
}

/** "18.4 %" for a computed IV (per year), "n/a" when it could not be solved. */
export function ivText(iv: number | null | undefined): string {
  return iv == null ? "n/a" : `${(iv * 100).toFixed(1)} %`;
}

/** Whether contract charts are on for this organisation (a search answering 503 means the flag is off). */
export async function contractsAvailable(underlying: string): Promise<boolean> {
  try {
    await contractsApi.search(underlying, { limit: 1 });
    return true;
  } catch (e) {
    return !(e instanceof ApiError && e.status === 503);
  }
}

/** Chain -> chart links for one strike of a live chain (never a sample chain: its expiry is not a real contract). */
export function chainLinks(chain: { underlying: string; expiry: string } | null, strike: number): { ce: string; pe: string } | null {
  if (!chain || !/^\d{4}-\d{2}-\d{2}$/.test(chain.expiry) || !Number.isFinite(strike) || strike <= 0) return null;
  return {
    ce: contractChartUrl([contractKey(chain.underlying, "CE", chain.expiry, strike)]),
    pe: contractChartUrl([contractKey(chain.underlying, "PE", chain.expiry, strike)]),
  };
}
