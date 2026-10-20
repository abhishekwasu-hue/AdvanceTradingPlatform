import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { AuthProvider } from "../auth/AuthContext";
import strategiesSource from "../pages/StrategiesPage.tsx?raw";
import appShellSource from "../shell/AppShellV2.tsx?raw";
import { PAGE_V2 } from "../shell/pagesV2";
import watchtowerSource from "../copilot/tabs/WatchtowerTab.tsx?raw";
import copilotPageSource from "../pages/AiCopilotPage.tsx?raw";
import type { AiAction, StrategyInfo } from "../types";
import { FeaturesProvider } from "../ui/useFeatures";
import feedSource from "./feed.ts?raw";
import { LevelsChart, SignalDetail, StatusChip } from "./SignalPartsV2";
import partsSource from "./SignalPartsV2.tsx?raw";
import { feedItem } from "./signals.fixture";
import SignalsV2 from "./SignalsV2";
import { TryOnSymbolBody } from "./TryOnSymbolV2";
import tryOnSource from "./TryOnSymbolV2.tsx?raw";
import hookSource from "./useSignalFeed.ts?raw";

const NOW = new Date("2026-10-08T05:00:00Z");
const noop = () => undefined;
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/\s+/g, " ");
const inRouter = (node: React.ReactNode) => renderToStaticMarkup(<MemoryRouter>{node}</MemoryRouter>);
const primaries = (html: string) => html.match(/(?<![:\w-])bg-brand(?![\w-/])/g)?.length ?? 0;
const proposal = {
  id: 11, risk_check: {
    sizing: { cap: 6000, risk_amount: 5250, within_cap: true, suggested_quantity: 75, lot_size: 75 }, costs: null, refusals: [], warnings: [], errors: [],
  }, portfolio_impact: null,
} as unknown as AiAction;

describe("UI2-3 status chip", () => {
  it("waiting is the brand tint with the time left; every other status is a neutral outline with words", () => {
    const waiting = renderToStaticMarkup(<StatusChip item={feedItem()} now={NOW} />);
    expect(waiting).toContain("bg-brand/10");
    expect(text(waiting)).toContain("Waiting · 8 min");
    for (const status of ["taken", "skipped", "expired", "not_placed"] as const) {
      const html = renderToStaticMarkup(<StatusChip item={feedItem({ status })} now={NOW} />);
      expect(html, status).not.toMatch(/bg-brand|text-up|text-down|text-warn/);
      expect(html, status).toContain("border-border");
    }
  });
});

describe("UI2-3 detail panel", () => {
  it("a waiting signal: header, levels, six figures, reasons, risk sentences and one primary action", () => {
    const html = inRouter(<SignalDetail item={feedItem()} proposal={proposal} now={NOW} canTrade onDone={noop} onError={noop} />);
    const words = text(html);
    expect(words).toContain("DEMO 50");
    expect(words).toContain("Long");
    expect(words).toContain("Demo breakout · from Autopilot · Deployment #4 · Paper");
    for (const label of ["Entry", "Stop", "Target", "Size", "Max loss", "Reward : risk"]) expect(words).toContain(label);
    expect(words).toContain("−2.00");                                 // stop below the entry
    expect(words).toContain("+4.00");
    expect(words).toContain("1 lot (75)");
    expect(words).toContain("−₹5,250.00");
    expect(words).toContain("Why this trade");
    expect(words).toContain("Fits your risk limits");
    expect(words).toContain("Most you can lose (₹5,250.00) is within your ₹6,000.00 per-trade limit.");
    expect(words).toContain("Approve paper trade");
    expect(words).toContain("Skip");
    expect(html).toContain('href="/charts/DEMO%2050"');
    expect(primaries(html)).toBe(1);
    expect(html).toContain('aria-label="More for DEMO 50"');      // snooze sits under More
  });

  it("an expired signal: no decision buttons, says nothing was placed", () => {
    const html = inRouter(<SignalDetail item={feedItem({ status: "expired" })} now={NOW} canTrade onDone={noop} onError={noop} />);
    expect(text(html)).toContain("This signal expired before anyone decided. Nothing was placed.");
    expect(html).not.toContain("Approve");
    expect(html).not.toContain(">Skip<");
    expect(text(html)).toContain("Open chart");
  });

  it("a waiting signal whose expiry passes on screen loses its buttons too", () => {
    const html = inRouter(<SignalDetail item={feedItem()} now={new Date("2026-10-08T05:10:00Z")} canTrade onDone={noop} onError={noop} />);
    expect(html).not.toContain("Approve");
    expect(text(html)).toContain("Expired");
  });

  it("without the risk pre-check nothing is invented: the figures say – and the page says the limits apply on approval", () => {
    const words = text(inRouter(<SignalDetail item={feedItem()} now={NOW} canTrade onDone={noop} onError={noop} />));
    expect(words).toContain("Your limits are checked again when you approve");
    expect(words).not.toContain("Fits your risk limits");
  });

  it("phone card content: Approve, Skip, a chart button and More, each 44 px", () => {
    const html = inRouter(<SignalDetail item={feedItem()} proposal={proposal} now={NOW} compact canTrade onDone={noop} onError={noop} />);
    expect(text(html)).toContain("Fits your risk limits · most you can lose ₹5,250.00");
    expect(text(html)).toContain("Approve");
    expect(html).toContain('aria-label="Open chart for DEMO 50"');
    expect(html.match(/min-h-11/g)?.length ?? 0).toBeGreaterThanOrEqual(4);
  });

  it("a LIVE proposal says so and never promises that nothing reaches the broker", () => {
    const words = text(inRouter(<SignalDetail item={feedItem({ mode: "LIVE" })} proposal={proposal} now={NOW} canTrade onDone={noop} onError={noop} />));
    expect(words).toContain("Approve live trade");
    expect(words).toContain("· Live");
    expect(words).toContain("approving asks for your authenticator code first");
    expect(words).not.toContain("Nothing reaches your broker");
    expect(words).not.toContain("Approve paper trade");
  });

  it("a role without trading rights sees the signal but no Approve, Skip or Snooze", () => {
    for (const compact of [false, true]) {
      const html = inRouter(<SignalDetail item={feedItem()} proposal={proposal} now={NOW} compact={compact} canTrade={false} onDone={noop} onError={noop} />);
      expect(html).not.toMatch(/>Approve|>Skip<|More for DEMO 50/);
      expect(text(html)).toContain("Your role can view signals but not approve or skip them.");
      expect(html).toContain('href="/charts/DEMO%2050"');
      expect(primaries(html)).toBe(0);
    }
  });

  it("levels are drawn with Entry neutral, Stop red and Target green", () => {
    const html = renderToStaticMarkup(<LevelsChart item={feedItem()} />);
    expect(html).toContain('aria-label="Levels: entry 100.00, stop 98.00, target 104.00"');
    expect(html).toContain("stroke-up");
    expect(html).toContain("stroke-down");
    expect(html).toContain("stroke-fg-muted");
  });
});

