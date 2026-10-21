import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import appShellSource from "../shell/AppShellV2.tsx?raw";
import type { CandlesResponse, SystemStatus } from "../types";
import { enterChoice, listKeyFor, readRecent, recentKeyFor, rememberSymbol, withRecent } from "../ui/SymbolPicker";
import pageSource from "./ChartsPageV2.tsx?raw";
import { cleanView, drawingNote, marketChip, offeredTimeframes, readLastSymbol, readView, saveLastSymbol, saveView, sessionFamily, volumeLabelFor } from "./chartsPage";
import { isOn, menuGroups, onCount, toggle, type IndicatorState } from "./indicatorsModel";
import { CandleFeed, OLDER_RETRY_MS, OlderGuard, candleErrorText, refreshBars, refreshDaysFor, type FeedEvents } from "./useChartCandles";
import { fullscreenKey, isTyping } from "./useFullscreen";

vi.mock("lightweight-charts", () => import("./lightweightFake.fixture"));
const { crosshairTimeLabel, PaneLabel } = await import("./chartCore");
const { PAGE_COMPONENTS_V2 } = await import("../shell/pagesV2");

const el = (tagName: string, isContentEditable = false) => ({ tagName, isContentEditable }) as unknown as EventTarget;

describe("UI2-4b2 full screen keys", () => {
  const off = { on: false, busy: false };
  const on = { on: true, busy: false };

  it("F toggles, in either case, from anywhere but a text field", () => {
    expect(fullscreenKey({ key: "f", target: el("DIV") }, off)).toBe("toggle");
    expect(fullscreenKey({ key: "F", target: el("BODY") }, on)).toBe("toggle");
    for (const t of [el("INPUT"), el("TEXTAREA"), el("SELECT"), el("DIV", true)]) expect(fullscreenKey({ key: "f", target: t }, off)).toBeNull();
    expect(isTyping(null)).toBe(false);
  });

  it("F with Ctrl, Cmd or Alt is someone else's shortcut", () => {
    for (const mod of ["ctrlKey", "metaKey", "altKey"]) expect(fullscreenKey({ key: "f", target: el("DIV"), [mod]: true }, off)).toBeNull();
  });

  it("Escape leaves full screen, and does nothing outside it", () => {
    expect(fullscreenKey({ key: "Escape", target: el("DIV") }, on)).toBe("exit");
    expect(fullscreenKey({ key: "Escape", target: el("INPUT") }, on)).toBe("exit");
    expect(fullscreenKey({ key: "Escape", target: el("DIV") }, off)).toBeNull();
  });

  it("a drawing tool placing (or a menu open) has the keys first: its Escape cancels it, F is not taken", () => {
    const busy = { on: true, busy: true };
    expect(fullscreenKey({ key: "Escape", target: el("DIV") }, busy)).toBeNull();
    expect(fullscreenKey({ key: "f", target: el("DIV") }, busy)).toBeNull();
    expect(fullscreenKey({ key: "Escape", target: el("DIV"), defaultPrevented: true }, on)).toBeNull();
  });
});

describe("UI2-4b2 timeframes the page offers", () => {
  it("W and M only when the server lists them; the rest always", () => {
    expect(offeredTimeframes(null)).toEqual(["1min", "3min", "5min", "15min", "30min", "60min", "day"]);
    expect(offeredTimeframes(["1min", "day"])).not.toContain("week");
    expect(offeredTimeframes(["1min", "3min", "5min", "15min", "30min", "60min", "day", "week", "month"]).slice(-3)).toEqual(["day", "week", "month"]);
    expect(offeredTimeframes(["week"])).toContain("week");
    expect(offeredTimeframes(["week"])).not.toContain("month");
  });

  it("the stored view is cleaned: a wrong field takes its default, overlays keep the chart's order", () => {
    expect(cleanView(null)).toEqual({ timeframe: "5min", type: "candles", overlays: [], layers: [] });
    expect(cleanView({ timeframe: "week", type: "renko", overlays: ["sma", "x", "ema_fast"], layers: ["trades", "trades", 3] }))
      .toEqual({ timeframe: "week", type: "candles", overlays: ["ema_fast", "sma"], layers: ["trades"] });
  });
});

