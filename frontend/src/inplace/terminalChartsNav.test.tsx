import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it } from "vitest";
import { BROKER_PATH } from "../broker/paths";
import { ChartToolbar } from "../chart/ChartToolbar";
import { chartWindowUrl } from "../components/chartHelpers";
import { pathFor } from "../routes";
import { chartTabPath } from "../shell/shellRoutes";
import { createInPlaceApi, handoffs, openPanels, pushedIn, type NavigateFn, type RouterLocation } from "./api";
import { DEFAULT_REGISTRY } from "./panels";

/**
 * M75 NAV-3 (docs/design/M75_NO_NAVIGATION.md, the Terminal / charts rows): the 18 Terminal / chart click-navigations
 * open in place through the host. For each site, the destination it opens (the same expression the site uses) is
 * opened on the tab's own address: the PATH stays the same, the panel registered for it is on top, nothing extra is
 * handed over (none of these buttons carried state), and closing goes back to the exact entry before. The classic
 * chart window's "Open in workspace" lays the workspace over the window by state (no address change). Clicking each
 * button in a real browser (phone 390 and desktop 1440) is e2e/terminal-charts-inplace-v2.e2e.ts.
 */

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..");
const read = (f: string) => readFileSync(join(SRC, f), "utf8");

/** A made-up browser history driving the same API the provider uses (as in copilotNav.test.tsx). */
function history(start: string) {
  const at = (path: string, st: unknown): RouterLocation => {
    const u = new URL(path, "http://app.invalid");
    return { pathname: u.pathname, search: u.search, hash: u.hash, state: st };
  };
  const entries: RouterLocation[] = [at(start, null)];
  let index = 0;
  const navigate = ((to: number | string | { pathname: string; search: string; hash: string }, opts?: { replace?: boolean; state?: unknown }) => {
    if (typeof to === "number") { index = Math.max(0, Math.min(entries.length - 1, index + to)); return; }
    const next = typeof to === "string" ? at(to, opts?.state ?? null) : { ...to, state: opts?.state ?? null };
    if (opts?.replace) entries[index] = next;
    else { entries.splice(index + 1); entries.push(next); index += 1; }
  }) as NavigateFn;
  return { navigate, location: () => entries[index], url: () => { const l = entries[index]; return `${l.pathname}${l.search}${l.hash}`; } };
}

const FLAGS: Record<string, boolean> = { chart_analyze: true };
function inPlace(h: ReturnType<typeof history>, { enabled = true, newTabs = [] as string[] } = {}) {
  return createInPlaceApi({ enabled, on: (f) => FLAGS[f] === true, registry: DEFAULT_REGISTRY, location: h.location, navigate: h.navigate,
    newTab: (p) => newTabs.push(p) });
}

afterEach(() => { delete (globalThis as { window?: unknown }).window; });

interface Site { site: string; from: string; to: () => string; kind: string; params?: Record<string, string> }

const marketsList = (key: string) => `/markets?${new URLSearchParams({ view: "sectors", period: "day", list: key }).toString()}`;

/** The 17 sites of the design table that open a panel (the 18th, the classic chart window's, is below), each with
 * the destination its code builds. */
