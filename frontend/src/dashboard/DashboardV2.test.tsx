import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import appSource from "../App.tsx?raw";
import routesSource from "../routes.ts?raw";
import { OWN_BROKER_STATES, PAGE_COMPONENTS_V2 } from "../shell/pagesV2";
import { isGatedPath, shellGates } from "../shell/shellRoutes";
import type { BrokerLogin } from "../ui/BrokerGate";
import { TermHint } from "../ui/TermHint";
import { ActiveSignalsV2, OpenPositionsV2, TodaySummaryV2 } from "./BlocksV2";
import dashboardSource from "./DashboardV2.tsx?raw";
import blocksSource from "./BlocksV2.tsx?raw";
import stripSource from "./MarketStripV2.tsx?raw";
import attentionSource from "./NeedsAttentionV2.tsx?raw";
import modelSource from "./model.ts?raw";
import MarketStripV2 from "./MarketStripV2";
import NeedsAttentionV2 from "./NeedsAttentionV2";
import type { AttentionRow } from "./model";
import type { MarketStrip } from "./types";

/** UI2-2: the v2 dashboard's blocks as rendered, the router wiring, and the removals. */

const html = (node: React.ReactNode) => renderToStaticMarkup(<MemoryRouter>{node}</MemoryRouter>);
const idle: BrokerLogin = { busy: null, error: null, codeStep: null, login: () => undefined, submitCode: () => undefined };
const rows: AttentionRow[] = [
  { id: "approvals", icon: "approval", tone: "neutral", title: "2 trades are waiting for your approval", detail: "d", action: { label: "Review 2 trades", to: "/copilot/watchtower" } },
  { id: "paused", icon: "paused", tone: "warn", title: "Autopilot paused", detail: "d", action: { label: "Review", to: "/deployments" } },
  { id: "live-paused", icon: "warning", tone: "warn", title: "Live trading is paused on Upstox", detail: "d" },
];

describe("UI2-2 Needs attention", () => {
  it("one primary action - the first row's - and secondary for the rest", () => {
    const out = html(<NeedsAttentionV2 rows={rows} loginBroker={null} login={idle} />);
    expect(out.match(/bg-brand /g)).toHaveLength(1);
    expect(out).toMatch(/class="[^"]*bg-brand [^"]*" href="\/copilot\/watchtower"/);
    expect(out).toMatch(/class="[^"]*border-border[^"]*" href="\/deployments"/);
    expect(out).toContain("Needs attention");
    expect((out.match(/data-attention=/g) ?? []).length).toBe(3);
  });

  it("nothing waiting: one line, 'All clear'", () => {
    const out = html(<NeedsAttentionV2 rows={[]} loginBroker={null} login={idle} />);
    expect(out).toContain("All clear.");
    expect(out).toContain("Nothing needs you right now.");
    expect(out).not.toContain("<ul");
  });

  it("the broker login row runs the login here, with the broker's own note", () => {
    const broker = { broker_name: "angel_one", account_label: "primary", token_status: "EXPIRED", token_expires_at: null, last_verified_at: null,
      needs_login: true, oauth_supported: false, oauth_callback_url: null } as const;
    const out = html(<NeedsAttentionV2 rows={[{ id: "login", icon: "login", tone: "warn", title: "Log in to Angel One", detail: "", action: { label: "Log in", login: true } }]}
      loginBroker={{ ...broker }} login={{ ...idle, error: "Angel One did not accept the login." }} />);
    expect(out).toMatch(/<button[^>]*class="[^"]*bg-brand[^"]*"[^>]*>.*Log in<\/button>/);
    expect(out).toContain("We log in to Angel One for you with the PIN and authenticator key you stored, encrypted.");
    expect(out).not.toContain("never see or store");                  // never for a TOTP broker (UI-6)
    expect(out).toContain('role="alert"');
  });
});

