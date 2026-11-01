import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { CardV2 } from "../dashboard/CardV2";
import { chartLink } from "../fno/ChainActionSheetV2";
import { adjustHref } from "../fno/contracts";
import { basketHref } from "../fno/hubModel";
import { setupChainHref } from "../fno/insights";
import { blockedBy } from "../fno/ticketModel";
import { BROKER_PAGE_PATH } from "../pages/OptionChainPage";
import { SETTINGS_PATH } from "../settings/paths";
import { chartTabPath, chartsPath } from "../shell/shellRoutes";
import { createInPlaceApi, handoffs, openPanels, pushedIn, type NavigateFn, type RouterLocation } from "./api";
import { optionChainPanelPath } from "./fnoPaths";
import { InPlaceLink } from "./InPlaceLink";
import { DEFAULT_REGISTRY } from "./panels";

/**
 * M75 NAV-4 (docs/design/M75_NO_NAVIGATION.md, the Option chain rows): the 14 option-chain click-navigations open in
 * place through the host. For each site, the destination its code builds is opened on the tab the site lives on: the
 * PATH stays the same, the panel registered for it is on top with the same parameters, nothing extra is handed over,
 * and closing goes back to the exact entry before. The two classic-shell contract charts (`/?contract=`) have no panel:
 * they keep the new browser tab they opened before (the classic shell has no host).
 */

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..");
const read = (f: string) => readFileSync(join(SRC, f), "utf8");

/** A made-up browser history driving the same API the provider uses (as in terminalChartsNav.test.tsx). */
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

const ALL_ON: Record<string, boolean> = { options_builder: true };
function inPlace(h: ReturnType<typeof history>, { enabled = true, flags = ALL_ON } = {}) {
  return createInPlaceApi({ enabled, on: (f) => flags[f] === true, registry: DEFAULT_REGISTRY, location: h.location, navigate: h.navigate });
}

interface Site { site: string; from: string; to: () => string; kind: string; params?: Record<string, string> }

const TARGET = { underlying: "NIFTY", expiry: "2026-11-26", strike: 24000, right: "CE" as const, spotSymbol: "NIFTY 50", leg: { ltp: 100, iv: 12 } };
const CONTRACT = { tradingsymbol: "NIFTY26NOV24000CE", exchange: "NFO", label: "NIFTY 26 Nov 24000 CE" };
const OPTION_FACTS = { known: true, underlying: "NIFTY", right: "CE", expiry: "2026-11-26", spot_symbol: "NIFTY 50", spot_exchange: "NSE", expired: false, label: "x", lot_size: 75 };
const FUTURE_FACTS = { ...OPTION_FACTS, right: "FUT" };
const OWN_LIMIT = { can_place: false, errors: [], refusals: ["This is above your limit of 2 lots per order."] };
const SIGNAL = { id: "action:12", symbol: "NIFTY 50", direction: "LONG" as const };

/** The 12 sites of the design table that open a panel (the classic page's two contract charts are below), each with
 * the destination its code builds. */
const SITES: Site[] = [
  { site: "fno/BasketPanelV2.tsx:304 Open in Options Builder", from: "/option-chain", to: () => "/option-chain/plan", kind: "options-plan" },
  { site: "fno/BasketPanelV2.tsx:775 Change limit", from: "/terminal", to: () => blockedBy(OWN_LIMIT)!.link!, kind: "risk-limits" },
  { site: "fno/ChainActionSheetV2.tsx:244 Chart of this strike (contract)", from: "/option-chain",
    to: () => chartLink(TARGET as never, CONTRACT as never), kind: "chart", params: { symbol: "NIFTY26NOV24000CE", exchange: "NFO" } },
  { site: "fno/CockpitDeskV2.tsx:118 Open the chart", from: "/positions", to: () => chartsPath("NIFTY 50"), kind: "chart", params: { symbol: "NIFTY 50" } },
  { site: "fno/CompareV2.tsx:327 Open the chain (an option)", from: "/positions", to: () => adjustHref(OPTION_FACTS as never)!, kind: "option-chain",
    params: { underlying: "NIFTY", expiry: "2026-11-26" } },
  { site: "fno/FuturesChainV2.tsx:114 Chart", from: "/charts/NIFTY%2050", to: () => chartTabPath("NIFTY26NOVFUT", "NFO", "15min"), kind: "chart",
    params: { symbol: "NIFTY26NOVFUT", tf: "15min", exchange: "NFO" } },
  { site: "fno/FuturesChainV2.tsx:276 basket badge", from: "/charts/NIFTY%2050", to: () => basketHref("NIFTY", "15min"), kind: "option-chain",
    params: { underlying: "NIFTY", tf: "15min", basket: "open" } },
  { site: "fno/InsightsV2.tsx:188 Your strikes on the chain", from: "/signals", to: () => setupChainHref(SIGNAL)!, kind: "option-chain",
    params: { underlying: "NIFTY", signal: "action:12" } },
  { site: "fno/InsightsV2.tsx:192 Trade this setup", from: "/signals", to: () => setupChainHref(SIGNAL, true)!, kind: "option-chain",
    params: { underlying: "NIFTY", signal: "action:12", trade: "1" } },
  { site: "fno/OrderTicketV2.tsx:493 Change limit", from: "/option-chain", to: () => blockedBy(OWN_LIMIT)!.link!, kind: "risk-limits" },
  { site: "optionChain/AskAiV2.tsx:187 Add your AI key", from: "/option-chain", to: () => SETTINGS_PATH.ai, kind: "ai-key" },
  { site: "pages/OptionChainPage.tsx:103 Broker page", from: "/option-chain", to: () => BROKER_PAGE_PATH, kind: "broker" },
];

