import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ScreenLegend, overlayLayers, type ScreenOverlayState } from "./ScreenOverlay";
import { CHART_PANEL } from "../inplace/panels";
import { WatchScreenBar, type WatchScreenState } from "./WatchScreenBar";
import { SystemListView } from "./WatchlistPanel";
import {
  NO_CHOICE, ScreenChecker, byExchange, checkList, countText, listKeyOf, markOf, onCandles, orderByMatch, screenParam, shownRows, waitUntil, withScreen,
  type CheckOutcome, type CheckReply, type CheckerTarget, type OverlayReply,
} from "./watchScreen";
import type { WatchItem } from "./watchlistsV2";

/** M91 (owner order): a watchlist's Screen (W1-W4) and the same screen on the chart (C1). C2 - the list and the chart
 * read one server evaluation - is tested on the server (backend/tests/test_m91_watchlist_screen.py). */

const rows = (...s: string[]): WatchItem[] => s.map((symbol) => ({ symbol, exchange: "NSE" }));
const LIST = rows("AAA", "BBB", "CCC", "DDD", "EEE");
const MATCHED = new Set(["NSE:CCC", "NSE:EEE"]);
const SCREENS = [{ id: 4, name: "My trend screen", base_tf: "1d", usable: true }, { id: 9, name: "Rank", base_tf: "1d", usable: false, why: "ranks" }];

describe("W1 the choice per watchlist", () => {
  it("each list keeps its own key: one of his lists by id, a system list by its key", () => {
    expect(listKeyOf({ kind: "own", id: 7 })).toBe("o:7");
    expect(listKeyOf({ kind: "system", key: "index-nifty50" })).toBe("s:index-nifty50");
    expect(NO_CHOICE).toEqual({ screen_id: null, matches_only: false, alert: false, telegram: false });   // no default screen
  });
  it("the dropdown offers None and his own screens; a screen that cannot run on a list is shown, not choosable", () => {
    const state: WatchScreenState = { choice: NO_CHOICE, screen: null, matched: new Set(), outcome: null, checking: false, failed: false };
    const html = renderToStaticMarkup(<WatchScreenBar listKey="o:7" screens={SCREENS} choice={NO_CHOICE} onChoose={() => {}} state={state} items={LIST} />);
    expect(html).toContain(">None</option>");
    expect(html).toContain("My trend screen</option>");
    expect(html).toMatch(/disabled="">Rank \(not for lists\)/);
    expect(html).not.toContain("match");                                      // nothing chosen: no count, no toggles
    expect(html).not.toContain("Matches only");
  });
});

describe("W2 matches on top, the rest dimmed, Matches only, the count", () => {
  it("orders the matches first in the list's own order and never removes a row", () => {
    expect(orderByMatch(LIST, MATCHED).map((i) => i.symbol)).toEqual(["CCC", "EEE", "AAA", "BBB", "DDD"]);
    expect(shownRows(LIST, MATCHED, false)).toHaveLength(5);
    expect(shownRows(LIST, MATCHED, true).map((i) => i.symbol)).toEqual(["CCC", "EEE"]);
    expect(orderByMatch(LIST, new Set()).map((i) => i.symbol)).toEqual(LIST.map((i) => i.symbol));
  });
  it("says how many match out of every row", () => {
    expect(countText(LIST, MATCHED)).toBe("2 of 5 match");
    expect(countText(Array.from({ length: 80 }, (_, i) => ({ symbol: `S${i}`, exchange: "NSE" })), new Set(Array.from({ length: 12 }, (_, i) => `NSE:S${i}`))))
      .toBe("12 of 80 match");
  });
  it("the bar shows the count and Matches only once a screen is chosen", () => {
    const choice = { ...NO_CHOICE, screen_id: 4 };
    const state: WatchScreenState = { choice, screen: SCREENS[0], matched: MATCHED, outcome: null, checking: false, failed: false };
    const html = renderToStaticMarkup(<WatchScreenBar listKey="o:7" screens={SCREENS} choice={choice} onChoose={() => {}} state={state} items={LIST} />);
    expect(html).toContain("2 of 5 match");
    expect(html).toMatch(/aria-pressed="false"[^>]*>.*Matches only/);
  });
  it("a system list marks its matches and dims the rest", () => {
    const html = renderToStaticMarkup(<SystemListView summary={{ key: "k", name: "K", count: 5 }} list={{ key: "k", name: "K", items: LIST, refreshed_at: null } as never}
      rows={shownRows(LIST, MATCHED, false)} total={5} matching={5} query="" onQuery={() => {}} sort="list" onSort={() => {}} quotes={new Map()} quotesFailed={false}
      current={() => false} onOpen={() => {}} onCopy={() => {}} copying={false} onRetry={() => {}} touch={false} field="" textBtn=""
      screen={{ isMatch: (i) => MATCHED.has(`NSE:${i.symbol}`), only: false }} />);
    const marks = [...html.matchAll(/data-match="(yes|no)"/g)].map((m) => m[1]);
    expect(marks).toEqual(["yes", "yes", "no", "no", "no"]);
    expect(html.match(/data-match="no"[^>]*opacity-60/g)).toHaveLength(3);
    expect(html.match(/data-match="yes"[^>]*border-up/g)).toHaveLength(2);
  });
});

