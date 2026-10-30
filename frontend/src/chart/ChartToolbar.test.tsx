import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { ChartToolbar, type ChartToolbarProps } from "./ChartToolbar";
import { DEFAULT_CHART_PREFS } from "./useChartPrefs";
import { menuGroups, onCount, type IndicatorState } from "./indicatorsModel";

/** CT-1 C1 + L1: the one chart toolbar (full / compact; the phone's one line with More) and its hosts. */

const noop = () => undefined;
const STATE: IndicatorState = { panes: { volume: true, rsi: true }, overlays: ["vwap"], layers: [] };
const base: ChartToolbarProps = {
  mode: "full", label: "Chart controls",
  symbol: { value: "NIFTY 50", exchange: "NSE", onPick: noop, storageKey: null },
  timeframe: { offered: ["1min", "5min", "15min", "60min", "day"], value: "5min", onChange: noop, favourites: ["5min", "15min", "60min"], onFavourites: noop },
  chartType: { id: "t", value: "candles", onChange: noop },
  indicators: { state: STATE, on: () => false, onToggle: noop },
  settings: { prefs: DEFAULT_CHART_PREFS, onChange: noop },
  actions: [{ id: "read", label: "Read this chart", icon: <i />, onClick: noop }],
  endActions: [{ id: "fullscreen", label: "Full screen", icon: <i />, onClick: noop }, { id: "multi", label: "Multi-chart", icon: <i />, onClick: noop }],
};
const render = (p: Partial<ChartToolbarProps>) => renderToStaticMarkup(<MemoryRouter><ChartToolbar {...base} {...p} /></MemoryRouter>);
const src = (f: string) => readFileSync(new URL(f, import.meta.url), "utf8");

describe("CT-1 C1: one ChartToolbar", () => {
  it("full mode from 768 px: symbol, timeframe, chart type, Indicators, the actions and the settings in one bar", () => {
    const html = render({ phone: false });
    expect(html).toContain('data-chart-toolbar="full"');
    expect(html).toContain('>Symbol</label>');
    expect(html).toContain('aria-label="Chart type"');
    expect(html).toMatch(/Indicators<span[^>]*>· 3</);                      // the word and its count
    expect(html).toContain('aria-label="Read this chart"');
    expect(html).toContain('aria-label="Chart settings"');
    expect(html).not.toContain("More chart controls");
    // The settings sit between the actions and the end actions (the Charts page's order before CT-1).
    expect(html.indexOf("Read this chart")).toBeLessThan(html.indexOf("Chart settings"));
    expect(html.indexOf("Chart settings")).toBeLessThan(html.indexOf('aria-label="Full screen"'));
  });

  it("L1 phone: one line - Symbol · Timeframe · Indicators · Draw · More; the rest only under More", () => {
    const html = render({ phone: true, drawing: { tools: null, symbol: "NIFTY 50", hidden: false, onHidden: noop } });
    expect(html).toContain('data-chart-toolbar="phone"');
    const order = ['>Symbol</label>', "data-timeframe-dropdown", 'aria-label="Indicators · 3 on"', 'aria-label="Drawing tools"', 'aria-label="More chart controls"']
      .map((s) => html.indexOf(s));
    expect(order.every((at) => at >= 0)).toBe(true);
    expect([...order].sort((a, b) => a - b)).toEqual(order);
    // More is closed: the chart type, undo / redo, the actions and the settings are not in the bar.
    for (const gone of ['aria-label="Chart type"', "Undo drawing", "Read this chart", "Full screen", "Multi-chart", "Chart settings"]) expect(html).not.toContain(gone);
    // The timeframe is the dropdown alone (no starred buttons) so the line fits 390 px.
    expect(html).not.toMatch(/aria-pressed="(?:true|false)"[^>]*>15m</);
  });

  it("compact mode: no More, Indicators limited to the groups the host allows, the host's link last", () => {
    const html = render({
      mode: "compact", symbol: undefined, chartType: undefined, actions: [],
      indicators: { state: STATE, on: () => false, onToggle: noop, groups: ["popular"] },
      endActions: [{ id: "open", label: "Open in Chart tab", icon: <i />, to: "/charts/NIFTY%2050" }],
    });
    expect(html).toContain('data-chart-toolbar="compact"');
    expect(html).not.toContain("More chart controls");
    expect(html).not.toContain('>Symbol</label>');
    expect(html).toContain('aria-label="Indicators · 1 on"');                 // the panes are not offered, so not counted
    expect(html).toMatch(/href="\/charts\/NIFTY%2050"[^>]*aria-label="Open in Chart tab"|aria-label="Open in Chart tab"[^>]*href="\/charts\/NIFTY%2050"/);
  });

  it("the Indicators groups: an embedded chart offers the price-pane lines only", () => {
    expect(menuGroups("", () => true, ["popular"]).map((g) => g.group)).toEqual(["popular"]);
    expect(menuGroups("", () => true).map((g) => g.group)).toEqual(["chart", "popular", "layers"]);
    expect(onCount(STATE, () => false, ["popular"])).toBe(1);
    expect(onCount(STATE, () => false)).toBe(3);
  });

  it("no second toolbar implementation: the Charts page, the embedded chart and the Multi-chart cells use ChartToolbar", () => {
    for (const f of ["./ChartsPageV2.tsx", "./EmbeddedChartV2.tsx", "./MultiChartV2.tsx"]) {
      const s = src(f);
      expect(s).toMatch(/<ChartToolbar mode="(full|compact)"/);
      expect(s).not.toMatch(/<TimeframePicker |<IndicatorsMenu |<SymbolPicker /);
    }
    expect(src("./ChartsPageV2.tsx")).toContain('<ChartToolbar mode="full"');
    expect(src("./EmbeddedChartV2.tsx")).toContain('<ChartToolbar mode="compact"');
    expect(src("./MultiChartV2.tsx")).toContain('<ChartToolbar mode="compact"');
    // The embedded chart keeps its rule: no lower panes.
    expect(src("./EmbeddedChartV2.tsx")).toMatch(/NO_PANES = \{ volume: false, rsi: false \}/);
  });

  it("L1: a phone has one tab row - the Multi-chart sub-tabs only from 768 px, Multi-chart a toolbar item on a phone", () => {
    const page = src("./ChartsPageV2.tsx");
    expect(page).toContain("{multi && !fs.on && !hubView && !phone && (");
    expect(page).toMatch(/multi && phone && !fs\.on \? \[\{ id: "multi", label: "Multi-chart"/);
    expect(page).toContain('aria-label="Back to one chart"');
  });
});
