import { afterEach, describe, expect, it, vi } from "vitest";
import { setToken } from "../api/client";
import { liveChipText, tickAgeChipState } from "../chart/liveChip";
import { LtpHub } from "../charting/ltpHub";
import type { LtpResponse } from "../types";
import { BarCloseWatch, livePushOn, tickTime } from "./livePush";
import { LiveTicksStore, liveTicks, pushConfigOf, resetLiveTicks } from "./liveTicks";

/**
 * AUD-G2.2 (flag `live_push`): the push settings from the stream, the push being live (stream up, heard within two
 * beats), a fresh tick per symbol, the fallback to the timers (stream error, quiet stream, old tick, flag off), the
 * bar-close reload and the chip from the age of the last tick.
 */
const READY = { retry_ms: 2000, push: { stale_after_s: 30, beat_s: 10, bar_settle_s: 5, max_symbols: 50 } };
const tick = (ltp: number, ts = "") => ({ items: [{ symbol: "NIFTY", exchange: "NSE", ltp, ts }] });
const polled: LtpResponse = { symbol: "NIFTY", exchange: "NSE", source_broker: "upstox", fetched_at: "f", ltp: 24000, timestamp: "t0",
  age_seconds: 1, stale: false, stale_reason: null, source: "quote", bid: null, ask: null, volume: null };

function liveStore() {
  const store = new LiveTicksStore(async () => ({}), async () => ({ status: null }));
  store.handle("stream", { up: true });
  store.handle("ready", READY);
  return store;
}

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); resetLiveTicks(); });

describe("push settings and state (AUD-G2.2)", () => {
  it("reads the push settings of `ready`; none while the flag is off or a value is malformed", () => {
    expect(pushConfigOf(READY)).toEqual({ staleAfterMs: 30_000, beatMs: 10_000, barSettleMs: 5_000 });
    expect(pushConfigOf({ retry_ms: 2000 })).toBeNull();
    expect(pushConfigOf({ push: { stale_after_s: 0, beat_s: 10 } })).toBeNull();
    expect(pushConfigOf({ push: { stale_after_s: "x", beat_s: 10 } })).toBeNull();
    expect(pushConfigOf(null)).toBeNull();
  });

  it("needs the flag and the two flags it depends on", () => {
    const on = (names: string[]) => (n: string) => names.includes(n);
    expect(livePushOn(on(["live_push", "data_hub", "ui_v2"]))).toBe(true);
    expect(livePushOn(on(["live_push", "ui_v2"]))).toBe(false);
    expect(livePushOn(on(["data_hub", "ui_v2"]))).toBe(false);
  });

  it("is live while the stream is up and heard within two beats; a fresh tick per symbol", () => {
    let now = 1_000_000;
    const store = new LiveTicksStore(async () => ({}), async () => ({ status: null }), () => now);
    store.handle("stream", { up: true });
    expect(store.pushLive()).toBe(false);                       // no `ready` with push yet
    store.handle("ready", READY);
    expect(store.pushLive()).toBe(true);
    expect(store.tickFresh("NIFTY", "NSE")).toBe(false);         // no tick yet
    store.handle("ticks", tick(24001));
    expect(store.tickFresh("nifty", "nse")).toBe(true);
    now += 19_000; store.handle("beat", {});
    now += 12_000;                                               // 31 s after the tick, 12 s after the beat
    expect(store.pushLive()).toBe(true);
    expect(store.tickFresh("NIFTY", "NSE")).toBe(false);         // older than the tick-age limit
    now += 9_000;                                                // 21 s without a word: two beats
    expect(store.pushLive()).toBe(false);
    store.handle("beat", {});
    expect(store.pushLive()).toBe(true);
    store.handle("stream", { up: false });                       // the stream errored
    expect(store.pushLive()).toBe(false);
    store.handle("stream", { up: true });
    store.handle("ready", { retry_ms: 2000 });                   // the flag was turned off
    expect(store.push).toBeNull();
    expect(store.pushLive()).toBe(false);
  });
});

describe("the stream feeds the store (AUD-G2.2)", () => {
  it("ready, ticks and beat events of the live stream reach the tab's store", async () => {
    vi.stubGlobal("document", { visibilityState: "visible" });
    const sse = `event: ready\ndata: ${JSON.stringify(READY)}\n\nevent: ticks\ndata: ${JSON.stringify(tick(24010.5, "2026-10-29T05:00:01+00:00"))}\n\nevent: beat\ndata: {}\n\n`;
    let calls = 0;
    vi.stubGlobal("fetch", vi.fn(async () => {
      calls += 1;
      if (calls > 1) return new Promise<Response>(() => undefined);                      // stays connecting
      return new Response(new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode(sse)); c.close(); } }), { status: 200 });
    }));
    setToken("token");
    const store = liveTicks();
    for (let i = 0; i < 50 && store.last("NIFTY", "NSE") === undefined; i++) await new Promise((r) => setTimeout(r, 5));
    expect(store.push).toEqual({ staleAfterMs: 30_000, beatMs: 10_000, barSettleMs: 5_000 });
    expect(store.last("NIFTY", "NSE")?.ltp).toBe(24010.5);
    expect(store.tickSeenAt("NIFTY", "NSE")).toBeTypeOf("number");
  });
});

