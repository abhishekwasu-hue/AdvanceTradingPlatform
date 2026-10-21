import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import appSource from "../App.tsx?raw";
import oldPositionsSource from "../pages/PositionsPage.tsx?raw";
import routesSource from "../routes.ts?raw";
import { OWN_BROKER_STATES, PAGE_COMPONENTS_V2 } from "../shell/pagesV2";
import confirmSource from "../ui/ConfirmDialog.tsx?raw";
import modelSource from "./model.ts?raw";
import partsSource from "./PartsV2.tsx?raw";
import portfolioSource from "./PortfolioV2.tsx?raw";
import positionsSource from "./PositionsV2.tsx?raw";
import { PositionRowV2, type RowActions } from "./PositionsV2";
import riskPartsSource from "./RiskPartsV2.tsx?raw";
import riskSource from "./RiskV2.tsx?raw";
import { heldRows } from "./model";
import { exposure, trade } from "./trading.fixture";

/** UI2-9a: the v2 My positions, My portfolio and Risk limits - wiring, exits, colour and wording rules. */

const noop = () => undefined;
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const code = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/[^\n]*/g, "$1");
const ALL = [positionsSource, portfolioSource, riskSource, riskPartsSource, partsSource, modelSource, confirmSource].map(code).join("\n");

function row(p = trade(), actions: Partial<RowActions> = {}, prices = true) {
  const [r] = heldRows([p], exposure());
  const a: RowActions = { exitNowOn: true, canTrade: true, onExit: noop, onCheck: noop, ...actions };
  return renderToStaticMarkup(<MemoryRouter><ul><PositionRowV2 row={r} position={p} actions={a} prices={prices} /></ul></MemoryRouter>);
}

describe("UI2-9a router wiring (UI v2 §12: PageV2 ?? Page)", () => {
  it("the v2 shell has the three pages; today's router and App do not load them", () => {
    for (const key of ["positions", "portfolio", "risk-management"] as const) expect(PAGE_COMPONENTS_V2[key], key).toBeDefined();
    expect(routesSource).toContain('positions: () => import("./pages/PositionsPage")');
    expect(routesSource).toContain('portfolio: () => import("./pages/PortfolioPage")');
    expect(routesSource).toContain('"risk-management": () => import("./pages/RiskManagementPage")');
    expect(routesSource).not.toMatch(/trading\/|pagesV2/);
    expect(appSource).not.toMatch(/trading\/|PositionsV2|PortfolioV2|RiskV2/);
  });

  it("My positions shows the broker states itself, so its exits are reachable before a broker login", () => {
    expect(OWN_BROKER_STATES).toContain("/positions");
    expect(OWN_BROKER_STATES).not.toContain("/portfolio");
    expect(positionsSource).toContain("<BrokerGateView state={broker}");
    expect(positionsSource).toMatch(/title="Your open positions"[\s\S]*<PositionList/);
  });
});

