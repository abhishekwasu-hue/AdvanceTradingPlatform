import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/errors";
import {
  CHANGED_ELSEWHERE, DELETED_ELSEWHERE, MERGED, QUOTE_POLL_MS, QUOTE_ROWS, QuotePoller, SWITCHED_OFF, WatchlistConflict, WatchlistsStore, applyEdit,
  conflictFrom, direction, moveItem, quoteItems, quoteKey, watchlistError, type QuotesReply, type WatchItem, type WatchlistV2, type WatchlistsApi,
} from "./watchlistsV2";

/** UI2-4c: the watchlist rows' order, the versioned saves and their 409 merge, and the batch quote poll. */

const it_ = (symbol: string, exchange = "NSE"): WatchItem => ({ symbol, exchange });
const list = (items: string[], version = 1, name = "Indices", id = 1): WatchlistV2 => ({ id, name, items: items.map((s) => it_(s)), version });
const names = (l: WatchlistV2 | undefined) => l?.items.map((i) => i.symbol);

/** An in-memory server with the backend's version rule; `other` changes a list as another device would. */
class Server implements WatchlistsApi {
  lists = new Map<number, WatchlistV2>();
  saves: { id: number; version: number; items: string[]; name: string }[] = [];
  fail: unknown = null;
  next = 10;
  constructor(...ls: WatchlistV2[]) { ls.forEach((l) => this.lists.set(l.id, l)); }
  other(id: number, change: (l: WatchlistV2) => WatchlistV2) {
    const l = this.lists.get(id)!;
    this.lists.set(id, { ...change(l), version: l.version + 1 });
  }
  list = async () => [...this.lists.values()];
  create = async (name: string, items: WatchItem[] = []) => {
    if (this.fail) throw this.fail;
    const l = { id: this.next++, name, items, version: 1 };
    this.lists.set(l.id, l);
    return l;
  };
  save = async (l: WatchlistV2) => {
    this.saves.push({ id: l.id, version: l.version, items: l.items.map((i) => i.symbol), name: l.name });
    if (this.fail) throw this.fail;
    const cur = this.lists.get(l.id);
    if (!cur) throw new WatchlistConflict("deleted");
    if (cur.version !== l.version) {
      // The server's real 409 body, as the API client keeps it: cut at 2000 characters (a long list no longer parses).
      const body = JSON.stringify({ detail: { message: "the watchlist changed since this edit was made", current: cur } });
      conflictFrom(new ApiError(409, "the watchlist changed since this edit was made", "the watchlist changed since this edit was made", body.slice(0, 2000)));
    }
    const next = { ...l, version: cur.version + 1 };
    this.lists.set(l.id, next);
    return next;
  };
  remove = async (id: number) => { this.lists.delete(id); return { deleted: id }; };
}

async function loaded(server: Server) {
  const store = new WatchlistsStore(server);
  await store.load();
  return store;
}

describe("UI2-4c watchlist order (pure)", () => {
  it("moveItem moves one row and clamps; a bad index changes nothing", () => {
    expect(moveItem(["a", "b", "c", "d"], 0, 2)).toEqual(["b", "c", "a", "d"]);
    expect(moveItem(["a", "b", "c", "d"], 3, 0)).toEqual(["d", "a", "b", "c"]);
    expect(moveItem(["a", "b"], 0, 9)).toEqual(["b", "a"]);
    expect(moveItem(["a", "b"], 5, 0)).toEqual(["a", "b"]);
  });

  it("applyEdit: add once (upper-case), remove and move by symbol and exchange, rename; a missing row changes nothing", () => {
    const l = list(["NIFTY 50", "TCS"]);
    expect(names(applyEdit(l, { kind: "add", item: it_("infy") }))).toEqual(["NIFTY 50", "TCS", "INFY"]);
    expect(applyEdit(l, { kind: "add", item: it_("TCS") })).toBe(l);
    expect(names(applyEdit(l, { kind: "add", item: it_("TCS", "BSE") }))).toEqual(["NIFTY 50", "TCS", "TCS"]);   // another exchange is another row
    expect(names(applyEdit(l, { kind: "remove", item: it_("NIFTY 50") }))).toEqual(["TCS"]);
    expect(applyEdit(l, { kind: "remove", item: it_("TCS", "BSE") })).toBe(l);
    expect(names(applyEdit(l, { kind: "move", item: it_("TCS"), to: 0 }))).toEqual(["TCS", "NIFTY 50"]);
    expect(applyEdit(l, { kind: "rename", name: "Mine" }).name).toBe("Mine");
  });

  it("direction gives the sign as well as the colour; no change known is null", () => {
    expect(direction({ change: 12.5 })).toBe("up");
    expect(direction({ change: -0.05 })).toBe("down");
    expect(direction({ change: 0 })).toBe("flat");
    expect(direction({ change: null })).toBeNull();
    expect(direction(undefined)).toBeNull();
  });
});