describe("timers only as the fallback (AUD-G2.2)", () => {
  async function polls(setup: (store: LiveTicksStore) => void, push: boolean): Promise<number> {
    vi.useFakeTimers();
    const store = liveStore();
    let fetched = 0;
    const hub = new LtpHub(async () => { fetched += 1; return polled; }, () => true, () => Date.now(), store,
      push ? (s, e) => store.tickFresh(s, e) : undefined);
    const off = hub.subscribe("NIFTY", "NSE", 5000, () => undefined);
    await vi.advanceTimersByTimeAsync(0);                         // the first read
    store.handle("ticks", tick(24001));
    setup(store);
    await vi.advanceTimersByTimeAsync(5000 * 3);
    off(); hub.dispose();
    return fetched;
  }

  it("no poll while the symbol ticks fresh over a live push", async () => {
    expect(await polls(() => undefined, true)).toBe(1);
  });

  it("polls again while the stream is down", async () => {
    expect(await polls((s) => s.handle("stream", { up: false }), true)).toBe(4);
  });

  it("polls again when the flag is off (no push in `ready`), and exactly as before without the push hook", async () => {
    expect(await polls((s) => s.handle("ready", { retry_ms: 2000 }), true)).toBe(4);
    expect(await polls(() => undefined, false)).toBe(4);
  });

  it("polls again once the symbol's last tick is older than the limit", async () => {
    vi.useFakeTimers();
    const store = liveStore();
    let fetched = 0;
    const hub = new LtpHub(async () => { fetched += 1; return polled; }, () => true, () => Date.now(), store, (s, e) => store.tickFresh(s, e));
    const off = hub.subscribe("NIFTY", "NSE", 5000, () => undefined);
    await vi.advanceTimersByTimeAsync(0);
    store.handle("ticks", tick(24001));
    for (let i = 0; i < 6; i++) { await vi.advanceTimersByTimeAsync(5000); store.handle("beat", {}); }   // 30 s: fresh
    expect(fetched).toBe(1);
    await vi.advanceTimersByTimeAsync(5000);                      // 35 s: the tick is old, the poll comes back
    expect(fetched).toBe(2);
    off(); hub.dispose();
  });
});

describe("candles on bar close (AUD-G2.2)", () => {
  it("says once per new bar boundary, never inside the newest bar", () => {
    const w = new BarCloseWatch();
    const start = Date.parse("2026-10-29T03:45:00Z");             // a 5-minute bar
    const bar = 5 * 60_000;
    expect(w.due(start + 60_000, start, bar)).toBe(false);
    expect(w.due(start + bar + 1_000, start, bar)).toBe(true);
    expect(w.due(start + bar + 2_000, start, bar)).toBe(false);  // the same boundary: already read
    expect(w.due(start + 2 * bar + 1, start, bar)).toBe(true);   // the next one
    expect(w.due(start + 3 * bar, null, bar)).toBe(false);       // no bars yet
    expect(tickTime({ ts: "2026-10-29T03:50:00Z" }, 5)).toBe(Date.parse("2026-10-29T03:50:00Z"));
    expect(tickTime({ ts: "" }, 5)).toBe(5);
  });
});

describe("the delayed chip from the age of the last tick (AUD-G2.2)", () => {
  it("reads Live up to the limit, then Price delayed with the tick's age; nothing while the market is closed", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-29T05:00:00Z"));
    const store = liveStore();
    store.handle("ticks", tick(24001));
    const chip = () => tickAgeChipState(store.tickSeenAt("NIFTY", "NSE") as number, true, Date.now(), store.push!.staleAfterMs);
    expect(chip()).toEqual({ kind: "live", ageSeconds: 0 });
    await vi.advanceTimersByTimeAsync(30_000);
    expect(chip()?.kind).toBe("live");
    await vi.advanceTimersByTimeAsync(5_000);
    expect(chip()).toEqual({ kind: "delayed", ageSeconds: 35 });
    expect(liveChipText(chip()!).text).toBe("Price delayed · 35 s");
    store.handle("ticks", tick(24002));                           // a new tick: live again
    expect(chip()?.kind).toBe("live");
    expect(tickAgeChipState(Date.now(), false, Date.now(), 30_000)).toBeNull();
  });
});
