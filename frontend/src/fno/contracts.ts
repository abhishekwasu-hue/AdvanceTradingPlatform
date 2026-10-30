/**
 * FNO-4 (owner order sections 2 and 8): F&O contracts in the watchlists and on the positions page - the pure parts
 * (tested in contracts.test.tsx) and the two read-only calls (backend app/paper/fno_contracts.py).
 *
 * - A contract row is the watchlist's existing item `{symbol, exchange}` on NFO / BFO (the chain's "Add to watchlist"
 *   saves it); the server says what it is: its name as traders write it, the days left, "Expired", and the roll target.
 * - "Roll to next expiry" only EDITS THE WATCHLIST: the expired row is replaced in place by the same strike in the next
 *   expiry. It opens no position and places nothing.
 * - Positions: Add opens the ONE order ticket (PAPER) on the same contract and side; Adjust opens the chain of that
 *   expiry (roll a strike, add a hedge - the user picks there); Exit is the page's own "Exit now", never blocked here.
 *   Legs placed together (one basket, one structure) show together with their combined P&L.
 * Information only: nothing here tells the user what to trade, and no level is ever suggested.
 */
import { request } from "../api/client";
import type { WatchItem, WatchlistV2, ListEdit } from "../chart/watchlistsV2";
import { itemKey } from "../chart/watchlistsV2";
import type { TradeRecord } from "../types";
import { chartsPath } from "../shell/shellRoutes";
import { formatCount } from "../ui/format";
import type { ContractRight, Side } from "./api";
import type { TicketTarget } from "./ticketModel";

export const DERIVATIVE_EXCHANGES = ["NFO", "BFO"] as const;
/** The server's limit for one lookup (app/paper/fno_contracts.py MAX_ITEMS). */
export const LOOKUP_MAX = 60;

export interface RollTarget {
  symbol: string;
  exchange: string;
  underlying: string;
  right: ContractRight;
  strike: number | null;
  expiry: string | null;
  lot_size: number;
  label: string;
}

/** `POST /api/paper/fno/contracts` row. `known` false: not a contract ATP can read (shown as a plain row). */
export type ContractFacts =
  | { symbol: string; exchange: string; known: false }
  | {
    symbol: string;
    exchange: string;
    known: true;
    /** Still in the instrument master (false: read from the trading symbol itself). */
    listed: boolean;
    underlying: string;
    right: ContractRight;
    strike: number | null;
    expiry: string | null;
    lot_size: number | null;
    label: string;
    expired: boolean;
    days_left: number | null;
    spot_symbol: string;
    spot_exchange: string;
    roll: RollTarget | null;
    roll_note: string | null;
  };
export type KnownContract = Extract<ContractFacts, { known: true }>;

export interface PositionGroup {
  key: string; kind: "basket" | "structure"; name: string; trade_ids: number[];
  /** TT-8: a basket's bought legs that protect an open sold leg ("Exit this leg" warns first). */
  hedge_ids?: number[];
}

export const contractsApi = {
  lookup: (items: readonly WatchItem[]) =>
    request<{ items: ContractFacts[] }>("/paper/fno/contracts", { method: "POST", body: JSON.stringify({ items: items.map(({ symbol, exchange }) => ({ symbol, exchange })) }) }),
  groups: () => request<{ groups: PositionGroup[] }>("/paper/fno/positions/groups"),
};
export type ContractsApi = typeof contractsApi;

// --- watchlist rows ------------------------------------------------------------------------------------------------------

export const isContractItem = (item: Pick<WatchItem, "exchange">): boolean =>
  (DERIVATIVE_EXCHANGES as readonly string[]).includes(item.exchange.toUpperCase());

/** The contract rows of a list that get looked up (the first LOOKUP_MAX). */
export const contractItems = (items: readonly WatchItem[]): WatchItem[] => items.filter(isContractItem).slice(0, LOOKUP_MAX);

/** The lookup's identity: the set of contract rows, in any order (a reorder asks for nothing new). */
export const contractsKey = (items: readonly WatchItem[]): string => contractItems(items).map(itemKey).sort().join("|");

export const known = (f: ContractFacts | undefined): f is KnownContract => !!f && f.known;

/** The rows still trading first, in list order; the expired contracts after them ("Expired"), in list order. */
export function splitExpired<T extends WatchItem>(items: readonly T[], facts: ReadonlyMap<string, ContractFacts>): { live: T[]; expired: T[] } {
  const live: T[] = [];
  const expired: T[] = [];
  for (const item of items) {
    const f = facts.get(itemKey(item));
    (known(f) && f.expired ? expired : live).push(item);
  }
  return { live, expired };
}

/** The roll as one watchlist edit: the expired row replaced in place by the next expiry's contract. Null without a target. */
export function rollEdit(item: WatchItem, facts: ContractFacts | undefined): ListEdit | null {
  if (!known(facts) || !facts.expired || !facts.roll) return null;
  return { kind: "replace", item, with: { symbol: facts.roll.symbol, exchange: facts.roll.exchange } };
}

