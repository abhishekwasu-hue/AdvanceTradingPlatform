import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../auth/AuthContext";
import fixtureRaw from "../../e2e/fixtures/option_chain_charts.json?raw";
import { EXPIRIES, liveChain } from "./chain.fixture";
import {
  DEFAULT_RANGE, INTERVALS, MAX_PICKED, RANGES, axisScale, axisTick, chartsProvenance, chartsQuery, chartsStateMessage, fetchCharts, initialPicks,
  labelEvery, layout, markerAt, pickedSeries, pointNote, togglePick, xAt, xValuesOf, yAt,
  type ChainChart, type ChartsResponse,
} from "./charts";
import chartsSource from "./charts.ts?raw";
import ChainCharts, { ChartCard, ChartDeck, ChartSvg, ChartsPanel } from "./ChartsViewV2";
import viewSource from "./ChartsViewV2.tsx?raw";
import { OptionChainView, ViewToggle } from "./OptionChainPageV2";
import pageSource from "./OptionChainPageV2.tsx?raw";
import type { OptionChainData } from "./useOptionChain";

/**
 * OC5: the Option chain "Charts" view - the request, the strike range and the 5 / 10 / 15-minute views, the ten charts
 * with their captions, the plain states ("builds through the session", no broker), the phone deck and the "Chain |
 * Charts" toggle. The data is the backend's own output for a hand-made chain (e2e/fixtures/option_chain_charts.json,
 * made by e2e/fixtures/make_option_chain_charts.py; no real market data).
 */
const DATA = JSON.parse(fixtureRaw) as ChartsResponse;
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const noop = () => undefined;
const chart = (key: string): ChainChart => DATA.charts.find((c) => c.key === key)!;
const panel = (data: ChartsResponse | null, extra: { error?: string | null; loading?: boolean; range?: number; interval?: number } = {}) =>
  renderToStaticMarkup(<ChartsPanel data={data} error={extra.error ?? null} loading={extra.loading ?? false} range={extra.range ?? DEFAULT_RANGE}
    onRange={noop} interval={extra.interval ?? 5} onInterval={noop} />);
// The platform's advice words (backend app/portfolio/risk_text.py ADVICE_WORDS and app/screener/taxonomy.py SCREENER_WORDS).
const ADVICE = /\b(recommend\w*|advice|advise|should|must|buy|buying|sell|selling|reduce|cut your|add more|hedge now|guarantee\w*|best|safe bet|target\w*|top pick)\b/i;

afterEach(() => { vi.restoreAllMocks(); });

describe("OC5 charts request", () => {
  it("reads GET /api/option-chain/charts with the symbol, the expiry, the strike range and the interval - a read, never a write", async () => {
    expect(chartsQuery(" nifty ", null, 10, 5)).toBe("symbol=NIFTY&strikes=10&interval=5");
    expect(chartsQuery("NIFTY", "2026-11-03", 15, 10)).toBe("symbol=NIFTY&strikes=15&interval=10&expiry=2026-11-03");
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(DATA), { status: 200 }));
    const body = await fetchCharts("nifty", "2026-11-03", 10, 5);
    const [url, init] = spy.mock.calls[0];
    expect(String(url)).toMatch(/\/option-chain\/charts\?symbol=NIFTY&strikes=10&interval=5&expiry=2026-11-03$/);
    expect(init?.method ?? "GET").toBe("GET");
    expect(body.charts).toHaveLength(10);
  });

  it("offers ATM ± 5 / 10 / 15 / 20 (default 10) and 5 / 10 / 15-minute views", () => {
    expect(RANGES).toEqual([5, 10, 15, 20]);
    expect(DEFAULT_RANGE).toBe(10);
    expect(INTERVALS).toEqual([5, 10, 15]);
  });
});