describe("UI2-4c versioned saves and the 409 merge", () => {
  it("an edit shows at once and is saved with the list's version; the next edit uses the version that came back", async () => {
    const server = new Server(list(["NIFTY 50", "TCS", "INFY"]));
    const store = await loaded(server);
    const first = store.edit(1, { kind: "move", item: it_("INFY"), to: 0 });
    expect(names(store.getState().lists[0])).toEqual(["INFY", "NIFTY 50", "TCS"]);          // before the server answers
    const second = store.edit(1, { kind: "remove", item: it_("TCS") });
    expect(names(store.getState().lists[0])).toEqual(["INFY", "NIFTY 50"]);
    await Promise.all([first, second]);
    expect(server.saves).toEqual([
      { id: 1, version: 1, items: ["INFY", "NIFTY 50", "TCS"], name: "Indices" },
      { id: 1, version: 2, items: ["INFY", "NIFTY 50"], name: "Indices" },
    ]);
    expect(store.getState().lists[0].version).toBe(3);
    expect(store.getState().note).toBeNull();
  });

  it("409: the edit is applied again to the other device's list, so both changes are kept, and the trader is told", async () => {
    const server = new Server(list(["NIFTY 50", "TCS"]));
    const store = await loaded(server);
    server.other(1, (l) => ({ ...l, items: [...l.items, it_("HDFCBANK")] }));                 // another device added a row
    await store.edit(1, { kind: "move", item: it_("TCS"), to: 0 });
    expect(server.saves.map((s) => s.version)).toEqual([1, 2]);
    expect(names(server.lists.get(1))).toEqual(["TCS", "NIFTY 50", "HDFCBANK"]);
    expect(names(store.getState().lists[0])).toEqual(["TCS", "NIFTY 50", "HDFCBANK"]);
    expect(store.getState().note).toBe(MERGED);
  });

  it("409 where the other device already made the change: nothing more is saved, the latest list is shown and said so", async () => {
    const server = new Server(list(["NIFTY 50", "TCS"]));
    const store = await loaded(server);
    server.other(1, (l) => ({ ...l, items: [it_("NIFTY 50")] }));
    await store.edit(1, { kind: "remove", item: it_("TCS") });
    expect(server.saves).toHaveLength(1);
    expect(names(store.getState().lists[0])).toEqual(["NIFTY 50"]);
    expect(store.getState().note).toBe(CHANGED_ELSEWHERE);                 // not "both changes are kept": nothing was saved
  });

  it("review: a 60-row list - the 409 body is cut and does not parse; the list is read again and the edit still lands", async () => {
    const sixty = Array.from({ length: 60 }, (_, i) => `SYMBOL${i}`);
    const server = new Server(list(sixty));
    const store = await loaded(server);
    const reads = vi.spyOn(server, "list");
    server.other(1, (l) => ({ ...l, items: [...l.items, it_("HDFCBANK")] }));
    await store.edit(1, { kind: "move", item: it_("SYMBOL59"), to: 0 });
    expect(reads).toHaveBeenCalledTimes(1);                                 // the body could not say what the list is now
    expect(server.saves.map((s) => s.version)).toEqual([1, 2]);
    expect(names(store.getState().lists[0])?.slice(0, 2)).toEqual(["SYMBOL59", "SYMBOL0"]);
    expect(names(store.getState().lists[0])?.[60]).toBe("HDFCBANK");
    expect(store.getState().lists[0].version).toBe(3);
    expect(store.getState().note).toBe(MERGED);
    expect(store.getState().error).toBeNull();
    await store.edit(1, { kind: "remove", item: it_("SYMBOL1") });          // the next edit is on the current version
    expect(server.saves[server.saves.length - 1].version).toBe(3);
  });

  it("review: the 409 helper - a taken name stays an error, any other 409 is a conflict, a body that is cut gives no list", () => {
    const taken = new ApiError(409, "x", "you already have a watchlist named 'A'");
    expect(() => conflictFrom(taken)).toThrow(taken);
    let thrown: unknown;
    try { conflictFrom(new ApiError(409, "x", "the watchlist changed since this edit was made", '{"detail": {"current": {"id": 1, "items": [')); } catch (e) { thrown = e; }
    expect(thrown).toBeInstanceOf(WatchlistConflict);
    expect((thrown as WatchlistConflict).current).toBeNull();
    try { conflictFrom(new ApiError(404, "x", "Watchlist not found")); } catch (e) { thrown = e; }
    expect((thrown as WatchlistConflict).kind).toBe("deleted");
    const off = new ApiError(404, "x", "Not found");
    expect(() => conflictFrom(off)).toThrow(off);
  });

  it("review: a 404 while the feature is off keeps the lists and shows that state", async () => {
    const server = new Server(list(["NIFTY 50"]));
    const store = await loaded(server);
    server.fail = new ApiError(404, "Not found", "Not found");
    await store.edit(1, { kind: "add", item: it_("INFY") });
    expect(store.getState().off).toBe(true);
    expect(store.getState().lists.map((l) => l.id)).toEqual([1]);
    expect(store.getState().error).toBeNull();
    expect(watchlistError(server.fail, "save")).toBe(SWITCHED_OFF);
    server.list = async () => { throw new ApiError(404, "Not found", "Not found"); };
    await store.load();
    expect(store.getState().off).toBe(true);
  });

  it("review: a load keeps the edits made after it was asked for", async () => {
    const server = new Server(list(["NIFTY 50"]));
    const store = await loaded(server);
    let release: () => void = () => undefined;
    const held = new Promise<void>((r) => { release = r; });
    const read = server.list;
    server.list = async () => { await held; return read(); };
    const loading = store.load();
    const editing = store.edit(1, { kind: "add", item: it_("INFY") });
    release();
    await loading;
    expect(names(store.getState().lists[0])).toEqual(["NIFTY 50", "INFY"]);   // still shown after the load
    await editing;
    expect(names(server.lists.get(1))).toEqual(["NIFTY 50", "INFY"]);
  });

  it("a second 409 in a row shows the server's list and says so", async () => {
    const server = new Server(list(["NIFTY 50", "TCS"]));
    const store = await loaded(server);
    const save = server.save;
    server.save = async (l) => { server.other(1, (x) => ({ ...x, name: `${x.name}!` })); return save(l); };   // always one step ahead
    await store.edit(1, { kind: "add", item: it_("INFY") });
    expect(server.saves).toHaveLength(2);
    expect(store.getState().note).not.toBe(MERGED);
    expect(store.getState().note).toBe(CHANGED_ELSEWHERE);
    expect(store.getState().lists[0].name).toBe("Indices!!");
    expect(names(store.getState().lists[0])).toEqual(["NIFTY 50", "TCS"]);
  });

  it("a list deleted on another device goes, and the trader is told", async () => {
    const server = new Server(list(["NIFTY 50"]), list(["TCS"], 1, "Banks", 2));
    const store = await loaded(server);
    server.lists.delete(1);
    await store.edit(1, { kind: "add", item: it_("INFY") });
    expect(store.getState().lists.map((l) => l.id)).toEqual([2]);
    expect(store.getState().note).toBe(DELETED_ELSEWHERE);
  });

  it("a failed save puts the list back as the server has it and says what to do", async () => {
    const server = new Server(list(["NIFTY 50"]));
    const store = await loaded(server);
    server.fail = new TypeError("Failed to fetch");
    await store.edit(1, { kind: "add", item: it_("INFY") });
    expect(names(store.getState().lists[0])).toEqual(["NIFTY 50"]);
    expect(store.getState().error).toBe("Your change could not be saved. Check your connection, then try again.");
  });

  it("create, rename to a taken name, delete; the server's limit is shown in its words", async () => {
    const server = new Server(list(["NIFTY 50"]));
    const store = await loaded(server);
    const made = await store.create("Banks");
    expect(made?.name).toBe("Banks");
    expect(store.getState().lists.map((l) => l.name)).toEqual(["Indices", "Banks"]);
    server.fail = new ApiError(409, "conflict", "you already have a watchlist named 'Indices'");
    await store.edit(made!.id, { kind: "rename", name: "Indices" });
    expect(store.getState().error).toBe('You already have a list named "Indices". Pick another name.');
    server.fail = null;
    expect(await store.remove(made!.id)).toBe(true);
    expect(store.getState().lists.map((l) => l.name)).toEqual(["Indices"]);
    server.fail = new ApiError(402, "limit", "The Free plan allows 5 watchlists; you have 5.");
    expect(await store.create("Six")).toBeNull();
    expect(store.getState().error).toBe("The Free plan allows 5 watchlists; you have 5. Delete a list you no longer need to make room.");
    expect(watchlistError(new ApiError(409, "x", "at most 100 watchlists per user - delete one first"), "create"))
      .toBe("at most 100 watchlists per user - delete one first");
    expect(watchlistError(new TypeError("x"), "load")).toBe("Your watchlists could not be loaded. Check your connection, then try again.");
  });
});