/** Whether a list already holds the roll target (rolling then only removes the expired row). */
export function holdsTarget(list: Pick<WatchlistV2, "items">, facts: ContractFacts | undefined): boolean {
  if (!known(facts) || !facts.roll) return false;
  const key = itemKey(facts.roll);
  return list.items.some((i) => itemKey(i) === key);
}

/** Open interest the way Indian traders read it: 1.25 Cr, 12.5 L, 85,000 (M61 OI-1: the app's one count formatter);
 * null when there is none. */
export function formatOi(oi: number | null | undefined): string | null {
  if (typeof oi !== "number" || !Number.isFinite(oi) || oi <= 0) return null;
  return formatCount(oi);
}

/** "Expires today", "Expires tomorrow", "6 days left"; null when not known. */
export function countdown(daysLeft: number | null | undefined): string | null {
  if (typeof daysLeft !== "number") return null;
  if (daysLeft <= 0) return "Expires today";
  if (daysLeft === 1) return "Expires tomorrow";
  return `${daysLeft} days left`;
}

/** The second line of a contract row: "6 days left · OI 12.5 L" (the parts known). */
export function contractSubline(facts: KnownContract, oi: number | null | undefined): string {
  if (facts.expired) return "Expired";
  return [countdown(facts.days_left), formatOi(oi) ? `OI ${formatOi(oi)}` : null].filter(Boolean).join(" · ") || facts.exchange;
}

// --- positions --------------------------------------------------------------------------------------------------------------

export const isFnoPosition = (p: Pick<TradeRecord, "instrument_kind">): boolean => p.instrument_kind === "OPTION" || p.instrument_kind === "FUTURE";

/** The position's contract as a watch item (its trading symbol on its exchange). */
export function positionItem(p: Pick<TradeRecord, "symbol" | "exchange" | "instrument_kind">): WatchItem {
  return { symbol: p.symbol.toUpperCase(), exchange: (p.exchange ?? "NFO").toUpperCase() };
}

/** The side that ADDS to a position: Buy for a long one, Sell for a short one. */
export const addSide = (p: Pick<TradeRecord, "direction">): Side => (p.direction.toUpperCase() === "SHORT" ? "SELL" : "BUY");

/** The ticket's target for Add: the same contract, the position's last price as the price helper. Null while the contract
 * is not known or has expired (nothing to add to). */
export function positionTarget(p: Pick<TradeRecord, "lot_size" | "entry_price">, facts: ContractFacts | undefined, ltp: number | null): TicketTarget | null {
  if (!known(facts) || facts.expired || !facts.expiry) return null;
  return { underlying: facts.underlying, expiry: facts.expiry, strike: facts.right === "FUT" ? null : facts.strike, right: facts.right,
    label: facts.label, lotSize: facts.lot_size ?? p.lot_size ?? null, ltp, bid: null, ask: null };
}

/** Adjust: the chain of the position's expiry (an option) or the futures chain (a future), where the user picks the change. */
export function adjustHref(facts: ContractFacts | undefined): string | null {
  if (!known(facts) || facts.expired) return null;
  if (facts.right === "FUT") {
    const q = new URLSearchParams({ hub: "futures" });
    if (facts.spot_exchange && facts.spot_exchange !== "NSE") q.set("exchange", facts.spot_exchange);
    if (facts.expiry) q.set("expiry", facts.expiry);
    return `${chartsPath(facts.spot_symbol)}?${q.toString()}`;
  }
  const q = new URLSearchParams({ underlying: facts.underlying });
  if (facts.expiry) q.set("expiry", facts.expiry);
  return `/option-chain?${q.toString()}`;
}

export interface GroupedLegs<T extends { id: number; pnl: number | null }> {
  group: PositionGroup;
  legs: T[];
  /** The legs' P&L added up; null while any leg has no live P&L (never a partial sum shown as the whole). */
  pnl: number | null;
}

/** The rows in groups (legs placed together, in the rows' order) and the rows on their own. A group needs two or more of
 * the rows on screen; a leg is in one group at most. Pure. */
export function groupRows<T extends { id: number; pnl: number | null }>(rows: readonly T[], groups: readonly PositionGroup[]): { grouped: GroupedLegs<T>[]; single: T[] } {
  const taken = new Set<number>();
  const grouped: GroupedLegs<T>[] = [];
  for (const group of groups) {
    const ids = new Set(group.trade_ids);
    const legs = rows.filter((r) => ids.has(r.id) && !taken.has(r.id));
    if (legs.length < 2) continue;
    legs.forEach((l) => taken.add(l.id));
    grouped.push({ group, legs, pnl: combinedPnl(legs) });
  }
  return { grouped, single: rows.filter((r) => !taken.has(r.id)) };
}

export function combinedPnl(legs: readonly { pnl: number | null }[]): number | null {
  if (legs.length === 0 || legs.some((l) => typeof l.pnl !== "number" || !Number.isFinite(l.pnl))) return null;
  return Math.round(legs.reduce((s, l) => s + (l.pnl as number), 0) * 100) / 100;
}

/** "Basket · 4 legs", "Iron Condor · 4 legs". */
export function groupTitle(g: Pick<GroupedLegs<{ id: number; pnl: number | null }>, "group" | "legs">): string {
  return `${g.group.name} · ${g.legs.length} legs`;
}
