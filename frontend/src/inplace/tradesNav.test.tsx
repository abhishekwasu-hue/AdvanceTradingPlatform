import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { EmptyStateV2 } from "../dashboard/CardV2";
import { adjustHref } from "../fno/contracts";
import { RISK_LIMITS_PATH } from "../settings/riskLimits";
import { SETUP_PATH } from "../setup/model";
import { chartsPath } from "../shell/shellRoutes";
import { CONNECT_BROKER_PATH } from "../ui/BrokerGate";
import { createInPlaceApi, handoffs, openPanels, pushedIn, type NavigateFn, type RouterLocation } from "./api";
import { DEFAULT_REGISTRY } from "./panels";

/**
 * M75 NAV-5b (docs/design/M75_NO_NAVIGATION.md, the Positions / orders / portfolio / risk rows): the 10
 * click-navigations open in place through the host: the PATH stays, the panel registered for the destination is on
 * top, nothing is handed over, and closing goes back to the exact entry before. Exits on My positions are untouched
 * (ADR-0004): only links to other facilities changed.
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

const ON: Record<string, boolean> = { screener_v2: true };
function inPlace(h: ReturnType<typeof history>) {
  return createInPlaceApi({ enabled: true, on: (f) => ON[f] === true, registry: DEFAULT_REGISTRY, location: h.location, navigate: h.navigate });
}

const FACTS = { known: true, underlying: "BANKNIFTY", right: "PE", expiry: "2026-11-26", spot_symbol: "NIFTY BANK", spot_exchange: "NSE", expired: false, label: "x", lot_size: 30 };

const SITES: { site: string; from: string; to: () => string; kind: string; params?: Record<string, string> }[] = [
  { site: "trading/OrdersV2.tsx:73 Signals (empty)", from: "/positions/orders", to: () => "/signals", kind: "signals" },
  { site: "trading/PortfolioV2.tsx:79 Find setups (empty)", from: "/positions/money", to: () => "/screener", kind: "screener" },
  { site: "trading/PortfolioV2.tsx:105 Risk limits (the Risk status page, as before)", from: "/positions/money", to: () => "/risk-management", kind: "risk" },
  { site: "trading/PositionsV2.tsx:196 Connect your broker", from: "/positions", to: () => CONNECT_BROKER_PATH, kind: "broker" },
  { site: "trading/PositionsV2.tsx:272 Find setups (empty)", from: "/positions", to: () => "/screener", kind: "screener" },
  { site: "trading/PositionsV2.tsx:598 Adjust", from: "/positions", to: () => adjustHref(FACTS as never)!, kind: "option-chain", params: { underlying: "BANKNIFTY", expiry: "2026-11-26" } },
  { site: "trading/PositionsV2.tsx:602 Chart", from: "/positions", to: () => chartsPath("NIFTY BANK"), kind: "chart", params: { symbol: "NIFTY BANK" } },
  { site: "trading/PositionsV2.tsx:679 Orders", from: "/positions", to: () => "/positions/orders", kind: "orders" },
  { site: "trading/RiskV2.tsx:308 Open Settings › Risk limits", from: "/risk-management", to: () => RISK_LIMITS_PATH, kind: "risk-limits" },
  { site: "trading/RiskV2.tsx:340 change my set-up answers", from: "/risk-management", to: () => SETUP_PATH, kind: "setup" },
];

describe("NAV-5b: every Positions / orders / portfolio / risk button opens in place (the path stays)", () => {
  it("covers the 10 sites of the design table", () => expect(SITES).toHaveLength(10));

  for (const s of SITES) {
    it(s.site, () => {
      const h = history(s.from);
      const before = h.location();
      const api = inPlace(h);
      const to = s.to();
      expect(api.handles(to)).toBe(true);
      api.open(to);
      expect(h.location().pathname).toBe(s.from);
      expect(openPanels(h.location().search, DEFAULT_REGISTRY, (f) => ON[f] === true).map((p) => p.spec)).toEqual([{ kind: s.kind, params: s.params ?? {} }]);
      expect(pushedIn(h.location().state)).toBe(1);
      expect(handoffs(h.location().state)).toEqual([]);
      api.close();
      expect(h.location()).toBe(before);
    });
  }

  it("the old Orders address answers for the same panel; each new panel's href resolves back to itself", () => {
    expect(DEFAULT_REGISTRY.resolve("/orders")).toEqual({ kind: "orders", params: {} });
    for (const kind of ["orders", "risk", "setup"]) expect(DEFAULT_REGISTRY.resolve(DEFAULT_REGISTRY.get(kind)!.href({}))).toEqual({ kind, params: {} });
  });

  it("an empty state's action keeps its real address", () => {
    const html = renderToStaticMarkup(<MemoryRouter><EmptyStateV2 text="Nothing yet." action={{ label: "Signals", to: "/signals" }} /></MemoryRouter>);
    expect(html).toContain('href="/signals"');
  });
});

describe("NAV-5b: the sites use the host (source)", () => {
  const uses: [string, RegExp][] = [
    ["trading/OrdersV2.tsx", /action=\{\{ label: "Signals", to: "\/signals" \}\}/],
    ["trading/PortfolioV2.tsx", /action=\{\{ label: "Find setups", to: "\/screener" \}\}/],
    ["trading/PortfolioV2.tsx", /<InPlaceLink to="\/risk-management"/],
    ["trading/PositionsV2.tsx", /onConnect=\{\(\) => inPlace\.open\(CONNECT_BROKER_PATH\)\}/],
    ["trading/PositionsV2.tsx", /action=\{\{ label: "Find setups", to: "\/screener" \}\}/],
    ["trading/PositionsV2.tsx", /<InPlaceLink to=\{adjust\} data-testid="fno-adjust"/],
    ["trading/PositionsV2.tsx", /<InPlaceLink to=\{row\.chartPath\} data-testid="position-chart"/],
    ["trading/PositionsV2.tsx", /more=\{\{ label: "Orders", to: "\/positions\/orders" \}\}/],
    ["trading/RiskV2.tsx", /<InPlaceLink to=\{RISK_LIMITS_PATH\}/],
    ["trading/RiskV2.tsx", /<InPlaceLink to=\{SETUP_PATH\}/],
  ];
  for (const [file, re] of uses) it(`${file}: ${re.source.slice(0, 60)}`, () => expect(read(file)).toMatch(re));
});
