/**
 * UI2-4c (Charts page plan §3, CHP-4 / CHP-7): the Charts page's watchlists - the client for the UI2-4a1 rows (symbol
 * and exchange, kept in order, with a version), the UI2-4a2 batch quotes, and a store that saves each edit with the
 * list's version. Behind the `watchlists` flag (every route answers 404 while it is off; the page then shows nothing
 * of it). A watchlist is a list of names: nothing here places, changes or proposes an order.
 *
 * Saving: the list on screen is the server's last answer with the edits still being saved applied on top, so a change
 * shows at once. Edits are saved one at a time, each on the version the server last gave. A 409 (the list changed on
 * another device) applies the same edit once more to the server's current list, so neither device loses its change;
 * a second 409 shows the server's list and says so.
 */
import { ApiError } from "../api/errors";
import { request } from "../api/client";
import { startPolling } from "../dashboard/useDashboardData";

export interface WatchItem { symbol: string; exchange: string }

/** `GET /api/watchlists` row (UI2-4a1 fields; `symbols` is the plain list the Screener reads). */
export interface WatchlistV2 {
  id: number;
  name: string;
  items: WatchItem[];
  version: number;
  symbols?: string[];
  position?: number | null;
}

/** One `POST /api/market-data/quotes` row: a real price, or `missing` with the reason (never 0). */
export interface QuoteRow {
  symbol: string;
  exchange: string;
  ltp: number | null;
  change: number | null;
  change_pct: number | null;
  missing: boolean;
  stale?: boolean | null;
  error?: string | null;
  /** FNO-4: a contract's open interest when the broker's quote carries it. */
  oi?: number | null;
}
export interface QuotesReply { items: QuoteRow[]; fetched_at?: string; warnings?: string[] }

/** The server's limits (AMW-2, CHP-7) and the quote budget: one batch for the first 50 rows every 10 s. */
export const MAX_ITEMS = 500;
export const NAME_MAX = 60;
export const QUOTE_ROWS = 50;
export const QUOTE_POLL_MS = 10_000;

export const CHANGED_ELSEWHERE = "This list changed on another device. The latest version is shown.";
export const MERGED = "This list also changed on another device. Both changes are kept.";
export const DELETED_ELSEWHERE = "This list was deleted on another device.";
export const SWITCHED_OFF = "Watchlists are switched off for your account just now. Your lists are kept; try again later.";

/** A save refused because the list changed elsewhere: "changed" (409, a stale version) or "deleted" (404 on the list).
 * `current` is the list the 409 carried when its body could be read; null otherwise (the store then reads the lists). */
export class WatchlistConflict extends Error {
  readonly kind: "changed" | "deleted";
  readonly current: WatchlistV2 | null;

  constructor(kind: "changed" | "deleted", current: WatchlistV2 | null = null) {
    super(kind === "changed" ? CHANGED_ELSEWHERE : DELETED_ELSEWHERE);
    this.name = "WatchlistConflict";
    this.kind = kind;
    this.current = current;
  }
}

/** The routes answer 404 "Not found" while the `watchlists` flag is off and 404 "Watchlist not found" for a list that
 * is gone (the client keeps the detail, not the X-Feature-Disabled header). */
export const isSwitchedOff = (error: unknown): boolean =>
  error instanceof ApiError && error.status === 404 && !/watchlist not found/i.test(error.detail);

const takenName = (error: unknown): boolean => error instanceof ApiError && error.status === 409 && /already have a watchlist/i.test(error.detail);

/** Any 409 on a save except a taken name is a version conflict. The current list is read from the body when it can be
 * (the client keeps only its first 2000 characters, so a long list's body does not parse). */
export function conflictFrom(error: unknown): never {
  if (error instanceof ApiError && error.status === 404 && !isSwitchedOff(error)) throw new WatchlistConflict("deleted");
  if (error instanceof ApiError && error.status === 409 && !takenName(error)) {
    let current: WatchlistV2 | null = null;
    try {
      current = (JSON.parse(error.body) as { detail?: { current?: WatchlistV2 } }).detail?.current ?? null;
    } catch {
      current = null;
    }
    throw new WatchlistConflict("changed", current && Array.isArray(current.items) ? current : null);
  }
  throw error;
}