describe("UI2-4c batch quote poll", () => {
  afterEach(() => { vi.useRealTimers(); });
  const reply: QuotesReply = { items: [] };
  const poller = (load: (items: WatchItem[], signal: AbortSignal) => Promise<QuotesReply>, hidden = () => false, error = () => {}) =>
    new QuotePoller(load, { data: () => {}, error }, hidden, () => Date.now());

  it("one batch for the first 50 rows now, then every 10 s after each answer; close stops it", async () => {
    vi.useFakeTimers();
    const items = Array.from({ length: 70 }, (_, i) => it_(`S${i}`));
    const load = vi.fn(async (_: WatchItem[], __: AbortSignal) => reply);
    const p = poller(load);
    p.update(items);
    expect(load).toHaveBeenCalledTimes(1);
    expect(load.mock.calls[0][0]).toHaveLength(QUOTE_ROWS);
    expect(load.mock.calls[0][0][QUOTE_ROWS - 1].symbol).toBe("S49");
    await vi.advanceTimersByTimeAsync(QUOTE_POLL_MS - 1);
    expect(load).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(load).toHaveBeenCalledTimes(2);
    p.close();
    await vi.advanceTimersByTimeAsync(QUOTE_POLL_MS * 3);
    expect(load).toHaveBeenCalledTimes(2);
    expect(QUOTE_POLL_MS).toBe(10_000);
    expect(quoteItems(items)).toHaveLength(50);
  });

  it("review: reordering asks for nothing; a burst of adds and removes waits out the 10 s and then asks once", async () => {
    vi.useFakeTimers();
    const load = vi.fn(async (_: WatchItem[], __: AbortSignal) => reply);
    const p = poller(load);
    const rows = ["A", "B", "C", "D"].map((s) => it_(s));
    p.update(rows);
    expect(load).toHaveBeenCalledTimes(1);
    for (let i = 0; i < 20; i += 1) p.update(moveItem(rows, i % 4, (i + 1) % 4));         // key repeat on the reorder keys
    expect(quoteKey(moveItem(rows, 0, 3))).toBe(quoteKey(rows));
    await vi.advanceTimersByTimeAsync(2_000);
    expect(load).toHaveBeenCalledTimes(1);
    for (let i = 0; i < 10; i += 1) p.update(i % 2 ? rows : [...rows, it_("E")]);          // add, remove, add ...
    await vi.advanceTimersByTimeAsync(QUOTE_POLL_MS - 2_001);
    expect(load).toHaveBeenCalledTimes(1);                                                  // not before 10 s since the last
    await vi.advanceTimersByTimeAsync(1);
    expect(load).toHaveBeenCalledTimes(2);
    expect(load.mock.calls[1][0].map((i) => i.symbol)).toEqual(["A", "B", "C", "D"]);      // the last update wins
    // At most one request per 10 s over a minute of edits.
    for (let t = 0; t < 60; t += 1) { p.update(t % 2 ? rows : [...rows, it_("F")]); await vi.advanceTimersByTimeAsync(1_000); }
    expect(load.mock.calls.length).toBeLessThanOrEqual(2 + 6);
    p.close();
  });

  it("review: a superseded request is aborted", async () => {
    vi.useFakeTimers();
    const signals: AbortSignal[] = [];
    const p = poller(async (_items, signal) => { signals.push(signal); return new Promise<QuotesReply>(() => {}); });
    p.update([it_("A")]);
    p.update([it_("B")]);
    expect(signals[0].aborted).toBe(true);
    p.close();
  });

  it("no request while the tab is hidden; the next turn after it shows again asks", async () => {
    vi.useFakeTimers();
    let hidden = true;
    const load = vi.fn(async () => reply);
    const p = poller(load, () => hidden);
    p.update([it_("TCS")]);
    await vi.advanceTimersByTimeAsync(QUOTE_POLL_MS * 3);
    expect(load).not.toHaveBeenCalled();
    hidden = false;
    await vi.advanceTimersByTimeAsync(QUOTE_POLL_MS);
    expect(load).toHaveBeenCalledTimes(1);
    p.close();
  });

  it("an empty list asks for nothing; a failure is reported and the poll goes on", async () => {
    vi.useFakeTimers();
    const load = vi.fn(async () => reply);
    poller(load).update([]);
    expect(load).not.toHaveBeenCalled();
    const failing = vi.fn(async () => { throw new Error("down"); });
    const error = vi.fn();
    const p = poller(failing, () => false, error);
    p.update([it_("TCS")]);
    await vi.advanceTimersByTimeAsync(QUOTE_POLL_MS);
    expect(failing).toHaveBeenCalledTimes(2);
    expect(error).toHaveBeenCalled();
    p.close();
  });
});