describe("UI2-9a exits (ADR-0004, UI-11)", () => {
  it("Exit now: a neutral button, never disabled, with today's test id and today's confirm before anything is sent", () => {
    const html = row();
    expect(html).toContain('data-testid="exit-now"');
    const button = html.match(/<button[^>]*data-testid="exit-now"[^>]*>/)?.[0] ?? "";
    expect(button).not.toMatch(/\sdisabled=""|(?<![:\w-])bg-brand|text-down|bg-down|border-down/);
    expect(button).toContain("border-border");
    expect(code(positionsSource)).toMatch(/if \(!window\.confirm\(exitConfirmText\(p\)\)\) return;\s*try \{\s*const result = await api\.exitNow\(p\.id\)/);
    expect(code(oldPositionsSource)).toContain("window.confirm(`Exit ${p.direction} ${p.symbol} x${p.quantity} (PAPER) now, at the current price?`)");
  });

  it("without a broker session: the same Exit now, no price or P&L columns, and the paper account's pause stays", () => {
    const html = row(trade(), {}, false);
    expect(html).toContain('data-testid="exit-now"');
    expect(html).not.toContain("data-col");
    expect(text(html)).not.toMatch(/LTP|P&L|₹/);
    const gated = code(positionsSource).slice(code(positionsSource).indexOf("if (!connected)"), code(positionsSource).indexOf("const tiles"));
    expect(gated).toContain("<PaperAccountCard canTrade={canTrade} />");
    expect(gated).toContain("prices={false}");
    expect(gated).toContain('<LoadFailedV2 what="your positions" />');
    expect(gated).not.toContain('broker.kind !== "loading"');
    expect(gated).toMatch(/exitNowOn && canTrade \? " You can still exit a paper position here\." : ""/);
  });

  it("only for a paper position the trader can trade; a live one is closed in the broker's app", () => {
    expect(row(trade(), { canTrade: false })).not.toContain("exit-now");
    const live = row(trade({ mode: "LIVE" }));
    expect(live).not.toContain("exit-now");
    expect(text(live)).toContain("Close a live position in your broker's app.");
  });

  it("with Exit now off, the price check by hand stays (it closes the position at a level it reaches)", () => {
    const html = row(trade(), { exitNowOn: false });
    expect(html).not.toContain("exit-now");
    expect(text(html)).toContain("Check price");
    expect(html).toContain('aria-hidden="true"');
  });

  it("a row: symbol, Long, Paper, size, levels in red and green, live price and signed P&L, a chart link", () => {
    const html = row();
    const words = text(html);
    for (const s of ["DEMO A", "Long", "Paper", "Buy · 2 lots (150) · avg 100.00", "Stop 95.00", "Target 110.00", "110.00", "+₹1,500.00", "+10.00%"]) {
      expect(words, s).toContain(s);
    }
    expect(html).toContain("text-down");
    expect(html).toContain("text-up");
    expect(html).toContain('href="/charts/DEMO%20A"');
  });

  it("removing a market event or a limit is a neutral button with a confirm that names the consequence", () => {
    expect(riskPartsSource.match(/<ConfirmDialog/g)?.length).toBe(2);
    expect(riskPartsSource).toContain("eventRemoveConsequence(removing)");
    expect(riskPartsSource).toContain("Orders will no longer be checked against");
    expect(code(confirmSource)).not.toMatch(/bg-down|text-down|bg-brand/);
    expect(code(confirmSource)).toContain("bg-fg text-surface-1");
    expect(code(confirmSource)).toMatch(/description=\{<span[^>]*data-consequence>\{consequence\}<\/span>\}/);   // read with the title
  });
});

describe("UI2-9a one primary action, colour and wording", () => {
  const primaries = (src: string) => code(src).match(/BUTTON_PRIMARY|LINK_PRIMARY|(?<![:\w-])bg-brand(?![\w/-])/g) ?? [];
  it("Risk limits has one primary action, 'Save limits'; Positions and Portfolio have none of their own", () => {
    expect(primaries(riskSource)).toEqual(["BUTTON_PRIMARY", "BUTTON_PRIMARY"]);   // the import and the Save button
    expect(riskSource).toContain('"Save limits"');
    expect(primaries(riskPartsSource)).toEqual([]);
    expect(primaries(positionsSource)).toEqual([]);
    expect(primaries(portfolioSource)).toEqual([]);
  });

  it("no raw error text, no advice and no claims about results", () => {
    expect(ALL).not.toMatch(/String\((e|err)\)/);
    expect(ALL).not.toMatch(/\b(recommended|recommend|best|guaranteed?|you should|sure profit|winning strategy)\b/i);
    expect(ALL).not.toMatch(/\b(registry|adapter|backend|endpoint|503|feature flag|Phase [A-Z])\b/);
  });

  it("titles from the proposal: My positions, My portfolio, Risk limits", () => {
    expect(positionsSource).toContain('title="My positions"');
    expect(portfolioSource).toContain('title="My portfolio"');
    expect(riskSource).toContain('title="Risk limits"');
  });
});