describe("NAV-4: every option-chain button opens in place (the path stays)", () => {
  it("covers the 12 panel sites of the design table (plus the classic page's two contract charts: 14)", () => {
    expect(SITES).toHaveLength(12);
  });

  for (const s of SITES) {
    it(s.site, () => {
      const h = history(s.from);
      const before = h.location();
      const api = inPlace(h);
      const to = s.to();
      expect(api.handles(to)).toBe(true);
      api.open(to);
      expect(h.location().pathname).toBe(new URL(s.from, "http://app.invalid").pathname);   // the URL path is unchanged
      const open = openPanels(h.location().search, DEFAULT_REGISTRY, (f) => ALL_ON[f] === true);
      expect(open.map((p) => p.spec)).toEqual([{ kind: s.kind, params: s.params ?? {} }]);
      expect(pushedIn(h.location().state)).toBe(1);                          // one history entry: Back closes it
      expect(handoffs(h.location().state)).toEqual([]);                      // nothing was handed over
      api.close();
      expect(h.location()).toBe(before);                                     // the exact entry before (the tab's state)
    });
  }

  it("a future position's Adjust opens the symbol hub's Futures tab in place (not a bare chart)", () => {
    const h = history("/positions");
    const api = inPlace(h);
    api.open(adjustHref(FUTURE_FACTS as never)!);
    expect(h.location().pathname).toBe("/positions");
    expect(openPanels(h.location().search, DEFAULT_REGISTRY, (f) => ALL_ON[f] === true).map((p) => p.spec))
      .toEqual([{ kind: "futures", params: { symbol: "NIFTY 50", expiry: "2026-11-26" } }]);
  });

  it("a chart without the hub is still the chart panel (the Futures panel answers only for hub=futures)", () => {
    expect(DEFAULT_REGISTRY.resolve("/charts/NIFTY%2050?tf=15min")).toEqual({ kind: "chart", params: { symbol: "NIFTY 50", tf: "15min" } });
    expect(DEFAULT_REGISTRY.resolve("/charts/NIFTY%2050?hub=overview")).toEqual({ kind: "chart", params: { symbol: "NIFTY 50" } });
  });

  it("the chain panel on top of the Positions tab keeps the tab's own search and closes back to it", () => {
    const h = history("/positions?tab=open");
    const before = h.location();
    const api = inPlace(h);
    api.open(basketHref("BANKNIFTY", null));
    expect(h.url()).toBe("/positions?tab=open&panel=option-chain:BANKNIFTY:::open");
    api.close();
    expect(h.location()).toBe(before);
  });
});

describe("NAV-4: each panel's own address is the full route (deep links unchanged)", () => {
  it("every new panel resolves its own href back to itself", () => {
    const cases: [string, Record<string, string>][] = [
      ["option-chain", { underlying: "NIFTY", expiry: "2026-11-26", signal: "action:12", trade: "1" }],
      ["futures", { symbol: "NIFTY 50", tf: "15min", exchange: "BSE", expiry: "2026-11-26" }],
      ["options-plan", {}],
      ["risk-limits", {}],
    ];
    for (const [kind, params] of cases) {
      const entry = DEFAULT_REGISTRY.get(kind)!;
      expect(DEFAULT_REGISTRY.resolve(entry.href(params))).toEqual({ kind, params });
    }
  });

  it("the old addresses answer for the same panels", () => {
    expect(DEFAULT_REGISTRY.resolve("/options-builder")).toEqual({ kind: "options-plan", params: {} });
    expect(DEFAULT_REGISTRY.resolve("/risk-management")).toEqual({ kind: "risk", params: {} });   // NAV-5b: the Risk status page
    expect(optionChainPanelPath({})).toBe("/option-chain");
  });

  it("a flag that is off navigates as before (Plan an options trade without options_builder)", () => {
    const h = history("/option-chain");
    const api = inPlace(h, { flags: {} });
    expect(api.handles("/option-chain/plan")).toBe(false);
    api.open("/option-chain/plan");
    expect(h.url()).toBe("/option-chain/plan");
  });

  it("with ui_v2 off the chain links navigate as before", () => {
    const h = history("/signals");
    const api = inPlace(h, { enabled: false });
    api.open(setupChainHref(SIGNAL)!);
    expect(h.url()).toBe("/option-chain?underlying=NIFTY&signal=action%3A12");
  });
});