describe("UI2-4b2 market state chip", () => {
  const now = Date.parse("2026-10-19T20:00:00+05:30");          // Monday evening
  type Sessions = NonNullable<SystemStatus["sessions"]>;
  const status = (s: Sessions): SystemStatus => ({ maintenance_mode: false, maintenance_message: null, disabled_brokers: [], sessions: s });

  it("open, closed until tomorrow (weekday named), closed until later today, unknown", () => {
    expect(marketChip(status({ NSE: { is_open: true, reason: "", next_open: null } }), "NSE", now)).toEqual({ text: "Market open", open: true });
    expect(marketChip(status({ NSE: { is_open: false, reason: "", next_open: "2026-10-20T09:15:00+05:30" } }), "NSE", now))
      .toEqual({ text: "Closed · opens Tue 09:15", open: false });
    const morning = Date.parse("2026-10-20T07:00:00+05:30");
    expect(marketChip(status({ NSE: { is_open: false, reason: "", next_open: "2026-10-20T09:15:00+05:30" } }), "NSE", morning)?.text).toBe("Closed · opens 09:15");
    expect(marketChip(status({ NSE: { is_open: false, reason: "", next_open: null } }), "NSE", now)?.text).toBe("Market closed");
    expect(marketChip(null, "NSE", now)).toBeNull();
    expect(marketChip(status({}), "NSE", now)).toBeNull();
  });

  it("each venue reads its own session family", () => {
    expect(["NSE", "BSE", "NFO", "MCX", "MCX_FO", "CDS", "BCD", "COINDCX", "XYZ"].map(sessionFamily))
      .toEqual(["NSE", "NSE", "NSE", "MCX", "MCX", "CDS", "CDS", "CRYPTO", "NSE"]);
    expect(marketChip(status({ NSE: { is_open: false, reason: "", next_open: null }, MCX: { is_open: true, reason: "", next_open: null } }), "MCX", now)?.open).toBe(true);
  });
});

describe("UI2-4b2 Indicators menu", () => {
  const state: IndicatorState = { panes: { volume: true, rsi: true }, overlays: [], layers: [] };
  const all = () => true;
  const none = () => false;

  it("three groups in order; a layer is listed only while its flag is on; no reader overlay", () => {
    expect(menuGroups("", all).map((g) => g.title)).toEqual(["On this chart", "Popular", "Layers"]);
    expect(menuGroups("", none).map((g) => g.title)).toEqual(["On this chart", "Popular"]);
    expect(menuGroups("", all)[0].items.map((i) => i.label)).toEqual(["Volume", "RSI 14"]);
    expect(menuGroups("", all)[1].items.map((i) => i.label)).toEqual(["EMA 9", "EMA 21", "SMA 50", "VWAP"]);
    expect(JSON.stringify(menuGroups("", all))).not.toMatch(/reader|zones/i);
    for (const item of menuGroups("", all)[1].items) expect(item.term, item.label).toBeTruthy();       // each has its "?"
  });

  it("the search matches names and plain words", () => {
    expect(menuGroups("average", all).flatMap((g) => g.items.map((i) => i.id))).toEqual(["ema_fast", "ema_slow", "sma", "vwap"]);
    expect(menuGroups("momentum", all).flatMap((g) => g.items.map((i) => i.id))).toEqual(["rsi"]);
    expect(menuGroups("trades", none)).toEqual([]);
    expect(menuGroups("zzz", all)).toEqual([]);
  });

  it("turning Volume or RSI off removes the pane (a prefs change); price lines keep the chart's order", () => {
    expect(toggle("volume", state)).toEqual({ panes: { volume: false } });
    expect(toggle("rsi", { ...state, panes: { volume: true, rsi: false } })).toEqual({ panes: { rsi: true } });
    expect(toggle("vwap", state)).toEqual({ overlays: ["vwap"] });
    expect(toggle("ema_fast", { ...state, overlays: ["vwap"] })).toEqual({ overlays: ["ema_fast", "vwap"] });
    expect(toggle("vwap", { ...state, overlays: ["ema_fast", "vwap"] })).toEqual({ overlays: ["ema_fast"] });
    expect(toggle("trades", state)).toEqual({ layers: ["trades"] });
    expect(toggle("trades", { ...state, layers: ["trades"] })).toEqual({ layers: [] });
  });

  it("counts what is on, not a layer whose flag is off", () => {
    expect(isOn("rsi", state)).toBe(true);
    const s = { ...state, overlays: ["vwap"] as const, layers: ["trades"] };
    expect(onCount(s, all)).toBe(4);
    expect(onCount(s, none)).toBe(3);
  });
});