const reply = (over: Partial<CheckReply> = {}): CheckReply => ({
  text: "x", base_tf: "1d", data_source: "lake:daily", disclaimer: "", scanned: 0, matched: [], results: [],
  funnel: { universe: 0, with_data: 0, stages: [] }, screen: { id: 4, name: "S" }, bar: "2026-11-06T03:45:00+00:00", next_check_at: 5_000_000,
  alerted: [], budget: { broker_calls: 0, refused: 0, per_minute: 100 }, ...over,
});

describe("W3 checked store-first, on bar close only, only for the list on screen", () => {
  it("sends each market's rows once, the pending ones next, and keeps the earliest next bar close", async () => {
    const calls: { symbols: string[]; exchange: string }[] = [];
    const api = { check: vi.fn(async (b: { symbols: string[]; exchange: string }) => {
      calls.push({ symbols: b.symbols, exchange: b.exchange });
      const [first, ...rest] = b.symbols;
      const done = b.exchange === "NSE" && b.symbols.length > 1 ? [first] : b.symbols;
      return reply({ scanned: done.length, matched: done.filter((s) => s !== "BBB"), results: done.map((s) => ({ symbol: s, matched: s !== "BBB", reason: null })),
        funnel: { universe: done.length, with_data: done.length, stages: [] }, pending: b.exchange === "NSE" && b.symbols.length > 1 ? rest : undefined,
        next_check_at: b.exchange === "BSE" ? 4_000_000 : 5_000_000 });
    }) };
    const items = [...rows("AAA", "BBB"), { symbol: "ZZZ", exchange: "BSE" }, { symbol: "NIFTY 50", exchange: "NSE_INDEX" }];
    const out = await checkList(api, { listKey: "o:1", listName: "L", screenId: 4, items }, { sleep: async () => {} });
    expect(calls).toEqual([{ symbols: ["AAA", "BBB"], exchange: "NSE" }, { symbols: ["BBB"], exchange: "NSE" }, { symbols: ["ZZZ"], exchange: "BSE" }]);
    expect([...out.matched].sort()).toEqual(["BSE:ZZZ", "NSE:AAA"]);
    expect(out.nextAt).toBe(4_000_000);
    expect(byExchange(items).has("NSE_INDEX")).toBe(false);                   // a market the screener does not run: not checked
  });

  function harness(hidden = { v: false }) {
    const timers: { fn: () => void; ms: number }[] = [];
    const checked: CheckerTarget[] = [];
    const outcomes: (CheckOutcome | null)[] = [];
    const checker = new ScreenChecker({
      check: async (t) => { checked.push(t); return { matched: new Set(["NSE:AAA"]), results: new Map(), nextAt: 1_060_000, alerted: [], slowed: false, bar: null }; },
      onOutcome: (o) => outcomes.push(o), onBusy: () => {}, onFailed: () => {}, hidden: () => hidden.v, now: () => 1_000_000,
      setTimer: (fn, ms) => { timers.push({ fn, ms }); return timers.length; }, clearTimer: (t) => { timers[(t as number) - 1].fn = () => {}; },
    });
    return { checker, timers, checked, outcomes };
  }
  const target: CheckerTarget = { listKey: "o:1", listName: "L", screenId: 4, items: rows("AAA", "BBB") };
  const flush = () => new Promise((r) => setTimeout(r, 0));

  it("a list not on screen is never checked", async () => {
    const h = harness();
    h.checker.update(false, target);
    await flush();
    expect(h.checked).toHaveLength(0);
    expect(h.timers).toHaveLength(0);
  });
  it("checks when it shows, then only at the next bar close; a price move or a reorder asks for nothing", async () => {
    const h = harness();
    h.checker.update(true, target);
    await flush();
    expect(h.checked).toHaveLength(1);
    expect(h.timers.map((t) => t.ms)).toEqual([60_000]);                   // the server's next bar close
    h.checker.update(true, { ...target, items: rows("BBB", "AAA") });      // same rows, new order
    await flush();
    expect(h.checked).toHaveLength(1);
    h.timers[0].fn();                                                       // the bar closed
    await flush();
    expect(h.checked).toHaveLength(2);
    h.checker.update(true, { ...target, items: rows("AAA", "BBB", "CCC") }); // a row added: checked again at once
    await flush();
    expect(h.checked).toHaveLength(3);
  });
  it("leaving the screen stops the timer; a check due while the tab is hidden waits for it", async () => {
    const hidden = { v: false };
    const h = harness(hidden);
    h.checker.update(true, target);
    await flush();
    h.checker.update(false, target);                                        // the list left the screen
    h.timers[0].fn();
    await flush();
    expect(h.checked).toHaveLength(1);
    hidden.v = true;
    h.checker.update(true, target);
    await flush();
    expect(h.checked).toHaveLength(1);                                      // due, but the tab is hidden
    hidden.v = false;
    h.checker.visible();
    await flush();
    expect(h.checked).toHaveLength(2);
  });
  it("waits at least a second and at most a day; no next close known: no timer", () => {
    expect(waitUntil(1_000_500, 1_000_000)).toBe(1_000);
    expect(waitUntil(1_000_000 + 3 * 86_400_000, 1_000_000)).toBe(86_400_000);
    expect(waitUntil(null, 0)).toBeNull();
  });
});