const SITES: Site[] = [
  { site: "chart/ChartToolbar.tsx:96 (icon action link)", from: "/terminal", to: () => chartTabPath("NIFTY 50", "NSE", "15min"), kind: "chart", params: { symbol: "NIFTY 50", tf: "15min" } },
  { site: "chart/ChartToolbar.tsx:105 (More row link)", from: "/charts/NIFTY", to: () => pathFor("chart-analyze"), kind: "analyze" },
  { site: "chart/ChartsPageV2.tsx:213 Analyze a chart", from: "/charts/NIFTY", to: () => pathFor("chart-analyze"), kind: "analyze" },
  { site: "chart/EmbeddedChartV2.tsx:93 Open in Chart tab", from: "/terminal", to: () => chartTabPath("BANKNIFTY", "NSE", "5min"), kind: "chart", params: { symbol: "BANKNIFTY", tf: "5min" } },
  { site: "components/ProChart.tsx:570 New tab", from: "/positions", to: () => chartWindowUrl("RELIANCE", "15min", "NSE", "upstox"), kind: "chart", params: { symbol: "RELIANCE", tf: "15min", exchange: "NSE" } },
  { site: "markets/MarketsPageV2.tsx:111 mover", from: "/markets", to: () => chartTabPath("THETA", "NSE"), kind: "chart", params: { symbol: "THETA" } },
  { site: "markets/MarketsPageV2.tsx:169 index tile", from: "/markets", to: () => chartTabPath("NIFTY 50", "NSE"), kind: "chart", params: { symbol: "NIFTY 50" } },
  { site: "markets/MarketsPageV2.tsx:193 index row", from: "/markets", to: () => chartTabPath("SENSEX", "BSE"), kind: "chart", params: { symbol: "SENSEX", exchange: "BSE" } },
  { site: "markets/MarketsPageV2.tsx:221 leader", from: "/markets", to: () => chartTabPath("NIFTY BANK", "NSE"), kind: "chart", params: { symbol: "NIFTY BANK" } },
  { site: "markets/MarketsPageV2.tsx:272 member", from: "/markets", to: () => chartTabPath("THETA", "NSE"), kind: "chart", params: { symbol: "THETA" } },
  { site: "markets/MarketsPageV2.tsx:317 Open chart", from: "/markets", to: () => chartTabPath("NIFTY IT"), kind: "chart", params: { symbol: "NIFTY IT" } },
  { site: "screener/EvidenceChart.tsx:180 Open in Charts", from: "/screener", to: () => chartTabPath("BRK", "NSE", "day"), kind: "chart", params: { symbol: "BRK", tf: "day" } },
  { site: "screener/EvidenceChart.tsx:181 (outside a router: gone)", from: "/screener", to: () => chartTabPath("ABC", "BSE", "15min"), kind: "chart", params: { symbol: "ABC", tf: "15min", exchange: "BSE" } },
  { site: "terminal/PanelsV2.tsx:139 Connect your broker", from: "/terminal", to: () => BROKER_PATH, kind: "broker" },
  { site: "terminal/PanelsV2.tsx:481 Open Signals", from: "/terminal", to: () => "/signals", kind: "signals" },
  { site: "terminal/PanelsV2.tsx:482 Open Trading bot", from: "/terminal", to: () => "/deployments", kind: "bots" },
  { site: "watchlist/WatchlistPageV2.tsx:44 row", from: "/watchlist", to: () => chartTabPath("SENSEX", "BSE"), kind: "chart", params: { symbol: "SENSEX", exchange: "BSE" } },
];

describe("NAV-3: every Terminal / chart button opens in place (the path stays)", () => {
  it("covers the 17 panel sites of the design table (plus the chart window's workspace below: 18)", () => {
    expect(SITES).toHaveLength(17);
  });

  for (const s of SITES) {
    it(s.site, () => {
      (globalThis as { window?: unknown }).window = { location: { pathname: s.from } };   // chartWindowUrl reads it
      const h = history(s.from);
      const before = h.location();
      const api = inPlace(h);
      const to = s.to();
      expect(api.handles(to)).toBe(true);
      api.open(to);
      expect(h.location().pathname).toBe(s.from);                           // the URL path is unchanged
      const open = openPanels(h.location().search, DEFAULT_REGISTRY, (f) => FLAGS[f] === true);
      expect(open.map((p) => p.spec)).toEqual([{ kind: s.kind, params: s.params ?? {} }]);
      expect(pushedIn(h.location().state)).toBe(1);                          // one history entry: Back closes it
      expect(handoffs(h.location().state)).toEqual([]);                      // nothing was handed over before, nothing now
      api.close();
      expect(h.location()).toBe(before);                                     // the exact entry before (the tab's state)
    });
  }

  it("a chart opened on top of the Terminal keeps the tab's own search (its symbol) and closes back to it", () => {
    const h = history("/terminal?symbol=BANKNIFTY");
    const before = h.location();
    const api = inPlace(h);
    api.open("/signals");
    expect(h.url()).toBe("/terminal?symbol=BANKNIFTY&panel=signals");
    api.close();
    expect(h.location()).toBe(before);
  });
});

describe("NAV-3: as before where no panel shows it", () => {
  it("a sector's stocks stay on the Markets tab's own address (a search change, Back returns to the board)", () => {
    const h = history("/markets");
    const api = inPlace(h);
    expect(api.handles(marketsList("sector-it"))).toBe(false);
    api.open(marketsList("sector-it"));
    expect(h.location().pathname).toBe("/markets");
  });

  it("with ui_v2 off ProChart's chart opens a new browser tab and the Terminal links navigate, as before", () => {
    (globalThis as { window?: unknown }).window = { location: { pathname: "/positions" } };
    const h = history("/positions");
    const newTabs: string[] = [];
    const api = inPlace(h, { enabled: false, newTabs });
    expect(api.handles(chartWindowUrl("RELIANCE"))).toBe(false);
    api.open("/deployments");
    expect(h.url()).toBe("/deployments");
  });

  it("the Trading bot panel's full route resolves back to the same panel (its href is the real address)", () => {
    expect(DEFAULT_REGISTRY.resolve(DEFAULT_REGISTRY.get("bots")!.href({}))).toEqual({ kind: "bots", params: {} });
    expect(DEFAULT_REGISTRY.get("bots")!.presentation).toBe("side-wide");   // the page's own form needs the width
  });
});

