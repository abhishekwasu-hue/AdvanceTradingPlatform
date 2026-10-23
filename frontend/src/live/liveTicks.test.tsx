import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LtpHub, withTick, type TickSource } from "../charting/ltpHub";
import { withTicks, type QuoteRow } from "../chart/watchlistsV2";
import type { LtpResponse } from "../types";
import { gateShowing } from "./gateShowing";
import { LivePricesNotice } from "./LivePricesBanner";
import {
  LiveTicksStore, MAX_TAB_SYMBOLS, RECONNECTING_TEXT, REGISTER_DEBOUNCE_MS, STREAM_DOWN_NOTICE_MS, noticeFor, tickKey, type HubStatus,
} from "./liveTicks";

const status = (over: Partial<HubStatus> = {}): HubStatus => ({
  state: "live", broker: "upstox", broker_name: "Upstox", account_label: "primary", message: "Live prices from Upstox.",
  since: "2026-10-23T05:00:00+00:00", retry_seconds: null, ...over,
});

afterEach(() => { vi.useRealTimers(); });

describe("live ticks store (TM Part 1)", () => {
  it("registers the tab's union once, debounced, at most 50, without repeats", async () => {
    vi.useFakeTimers();
    const put = vi.fn(async () => ({ status: status() }));
    const store = new LiveTicksStore(put, async () => ({ status: null }));
    store.register([{ symbol: "nifty", exchange: "nse" }, { symbol: "TCS", exchange: "NSE" }]);
    store.register([{ symbol: "NIFTY", exchange: "NSE" }, ...Array.from({ length: 60 }, (_, i) => ({ symbol: `S${i}`, exchange: "NSE" }))]);
    expect(put).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(REGISTER_DEBOUNCE_MS + 10);
    expect(put).toHaveBeenCalledTimes(1);
    const sent = (put.mock.calls[0] as unknown as [{ symbol: string; exchange: string }[]])[0];
    expect(sent.length).toBe(MAX_TAB_SYMBOLS);
    expect(sent.slice(0, 3)).toEqual([{ symbol: "NIFTY", exchange: "NSE" }, { symbol: "TCS", exchange: "NSE" }, { symbol: "S0", exchange: "NSE" }]);
    expect(store.status?.state).toBe("live");
    await store.send(false);                    // nothing changed: nothing sent
    expect(put).toHaveBeenCalledTimes(1);
    store.dispose();
  });

  it("hands each tick only to the pages that show that symbol and keeps the newest", () => {
    const store = new LiveTicksStore(async () => ({}), async () => ({ status: null }));
    const nifty: number[] = [];
    const tcs: number[] = [];
    const off = store.onTick("NIFTY", "NSE", (t) => nifty.push(t.ltp));
    store.onTick("TCS", "NSE", (t) => tcs.push(t.ltp));
    store.handle("ticks", { items: [{ symbol: "NIFTY", exchange: "NSE", ltp: 24000.5, ts: "t1" }, { symbol: "X", exchange: "NSE", ltp: 0, ts: "t" }] });
    off();
    store.handle("ticks", { items: [{ symbol: "NIFTY", exchange: "NSE", ltp: 24001, ts: "t2" }] });
    store.handle("ticks", { nonsense: true });
    expect(nifty).toEqual([24000.5]);
    expect(tcs).toEqual([]);
    expect(store.last("nifty", "nse")?.ltp).toBe(24001);
    expect(tickKey("nifty", "nse")).toBe("NSE:NIFTY");
  });

  it("follows the hub state and this tab's own stream", () => {
    const store = new LiveTicksStore(async () => ({}), async () => ({ status: null }), () => 1000);
    let changes = 0;
    store.onStatus(() => { changes++; });
    store.handle("hub", status({ state: "reconnecting" }));
    expect(store.status?.state).toBe("reconnecting");
    store.handle("stream", { up: false });
    expect(store.streamDownSince).toBe(1000);
    store.handle("stream", { up: false });
    expect(store.streamDownSince).toBe(1000);           // the first drop's time is kept
    store.handle("stream", { up: true });
    expect(store.streamDownSince).toBeNull();
    expect(changes).toBe(3);
  });
});