describe("UI2-4b2 older history: one request at a time, none past the end", () => {
  it("lets one request out; the chart's repeated asks while it is in flight do nothing", () => {
    const g = new OlderGuard();
    const t = g.begin(true);
    expect(t).not.toBeNull();
    expect(g.begin(true)).toBeNull();
    g.end(t!, 120);
    expect(g.begin(true)).not.toBeNull();
  });

  it("stops for good when the broker answered with nothing older", () => {
    const g = new OlderGuard();
    g.end(g.begin(true)!, 0);
    expect(g.exhausted).toBe(true);
    expect(g.begin(true)).toBeNull();
  });

  it("waits after a failed request or a per-symbol error (null), then tries again", () => {
    let now = 1_000;
    const g = new OlderGuard(() => now);
    g.end(g.begin(true)!, null);
    expect(g.exhausted).toBe(false);
    expect(g.begin(true)).toBeNull();
    now += OLDER_RETRY_MS;
    expect(g.begin(true)).not.toBeNull();
  });

  it("no bars yet: nothing; a late answer after a reset (new symbol, a retry, back to the same symbol) is dropped", () => {
    const g = new OlderGuard();
    expect(g.begin(false)).toBeNull();
    const a = g.begin(true)!;
    g.reset();                                           // the trader switched away (or back to the same symbol)
    expect(g.current(a)).toBe(false);
    g.end(a, 0);
    expect(g.exhausted).toBe(false);
    expect(g.begin(true)).not.toBeNull();
  });

  it("a refresh reads a few recent days; week and month drop their first, possibly cut, bar", () => {
    expect(["5min", "day", "week", "month"].map((tf) => refreshDaysFor(tf as never))).toEqual([2, 10, 21, 70]);
    const bars = [1, 2, 3].map((i) => ({ timestamp: `2026-10-0${i}T00:00:00Z`, open: i, high: i, low: i, close: i, volume: 0 }));
    expect(refreshBars(bars, "week")).toEqual(bars.slice(1));
    expect(refreshBars(bars, "day")).toBe(bars);
  });
});

describe("UI2-4b2 plain error copy", () => {
  const developer = /error|exception|\b\d{3}\b|token|api|null|undefined|budget|fetch|http/i;

  it("says what happened, then what to do; no developer wording", () => {
    const texts = [
      candleErrorText(new ApiError(409, "no usable broker session"), "NIFTY 50"),
      candleErrorText(new ApiError(429, "rate"), "NIFTY 50"),
      candleErrorText(new Error("budget used"), "NIFTY 50"),
      candleErrorText(new Error("no bars"), "NIFTY 50"),
      candleErrorText(new TypeError("Failed to fetch"), "NIFTY 50"),
      drawingNote("Drawings could not be loaded: TypeError: x is undefined"),
      drawingNote("HTTP 500 Internal Server Error"),
      drawingNote("a trendline needs 2 anchor(s), got 1"),
    ];
    for (const t of texts) {
      expect(t, t ?? "").toMatch(/^[A-Z][^.]*\.\s[A-Z].*\.$/);              // two sentences: what happened. What to do.
      expect(t, t ?? "").not.toMatch(developer);
    }
    expect(drawingNote("This drawing is locked. Unlock it to move it.")).toBe("This drawing is locked. Unlock it to move it.");
    expect(drawingNote(null)).toBeNull();
  });

  it("the page has no developer text, no sample data and no raw error", () => {
    const code = pageSource.replace(/\/\*[\s\S]*?\*\/|\/\/[^\n]*/g, "");
    expect(code).not.toMatch(/sample|seed|String\(e\)|\.message\}|via \{|debug/i);
  });
});

describe("UI2-4b2 chart labels", () => {
  const t = Date.parse("2026-10-19T04:45:00Z") / 1000;            // Mon 19 Oct 2026, 10:15 IST

  it("intraday shows the time; day and week the date only; month the month", () => {
    expect(crosshairTimeLabel(t, "Asia/Kolkata", "5min")).toMatch(/19 Oct.*10:15/);
    for (const tf of ["day", "week"] as const) {
      const label = crosshairTimeLabel(t, "Asia/Kolkata", tf);
      expect(label).toMatch(/Mon.*19 Oct.*2026/);
      expect(label).not.toMatch(/\d:\d/);
    }
    expect(crosshairTimeLabel(t, "Asia/Kolkata", "month")).toBe("Oct 2026");
  });

  it("a pane label repaints only when its text or colour changes", () => {
    const label = new PaneLabel("Volume", "red");
    const requestUpdate = vi.fn();
    label.attached({ requestUpdate });
    label.set("Volume", "red");
    expect(requestUpdate).not.toHaveBeenCalled();
    label.set("Volume · NIFTY OCT FUT", "red");
    expect(requestUpdate).toHaveBeenCalledTimes(1);
    expect(label.paneViews()).toHaveLength(1);
  });

  it("an index's volume names its future", () => {
    expect(volumeLabelFor(null)).toBe("Volume");
    expect(volumeLabelFor({ symbol: "NIFTY26OCTFUT", expiry: "2026-10-27", exchange: "NFO", underlying: "NIFTY", from: null, label: "" }))
      .toBe("Volume · NIFTY OCT FUT");
    expect(volumeLabelFor({ symbol: "X", expiry: "", exchange: "NFO", underlying: "NIFTY", from: null, label: "" })).toBe("Volume");
  });
});

