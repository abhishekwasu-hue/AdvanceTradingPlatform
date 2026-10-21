import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/errors";
import {
  CHANGED_ELSEWHERE, DELETED_ELSEWHERE, MERGED, QUOTE_POLL_MS, QUOTE_ROWS, WatchlistConflict, WatchlistsStore, applyEdit, direction, moveItem,
  pollQuotes, quoteItems, watchlistError, type QuotesReply, type WatchItem, type WatchlistV2, type WatchlistsApi,
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
    if (!cur) throw new WatchlistConflict(null);
    if (cur.version !== l.version) throw new WatchlistConflict(cur);
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

  it("409 where the other device already made the change: nothing more is saved, the latest list is shown", async () => {
    const server = new Server(list(["NIFTY 50", "TCS"]));
    const store = await loaded(server);
    server.other(1, (l) => ({ ...l, items: [it_("NIFTY 50")] }));
    await store.edit(1, { kind: "remove", item: it_("TCS") });
    expect(server.saves).toHaveLength(1);
    expect(names(store.getState().lists[0])).toEqual(["NIFTY 50"]);
  });

  it("a second 409 in a row shows the server's list and says so", async () => {
    const server = new Server(list(["NIFTY 50", "TCS"]));
    const store = await loaded(server);
    const save = server.save;
    server.save = async (l) => { server.other(1, (x) => ({ ...x, name: `${x.name}!` })); return save(l); };   // always one step ahead
    await store.edit(1, { kind: "add", item: it_("INFY") });
    expect(server.saves).toHaveLength(2);
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

  it("one batch for the first 50 rows now, then every 10 s after each answer", async () => {
    vi.useFakeTimers();
    const items = Array.from({ length: 70 }, (_, i) => it_(`S${i}`));
    const load = vi.fn(async (_: WatchItem[]) => reply);
    const stop = pollQuotes(load, items, { data: () => {}, error: () => {} }, () => false);
    expect(load).toHaveBeenCalledTimes(1);
    expect(load.mock.calls[0][0]).toHaveLength(QUOTE_ROWS);
    expect(load.mock.calls[0][0][QUOTE_ROWS - 1].symbol).toBe("S49");
    await vi.advanceTimersByTimeAsync(QUOTE_POLL_MS - 1);
    expect(load).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(load).toHaveBeenCalledTimes(2);
    stop();
    await vi.advanceTimersByTimeAsync(QUOTE_POLL_MS * 3);
    expect(load).toHaveBeenCalledTimes(2);
    expect(QUOTE_POLL_MS).toBe(10_000);
    expect(quoteItems(items)).toHaveLength(50);
  });

  it("no request while the tab is hidden; the next turn after it shows again asks", async () => {
    vi.useFakeTimers();
    let hidden = true;
    const load = vi.fn(async () => reply);
    const stop = pollQuotes(load, [it_("TCS")], { data: () => {}, error: () => {} }, () => hidden);
    await vi.advanceTimersByTimeAsync(QUOTE_POLL_MS * 3);
    expect(load).not.toHaveBeenCalled();
    hidden = false;
    await vi.advanceTimersByTimeAsync(QUOTE_POLL_MS);
    expect(load).toHaveBeenCalledTimes(1);
    stop();
  });

  it("an empty list asks for nothing; a failure is reported and the poll goes on", async () => {
    vi.useFakeTimers();
    const load = vi.fn(async () => reply);
    pollQuotes(load, [], { data: () => {}, error: () => {} }, () => false)();
    expect(load).not.toHaveBeenCalled();
    const failing = vi.fn(async () => { throw new Error("down"); });
    const error = vi.fn();
    const stop = pollQuotes(failing, [it_("TCS")], { data: () => {}, error }, () => false);
    await vi.advanceTimersByTimeAsync(QUOTE_POLL_MS);
    expect(failing).toHaveBeenCalledTimes(2);
    expect(error).toHaveBeenCalled();
    stop();
  });
});
