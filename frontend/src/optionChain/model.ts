/**
 * OC1: the automatic Option chain page's pure parts (tested in model.test.ts) - the symbol list and the first symbol,
 * which expiry to show, the plain-word states, the columns, where the ATM row scrolls to and the number formats.
 * Nothing here reads the network or makes up a number: a missing value is shown as "–" (src/ui/format.ts).
 */
import { EMPTY, MINUS, formatNumber } from "../ui/format";
import { FNO_STOCKS } from "../utils/fnoSymbols";
import type { ChainExpiries, ChainLeg, ChainRow, LiveChain } from "./types";

/** The indices first (NIFTY is the default), then the F&O stocks (src/utils/fnoSymbols.ts). */
export const INDEX_SYMBOLS = ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"] as const;
export const STOCK_SYMBOLS: readonly string[] = FNO_STOCKS;
export const DEFAULT_SYMBOL = "NIFTY";

/** A symbol as the page accepts it: letters, digits, "&", "-", "_" and spaces, at most 30 characters (as the server). */
const SYMBOL = /^[A-Z0-9&\-_ ]{1,30}$/;

/** The symbol the page opens on: a deep link's `?underlying=` (an OI alert links one), else NIFTY. */
export function initialSymbol(search: string): string {
  let raw: string | null = null;
  try { raw = new URLSearchParams(search).get("underlying"); } catch { raw = null; }
  const value = (raw ?? "").trim().toUpperCase().replace(/\s+/g, " ");
  return value && SYMBOL.test(value) ? value : DEFAULT_SYMBOL;
}

/** The expiry to show: the one on screen while the list still holds it, else the server's default (the nearest; on an
 * expiry day the next one), else the first listed; null when there is no list (the broker's nearest is read). */
export function chooseExpiry(list: ChainExpiries | null, current: string | null): string | null {
  if (!list || list.expiries.length === 0) return null;
  if (current && list.expiries.some((e) => e.date === current)) return current;
  if (list.default && list.expiries.some((e) => e.date === list.default)) return list.default;
  return list.expiries[0].date;
}

// --- States, in plain words --------------------------------------------------------------------------------------

export const TEXT = {
  building: "OI change will show after the first few minutes",
  closed: "Market closed — showing the last session's chain",
  loadFailed: "We could not load the option chain. We will try again in a moment.",
  noSession: "Log in to your broker to see the option chain.",
  plan: "This symbol is not available live on your plan.",
  empty: "Your broker sent no strikes for this expiry.",
} as const;

export type Note = { key: "closed" | "building" | "since"; text: string };

/** The notes above the chain: market closed (the server's answer for the underlying's exchange; the system status
 * until the first answer), OI change still building, or since when the OI change counts (when it is counted from the
 * first reading of the day, not from the broker). */
export function chainNotes(live: LiveChain | null, marketOpen: boolean | null, sinceText: (iso: string) => string): Note[] {
  const notes: Note[] = [];
  const open = live?.market_open ?? marketOpen;
  if (open === false) notes.push({ key: "closed", text: TEXT.closed });
  if (live && live.oi_change.status === "building" && open !== false) notes.push({ key: "building", text: TEXT.building });
  if (live && live.oi_change.status === "ready" && live.oi_change.source === "first_reading" && live.oi_change.since) {
    notes.push({ key: "since", text: `OI change since ${sinceText(live.oi_change.since)}` });
  }
  return notes;
}

/** How often the chain is read again while the page is shown: every 15 s in market hours, every 5 min otherwise. */
export const POLL_OPEN_MS = 15_000;
export const POLL_CLOSED_MS = 300_000;
export function pollEvery(marketOpen: boolean | null | undefined): number {
  return marketOpen === false ? POLL_CLOSED_MS : POLL_OPEN_MS;
}

// --- Columns -------------------------------------------------------------------------------------------------------

export type ColumnKey = keyof ChainLeg;

export interface ColumnPrefs {
  /** Delta, gamma, theta, vega. */
  greeks: boolean;
  /** Bid and ask. */
  quotes: boolean;
  /** Phone: OI change, IV and volume next to OI and LTP (a desktop always shows them). */
  more: boolean;
  /** FNO-1: OI change in percent of the earlier OI instead of the count. */
  pct?: boolean;
}

export const DEFAULT_COLUMNS: ColumnPrefs = { greeks: false, quotes: false, more: false };
export const COLUMNS_KEY = "atp_option_chain_columns";

export function readColumns(): ColumnPrefs {
  try {
    const raw = JSON.parse(localStorage.getItem(COLUMNS_KEY) ?? "{}") as Partial<ColumnPrefs>;
    return { greeks: raw.greeks === true, quotes: raw.quotes === true, more: raw.more === true, pct: raw.pct === true };
  } catch {
    return { ...DEFAULT_COLUMNS };
  }
}

export function writeColumns(prefs: ColumnPrefs): void {
  try { localStorage.setItem(COLUMNS_KEY, JSON.stringify(prefs)); } catch { /* storage unavailable */ }
}

export interface ColumnDef {
  key: ColumnKey;
  label: string;
  /** "core": always (phone too); "main": desktop always, phone with `more`; "greeks" / "quotes": with that choice. */
  group: "core" | "main" | "greeks" | "quotes";
}