describe("UI2-4b2 wiring and the symbol picker", () => {
  it("the v2 shell's /charts route takes the page from the v2 registry", () => {
    expect(PAGE_COMPONENTS_V2.charts).toBeDefined();
    expect(appShellSource).toContain("PAGE_COMPONENTS_V2.charts");
    expect(appShellSource).toMatch(/path="\/charts\/:symbol\?"[^\n]*<ChartsPage /);
    expect(appShellSource).not.toMatch(/ChartWindow/);
  });

  it("recent symbols: newest first, no repeats, eight at most", () => {
    const list = Array.from({ length: 8 }, (_, i) => ({ symbol: `S${i}`, exchange: "NSE" }));
    const out = withRecent(list, { symbol: "S3", exchange: "NSE" });
    expect(out[0].symbol).toBe("S3");
    expect(out).toHaveLength(8);
    expect(withRecent(list, { symbol: "NEW", exchange: "NSE" }).map((r) => r.symbol)).toEqual(["NEW", ...list.slice(0, 7).map((r) => r.symbol)]);
    expect(withRecent(list, { symbol: "S0", exchange: "BSE" })).toHaveLength(8);
  });
});

describe("UI2-4b2 review: the first load is retried whole, late answers are dropped", () => {
  const answer = (bars: number, error: string | null = null): CandlesResponse => ({
    source: { broker: "upstox", account_label: "primary" }, exchange: "NSE", timeframe: "5min", base_interval: "1min", lookback_days: 10,
    fetched_at: "", warnings: [], note: "",
    symbols: { "NIFTY 50": { bars: Array.from({ length: bars }, (_, i) => ({ timestamp: `2026-10-0${i + 1}T04:00:00Z`, open: 1, high: 1, low: 1, close: 1, volume: 0 })),
                             count: bars, first: null, last: null, error } },
  });
  const events = () => {
    const log: string[] = [];
    const on: FeedEvents = {
      loading: (v) => log.push(`loading:${v}`), replace: (b) => log.push(`replace:${b.length}`), merge: (b) => log.push(`merge:${b.length}`),
      error: (t) => log.push(t ? "error" : "ok"),
    };
    return { log, on };
  };

  it("a failed first read: the next tick asks for the whole history again, not a short refresh", async () => {
    const asked: number[] = [];
    const replies = [Promise.reject(new TypeError("Failed to fetch")), Promise.resolve(answer(3)), Promise.resolve(answer(2))];
    const { log, on } = events();
    const feed = new CandleFeed((days) => { asked.push(days); return replies.shift()!; }, "NIFTY 50", "5min", on);
    await feed.tick();
    await feed.tick();
    await feed.tick();
    expect(asked).toEqual([10, 10, 2]);
    expect(log).toEqual(["loading:true", "error", "loading:false", "loading:true", "replace:3", "ok", "loading:false", "merge:2", "ok"]);
  });

  it("a per-symbol error on the first read is an error, and the first read is retried", async () => {
    const asked: number[] = [];
    const { log, on } = events();
    const feed = new CandleFeed(async (days) => { asked.push(days); return answer(0, "budget used"); }, "NIFTY 50", "day", on);
    await feed.tick();
    await feed.tick();
    expect(asked).toEqual([730, 730]);
    expect(log.filter((l) => l === "error")).toHaveLength(2);
  });

  it("after stop (a new symbol, a retry, back to the same symbol) a late answer changes nothing", async () => {
    let release: (r: CandlesResponse) => void = () => undefined;
    const { log, on } = events();
    const feed = new CandleFeed(() => new Promise((r) => { release = r; }), "NIFTY 50", "5min", on);
    const pending = feed.tick();
    feed.stop();
    release(answer(5));
    await pending;
    expect(log).toEqual(["loading:true"]);
    await feed.tick();
    expect(log).toEqual(["loading:true"]);
  });

  it("one read at a time: a tick while one is out does nothing", async () => {
    let calls = 0;
    const { on } = events();
    const feed = new CandleFeed(() => { calls += 1; return new Promise(() => undefined); }, "NIFTY 50", "5min", on);
    void feed.tick();
    void feed.tick();
    expect(calls).toBe(1);
  });
});

