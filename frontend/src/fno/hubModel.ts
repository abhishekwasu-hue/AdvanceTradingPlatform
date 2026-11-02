/**
 * FNO-3 (owner order ATP_SESSION_FNO_WORKSPACE.md, sections 1 and 4): the symbol hub's pure parts (tested in hub.test.tsx)
 * - its reads (backend app/option_chain/futures_routes.py, behind `ui_v2`), which tabs a symbol has, the addresses that
 * carry the symbol, the expiry and the timeframe across the tabs, the futures rows' words and the Overview's ranges.
 * Nothing here makes up a number: a value the server did not give is shown as "–" or left out.
 */
import { request } from "../api/client";
import { chartsPath } from "../shell/shellRoutes";
import { formatDateIST } from "../ui/format";
import type { FnoContract, Side } from "./api";
import type { BasketLeg } from "./basket";
import type { TicketTarget } from "./ticketModel";

// --- the reads -------------------------------------------------------------------------------------------------------

/** `GET /option-chain/{symbol}/hub`: the instrument master's F&O facts for a symbol (no broker call). */
export interface HubInfo {
  symbol: string;
  /** The F&O underlying ("NIFTY" for the chart's "NIFTY 50"). */
  underlying: string;
  /** The underlying's own chart symbol and exchange ("NIFTY 50", NSE). */
  spot_symbol: string;
  spot_exchange: string;
  has_fno: boolean;
  has_options: boolean;
  has_futures: boolean;
  lot_size: number | null;
  option_expiries: { date: string; kind: "weekly" | "monthly" }[];
  default_option_expiry: string | null;
  future_expiries: string[];
  today: string;
}

/** One future of `GET /option-chain/{symbol}/futures`. A price the broker did not give is null (`missing`). */
export interface FutureRow {
  underlying: string;
  tradingsymbol: string;
  exchange: string;
  instrument_key: string;
  expiry: string;
  lot_size: number;
  tick_size: number;
  /** "NIFTY 28 OCT FUT" */
  label: string;
  days_to_expiry: number;
  ltp: number | null;
  prev_close: number | null;
  change: number | null;
  change_pct: number | null;
  oi: number | null;
  oi_change: number | null;
  volume: number | null;
  bid: number | null;
  ask: number | null;
  basis: number | null;
  basis_pct: number | null;
  missing: boolean;
  error: string | null;
}

export interface Rollover {
  near_expiry: string;
  next_expiry: string;
  near_label: string;
  next_label: string;
  next_share_pct: number | null;
  spread: number | null;
  spread_pct: number | null;
  near_oi: number | null;
  next_oi: number | null;
  /** One plain line with the two numbers. */
  caption: string | null;
}

export interface FuturesChain {
  underlying: string;
  spot: { symbol: string; exchange: string; ltp: number | null; change: number | null; change_pct: number | null };
  rows: FutureRow[];
  rollover: Rollover | null;
  oi_change: { status: "ready" | "building" | "none"; source: string | null; since: string | null };
  market_open?: boolean;
  message?: string | null;
  fetched_at: string;
}

const path = (symbol: string) => `/option-chain/${encodeURIComponent(symbol.trim().toUpperCase())}`;

export const hubApi = {
  info: (symbol: string) => request<HubInfo>(`${path(symbol)}/hub`),
  futures: (symbol: string) => request<FuturesChain>(`${path(symbol)}/futures`),
};
export type HubApi = typeof hubApi;

/** A hub answer the page can trust (a mocked or older server may answer something else: no tabs then). */
export function isHubInfo(v: unknown): v is HubInfo {
  const o = v as Partial<HubInfo> | null;
  return !!o && typeof o.underlying === "string" && typeof o.has_fno === "boolean" && typeof o.spot_symbol === "string"
    && Array.isArray(o.future_expiries) && Array.isArray(o.option_expiries);
}

// --- the tabs --------------------------------------------------------------------------------------------------------

export type HubTab = "chart" | "option-chain" | "futures" | "overview";
export const HUB_LABELS: Record<HubTab, string> = { chart: "Chart", "option-chain": "Option chain", futures: "Futures", overview: "Overview" };

/** The tabs a symbol has: Option chain and Futures only when it has F&O (each when it has that part); none while the
 * facts are not known (the page then shows its own content only, as before). */
export function hubTabs(info: HubInfo | null): HubTab[] {
  if (!info) return [];
  const tabs: HubTab[] = ["chart"];
  if (info.has_fno && info.has_options) tabs.push("option-chain");
  if (info.has_fno && info.has_futures) tabs.push("futures");
  tabs.push("overview");
  return tabs;
}

/** What carries across the tabs: the symbol (both spellings come from the hub facts), the expiry and the timeframe. */
export interface HubCarry {
  expiry: string | null;
  timeframe: string | null;
}

const EXPIRY = /^\d{4}-\d{2}-\d{2}$/;
const TIMEFRAME = /^[a-z0-9]{1,8}$/i;

/** The carried values from an address (`?expiry=2026-10-28&tf=15min`); anything malformed is dropped. */
export function readCarry(search: string | URLSearchParams): HubCarry {
  const p = typeof search === "string" ? new URLSearchParams(search) : search;
  const expiry = (p.get("expiry") ?? "").trim();
  const tf = (p.get("tf") ?? "").trim();
  return { expiry: EXPIRY.test(expiry) ? expiry : null, timeframe: TIMEFRAME.test(tf) ? tf : null };
}

