/** AMW-2 (flag `watchlists`): the caller's own named watchlists - saved symbol lists the Screener and the Scanner load
 * as their universe. 404 while the flag is off; the pages then show nothing of it. */
import { request } from "../api/client";

export interface Watchlist { id: number; name: string; symbols: string[]; count: number; created_at: string | null; updated_at: string | null }

export interface WatchlistClient {
  list: () => Promise<Watchlist[]>;
  create: (name: string, symbols: string[]) => Promise<Watchlist>;
  update: (id: number, name: string, symbols: string[]) => Promise<Watchlist>;
  remove: (id: number) => Promise<unknown>;
}

export const watchlistsApi: WatchlistClient = {
  list: () => request<Watchlist[]>("/watchlists"),
  create: (name, symbols) => request<Watchlist>("/watchlists", { method: "POST", body: JSON.stringify({ name, symbols }) }),
  update: (id, name, symbols) => request<Watchlist>(`/watchlists/${id}`, { method: "PUT", body: JSON.stringify({ name, symbols }) }),
  remove: (id) => request<unknown>(`/watchlists/${id}`, { method: "DELETE" }),
};

export function isWatchlists(value: unknown): value is Watchlist[] {
  return Array.isArray(value) && value.every((w) => !!w && typeof w === "object" && typeof (w as Watchlist).id === "number"
    && typeof (w as Watchlist).name === "string" && Array.isArray((w as Watchlist).symbols));
}

/** What loading a list into a page with a symbol cap does, in words ("" when everything fits). */
export function loadNote(list: Watchlist | undefined, max?: number): string {
  if (!list) return "";
  if (list.symbols.length === 0) return `"${list.name}" has no symbols yet.`;
  if (max !== undefined && list.symbols.length > max) return `"${list.name}" has ${list.symbols.length} symbols; the first ${max} are used here.`;
  return "";
}

/** The label of a list in the picker. */
export const optionLabel = (w: Watchlist) => `${w.name} (${w.symbols.length})`;

/** The Scanner's symbol box as its run reads it (comma-separated, upper-case, no blanks). */
export function splitSymbols(text: string): string[] {
  return text.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean);
}

/** The list the picker shows and acts on: the selected one, else the first (after a delete, or before any choice). */
export function shownList(lists: Watchlist[], selected: number | ""): Watchlist | undefined {
  return lists.find((w) => w.id === selected) ?? lists[0];
}

/** The question to ask before replacing a stored list with fewer symbols (null when nothing would be dropped). */
export function replaceWarning(stored: Watchlist, newCount: number): string | null {
  if (newCount >= stored.symbols.length) return null;
  return `"${stored.name}" has ${stored.symbols.length} symbols; replacing it keeps only these ${newCount}. Replace anyway?`;
}