export const watchlistsV2Api = {
  list: () => request<WatchlistV2[]>("/watchlists"),
  create: (name: string, items: WatchItem[] = []) =>
    request<WatchlistV2>("/watchlists", { method: "POST", body: JSON.stringify({ name, items }) }),
  /** The whole list (name and rows) on the version it was read at. */
  save: (list: WatchlistV2) =>
    request<WatchlistV2>(`/watchlists/${list.id}`, { method: "PUT", body: JSON.stringify({ name: list.name, items: list.items, version: list.version }) })
      .catch(conflictFrom),
  remove: (id: number) => request<unknown>(`/watchlists/${id}`, { method: "DELETE" }),
  quotes: (items: WatchItem[], signal?: AbortSignal) =>
    request<QuotesReply>("/market-data/quotes", { method: "POST", signal, body: JSON.stringify({ items: items.map(({ symbol, exchange }) => ({ symbol, exchange })) }) }),
};
export type WatchlistsApi = Pick<typeof watchlistsV2Api, "list" | "create" | "save" | "remove">;

// --- Pure rules --------------------------------------------------------------------------------------------------------

export const itemKey = (i: WatchItem): string => `${i.exchange.toUpperCase()}:${i.symbol.toUpperCase()}`;
const same = (a: WatchItem, b: WatchItem) => itemKey(a) === itemKey(b);

/** One edit of a list, applied the same way on screen and again on top of another device's version (a 409). */
export type ListEdit =
  | { kind: "add"; item: WatchItem }
  | { kind: "remove"; item: WatchItem }
  | { kind: "move"; item: WatchItem; to: number }
  | { kind: "rename"; name: string }
  /** FNO-4 "Roll to next expiry": the row replaced in place by another (when the list already holds that one, the row
   * is only removed, so nothing shows twice). */
  | { kind: "replace"; item: WatchItem; with: WatchItem };

/** `items` with the row at `from` moved to `to` (both clamped). Pure. */
export function moveItem<T>(items: readonly T[], from: number, to: number): T[] {
  const out = [...items];
  if (from < 0 || from >= out.length) return out;
  const [row] = out.splice(from, 1);
  out.splice(Math.max(0, Math.min(out.length, to)), 0, row);
  return out;
}

/** The list after one edit. An add of a row it holds, a remove or move of a row it lacks, or an add past the limit
 * changes nothing. Pure. */
export function applyEdit(list: WatchlistV2, edit: ListEdit): WatchlistV2 {
  if (edit.kind === "rename") return { ...list, name: edit.name };
  const at = list.items.findIndex((i) => same(i, edit.item));
  if (edit.kind === "add") {
    if (at >= 0 || list.items.length >= MAX_ITEMS) return list;
    return { ...list, items: [...list.items, { symbol: edit.item.symbol.toUpperCase(), exchange: edit.item.exchange.toUpperCase() }] };
  }
  if (at < 0) return list;
  if (edit.kind === "remove") return { ...list, items: list.items.filter((_, i) => i !== at) };
  if (edit.kind === "replace") {
    const next = { symbol: edit.with.symbol.toUpperCase(), exchange: edit.with.exchange.toUpperCase() };
    if (same(next, edit.item)) return list;
    if (list.items.some((i) => same(i, next))) return { ...list, items: list.items.filter((_, i) => i !== at) };
    return { ...list, items: list.items.map((x, i) => (i === at ? next : x)) };
  }
  return { ...list, items: moveItem(list.items, at, edit.to) };
}

const sameList = (a: WatchlistV2, b: WatchlistV2) =>
  a.name === b.name && a.items.length === b.items.length && a.items.every((x, i) => same(x, b.items[i]));

/** The rows that get a live price: the first QUOTE_ROWS (one batch call). */
export const quoteItems = (items: readonly WatchItem[]): WatchItem[] => items.slice(0, QUOTE_ROWS);

/** Up, down or flat, for the sign and the arrow next to the colour (colour is never the only signal); null: no change known. */
/** TM Part 1 (flag `data_hub`): the polled rows with each symbol's newest live tick on top - the last price from the
 * tick, the change measured again from the previous close the row's quote gave (unknown stays unknown). A tick never
 * invents a row's change; a row the poll has not priced yet shows the tick's price with no change. Pure. */
export function withTicks(rows: ReadonlyMap<string, QuoteRow>, ticks: ReadonlyMap<string, { symbol: string; exchange: string; ltp: number }>): Map<string, QuoteRow> {
  const out = new Map(rows);
  for (const [key, tick] of ticks) {
    if (!(tick.ltp > 0)) continue;
    const row = rows.get(key);
    const prev = row && !row.missing && row.ltp != null && row.change != null ? row.ltp - row.change : null;
    const change = prev != null ? tick.ltp - prev : null;
    out.set(key, {
      ...(row ?? { symbol: tick.symbol, exchange: tick.exchange }),
      ltp: tick.ltp, change, change_pct: prev ? (change! / prev) * 100 : null, missing: false, stale: false, error: null,
    } as QuoteRow);
  }
  return out;
}