describe("W4 the alert is optional and OFF by default", () => {
  it("the alert box opens with both choices off, Telegram only after the app alert", () => {
    const choice = { ...NO_CHOICE, screen_id: 4 };
    const state: WatchScreenState = { choice, screen: SCREENS[0], matched: new Set(), outcome: null, checking: false, failed: false };
    const html = renderToStaticMarkup(<WatchScreenBar listKey="o:7" screens={SCREENS} choice={choice} onChoose={() => {}} state={state} items={LIST} />);
    expect(html).toMatch(/data-testid="watch-screen-alerts"[^>]*>.*Off/);
    expect(html).not.toContain("watch-screen-alert-box");                  // folded until he opens it
  });
});

const OVERLAY: OverlayReply = {
  symbol: "AAA", exchange: "NSE", base_tf: "1d", screen: { id: 4, name: "My trend screen" }, text: "close > SMA(close, 5)", disclaimer: "Not a recommendation.",
  available: true, matched: true,
  legend: [{ id: "b1.c1", block: "b1", text: "close > SMA(close, 5)", state: "met", value: "close 120.00 > SMA(close, 5) 118.00" },
    { id: "b2.c1", block: "b2", text: "RSI(3) > 40", state: "failed", value: "RSI(3) 35.10 > 40" },
    { id: "b3.c1", block: "b3", text: "volume > 1", state: "not_needed", value: "volume 900 > 1" }],
  hits: ["2026-11-04T00:00:00+00:00", "2026-11-06T00:00:00+00:00"],
  as_of: { bar_start: "2026-11-06T00:00:00+00:00" },
  bars: { t: ["2026-11-04T00:00:00+00:00", "2026-11-05T00:00:00+00:00", "2026-11-06T00:00:00+00:00"], o: [1, 1, 1], h: [2, 2, 2], l: [0, 0, 0], c: [1, 1, 1], v: [] },
  drawables: [{ id: "d1", row: "b1.c1", kind: "overlay", pane: "price", label: "close > SMA(close, 5)", series: [{ name: "SMA(close, 5)", values: [1, 1.1, 1.2], style: "line" }],
    levels: [{ value: 1.5, label: "level", value_text: "1.50" }], failed: false } as never],
  matched_index: 2,
};
const CANDLES = ["2026-11-04T03:45:00Z", "2026-11-05T03:45:00Z", "2026-11-06T03:45:00Z"].map((timestamp) => ({ timestamp, open: 1, high: 2, low: 0, close: 1, volume: 0 }));