describe("the notice (TM Part 1)", () => {
  it("says renew when the login ended, reconnecting while it reconnects, nothing while live", () => {
    expect(noticeFor(status(), null, 0)).toBeNull();
    expect(noticeFor(status({ state: "polling" }), null, 0)).toBeNull();
    expect(noticeFor(null, null, 0)).toBeNull();
    const renew = noticeFor(status({ state: "renew_needed", message: "Your Upstox login has ended. Log in again to resume live prices." }), null, 0);
    expect(renew).toEqual({ kind: "renew", message: "Your Upstox login has ended. Log in again to resume live prices.", broker: "upstox",
      brokerName: "Upstox", accountLabel: "primary" });
    expect(noticeFor(status({ state: "reconnecting", message: "Live prices from Upstox are reconnecting." }), null, 0))
      .toEqual({ kind: "reconnecting", message: "Live prices from Upstox are reconnecting." });
    // The tab's own stream: a blip is not worth a notice; a few seconds is.
    expect(noticeFor(status(), 1000, 1000 + STREAM_DOWN_NOTICE_MS - 1)).toBeNull();
    expect(noticeFor(status(), 1000, 1000 + STREAM_DOWN_NOTICE_MS)).toEqual({ kind: "reconnecting", message: RECONNECTING_TEXT });
  });

  it("renders plain words, one button, no ids or technical terms", () => {
    const renew = renderToStaticMarkup(<LivePricesNotice notice={{ kind: "renew", message: "Your Upstox login has ended. Log in again to resume live prices.",
      broker: "upstox", brokerName: "Upstox", accountLabel: "primary" }} onRenew={() => undefined} />);
    expect(renew).toContain("Log in again");
    expect(renew.match(/<button/g)?.length).toBe(1);
    expect(renew).not.toMatch(/token|oauth|namespace|org\.t|websocket|stream/i);
    const ask = renderToStaticMarkup(<LivePricesNotice canChange={false} notice={{ kind: "renew", message: "m", broker: "upstox", brokerName: "Upstox", accountLabel: "primary" }} />);
    expect(ask).not.toContain("<button");
    expect(ask).toContain("log in to Upstox again");
    const rec = renderToStaticMarkup(<LivePricesNotice notice={{ kind: "reconnecting", message: RECONNECTING_TEXT }} />);
    expect(rec).toContain("reconnecting") ;
    expect(rec).not.toContain("<button");
  });
});

describe("prices on top of the polls (TM Part 1)", () => {
  it("a watchlist row moves with its tick; the change is measured from the quote's previous close", () => {
    const rows = new Map<string, QuoteRow>([
      ["NSE:TCS", { symbol: "TCS", exchange: "NSE", ltp: 110, change: 10, change_pct: 10, missing: false }],
      ["NSE:INFY", { symbol: "INFY", exchange: "NSE", ltp: null, change: null, change_pct: null, missing: true, error: "budget used" }],
    ]);
    const out = withTicks(rows, new Map([
      ["NSE:TCS", { symbol: "TCS", exchange: "NSE", ltp: 120 }],
      ["NSE:INFY", { symbol: "INFY", exchange: "NSE", ltp: 1500 }],
    ]));
    expect(out.get("NSE:TCS")).toMatchObject({ ltp: 120, change: 20, missing: false });
    expect(out.get("NSE:TCS")!.change_pct).toBeCloseTo(20);
    expect(out.get("NSE:INFY")).toMatchObject({ ltp: 1500, change: null, change_pct: null, missing: false });   // no invented change
    expect(rows.get("NSE:TCS")!.ltp).toBe(110);                                                                   // pure
  });

  it("a chart's polled price moves with each tick between polls, and stops listening when the chart closes", async () => {
    const listeners = new Map<string, (t: { ltp: number; ts: string }) => void>();
    const released: string[] = [];
    const ticks: TickSource = {
      register: (items) => ({ owner: 1, release: () => { released.push(items[0].symbol); } }),
      onTick: (symbol, _exchange, l) => { listeners.set(symbol, l); return () => { listeners.delete(symbol); }; },
    };
    const polled: LtpResponse = { symbol: "NIFTY", exchange: "NSE", source_broker: "upstox", fetched_at: "f", ltp: 24000, timestamp: "t0",
      age_seconds: 3, stale: false, stale_reason: null, source: "quote", bid: null, ask: null, volume: null };
    const hub = new LtpHub(async () => polled, () => true, () => 0, ticks);
    const seen: (number | undefined)[] = [];
    const off = hub.subscribe("NIFTY", "NSE", 5000, (u) => seen.push(u.ltp?.ltp));
    await Promise.resolve(); await Promise.resolve();
    listeners.get("NIFTY")!({ ltp: 24002.5, ts: "t1" });
    expect(seen).toEqual([24000, 24002.5]);
    off();
    expect(listeners.size).toBe(0);
    expect(released).toEqual(["NIFTY"]);
    hub.dispose();
    expect(withTick(polled, { ltp: 1, ts: "" }, "now")).toMatchObject({ ltp: 1, timestamp: "now", source: "tick", stale: false, age_seconds: 0 });
  });
});

describe("one renew prompt (TM Part 1)", () => {
  it("knows when the broker gate's login card is on screen", () => {
    const seen: boolean[] = [];
    const off = gateShowing.subscribe(() => seen.push(gateShowing.get()));
    gateShowing.set(true);
    gateShowing.set(true);
    gateShowing.set(false);
    off();
    expect(seen).toEqual([true, false]);
  });
});