export function direction(row: Pick<QuoteRow, "change"> | undefined): "up" | "down" | "flat" | null {
  const c = row?.change;
  if (c == null || !Number.isFinite(c)) return null;
  return c > 0 ? "up" : c < 0 ? "down" : "flat";
}

/** A failed request in plain words: what happened, then what to do. */
export function watchlistError(error: unknown, doing: "load" | "create" | "rename" | "save" | "delete", name = ""): string {
  const status = error instanceof ApiError ? error.status : 0;
  const detail = error instanceof ApiError ? error.detail : "";
  if (isSwitchedOff(error)) return SWITCHED_OFF;
  if (takenName(error)) return `You already have a list named "${name}". Pick another name.`;
  if (status === 403 && detail) return detail;
  if ((status === 402 || status === 409) && detail) {
    return /delete/i.test(detail) ? detail : `${detail.replace(/\.?$/, ".")} Delete a list you no longer need to make room.`;
  }
  if (status === 422 || status === 400) return "That name or symbol was not accepted. Use letters, digits and spaces, then try again.";
  if (doing === "load") return "Your watchlists could not be loaded. Check your connection, then try again.";
  if (doing === "delete") return "The list could not be deleted. Check your connection, then try again.";
  if (doing === "create") return "The list could not be created. Check your connection, then try again.";
  return "Your change could not be saved. Check your connection, then try again.";
}

// --- The store -----------------------------------------------------------------------------------------------------------

export interface WatchlistsState {
  /** The lists as shown: the server's last answer with the edits being saved applied on top. */
  lists: WatchlistV2[];
  loaded: boolean;
  /** A failure, in plain words (null when the last request went through). */
  error: string | null;
  /** A note about another device's change (409). */
  note: string | null;
  /** The server says the feature is off (404 without a list): the lists are kept on the server, nothing is shown. */
  off: boolean;
}

export class WatchlistsStore {
  private confirmed: WatchlistV2[] = [];
  private pending: { id: number; edit: ListEdit }[] = [];
  private queue: Promise<unknown> = Promise.resolve();
  private listeners = new Set<() => void>();
  private state: WatchlistsState = { lists: [], loaded: false, error: null, note: null, off: false };

  constructor(private readonly api: WatchlistsApi = watchlistsV2Api) {}

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getState = (): WatchlistsState => this.state;

  private emit(patch: Partial<WatchlistsState> = {}) {
    const lists = this.confirmed.map((l) => this.pending.filter((p) => p.id === l.id).reduce((acc, p) => applyEdit(acc, p.edit), l));
    this.state = { ...this.state, ...patch, lists };
    this.listeners.forEach((l) => l());
  }

  /** Requests run one at a time, in order, so each save starts from the version the one before it got back. */
  private run<T>(fn: () => Promise<T>): Promise<T> {
    const next = this.queue.then(fn, fn);
    this.queue = next.catch(() => undefined);  // intentional: keeps the queue going; the caller gets the failure through `next`
    return next;
  }

  private put(list: WatchlistV2) {
    const at = this.confirmed.findIndex((l) => l.id === list.id);
    this.confirmed = at >= 0 ? this.confirmed.map((l, i) => (i === at ? list : l)) : [...this.confirmed, list];
  }

  private drop(id: number) {
    this.confirmed = this.confirmed.filter((l) => l.id !== id);
    this.pending = this.pending.filter((p) => p.id !== id);
  }

  /** Edits made before this load are done (or dropped) by the time it runs; edits made after it stay pending. */
  load(): Promise<void> {
    const before = new Set(this.pending);
    return this.run(async () => {
      try {
        this.confirmed = await this.api.list();
        this.pending = this.pending.filter((p) => !before.has(p));
        this.emit({ loaded: true, error: null, off: false });
      } catch (error) {
        this.emit({ loaded: true, error: isSwitchedOff(error) ? null : watchlistError(error, "load"), off: isSwitchedOff(error) });
      }
    });
  }

  /** The list as the server has it now (after a 409 whose body did not carry it); null when it is gone. */
  private async fresh(id: number): Promise<WatchlistV2 | null> {
    return (await this.api.list()).find((l) => l.id === id) ?? null;
  }

