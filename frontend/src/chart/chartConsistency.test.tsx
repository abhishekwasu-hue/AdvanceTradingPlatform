import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import chartWindowSource from "../pages/ChartWindow.tsx?raw";
import signalsPageSource from "../pages/SignalsPage.tsx?raw";
import positionCardSource from "../components/PositionChartCard.tsx?raw";
import contractWindowSource from "../pages/ContractChartWindow.tsx?raw";
import partsSource from "../signals/SignalPartsV2.tsx?raw";
import topBarSource from "../shell/TopBarV2.tsx?raw";
import { feedItem } from "../signals/signals.fixture";
import { addressTimeframe, chartTabPath, chartWindowRedirect } from "./chartsPage";
import { RENEW_LIMIT_SECONDS, ageText, liveChipState, liveChipText } from "./liveChip";
import { STATUS_EVERY_MS, StatusPoll } from "./useMarketStatus";
import type { SystemStatus } from "../types";

vi.mock("lightweight-charts", () => import("./lightweightFake.fixture"));
const fake = await import("./lightweightFake.fixture");
const { ChartCore } = await import("./chartCore");
const { readChartPalette } = await import("./chartTheme");
const { default: EmbeddedChartV2 } = await import("./EmbeddedChartV2");
const { AuthProvider } = await import("../auth/AuthContext");
const { SignalDetail, signalLevels } = await import("../signals/SignalPartsV2");

const q = (s: string) => new URLSearchParams(s);
const palette = readChartPalette(null);