describe("UI2-2 blocks", () => {
  it("friendly empty states with one action each", () => {
    const pos = html(<OpenPositionsV2 data={{ status: "ok", rows: [], total: 0 }} />);
    expect(pos).toContain("No open positions.");
    expect(pos).toContain('href="/screener"');
    const sig = html(<ActiveSignalsV2 data={{ status: "ok", rows: [], total: 0 }} />);
    expect(sig).toContain("No signals yet today.");
    expect(sig).toContain('href="/screener/custom"');
    // Day-one fix 9: with the ready strategies on, the empty day points to the one-tap Signals watch
    const ready = html(<ActiveSignalsV2 data={{ status: "ok", rows: [], total: 0 }} readyWatch />);
    expect(ready).toContain("No signals yet today.");
    expect(ready).toContain("Watch a list on Signals");                       // TD-1: one line + one button
    expect(ready).toContain('href="/signals"');
    expect(ready).toContain("Watch a list on Signals");
    expect(html(<OpenPositionsV2 data={{ status: "error" }} />)).toContain("We could not load your positions. We will try again in a moment.");
  });

  it("positions as a list (no table), with Paper and Live chips and signed P&L", () => {
    const out = html(<OpenPositionsV2 data={{ status: "ok", total: 1, rows: [{ id: 1, symbol: "RELIANCE", mode: "Live", side: "Buy", detail: "Buy · 50 shares · avg 2,962.20", ltp: 2948.2, pnl: -700, pnlPct: -0.47 }] }} />);
    expect(out).not.toContain("<table");
    expect(out).toContain("Live");
    expect(out).toContain("−₹700");
    expect(out).not.toContain("−₹700.00");                                   // a card: whole rupees
    expect(out).toContain("text-down");
    expect(out).toContain('href="/positions"');
  });

  it("the Today tiles show '–' for what is unknown, never a made-up 0", () => {
    const out = html(<TodaySummaryV2 loading={false} tiles={{ booked: null, winRate: null, closedTrades: null, riskUsedPct: null, riskUsed: null, riskLimit: null,
      signals: { rows: [], count: 0, waiting: 0, taken: 0 } }} />);
    expect(out.match(/–/g)?.length).toBeGreaterThanOrEqual(3);
    expect(out).toContain("no daily limit set");
  });

  it("the strip: no price is '–'; a remembered level says when it is from; the broker chip is amber only for a login", () => {
    const strip: MarketStrip = { source: "memory", broker: null, refresh_seconds: 15, fetched_at: "",
      market: { exchange: "NSE", phase: "pre-open", is_open: false, opens_at: "09:15", closes_at: "15:30", next_open: null, holiday: null, reason: "before-open" },
      indices: [
        { key: "NIFTY 50", label: "NIFTY 50", symbol: "NIFTY 50", exchange: "NSE", ltp: 24693.95, prev_close: 24601.1, change: 92.85, change_pct: 0.38,
          as_of: "2026-10-16T10:00:00Z", stale: true, source: "memory", missing: false },
        { key: "INDIA VIX", label: "INDIA VIX", symbol: "INDIA VIX", exchange: "NSE", ltp: null, prev_close: null, change: null, change_pct: null,
          as_of: null, stale: null, source: null, missing: true },
      ] };
    const out = html(<MarketStripV2 strip={{ status: "ok", data: strip }} oi={{}} marketText="Pre-open · opens 09:15"
      broker={{ text: "Upstox · log in", tone: "warn", connected: false }} pnl={{ total: 0, booked: 0, open: 0 }} nowMs={Date.parse("2026-10-19T09:05:00+05:30")} />);
    expect(out).toContain("24,693.95");
    expect(out).toContain("As of 16 Oct");
    expect(out).toContain("No price yet");
    expect(out).toContain("bg-warn-tint text-warn");
    expect(out).toContain("Pre-open · opens 09:15");
    expect(out).not.toMatch(/>0\.00</);
  });

  it("the glossary '?' has a 44 px touch ring and names its term", () => {
    const out = renderToStaticMarkup(<TermHint term="pcr" />);
    expect(out).toContain('aria-label="What is PCR?"');
    expect(out).toContain("after:-inset-[14px]");
  });
});

describe("UI2-2 router wiring (UI v2 §12: PageV2 ?? Page)", () => {
  it("the v2 shell has a v2 dashboard; today's router and App do not load it", () => {
    expect(Object.keys(PAGE_COMPONENTS_V2)).toEqual(["dashboard", "signals", "charts", "option-chain", "positions", "portfolio", "risk-management", "setup", "trading-defaults", "deployments", "copilot",
      "settings", "markets", "admin", "ideas", "learn", "terminal", "watchlist", "settings-ai", "settings-alerts", "settings-appearance", "settings-help", "settings-advanced"]);
    expect(routesSource).not.toMatch(/DashboardV2|pagesV2/);
    expect(appSource).not.toMatch(/DashboardV2|pagesV2/);
  });

  it("the dashboard shows its own broker states; every other data page keeps the gate; a broken address stays gated", () => {
    // UI2-9a: My positions shows the broker states itself too, so exits stay reachable (src/trading/PositionsV2.tsx).
    expect(OWN_BROKER_STATES).toEqual(["/", "/positions", "/copilot"]);   // item 5: the chat Copilot says itself what a missing broker hides
    expect(isGatedPath("/")).toBe(true);                         // still a data page (the waiting screen and the gate rule)
    expect(isGatedPath("/positions")).toBe(true);
    for (const p of ["/", "/positions", "/%70ositions"]) expect(shellGates(p, OWN_BROKER_STATES), p).toBe(false);
    for (const p of ["/portfolio", "/charts/NIFTY", "/option-chain", "/positions/x", "/copilot/coach", "/%E0%A4"]) expect(shellGates(p, OWN_BROKER_STATES), p).toBe(true);
    for (const p of ["/settings", "/orders"]) expect(shellGates(p, OWN_BROKER_STATES), p).toBe(false);
  });

  it("signed out goes to the sign-in page (UI-14); no broker shows the gate's 'Connect your broker' and nothing else", () => {
    expect(dashboardSource).toContain("<Navigate to={SIGN_IN_PATH} replace />");
    expect(dashboardSource).toMatch(/BLOCKING[^\n]*"no-broker"/);
  });
});

describe("UI2-2 removals and wording (§3, §5 'Removed or moved')", () => {
  const sources = [dashboardSource, blocksSource, stripSource, attentionSource, modelSource].join("\n")
    .replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
  it("none of today's removed blocks or developer wording is in the v2 dashboard", () => {
    for (const gone of ["Backend online", "Autopilot trading", "Engines behind", "Deploy a strategy", "MarketPulseCard", "OiBannersCard",
      "GoLiveChecklist", "Strategy mix", "Broker funds", "workerStatus", "api.health", "String(e)", "Sample", "registry"]) {
      expect(sources, gone).not.toContain(gone);
    }
  });
});