  /** Shown at once, saved in turn with the list's version; on a 409 the edit is applied to the current list once more. */
  edit(id: number, edit: ListEdit): Promise<void> {
    const job = { id, edit };
    this.pending.push(job);
    this.emit({ note: null });
    return this.run(async () => {
      let note: string | null = null;
      let error: string | null = null;
      let off = false;
      let base = this.confirmed.find((l) => l.id === id);
      for (let attempt = 0; base; attempt += 1) {
        const next = applyEdit(base, edit);
        if (sameList(next, base)) {                                       // nothing left to change on this version
          if (attempt > 0) note = CHANGED_ELSEWHERE;                      // the other device's list already had it
          break;
        }
        try {
          this.put(await this.api.save(next));
          if (attempt > 0) note = MERGED;                                 // saved on top of the other device's change
          break;
        } catch (e) {
          try {
            if (e instanceof WatchlistConflict && e.kind === "changed") {
              const current = e.current ?? await this.fresh(id);
              if (!current) { this.drop(id); note = DELETED_ELSEWHERE; break; }
              this.put(current);
              note = CHANGED_ELSEWHERE;
              if (attempt === 0) { base = current; continue; }
            } else if (e instanceof WatchlistConflict) {
              this.drop(id);
              note = DELETED_ELSEWHERE;
            } else {
              off = isSwitchedOff(e);
              error = off ? null : watchlistError(e, edit.kind === "rename" ? "rename" : "save", edit.kind === "rename" ? edit.name : "");
            }
          } catch (readError) {                                            // the lists could not be read again
            error = watchlistError(readError, "save");
          }
          break;
        }
      }
      this.pending = this.pending.filter((p) => p !== job);
      this.emit({ note, error, ...(off ? { off } : {}) });
    });
  }

  /** WL1: a list the server made outside this store ("Copy to my list") joins the lists shown. */
  adopt(list: WatchlistV2): void {
    this.put(list);
    this.emit({ error: null, note: null });
  }

  create(name: string, items: WatchItem[] = []): Promise<WatchlistV2 | null> {
    return this.run(async () => {
      try {
        const made = await this.api.create(name, items);
        this.put(made);
        this.emit({ error: null, note: null });
        return made;
      } catch (e) {
        this.emit(isSwitchedOff(e) ? { off: true } : { error: watchlistError(e, "create", name) });
        return null;
      }
    });
  }

  remove(id: number): Promise<boolean> {
    return this.run(async () => {
      try {
        await this.api.remove(id);
      } catch (e) {
        if (isSwitchedOff(e)) { this.emit({ off: true }); return false; }
        if (!(e instanceof ApiError && e.status === 404)) {                  // already gone elsewhere: gone here too
          this.emit({ error: watchlistError(e, "delete") });
          return false;
        }
      }
      this.drop(id);
      this.emit({ error: null, note: null });
      return true;
    });
  }
}

// --- Quotes ----------------------------------------------------------------------------------------------------------------

const tabHidden = () => typeof document !== "undefined" && document.visibilityState === "hidden";

/** The poll's identity: the set of rows that get a price (first QUOTE_ROWS), in any order - a reorder inside them asks
 * for nothing new. */
export const quoteKey = (items: readonly WatchItem[]): string => quoteItems(items).map(itemKey).sort().join("|");

/**
 * The batch quote poll for one panel: one request for the first QUOTE_ROWS rows, then one every QUOTE_POLL_MS after
 * each answer; a turn while the tab is hidden is skipped (no request). `update(items)` restarts it only when the set of
 * priced rows changes, and then not sooner than QUOTE_POLL_MS after the last request (the route allows 30 a minute per
 * user), so adding, removing or reordering rows in a burst costs no extra requests. A superseded request is aborted.
 */
export class QuotePoller {
  private key = "";
  private lastAt = Number.NEGATIVE_INFINITY;
  private halt: () => void = () => undefined;

  constructor(private readonly load: (items: WatchItem[], signal: AbortSignal) => Promise<QuotesReply>,
              private readonly on: { data: (reply: QuotesReply) => void; error: () => void },
              private readonly hidden: () => boolean = tabHidden, private readonly now: () => number = () => Date.now()) {}

  update(items: readonly WatchItem[]): void {
    const key = quoteKey(items);
    if (key === this.key) return;
    this.halt();
    this.key = key;
    const first = quoteItems(items);
    if (first.length === 0) { this.halt = () => undefined; return; }
    const abort = new AbortController();
    let stop: () => void = () => undefined;
    const start = () => {
      stop = startPolling<QuotesReply>({
        load: () => { this.lastAt = this.now(); return this.load(first, abort.signal); },
        delay: () => QUOTE_POLL_MS, hidden: this.hidden, onData: this.on.data, onError: this.on.error,
      });
    };
    const wait = Math.max(0, this.lastAt + QUOTE_POLL_MS - this.now());
    const timer = wait > 0 ? setTimeout(start, wait) : undefined;
    if (timer === undefined) start();
    this.halt = () => { if (timer !== undefined) clearTimeout(timer); stop(); abort.abort(); };
  }

  close(): void {
    this.halt();
    this.halt = () => undefined;
    this.key = "";
  }
}