describe("NAV-4: the links keep the real address", () => {
  it("the classic page's contract chart has no panel: it keeps the new browser tab (as before)", () => {
    expect(DEFAULT_REGISTRY.resolve("/?contract=NIFTY%7CCE%7C2026-11-26%7C24000")).toBeNull();
    const html = renderToStaticMarkup(<MemoryRouter><InPlaceLink to="/?contract=X" newTabOtherwise rel="noopener">chart</InPlaceLink></MemoryRouter>);
    expect(html).toContain('target="_blank"');
    expect(html).toContain('href="/?contract=X"');
  });

  it("outside any router (a ticket in its own test) it is the plain link it was", () => {
    const html = renderToStaticMarkup(<InPlaceLink to="/settings/risk-limits" className="x" data-ticket="change-limits" state={{ a: 1 }}>Change limit</InPlaceLink>);
    expect(html).toBe('<a class="x" data-ticket="change-limits" href="/settings/risk-limits">Change limit</a>');
  });

  it("a card's converted \"more\" link and an unconverted one both keep the address", () => {
    const html = (inPlaceMore: boolean) => renderToStaticMarkup(
      <MemoryRouter><CardV2 title="NIFTY 50" more={{ label: "Open the chart", to: "/charts/NIFTY%2050", inPlace: inPlaceMore }}>x</CardV2></MemoryRouter>);
    expect(html(true)).toContain('href="/charts/NIFTY%2050"');
    expect(html(false)).toContain('href="/charts/NIFTY%2050"');
  });
});

describe("NAV-4: the sites use the host (source)", () => {
  const uses: [string, RegExp][] = [
    ["fno/BasketPanelV2.tsx", /openInPlace\("\/option-chain\/plan"\);/],
    ["fno/BasketPanelV2.tsx", /<InPlaceLink to=\{why\.link\} className="font-medium text-brand underline" data-basket="change-limits">/],
    ["fno/ChainActionSheetV2.tsx", /<InPlaceLink to=\{chartLink\(target, contract\)\} className=\{ROW\} data-action="chart" onClick=\{on\.onChart\}>/],
    ["fno/CockpitDeskV2.tsx", /more=\{\{ label: "Open the chart", to: chartsPath\(focus\.spotSymbol\), inPlace: true \}\}/],
    ["fno/CompareV2.tsx", /\{adjust && <InPlaceLink to=\{adjust\}/],
    ["fno/FuturesChainV2.tsx", /<InPlaceLink to=\{chartTabPath\(row\.tradingsymbol, row\.exchange, timeframe\)\}/],
    ["fno/FuturesChainV2.tsx", /<InPlaceLink to=\{basketHref\(data\.underlying, timeframe\)\} data-futures-basket/],
    ["fno/InsightsV2.tsx", /<InPlaceLink to=\{href\} data-setup-link/],
    ["fno/InsightsV2.tsx", /<InPlaceLink to=\{trade\} data-setup-trade-link/],
    ["fno/OrderTicketV2.tsx", /<InPlaceLink to=\{why\.link\} className="font-medium text-brand underline" data-ticket="change-limits">/],
    ["optionChain/AskAiV2.tsx", /<InPlaceLink to=\{SETTINGS_PATH\.ai\} data-oc-ask-add-key/],
    ["pages/OptionChainPage.tsx", /<InPlaceLink to=\{BROKER_PAGE_PATH\} className="text-brand underline">Broker page<\/InPlaceLink>/],
    ["pages/OptionChainPage.tsx", /<InPlaceLink to=\{links\.ce\} newTabOtherwise rel="noopener"/],
    ["pages/OptionChainPage.tsx", /<InPlaceLink to=\{links\.pe\} newTabOtherwise rel="noopener"/],
  ];
  for (const [file, re] of uses) it(`${file}: ${re.source.slice(0, 60)}`, () => expect(read(file)).toMatch(re));

  it("the basket no longer reloads the whole app to open the plan", () => {
    expect(read("fno/BasketPanelV2.tsx")).not.toMatch(/window\.location\.assign\(/);
  });

  it("the Option chain page and its basket read the router's address (the panel's own one inside a panel)", () => {
    expect(read("optionChain/OptionChainPageV2.tsx")).toMatch(/useState\(\(\) => initialSymbol\(params\.toString\(\)\)\)/);
    expect(read("fno/ChainTradingV2.tsx")).toMatch(/useContext\(UNSAFE_LocationContext\)\?\.location\?\.search/);
  });
});