describe("NAV-3: the toolbar link keeps the real address", () => {
  it("an action with `to` is a link to the full route (long-press / new tab still give the page)", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter initialEntries={["/terminal"]}>
        <ChartToolbar mode="compact" label="Chart controls" timeframe={{ offered: ["5min"], value: "5min", onChange: () => undefined, favourites: [], onFavourites: () => undefined }}
          endActions={[{ id: "open", label: "Open a larger chart", icon: <i />, to: "/charts/NIFTY%2050" }]} phone={false} />
      </MemoryRouter>,
    );
    expect(html).toMatch(/<a [^>]*href="\/charts\/NIFTY%2050"[^>]*data-action="open"|<a [^>]*data-action="open"[^>]*href="\/charts\/NIFTY%2050"/);
  });
});

describe("NAV-3: the sites use the host (source)", () => {
  const uses: [string, RegExp][] = [
    ["chart/ChartToolbar.tsx", /if \(a\.to\) return <InPlaceLink to=\{a\.to\} \{\.\.\.common\}>/],
    ["chart/ChartToolbar.tsx", /if \(a\.to\) return <InPlaceLink to=\{a\.to\} className=\{moreRow\}/],
    ["chart/ChartsPageV2.tsx", /\{ id: "analyze", label: "Analyze a chart", [^\n]*to: pathFor\("chart-analyze"\) \}/],
    ["chart/EmbeddedChartV2.tsx", /\{ id: "open", label: openLabel, [^\n]*to: openTo \}/],
    ["components/ProChart.tsx", /<InPlaceLink to=\{url\} target=\{here \? undefined : "_blank"\}/],
    ["markets/MarketsPageV2.tsx", /<InPlaceLink to=\{chartTabPath\(m\.symbol, m\.exchange\)\} className="inline-flex/],
    ["markets/MarketsPageV2.tsx", /<InPlaceLink to=\{to\} data-tile=\{g\.name\}/],
    ["markets/MarketsPageV2.tsx", /\{to \? <InPlaceLink to=\{to\} className=\{cx\(cls, "hover:bg-surface-2/],
    ["markets/MarketsPageV2.tsx", /\{to \? <InPlaceLink to=\{to\} className="inline-flex/],
    ["markets/MarketsPageV2.tsx", /<InPlaceLink to=\{chartTabPath\(m\.symbol, m\.exchange\)\}\n\s+className="grid/],
    ["markets/MarketsPageV2.tsx", /<InPlaceLink to=\{chartTabPath\(d\.symbol\)\} className=\{LINK_SECONDARY\}>/],
    ["pages/ChartWindow.tsx", /onClick=\{\(\) => setWorkspace\(true\)\} data-open-workspace/],
    ["pages/ChartWindow.tsx", /onClick=\{\(\) => setWorkspace\(false\)\} data-close-workspace/],
    ["pages/ChartWindow.tsx", /<div className="min-h-screen bg-surface p-4" hidden=\{workspace\}>/],
    ["screener/EvidenceChart.tsx", /return <InPlaceLink to=\{to\} className=\{cls\} data-testid="open-in-charts"/],
    ["terminal/PanelsV2.tsx", /<InPlaceLink to=\{BROKER_PATH\} className=\{LINK\}>Connect your broker<\/InPlaceLink>/],
    ["terminal/PanelsV2.tsx", /<InPlaceLink to="\/signals" className=\{LINK\}>Open Signals<\/InPlaceLink>/],
    ["terminal/PanelsV2.tsx", /<InPlaceLink to="\/deployments" className=\{LINK\}>Open Trading bot<\/InPlaceLink>/],
    ["watchlist/WatchlistPageV2.tsx", /const open = \(item: WatchItem\) => inPlace\.open\(chartTabPath\(item\.symbol, item\.exchange\)\);/],
  ];
  for (const [file, re] of uses) it(`${file}: ${re.source.slice(0, 60)}`, () => expect(read(file)).toMatch(re));

  it("the chart window's workspace no longer reloads the window (no workspace address written by a click)", () => {
    expect(read("pages/ChartWindow.tsx")).not.toMatch(/<a href=\{`\$\{window\.location\.pathname\}\?\$\{new URLSearchParams\(\{ workspace/);
  });
});