describe("UI2-3 the page", () => {
  it("renders the tabs and the phone default (Waiting for you) while the list loads", () => {
    const html = renderToStaticMarkup(
      <AuthProvider><FeaturesProvider userKey={1} value={{ ui_v2: true }}><MemoryRouter initialEntries={["/signals"]}><SignalsV2 /></MemoryRouter></FeaturesProvider></AuthProvider>,
    );
    expect(html).toContain('role="tablist"');
    expect(html.match(/role="tab"/g)).toHaveLength(3);
    expect(html).toMatch(/aria-selected="true"[^>]*>.*?Waiting/);
    expect(text(html)).toContain("Nothing is placed until you approve it.");
    for (const s of ["All sources", "Autopilot", "Scans", "Copilot"]) expect(text(html)).toContain(s);
    expect(html).toContain('aria-label="Loading signals"');
    expect(text(html)).not.toMatch(/sample|seed/i);                  // the generator is not on this page any more (M6)
  });

  it("is the v2 page for /signals; today's page stays for the old shell", () => {
    expect(PAGE_V2.signals).toBeDefined();
    expect(appShellSource).toContain("PAGE_V2[p] ?? PAGE_COMPONENTS[p]");
  });

  it("reads only: the feed is a GET, every decision goes to the existing proposal endpoints, nothing places an order", () => {
    expect(feedSource).not.toMatch(/method:\s*"(POST|PUT|PATCH|DELETE)"/);
    expect(hookSource).not.toMatch(/method:/);
    for (const call of ["api.aiApproveAction(", "api.aiRejectAction(", "api.aiSnoozeAction("]) expect(partsSource).toContain(call);
    expect(partsSource).not.toMatch(/paperExecute|placeOrder|\/orders/);
    expect(partsSource).toContain("StepUpDialog");                     // LIVE keeps the authenticator step
  });
});

describe("UI2-3 M6: the generator in the Library drawer", () => {
  const strategy: StrategyInfo = { id: "demo", name: "Demo breakout", description: "Made-up", category: "indicator_based" as StrategyInfo["category"], timeframes: ["5min"], default_params: {} };

  it("broker candles by default; sample data only under Advanced, off until chosen", () => {
    const html = inRouter(<AuthProvider><TryOnSymbolBody strategy={strategy} /></AuthProvider>);
    const words = text(html);
    expect(words).toContain("Try it");
    expect(words).toContain("Advanced");
    expect(words).toContain("Use sample data instead of your broker's candles");
    expect(words).not.toContain("Sample data seed");
    expect(words).not.toContain("Sample bars");
    expect(html).toMatch(/<details[^>]*>\s*<summary[^>]*>Advanced/);
  });

  it("a sample-data result is labelled from how it was made and offers no paper trade", () => {
    expect(tryOnSource).toContain('made?.sample ? " · sample data" : ""');
    expect(tryOnSource).toMatch(/\{item\.direction && made && !made\.sample && \(\s*<button[^>]*onClick=\{\(\) => void paperTrade\(\)\}/);
    expect(tryOnSource).toContain("setMade({ symbol: sym, data, sample: useSample })");
    expect(tryOnSource).toContain("if (!made || made.sample) return;");
  });

  it("the Library offers it only while ui_v2 is on", () => {
    expect(strategiesSource).toContain("const v2 = useUiV2();");
    expect(strategiesSource).toMatch(/\{v2 && \(\s*<button[^]*Try it on a symbol/);
    expect(strategiesSource).toMatch(/\{v2 && trying && <Suspense/);
  });
});

describe("UI2-3 M7: the Watchtower links to Signals", () => {
  it("only while ui_v2 is on, and keeps every non-entry proposal and other members' entries", () => {
    expect(watchtowerSource).toContain("watchtowerSplit(allPending, v2, me)");
    expect(watchtowerSource).toContain("watchtowerSplit(snoozedAll, v2, me).others");
    expect(watchtowerSource).toMatch(/\{v2 && \(\s*<Link to=\{signalsLinkFor\(entries\)\}/);
    expect(copilotPageSource).toContain("watchtowerSplit(allPending, useUiV2(), me).others");     // the tab badge agrees
  });
});
