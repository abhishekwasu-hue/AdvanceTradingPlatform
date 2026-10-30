/**
 * WL1 (handover item 4): the read-only system watchlists - "Indices", "Index stocks", "Sectors" and "F&O" - shown before
 * the trader's own lists ("My lists"). The server builds them from the exchange's index and classification files
 * (app/watchlists/system.py); here they are read, searched, sorted and copied. Nothing here changes a system list (the
 * server answers 403) and nothing places an order.
 */
import { request } from "../api/client";
import type { QuoteRow, WatchItem, WatchlistV2 } from "./watchlistsV2";
import { MAX_ITEMS, itemKey } from "./watchlistsV2";

export interface SystemListSummary {
  key: string;
  group: string;
  group_label: string;
  name: string;
  count: number;
  read_only: true;
  refreshed_at?: string | null;
  changed_at?: string | null;
}
export interface SystemItem extends WatchItem { name?: string }
export interface SystemList extends SystemListSummary { items: SystemItem[] }
export interface SystemGroup { group: string; label: string; lists: SystemListSummary[] }
/** M55 T3: `fallback` = the daily universe sync is off, so the lists are the stored fallback copy (LISTS_FALLBACK_NOTE). */
export interface SystemListsReply { groups: SystemGroup[]; refreshed_at?: string | null; fallback?: boolean }
/** M55 T3: the one line a page shows above index / sector lists while the daily universe sync is off. */
export const LISTS_FALLBACK_NOTE = "These index and sector lists are a fallback copy: the daily refresh from the exchange is off, so they may be out of date.";
export interface CopyReply extends WatchlistV2 { copied_from: string; truncated: boolean; source_count: number }

export const systemListsApi = {
  groups: () => request<SystemListsReply>("/watchlists/system"),
  list: (key: string) => request<SystemList>(`/watchlists/system/${encodeURIComponent(key)}`),
  copy: (key: string, name?: string) =>
    request<CopyReply>(`/watchlists/system/${encodeURIComponent(key)}/copy`, { method: "POST", body: JSON.stringify(name ? { name } : {}) }),
};
export type SystemListsApi = typeof systemListsApi;

/** The list on screen: one of the system lists (by key) or one of the trader's own (by id). */
export type ListRef = { kind: "system"; key: string } | { kind: "own"; id: number };
export const MY_LISTS = "MINE";

export interface BrowserEntry { ref: ListRef; name: string; count: number; readOnly: boolean }
export interface BrowserGroup { id: string; label: string; entries: BrowserEntry[] }

const has = (text: string, query: string) => text.toLowerCase().includes(query.trim().toLowerCase());
export const sameRef = (a: ListRef | null, b: ListRef | null): boolean =>
  !!a && !!b && (a.kind === "system" ? b.kind === "system" && a.key === b.key : b.kind === "own" && a.id === b.id);

/** The groups as the list browser shows them: the system groups in the server's order, then "My lists". With a search,
 * only the lists whose name matches, and only the groups that have one. Pure. */
export function browserGroups(system: readonly SystemGroup[], own: readonly WatchlistV2[], query = ""): BrowserGroup[] {
  const groups: BrowserGroup[] = system.map((g) => ({
    id: g.group, label: g.label,
    entries: g.lists.map((l) => ({ ref: { kind: "system", key: l.key }, name: l.name, count: l.count, readOnly: true })),
  }));
  groups.push({ id: MY_LISTS, label: "My lists", entries: own.map((l) => ({ ref: { kind: "own", id: l.id }, name: l.name, count: l.items.length, readOnly: false })) });
  if (!query.trim()) return groups;
  return groups.map((g) => ({ ...g, entries: g.entries.filter((e) => has(e.name, query)) })).filter((g) => g.entries.length > 0);
}

/** True when a list's name already ends with its count ("NIFTY 50 stocks (50)"), so the count is not shown twice. */
export const namesCount = (name: string): boolean => /\(\d+\)$/.test(name.trim());

/** The group a list belongs to (to open it in the browser). */
export function groupOf(ref: ListRef | null, system: readonly SystemGroup[]): string | null {
  if (!ref) return null;
  if (ref.kind === "own") return MY_LISTS;
  return system.find((g) => g.lists.some((l) => l.key === ref.key))?.group ?? null;
}

/** Which list to show: the one the page remembers when it still exists; else the trader's first own list (as before);
 * else the NIFTY 50 stocks (day-one fix 8), else the system "Indices" list (a ready list on the first open), else the first system list; null when there is none. */
export function pickList(systemKey: string | null, listId: number | null, own: readonly WatchlistV2[], system: readonly SystemGroup[]): ListRef | null {
  const keys = system.flatMap((g) => g.lists.map((l) => l.key));
  if (systemKey && keys.includes(systemKey)) return { kind: "system", key: systemKey };
  if (listId != null && own.some((l) => l.id === listId)) return { kind: "own", id: listId };
  if (own.length > 0) return { kind: "own", id: own[0].id };
  // Day-one fix 8: a list of stocks to look through first - the NIFTY 50 stocks (Find setups and Signals open on them too)
  if (keys.includes("index-nifty50")) return { kind: "system", key: "index-nifty50" };
  if (keys.includes("indices")) return { kind: "system", key: "indices" };
  return keys.length > 0 ? { kind: "system", key: keys[0] } : null;
}

/** The rows matching a search on the symbol or the company name (any case), in the list's order. Pure. */
export function filterRows<T extends SystemItem>(items: readonly T[], query: string): T[] {
  const q = query.trim();
  if (!q) return [...items];
  return items.filter((i) => has(i.symbol, q) || has(i.name ?? "", q));
}

export type RowSort = "list" | "az" | "gainers" | "losers" | "price";
export const ROW_SORTS: { id: RowSort; label: string }[] = [
  { id: "list", label: "List order" }, { id: "az", label: "A to Z" }, { id: "gainers", label: "Top gainers" },
  { id: "losers", label: "Top losers" }, { id: "price", label: "Price, high to low" },
];

/** The rows in the chosen order. Gainers / losers / price sort the rows that have a price; the rows without one keep the
 * list's order after them (a missing price is never read as 0). Stable. Pure. */
export function sortRows<T extends SystemItem>(items: readonly T[], sort: RowSort, quotes: ReadonlyMap<string, QuoteRow>): T[] {
  if (sort === "list") return [...items];
  if (sort === "az") return [...items].sort((a, b) => a.symbol.localeCompare(b.symbol));
  const value = (i: T): number | null => {
    const q = quotes.get(itemKey(i));
    if (!q || q.missing) return null;
    const v = sort === "price" ? q.ltp : q.change_pct;
    return v != null && Number.isFinite(v) ? v : null;
  };
  const known = items.filter((i) => value(i) !== null);
  const unknown = items.filter((i) => value(i) === null);
  const sign = sort === "losers" ? 1 : -1;
  return [...known.sort((a, b) => sign * ((value(a) as number) - (value(b) as number))), ...unknown];
}

/** What "Copy to my list" says when it is done, in plain words. */
export function copyNote(reply: Pick<CopyReply, "name" | "truncated" | "source_count" | "items">): string {
  if (reply.truncated) {
    return `Copied to your list "${reply.name}": the first ${reply.items.length} of ${reply.source_count} symbols (a list holds ${MAX_ITEMS}). You can change it.`;
  }
  return `Copied to your list "${reply.name}". You can change it.`;
}