/** The calls' columns, outside in: OI at the outer edge, LTP next to the strike. The puts mirror them. */
export const CALL_COLUMNS: readonly ColumnDef[] = [
  { key: "delta", label: "Delta", group: "greeks" },
  { key: "gamma", label: "Gamma", group: "greeks" },
  { key: "theta", label: "Theta", group: "greeks" },
  { key: "vega", label: "Vega", group: "greeks" },
  { key: "bid", label: "Bid", group: "quotes" },
  { key: "ask", label: "Ask", group: "quotes" },
  { key: "oi", label: "OI", group: "core" },
  { key: "oi_change", label: "OI change", group: "main" },
  { key: "volume", label: "Volume", group: "main" },
  { key: "iv", label: "IV", group: "main" },
  { key: "ltp", label: "LTP", group: "core" },
];
export const PUT_COLUMNS: readonly ColumnDef[] = [...CALL_COLUMNS].reverse();

/** Where a column shows: "all" = phone and desktop, "desktop" = from the md breakpoint only (the phone default),
 * "none" = not chosen under Columns. */
export function columnShown(col: ColumnDef, prefs: ColumnPrefs): "all" | "desktop" | "none" {
  if (col.group === "core") return "all";
  if (col.group === "main") return prefs.more ? "all" : "desktop";
  return prefs[col.group] ? "all" : "none";
}

// --- The table --------------------------------------------------------------------------------------------------

/** In the money: a call below the spot, a put above it. Unknown spot: neither. */
export function isItm(side: "call" | "put", strike: number, spot: number | null | undefined): boolean {
  if (typeof spot !== "number" || !Number.isFinite(spot)) return false;
  return side === "call" ? strike < spot : strike > spot;
}

/** The highest OI on one side (for the bars); 0 when no strike has one. */
export function maxOi(rows: readonly ChainRow[], side: "call" | "put"): number {
  return rows.reduce((m, r) => Math.max(m, r[side].oi ?? 0), 0);
}

/** A bar's width in percent of the side's highest OI (0-100). */
export function barPercent(oi: number | null | undefined, max: number): number {
  if (typeof oi !== "number" || !Number.isFinite(oi) || oi <= 0 || max <= 0) return 0;
  return Math.min(100, Math.round((oi / max) * 1000) / 10);
}

/** The scroll position that puts a row's middle in the middle of the visible area (never above the top). */
export function centreScrollTop(rowTop: number, rowHeight: number, viewHeight: number): number {
  return Math.max(0, Math.round(rowTop + rowHeight / 2 - viewHeight / 2));
}

/** The index of the ATM row (-1 when there is none). */
export function atmIndex(rows: readonly ChainRow[], atm: number | null | undefined): number {
  return typeof atm === "number" ? rows.findIndex((r) => r.strike === atm) : -1;
}

// --- Numbers -----------------------------------------------------------------------------------------------------

/** A strike: whole numbers without decimals (24,500), others with two (22.50). */
export function formatStrike(v: number | null | undefined): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return EMPTY;
  return formatNumber(v, Number.isInteger(v) ? 0 : 2);
}

/** A count (OI, volume) in the Indian way: 85,000; 12.35 L; 1.24 Cr. */
export function formatCount(v: number | null | undefined): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return EMPTY;
  const abs = Math.abs(v);
  const sign = v < 0 ? MINUS : "";
  const trim = (s: string) => s.replace(/\.?0+$/, "");
  if (abs >= 1e7) return `${sign}${trim(formatNumber(abs / 1e7, 2))} Cr`;
  if (abs >= 1e5) return `${sign}${trim((abs / 1e5).toFixed(2))} L`;
  return `${sign}${formatNumber(abs, 0)}`;
}

/** A change in a count: "+12.35 L", "−4,500", "0". */
export function formatCountChange(v: number | null | undefined): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return EMPTY;
  const text = formatCount(v);
  return v > 0 ? `+${text}` : text;
}

/** FNO-1: an OI change in percent of the OI before it ("+12.5%", "−4.0%"); "–" when the earlier OI is not known or 0. */
export function formatOiChangePct(oi: number | null | undefined, change: number | null | undefined): string {
  if (typeof oi !== "number" || typeof change !== "number" || !Number.isFinite(oi) || !Number.isFinite(change)) return EMPTY;
  const before = oi - change;
  if (before <= 0) return EMPTY;
  const pct = (change / before) * 100;
  const text = formatNumber(Math.abs(pct), 1);
  return pct > 0 ? `+${text}%` : pct < 0 ? `${MINUS}${text}%` : "0.0%";
}

/** IV in percent: "13.2%". */
export function formatIv(v: number | null | undefined): string {
  return typeof v === "number" && Number.isFinite(v) ? `${formatNumber(v, 1)}%` : EMPTY;
}

/** A Greek: delta with two decimals, the others with four (gamma is small) or two. */
export function formatGreek(key: "delta" | "gamma" | "theta" | "vega", v: number | null | undefined): string {
  return formatNumber(v, key === "gamma" ? 4 : 2);
}

/** Days to expiry: "Today", "1 day", "6 days". */
export function daysText(days: number | null | undefined): string {
  if (typeof days !== "number" || !Number.isFinite(days) || days < 0) return EMPTY;
  return days === 0 ? "Today" : days === 1 ? "1 day" : `${days} days`;
}