describe("OC5 charts view", () => {
  it("shows the ten charts, each with its title and one caption, the facts and what the numbers stand on", () => {
    const html = panel(DATA);
    expect(html.match(/data-chart-card=/g)).toHaveLength(10);
    const words = text(html);
    for (const c of DATA.charts) { expect(words).toContain(c.title); expect(words).toContain(c.caption); }
    for (const s of DATA.facts!.sentences) expect(words).toContain(s);
    expect(words).toContain("Data as of 10:30 IST · 8 stored readings since 09:50 IST");
    expect(words).toContain("ATM ± 10");
    expect(html).toMatch(/aria-pressed="true"[^>]*>5 min/);
    expect(html.match(/<svg/g)?.length).toBeGreaterThanOrEqual(10);
  });

  it("a chart without two readings yet says it builds through the session - never a number", () => {
    const building: ChartsResponse = {
      ...DATA, session: { readings: 0, since: null, note: "Builds through the session." },
      charts: DATA.charts.map((c) => c.x_kind === "time"
        ? { ...c, state: "building", message: "Builds through the session: a reading of the chain is stored every 5 minutes while the market is open, so this chart fills in as the day goes on.", series: [] }
        : c),
    };
    const html = panel(building);
    expect(html.match(/data-chart-building/g)).toHaveLength(DATA.charts.filter((c) => c.x_kind === "time").length);
    expect(text(html)).toContain("so this chart fills in as the day goes on");
    expect(text(html)).not.toMatch(/sample/i);
  });

  it("no broker, no chain and a failed read are plain sentences; before the first read a loading state", () => {
    const noBroker = { ...DATA, state: "no_broker" as const, message: "Connect your broker and log in to it today to see these analyses. They use your own broker's option chain.", charts: [], facts: null };
    expect(text(panel(noBroker))).toContain("Connect your broker");
    expect(panel(noBroker)).not.toContain("data-chart-card");
    expect(chartsStateMessage({ ...noBroker, message: null })).toBe("Connect your broker and log in to it today to see these charts.");
    expect(chartsStateMessage(DATA)).toBeNull();
    expect(panel(null, { loading: true })).toContain('aria-busy="true"');
    expect(text(panel(null, { error: "The charts could not be read. We will try again with the next refresh." }))).toContain("could not be read");
    // The mounted component reads after mount only (a server render never reads).
    const load = vi.fn(() => Promise.resolve(DATA));
    expect(renderToStaticMarkup(<ChainCharts symbol="NIFTY" expiry="2026-11-03" load={load} />)).toContain('aria-busy="true"');
    expect(load).not.toHaveBeenCalled();
  });

  it("describes only: no advice and no buy / sell words anywhere", () => {
    expect(text(panel(DATA))).not.toMatch(ADVICE);
    for (const c of DATA.charts) for (const t of [c.title, c.caption, c.message ?? "", ...c.series.map((s) => s.label)]) expect(t).not.toMatch(ADVICE);
  });

  it("a phone shows one chart at a time in a sideways deck with arrows; md and up a grid", () => {
    const html = renderToStaticMarkup(<ChartDeck charts={DATA.charts} />);
    expect(html).toMatch(/data-charts-deck[^>]*class="[^"]*snap-x snap-mandatory[^"]*overflow-x-auto[^"]*md:grid[^"]*md:grid-cols-2/);
    expect(html).toMatch(/<article class="[^"]*w-full shrink-0 snap-start[^"]*"[^>]*data-chart-card="oi_by_strike"/);
    expect(html).toContain('aria-label="Previous chart"');
    expect(html).toContain('aria-label="Next chart"');
    expect(text(html)).toContain("OI by strike · 1 of 10");
    expect(html).toMatch(/class="[^"]*md:hidden"[^>]*data-charts-pager/);
  });

  it("the multi-strike chart opens on ATM and one strike either side and lets the trader pick up to three", () => {
    const multi = chart("multi_strike_oi");
    expect(initialPicks(multi)).toEqual([24400, 24500, 24600]);
    expect(togglePick([24500], 24600)).toEqual([24500, 24600]);
    expect(togglePick([24400, 24500, 24600], 24700)).toEqual([24400, 24500, 24600]);     // at most MAX_PICKED
    expect(MAX_PICKED).toBe(3);
    expect(togglePick([24400, 24500], 24400)).toEqual([24500]);
    expect(togglePick([24500], 24500)).toEqual([24500]);                                 // never none
    const shown = pickedSeries(multi, [24400, 24500]);
    expect(shown.map((s) => s.key)).toEqual(["call_24400", "put_24400", "call_24500", "put_24500"]);
    expect(shown.map((s) => s.tone)).toEqual(["brand", "brand", "warn", "warn"]);
    const html = renderToStaticMarkup(<ChartCard chart={multi} />);
    expect(html.match(/aria-pressed="true"/g)).toHaveLength(3);
    expect(html).toContain("stroke-dasharray");
  });

  it("OI by strike draws calls and puts as bars with today's change in each bar's note and spot as a dashed line", () => {
    const html = renderToStaticMarkup(<ChartSvg chart={chart("oi_by_strike")} />);
    expect(html.match(/<rect/g)?.length).toBe(42);                                       // 21 strikes x calls and puts
    expect(html).toMatch(/<title>Call OI at 24500: 4 L \(today \+[\d,.]+( L)?\)<\/title>/);     // M61 OI-1
    expect(html).toContain("Dashed line: spot");
    expect(html).toContain("fill-down/70");
    expect(html).toContain("fill-up/70");
  });

  it("a second scale on the right for spot and for the future's OI", () => {
    const html = renderToStaticMarkup(<ChartSvg chart={chart("futures")} />);
    expect(text(html)).toContain("Futures OI (right scale)");
    expect(html).toContain('data-series="future_oi"');
    expect(text(renderToStaticMarkup(<ChartSvg chart={chart("cumulative_oi")} />))).toContain("Spot (right scale)");
  });
});

describe("OC5 chart geometry", () => {
  it("bars hold zero, lines are padded, a right axis takes room, labels thin out on narrow charts", () => {
    expect(axisScale([{ key: "a", label: "a", tone: "up", axis: "left", dashed: false, points: [{ x: 1, y: 5 }, { x: 2, y: 10 }] }], "bar")).toEqual({ min: 0, max: 10 });
    const line = axisScale([{ key: "a", label: "a", tone: "up", axis: "left", dashed: false, points: [{ x: 1, y: 100 }, { x: 2, y: 200 }] }], "line")!;
    expect(line.min).toBeCloseTo(95);
    expect(line.max).toBeCloseTo(205);
    expect(axisScale([{ key: "a", label: "a", tone: "up", axis: "left", dashed: false, points: [{ x: 1, y: null }] }], "line")).toBeNull();
    const l = layout(400, true);
    expect(l.right).toBeGreaterThan(layout(400, false).right);
    expect(yAt(10, { min: 0, max: 10 }, l)).toBe(l.top);
    expect(xAt(0, 2, l)).toBeLessThan(xAt(1, 2, l));
    expect(labelEvery(21, layout(358, false))).toBeGreaterThan(labelEvery(21, layout(1200, false)));
    expect(markerAt(24480, [24400, 24500], l)).toBeCloseTo(xAt(0, 2, l) + 0.8 * (xAt(1, 2, l) - xAt(0, 2, l)));
    expect(markerAt(1, [24400, 24500], l)).toBeNull();
    expect(xValuesOf(chart("pcr_session").series)).toHaveLength(9);                       // 8 stored readings + the read now
    expect(pointNote({ key: "a", label: "Put OI", tone: "up", axis: "left", dashed: false, points: [] }, { x: 24500, y: 390000, d: -5000 }, "strike"))
      .toBe("Put OI at 24500: 3.9 L (today −5,000)");
  });

  it("a narrow price or OI range keeps its ticks apart; a wide one stays compact", () => {
    const price = { min: 24420, max: 24520 };
    expect([price.max, (price.max + price.min) / 2, price.min].map((v) => axisTick(v, price))).toEqual(["24,520", "24,470", "24,420"]);
    expect(axisTick(11_800_000, { min: 11_500_000, max: 11_800_000 })).toBe("1.18 Cr");
    expect(axisTick(600_000, { min: 0, max: 600_000 })).toBe("6 L");
    expect(axisTick(0.91, { min: 0.89, max: 0.91 })).toBe("0.91");
  });

  it("provenance names the stored readings", () => {
    expect(chartsProvenance(DATA)).toBe("Data as of 10:30 IST · 8 stored readings since 09:50 IST");
    expect(chartsProvenance({ ...DATA, session: { readings: 0, since: null, note: null } })).toBe("Data as of 10:30 IST");
  });
});

describe("OC5 Chain | Charts toggle on the page", () => {
  const chainData = (over: Partial<OptionChainData> = {}): OptionChainData =>
    ({ expiries: EXPIRIES, expiry: "2026-11-03", setExpiry: noop, live: liveChain(), loading: false, problem: null, ...over });
  const page = (over: Partial<OptionChainData> = {}) => renderToStaticMarkup(
    <MemoryRouter><AuthProvider>
      <OptionChainView symbol="NIFTY" onSymbol={noop} chain={chainData(over)} marketOpen prefs={{ greeks: false, quotes: false, more: true }} onPrefs={noop} />
    </AuthProvider></MemoryRouter>);

  it("opens on the chain with the toggle over the strike column, so the chain does not move", () => {
    const html = page();
    expect(html).toContain('data-block="chain"');
    expect(html).not.toContain('data-block="charts"');
    expect(html).toMatch(/data-view-toggle[^>]*class="[^"]*absolute[^"]*left-1\/2/);
    expect(html.match(/aria-pressed="true"[^>]*>(<svg[\s\S]*?<\/svg>)?Chain</g)).toHaveLength(1);
  });

  it("without a chain on screen the toggle sits in the flow above the plain sentence", () => {
    const html = page({ live: null, problem: "failed" });
    expect(html).toContain("data-view-toggle");
    expect(html).not.toMatch(/data-view-toggle[^>]*class="[^"]*absolute/);
  });

  it("the toggle's three targets (Chain, Charts, OI - UI-28 U6.1) are 44 px on a phone, and it is a group of pressed buttons", () => {
    const html = renderToStaticMarkup(<ViewToggle view="charts" onView={noop} />);
    expect(html).toContain('role="group"');
    expect(html.match(/h-11/g)).toHaveLength(3);
    expect(html).toMatch(/aria-pressed="true"[^>]*>[\s\S]*Charts/);
  });

  it("names the v2 files so the colour lint covers them; tokens only; no fixed px text sizes", () => {
    expect(pageSource).toContain('import ChainCharts from "./ChartsViewV2"');
    for (const src of [viewSource, chartsSource]) {
      expect(src).not.toMatch(/text-\[\d+px\]/);
      expect(src).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
      expect(src).not.toMatch(/\b(sample|lookback|demo data)\b/i);
    }
  });
});