describe("C1 the overlay on the chart", () => {
  it("puts the screen's bars on the chart's own candles (nearest within half a bar)", () => {
    expect(onCandles(OVERLAY.bars!.t, CANDLES, "1d")).toEqual(CANDLES.map((c) => c.timestamp));
    expect(onCandles(["2026-10-01T00:00:00+00:00"], CANDLES, "1d")).toEqual(["2026-10-01T00:00:00+00:00"]);  // no candle near: kept
  });
  it("draws the screen's lines, levels and a marker on every bar where the whole screen held", () => {
    const d = overlayLayers(OVERLAY, CANDLES, true);
    expect(d.studies.map((s) => s.line.title)).toEqual(["close > SMA(close, 5)"]);
    expect(d.levels.map((l) => l.price)).toEqual([1.5]);
    expect(d.markers.map((m) => [m.timestamp, m.shape])).toEqual([[CANDLES[0].timestamp, "arrowUp"], [CANDLES[2].timestamp, "arrowUp"]]);
    expect(d.markers.some((m) => m.text === "Matched")).toBe(false);
  });
  it("on another timeframe only the price levels stay; nothing at all when the stock has no bars", () => {
    const d = overlayLayers(OVERLAY, CANDLES, false);
    expect(d.studies).toEqual([]);
    expect(d.markers).toEqual([]);
    expect(d.levels).toHaveLength(1);
    expect(overlayLayers({ ...OVERLAY, available: false, bars: null }, CANDLES, true).studies).toEqual([]);
  });
  it("the legend lists every condition with ✓ / ✗ and its value on the last closed bar", () => {
    const state: ScreenOverlayState = { reply: OVERLAY, loading: false, failed: false, sameTf: true, screenTf: "day", levels: [], markers: [] };
    const html = renderToStaticMarkup(<ScreenLegend state={state} />);
    expect(html).toContain("My trend screen");
    expect(html).toContain("Meets the screen");
    expect(html).toContain("1 of 3 met");
    expect([...html.matchAll(/data-state="(\w+)"/g)].map((m) => m[1])).toEqual(["met", "failed", "not_needed"]);
    expect(html).toContain("close 120.00 &gt; SMA(close, 5) 118.00");
    expect(html).toMatch(/aria-label="Met"[^>]*>✓/);
    expect(html).toMatch(/aria-label="Not met"[^>]*>✗/);
    expect(html).not.toContain("screen-legend-tf");
    const other = renderToStaticMarkup(<ScreenLegend state={{ ...state, sameTf: false }} onShowTf={() => {}} />);
    expect(other).toContain("Show lines on");
    expect(markOf("unknown").sign).toBe("?");
  });
  it("the chart address carries the screen only when the list has one chosen", () => {
    expect(screenParam("", { screenId: 4 })).toBe("?wscreen=4");
    expect(screenParam("?exchange=BSE", { screenId: 4 })).toBe("?exchange=BSE&wscreen=4");
    expect(screenParam("?exchange=BSE")).toBe("?exchange=BSE");
    expect(withScreen("/charts/AAA", { screenId: 4 })).toBe("/charts/AAA?wscreen=4");
    expect(withScreen("/charts/AAA?exchange=BSE", null)).toBe("/charts/AAA?exchange=BSE");
  });
  it("an in-place chart keeps the screen in its panel address; an older address reads as before", () => {
    const m = (path: string) => { const u = new URL(path, "http://x"); return CHART_PANEL.match({ pathname: u.pathname, search: u.searchParams, hash: "" }); };
    expect(m("/charts/AAA?exchange=BSE&wscreen=4")).toEqual({ symbol: "AAA", exchange: "BSE", wscreen: "4" });
    expect(m("/charts/AAA?wscreen=x")).toEqual({ symbol: "AAA" });
    expect(CHART_PANEL.href({ symbol: "AAA", wscreen: "4" })).toBe("/charts/AAA?wscreen=4");
    expect(CHART_PANEL.href({ symbol: "AAA", exchange: "BSE" })).toBe("/charts/AAA?exchange=BSE");
  });
});