describe("UI2-4e an old ?chart= window link under ui_v2", () => {
  it("opens the Chart tab with its timeframe and exchange", () => {
    expect(chartWindowRedirect(q("chart=NIFTY 50&tf=15min"))).toBe("/charts/NIFTY%2050?tf=15min");
    expect(chartWindowRedirect(q("chart=NIFTY%2050&tf=15min&exchange=NSE&broker_name=upstox"))).toBe("/charts/NIFTY%2050?tf=15min");
    expect(chartWindowRedirect(q("chart=sbin&tf=day&exchange=bse"))).toBe("/charts/SBIN?tf=day&exchange=BSE");
    expect(chartWindowRedirect(q("chart= reliance "))).toBe("/charts/RELIANCE");
  });

  it("drops an unknown timeframe and a missing symbol; never names the chart window again (no loop)", () => {
    expect(chartWindowRedirect(q("chart=INFY&tf=2min"))).toBe("/charts/INFY");
    expect(chartWindowRedirect(q("chart=&tf=5min"))).toBe("/charts?tf=5min");
    expect(chartWindowRedirect(q("chart"))).toBe("/charts");
    for (const s of ["chart=A&tf=5min", "chart=A%26B&exchange=MCX", "chart=X"]) expect(chartWindowRedirect(q(s))).not.toMatch(/[?&]chart=/);
  });

  it("is guarded: once the flags are read (or the shell's 3 s cap counts them off), sent once, with no reads before", () => {
    expect(chartWindowSource).toContain("const capped = useWaitCap(!!user && !features.loaded);");     // shell/shellWait SHELL_WAIT_MS
    expect(chartWindowSource).toContain("if (capped && !features.loaded && !flagsOff) setFlagsOff(true);");
    expect(chartWindowSource).toContain('const v2 = !flagsOff && features.loaded && features.on("ui_v2");');
    expect(chartWindowSource).toContain("const reads = known && !v2;");
    expect(chartWindowSource).toMatch(/if \(!v2 \|\| redirected\.current\) return;\s*redirected\.current = true;\s*window\.location\.replace\(chartWindowRedirect\(params\)\);/);
    expect(chartWindowSource).toContain("if (loading || v2 || (user && !known)) return");                 // "Loading…" while waiting
    expect(chartWindowSource).toMatch(/useBrokerChart\(\{ enabled: reads && /);
    expect(chartWindowSource).toMatch(/useLiveLtp\(reads && /);
  });

  it("the Chart tab takes ?tf= from the address (not an old workspace start, which has a symbol)", () => {
    expect(addressTimeframe(q("tf=15min"))).toBe("15min");
    expect(addressTimeframe(q("tf=week&exchange=BSE"))).toBe("week");
    expect(addressTimeframe(q("tf=7min"))).toBeNull();
    expect(addressTimeframe(q("tab=multi&symbol=SBIN&tf=15min"))).toBeNull();
    expect(chartTabPath("nifty bank", "NSE", "60min")).toBe("/charts/NIFTY%20BANK?tf=60min");
    expect(chartTabPath("SENSEX", "BSE", "5min")).toBe("/charts/SENSEX?tf=5min&exchange=BSE");
  });
});

describe("UI2-4e the Live / Price delayed chip", () => {
  const T = 1_000_000;
  const fresh = { stale: false, age_seconds: 2 };

  it("Live while the server calls the quote fresh and answers keep coming; the hover says when the last one came", () => {
    expect(liveChipState(fresh, T, true, T + 3_000)).toEqual({ kind: "live", ageSeconds: 3 });
    expect(liveChipState(fresh, T, null, T)).toEqual({ kind: "live", ageSeconds: 0 });     // market state not known yet
    // The server decides about its quote: an old exchange time it does not call stale (its limit is set per deployment,
    // or turned off) is not judged again here.
    expect(liveChipState({ stale: false, age_seconds: 900 }, T, true, T + 1_000)?.kind).toBe("live");
    expect(liveChipText({ kind: "live", ageSeconds: 3.2 })).toEqual({ text: "Live", title: "Updated 3 s ago" });
    expect(liveChipText({ kind: "live", ageSeconds: 0.4 }).title).toBe("Updated just now");
  });

  it("Price delayed with the age: marked stale by the server, or no renewed answer here for longer than the limit", () => {
    expect(RENEW_LIMIT_SECONDS).toBe(120);                       // the backend's default QUOTE_MAX_STALE_SECONDS
    expect(liveChipState({ stale: true, age_seconds: 300 }, T, true, T)).toEqual({ kind: "delayed", ageSeconds: 300 });
    expect(liveChipState(fresh, T, true, T + 120_000)?.kind).toBe("live");
    expect(liveChipState(fresh, T, true, T + 121_000)).toEqual({ kind: "delayed", ageSeconds: 123 });   // shown: the price's age
    expect(liveChipText({ kind: "delayed", ageSeconds: 300 })).toEqual({ text: "Price delayed · 5 min", title: "The newest price is 5 min old." });
    // A stale quote with no exchange time: delayed, with no made-up age.
    expect(liveChipState({ stale: true, age_seconds: null }, T, true, T)).toEqual({ kind: "delayed", ageSeconds: null });
    expect(liveChipText({ kind: "delayed", ageSeconds: null }).text).toBe("Price delayed");
    // A bare price (no exchange time, not stale): its age is how long ago it came.
    expect(liveChipState({ stale: false, age_seconds: null }, T, true, T + 130_000)).toEqual({ kind: "delayed", ageSeconds: 130 });
  });

  it("nothing while the market is closed, or without a live price", () => {
    expect(liveChipState({ stale: true, age_seconds: 9_000 }, T, false, T)).toBeNull();
    expect(liveChipState(null, T, true, T)).toBeNull();
    expect(liveChipState(fresh, null, true, T)).toBeNull();
  });

  it("ages read as a person says them", () => {
    expect([ageText(4), ageText(59.6), ageText(61), ageText(3_599), ageText(7_300)]).toEqual(["4 s", "60 s", "1 min", "59 min", "2 h"]);
  });
});

describe("UI2-4e level lines on the v2 chart", () => {
  beforeEach(() => { fake.made.length = 0; });

  function core() {
    const c = new ChartCore({} as HTMLElement, { palette, grid: false, panes: { volume: true, rsi: false }, type: "candles", overlays: [], zone: "Asia/Kolkata" });
    const chart = fake.made[fake.made.length - 1];
    return { c, candles: chart.series("Candlestick")[0] };
  }

  it("dashed, labelled on the price axis, Entry neutral, Stop red, Target green; replaced as a whole; recoloured in place", () => {
    const { c, candles } = core();
    c.setLevels(signalLevels(feedItem()));
    expect(candles.priceLines.map((l) => [l.options.title, l.options.price, l.options.color, l.options.lineStyle, l.options.axisLabelVisible]))
      .toEqual([["Exit at profit", 104, palette.up, 2, true], ["Entry", 100, palette.text, 2, true], ["Exit if wrong", 98, palette.down, 2, true]]);   // UX3 labels
    c.applyTheme({ ...palette, up: "rgb(1, 2, 3)" }, false);
    expect(candles.priceLines[0].options.color).toBe("rgb(1, 2, 3)");
    // Beside levels the last-price line is neutral and dotted, never the red dashed Stop look.
    expect([candles.options.priceLineColor, candles.options.priceLineStyle]).toEqual([palette.crosshair, 1]);
    c.setLevels([{ price: Number.NaN, title: "Entry", tone: "neutral" }]);
    expect(candles.priceLines).toHaveLength(0);
    expect([candles.options.priceLineColor, candles.options.priceLineStyle]).toEqual(["", 2]);
  });

  it("the price scale takes the levels in, so a stop away from the bars still shows", () => {
    const { c, candles } = core();
    const scale = candles.options.autoscaleInfoProvider as (base: () => unknown) => { priceRange: { minValue: number; maxValue: number } } | null;
    const base = () => ({ priceRange: { minValue: 99, maxValue: 101 }, margins: { above: 0, below: 0 } });
    expect(scale(base)?.priceRange).toEqual({ minValue: 99, maxValue: 101 });
    c.setLevels([{ price: 95, title: "Stop", tone: "down" }, { price: 110, title: "Target", tone: "up" }]);
    expect(scale(base)?.priceRange).toEqual({ minValue: 95, maxValue: 110 });
    expect(scale(() => null)).toBeNull();
  });

  it("a signal without a stop or target marks what it has", () => {
    expect(signalLevels({ entry: 50, stop: null, target: null })).toEqual([{ price: 50, title: "Entry", tone: "neutral" }]);
  });
});

describe("UI2-4e the v2 chart on other pages", () => {
  const render = (node: React.ReactNode) => renderToStaticMarkup(<AuthProvider><MemoryRouter>{node}</MemoryRouter></AuthProvider>);

  it("the embedded chart is ChartV2 with the shared timeframes, the one settings place and Open in Chart tab", () => {
    const html = render(<EmbeddedChartV2 symbol="nifty 50" />);
    expect(html).toContain('data-embedded-chart="NIFTY 50"');
    expect(html).toContain("data-chart-theme=");                       // ChartV2's root
    expect(html).toContain('aria-label="Chart settings"');
    expect(html).toMatch(/aria-label="Open in Chart tab"[^>]*href="\/charts\/NIFTY%2050\?tf=5min"/);
    const tfs = [...html.matchAll(/aria-pressed="(?:true|false)"[^>]*>([^<]+)</g)].map((m) => m[1]);
    // UI-H W0-1: the most-used timeframes in the row, the rest (3 min, 30 min) under More.
    expect(tfs).toEqual(["1 min", "5 min", "15 min", "1 hour", "Daily"]);
    expect(html).toContain('aria-label="More timeframes"');
    expect(html).not.toMatch(/candles via|refreshes every|as of|source/i);
  });

  it("a host that shows its own link turns the chart's off, and keeps the timeframe", () => {
    const html = render(<EmbeddedChartV2 symbol="SBIN" timeframe="15min" onTimeframe={() => undefined} openLink={false} />);
    expect(html).not.toContain("Open in Chart tab");
    expect(html).toMatch(/aria-pressed="true"[^>]*>15 min</);
  });

  it("Signals under v2: the detail shows the v2 chart of the signal (its code loads on demand); today's Signals page keeps ProChart", () => {
    const html = render(<SignalDetail item={feedItem()} now={new Date("2026-10-08T05:00:00Z")} canTrade onDone={() => undefined} onError={() => undefined} />);
    expect(html).toContain('data-signal-chart="DEMO 50"');
    expect(html).toContain("data-levels-chart");                       // the levels hold its place while the chart loads
    expect(html).toContain('href="/charts/DEMO%2050?tf=5min"');         // one "Open chart", with the chart's timeframe
    expect(html).not.toContain("Open in Chart tab");
    expect(partsSource).toContain('lazy(() => import("../chart/EmbeddedChartV2"))');
    expect(partsSource).not.toMatch(/ProChart/);
    expect(signalsPageSource).toMatch(/<ProChart /);
    // The phone card keeps its chart button (the detail chart is the desktop panel's).
    const compact = render(<SignalDetail item={feedItem()} now={new Date("2026-10-08T05:00:00Z")} compact canTrade onDone={() => undefined} onError={() => undefined} />);
    expect(compact).not.toContain("data-signal-chart");
  });

  it("pages with no v2 layout yet keep today's chart", () => {
    expect(positionCardSource).toMatch(/<ProChart /);
    expect(contractWindowSource).toMatch(/<ProChart /);
  });
});

describe("UI2-4e one shared status read", () => {
  afterEach(() => { vi.useRealTimers(); });

  it("one read for every reader, then one a minute while any reader is left; none after the last leaves", async () => {
    vi.useFakeTimers();
    let now = 0;
    const read = vi.fn(async () => ({ maintenance_mode: false, maintenance_message: null, disabled_brokers: [] }) as SystemStatus);
    const poll = new StatusPoll(read, () => now);
    const seen = vi.fn();
    const a = poll.subscribe(seen);
    const b = poll.subscribe(() => undefined);
    await vi.runAllTicks();
    expect(read).toHaveBeenCalledTimes(1);
    expect(poll.snapshot().status?.maintenance_mode).toBe(false);
    expect(seen).toHaveBeenCalledTimes(1);
    now += STATUS_EVERY_MS;
    await vi.advanceTimersByTimeAsync(STATUS_EVERY_MS);
    expect(read).toHaveBeenCalledTimes(2);
    a(); b();
    await vi.advanceTimersByTimeAsync(5 * STATUS_EVERY_MS);
    expect(read).toHaveBeenCalledTimes(2);
    // A reader back within the minute uses the last answer; the next read is on the timer.
    now += 10_000;
    const c = poll.subscribe(() => undefined);
    expect(read).toHaveBeenCalledTimes(2);
    c();
  });

  it("nothing is read while the tab is hidden; shown again with an answer a minute old, it reads at once", async () => {
    vi.useFakeTimers();
    let now = 0;
    let shown = true;
    const handlers = new Map<string, () => void>();
    const events = { addEventListener: (t: string, h: () => void) => { handlers.set(t, h); }, removeEventListener: (t: string) => { handlers.delete(t); } };
    const read = vi.fn(async () => ({ maintenance_mode: false, maintenance_message: null, disabled_brokers: [] }) as SystemStatus);
    const poll = new StatusPoll(read, () => now, () => shown, events as unknown as Document);
    const stop = poll.subscribe(() => undefined);
    await vi.runAllTicks();
    expect(read).toHaveBeenCalledTimes(1);
    shown = false;
    now += 3 * STATUS_EVERY_MS;
    await vi.advanceTimersByTimeAsync(3 * STATUS_EVERY_MS);
    expect(read).toHaveBeenCalledTimes(1);                      // hidden: the timer fires, nothing is read
    shown = true;
    handlers.get("visibilitychange")?.();
    await vi.runAllTicks();
    expect(read).toHaveBeenCalledTimes(2);
    handlers.get("visibilitychange")?.();                       // fresh now: no second read
    await vi.runAllTicks();
    expect(read).toHaveBeenCalledTimes(2);
    stop();
    expect(handlers.has("visibilitychange")).toBe(false);
  });

  it("the v2 top bar reads it too (no second status poll)", () => {
    expect(topBarSource).toContain("useMarketStatus()");
    expect(topBarSource).not.toContain("api.systemStatus");
  });
});

// The plan's banned words (Charts page plan §3 UI2-4e, 1(k)): none in what the v2 chart surfaces show - src/chart and
// the Signals pages that hold a v2 chart.
const sources = import.meta.glob(["./*.ts", "./*.tsx", "!./*.test.ts", "!./*.test.tsx", "!./*.fixture.ts",
  "../signals/SignalsV2.tsx", "../signals/SignalPartsV2.tsx", "../utils/usePageVisible.ts"], { query: "?raw", import: "default", eager: true }) as Record<string, string>;

/** The source without comments (as the colour check does: a `//` right after ":" is a URL, not a comment). */
const noComments = (text: string) => text.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/(^|[^:"'`\\])\/\/[^\n]*/g, "$1");

/** What a person can read: string literals and JSX text. */
function shownText(source: string): string[] {
  const code = noComments(source).split("\n").filter((l) => !/^\s*(import|export \* from)\b/.test(l)).join("\n");
  const literals = [...code.matchAll(/"((?:[^"\\\n]|\\.)*)"|'((?:[^'\\\n]|\\.)*)'|`((?:[^`\\]|\\.)*)`/g)].map((m) => m[1] ?? m[2] ?? m[3] ?? "");
  const jsx = [...code.matchAll(/[>}]([^<>{}=;]*[A-Za-z][^<>{}=;]*)[<{]/g)].map((m) => m[1]);
  return [...literals, ...jsx];
}

describe("UI2-4e no developer wording on the v2 chart surfaces", () => {
  it("covers the v2 chart files", () => {
    expect(Object.keys(sources)).toEqual(expect.arrayContaining(["./ChartV2.tsx", "./ChartsPageV2.tsx", "./EmbeddedChartV2.tsx", "./LiveChip.tsx", "./liveChip.ts",
      "../signals/SignalsV2.tsx", "../signals/SignalPartsV2.tsx"]));
    // The scan sees shown words, and not comments or code names.
    expect(shownText(`<span>{broker && <span>candles via {broker}</span>}</span>`)).toContain("candles via ");
    expect(shownText(`const t = \`as of \${x}\`; // a debug note`)).toEqual(["as of ${x}"]);
    expect(shownText(`const flagOn = on("x");`)).toEqual(["x"]);
  });

  for (const [file, source] of Object.entries(sources)) {
    it(file, () => {
      const banned = /\b(via|debug|flag|fingerprint|token|503|as of)\b/i;
      expect(shownText(source).filter((t) => banned.test(t))).toEqual([]);
      expect(noComments(source)).not.toMatch(/String\(e\)/);
    });
  }
});
