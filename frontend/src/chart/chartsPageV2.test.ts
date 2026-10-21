import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import appShellSource from "../shell/AppShellV2.tsx?raw";
import type { SystemStatus } from "../types";
import { withRecent } from "../ui/SymbolPicker";
import pageSource from "./ChartsPageV2.tsx?raw";
import { cleanView, drawingNote, marketChip, offeredTimeframes, sessionFamily, volumeLabelFor } from "./chartsPage";
import { isOn, menuGroups, onCount, toggle, type IndicatorState } from "./indicatorsModel";
import { OLDER_RETRY_MS, OlderGuard, candleErrorText, refreshBars, refreshDaysFor } from "./useChartCandles";
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
    g.reset("A");
    expect(g.begin("A", true)).toBe(true);
    expect(g.begin("A", true)).toBe(false);
    g.end("A", 120);
    expect(g.begin("A", true)).toBe(true);
  });

  it("stops for good when the broker has nothing older", () => {
    const g = new OlderGuard();
    g.reset("A");
    g.begin("A", true);
    g.end("A", 0);
    expect(g.exhausted).toBe(true);
    expect(g.begin("A", true)).toBe(false);
  });

  it("waits after a failed request, then tries again", () => {
    let t = 1_000;
    const g = new OlderGuard(() => t);
    g.reset("A");
    g.begin("A", true);
    g.end("A", null);
    expect(g.exhausted).toBe(false);
    expect(g.begin("A", true)).toBe(false);
    t += OLDER_RETRY_MS;
    expect(g.begin("A", true)).toBe(true);
  });

  it("no bars yet, another chart key, or a late answer for the previous symbol: nothing changes", () => {
    const g = new OlderGuard();
    g.reset("A");
    expect(g.begin("A", false)).toBe(false);
    expect(g.begin("B", true)).toBe(false);
    g.begin("A", true);
    g.reset("B");                                        // the trader switched symbol while A's request was out
    g.end("A", 0);
    expect(g.exhausted).toBe(false);
    expect(g.begin("B", true)).toBe(true);
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