/** The hub tab an address on the Charts page asks for (`?hub=futures` / `?hub=overview`); null = the chart itself. */
export function hubViewOf(search: string | URLSearchParams): "futures" | "overview" | null {
  const p = typeof search === "string" ? new URLSearchParams(search) : search;
  const v = p.get("hub");
  return v === "futures" || v === "overview" ? v : null;
}

/** The address of a hub tab. Chart, Futures and Overview live on the Charts page (`/charts/<symbol>`), the option chain
 * on its own page (`/option-chain?underlying=`). The expiry and the timeframe ride along in every address. */
export function hubHref(tab: HubTab, info: Pick<HubInfo, "underlying" | "spot_symbol" | "spot_exchange">, carry: HubCarry): string {
  const q = new URLSearchParams();
  if (tab === "option-chain") {
    q.set("underlying", info.underlying);
  } else {
    if (tab !== "chart") q.set("hub", tab);
    if (info.spot_exchange && info.spot_exchange !== "NSE") q.set("exchange", info.spot_exchange);
  }
  if (carry.expiry) q.set("expiry", carry.expiry);
  if (carry.timeframe) q.set("tf", carry.timeframe);
  const base = tab === "option-chain" ? "/option-chain" : chartsPath(info.spot_symbol);
  const query = q.toString();
  return query ? `${base}?${query}` : base;
}

/** The option chain page with the basket panel open (the futures chain's basket badge: its legs are reviewed there). */
export function basketHref(underlying: string, timeframe: string | null): string {
  const q = new URLSearchParams({ underlying, basket: "open" });
  if (timeframe) q.set("tf", timeframe);
  return `/option-chain?${q.toString()}`;
}

// --- the futures rows --------------------------------------------------------------------------------------------------

/** The ticket's target for a future (the ONE order ticket of FNO-1, PAPER). */
export function futureTarget(row: FutureRow): TicketTarget {
  return { underlying: row.underlying, expiry: row.expiry, strike: null, right: "FUT", label: row.label, lotSize: row.lot_size,
    ltp: row.ltp, bid: row.bid, ask: row.ask };
}

/** The basket leg of a future: his saved lots (M68 PR-2, TS1-022: else empty - the basket asks), strike 0 (a future has
 * none), the price on screen. */
export function futureLeg(row: FutureRow, side: Side, lots: number | null = null): Omit<BasketLeg, "id"> {
  return { underlying: row.underlying, expiry: row.expiry, strike: 0, right: "FUT", side, lots, price: row.ltp, label: row.label };
}

/** A future as the contract the watchlist helper takes. */
export function futureContract(row: FutureRow): FnoContract {
  return { underlying: row.underlying, tradingsymbol: row.tradingsymbol, exchange: row.exchange, instrument_key: row.instrument_key,
    expiry: row.expiry, strike: null, right: "FUT", lot_size: row.lot_size, tick_size: row.tick_size, label: row.label, freeze_quantity: null };
}

/** "Near month", "Next month", "Far month" by position (the chain is sorted nearest first). */
export function monthWord(index: number): string {
  return index === 0 ? "Near month" : index === 1 ? "Next month" : "Far month";
}

/** "Expires 28 Oct 2026 · 6 days" / "Expires today". */
export function expiryText(row: Pick<FutureRow, "expiry" | "days_to_expiry">): string {
  const days = row.days_to_expiry;
  if (days === 0) return "Expires today";
  return `Expires ${formatDateIST(row.expiry)}${typeof days === "number" && days > 0 ? ` · ${days} day${days === 1 ? "" : "s"}` : ""}`;
}

/** The notes above the futures chain, in plain words. */
export function futuresNotes(chain: FuturesChain | null): string[] {
  if (!chain) return [];
  const out: string[] = [];
  if (chain.market_open === false) out.push("Market closed — showing the last prices.");
  if (chain.oi_change.status === "building" && chain.market_open !== false) out.push("OI change will show after the first few minutes.");
  return out;
}

// --- the Overview -------------------------------------------------------------------------------------------------------

export interface Bar { timestamp: string; open: number; high: number; low: number; close: number; forming?: boolean }

const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** The day's range from the daily bars: today's forming bar ("today"), else the last completed one ("last"); null
 * without bars. */
export function dayRange(bars: Bar[]): { low: number; high: number; which: "today" | "last" } | null {
  const last = bars[bars.length - 1];
  if (!last || !finite(last.low) || !finite(last.high) || last.high <= 0) return null;
  return { low: last.low, high: last.high, which: last.forming ? "today" : "last" };
}

const YEAR_MS = 365 * 24 * 3600 * 1000;
/** Bars must reach back this far (a year less a fortnight of holidays) before a 52-week range is shown. */
const YEAR_COVER_MS = 350 * 24 * 3600 * 1000;

/** The 52-week range from the daily bars of the last year - only when the bars cover the year; null otherwise. */
export function yearRange(bars: Bar[]): { low: number; high: number } | null {
  if (bars.length === 0) return null;
  const end = Date.parse(bars[bars.length - 1].timestamp);
  const start = Date.parse(bars[0].timestamp);
  if (!Number.isFinite(end) || !Number.isFinite(start) || end - start < YEAR_COVER_MS) return null;
  const year = bars.filter((b) => end - Date.parse(b.timestamp) <= YEAR_MS && finite(b.low) && finite(b.high) && b.low > 0);
  if (year.length === 0) return null;
  return { low: Math.min(...year.map((b) => b.low)), high: Math.max(...year.map((b) => b.high)) };
}