describe("UI2-4b2 review: the symbol picker never takes a row left from earlier text", () => {
  const recent = [{ symbol: "TCS", exchange: "NSE" }, { symbol: "INFY", exchange: "NSE" }];
  const base = { open: true, rows: recent, rowsFor: "", active: 0, value: "NIFTY 50", exchange: "NSE" };

  it("the list key: the recent list for an empty field or the symbol on screen, else the text", () => {
    expect(listKeyFor("", "NIFTY 50")).toBe("");
    expect(listKeyFor(" nifty 50 ", "NIFTY 50")).toBe("");
    expect(listKeyFor("relia", "NIFTY 50")).toBe("RELIA");
  });

  it("typing 'reliance' over the recent list and pressing Enter before the search answers opens RELIANCE", () => {
    expect(enterChoice({ ...base, text: "reliance" })).toEqual({ symbol: "RELIANCE", exchange: "NSE" });
    // ... and the answer for an earlier text is not taken either
    expect(enterChoice({ ...base, rows: [{ symbol: "RELAXO", exchange: "NSE" }], rowsFor: "RELA", text: "reliance" }))
      .toEqual({ symbol: "RELIANCE", exchange: "NSE" });
  });

  it("rows that answer the text as typed now are taken; a closed list or an empty field takes nothing stale", () => {
    expect(enterChoice({ ...base, rows: [{ symbol: "RELIANCE", exchange: "BSE" }], rowsFor: "RELIANCE", text: "reliance" }))
      .toEqual({ symbol: "RELIANCE", exchange: "BSE" });
    expect(enterChoice({ ...base, text: "NIFTY 50", active: 1 })).toEqual({ symbol: "INFY", exchange: "NSE" });
    expect(enterChoice({ ...base, open: false, text: "tcs" })).toEqual({ symbol: "TCS", exchange: "NSE" });
    expect(enterChoice({ ...base, rowsFor: "X", text: "" })).toBeNull();
  });

  it("Enter on the unchanged field (recent list shown, no arrow key yet) opens nothing", () => {
    expect(enterChoice({ ...base, text: "NIFTY 50", active: -1 })).toBeNull();
    expect(enterChoice({ ...base, text: "", active: -1 })).toBeNull();
    expect(enterChoice({ ...base, open: false, text: "nifty 50" })).toBeNull();
  });
});

describe("UI2-4b2 review: per-user storage on this browser", () => {
  const store = new Map<string, string>();
  const fake = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => { store.set(k, v); }, removeItem: (k: string) => { store.delete(k); } };

  it("each user has their own last symbol, view and recent symbols; signed out reads and writes nothing", () => {
    vi.stubGlobal("localStorage", fake);
    try {
      saveLastSymbol(1, "TCS");
      saveView(1, { timeframe: "15min", type: "bars", overlays: ["vwap"], layers: [] });
      rememberSymbol(recentKeyFor(1), { symbol: "TCS", exchange: "NSE" });
      expect(readLastSymbol(1)).toBe("TCS");
      expect(readLastSymbol(2)).toBeNull();
      expect(readView(1).timeframe).toBe("15min");
      expect(readView(2).timeframe).toBe("5min");
      expect(readRecent(recentKeyFor(1))).toEqual([{ symbol: "TCS", exchange: "NSE" }]);
      expect(readRecent(recentKeyFor(2))).toEqual([]);
      const before = store.size;
      saveLastSymbol(null, "X");
      saveView(null, readView(1));
      rememberSymbol(recentKeyFor(null), { symbol: "X", exchange: "NSE" });
      expect(store.size).toBe(before);
      expect(readLastSymbol(null)).toBeNull();
      expect([...store.keys()].every((k) => /:1$/.test(k))).toBe(true);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("a held F key does not toggle again", () => {
    expect(fullscreenKey({ key: "f", repeat: true, target: el("DIV") }, { on: false, busy: false })).toBeNull();
  });
});
