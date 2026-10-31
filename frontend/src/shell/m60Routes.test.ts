import { describe, expect, it } from "vitest";
import { legacyCopilotPath } from "../copilot/tabs";
import { settingsHashTarget } from "../settings/paths";
import { allNavItems, isItemVisible, searchPages, type NavContext, type ShellKey } from "./navModel";
import { V2_MOVED, flagOffFallback, shellKeyFromPath, v2RedirectFor } from "./shellRoutes";

/**
 * M60 (owner order, approved 30 Oct with the five defaults): every address in the approved before / after table
 * (docs/ui/M60_SIDEBAR.html, sections A-D) still resolves in the v2 shell - to its own page, or forwarded once to its
 * new home (page + tab / part) - and every page is still found by search.
 *
 * Kept as they are until their new home exists (noted in the PR): /charts (the Terminal has no "Chart only" layout yet),
 * /watchlist (no Terminal Watchlist panel yet, UX-3) and /screener/custom (the scan merge, M49 U6, is not built).
 */
const ALL_ON = ["terminal", "screener_v2", "markets_view", "ai_copilot", "learn", "discipline_engine", "idea_library", "watchlists",
  "options_builder", "chart_analyze", "marketplace"];
const on = (f: string) => ALL_ON.includes(f);
const ctx = { signedIn: true, on };

/** [old address, where it ends (after at most one forward), the page shown there]. */
const TABLE: readonly [string, string, ShellKey][] = [
  // A. The sidebar entries of before.
  ["/", "/", "dashboard"],
  ["/charts", "/charts", "charts"],
  ["/charts/NIFTY%2050", "/charts/NIFTY%2050", "charts"],
  ["/watchlist", "/watchlist", "watchlist"],
  ["/option-chain", "/option-chain", "option-chain"],
  ["/markets", "/markets", "markets"],
  ["/screener", "/screener", "screener"],
  ["/terminal", "/terminal", "terminal"],
  ["/signals", "/signals", "signals"],
  ["/deployments", "/deployments", "deployments"],
  ["/positions", "/positions", "positions"],
  ["/analytics", "/positions/results", "analytics"],
  ["/strategies", "/strategies", "strategies"],
  ["/copilot", "/copilot", "copilot"],
  ["/news-events", "/learn/news", "news-events"],
  ["/learn", "/learn", "learn"],
  ["/settings", "/settings", "settings"],
  ["/broker", "/settings/broker", "broker"],
  ["/admin", "/admin", "admin"],
  // B. Tabs and sub-pages.
  ["/positions/orders", "/positions/orders", "orders"],
  ["/positions/money", "/positions/money", "portfolio"],
  ["/analytics/journal", "/positions/results/journal", "journal"],
  ["/analytics/coach", "/copilot/coach", "copilot"],
  ["/ideas", "/learn/ideas", "ideas"],
  ["/strategies/build", "/strategies/build", "strategy-builder"],
  ["/strategies/test", "/strategies/test", "backtest"],
  ["/screener/custom", "/screener/custom", "scanner"],
  ["/option-chain/plan", "/option-chain/plan", "options-builder"],
  ["/chart-analyze", "/chart-analyze", "chart-analyze"],
  ["/copilot/analyze", "/copilot/analyze", "copilot"],
  ["/copilot/brief", "/copilot/read", "copilot"],
  ["/copilot/signals", "/signals", "signals"],
  ["/copilot/review", "/copilot/coach", "copilot"],
  ["/copilot/market-pulse", "/copilot/market-pulse", "copilot"],
  ["/copilot/strategy-lab", "/copilot/strategy-lab", "copilot"],
  ["/copilot/idea-builder", "/copilot/idea-builder", "copilot"],
  ["/copilot/ask", "/copilot/ask", "copilot"],
  ["/copilot/watchtower", "/copilot/watchtower", "copilot"],
  ["/copilot/news-radar", "/copilot/news-radar", "copilot"],
  ["/copilot/coach", "/copilot/coach", "copilot"],
  ["/learn/words", "/learn/words", "learn-words"],
  ["/settings/account", "/settings/account", "account"],
  ["/settings/setup", "/settings/setup", "setup"],
  ["/team", "/team", "team"],
  ["/settings/trading-defaults", "/settings/trading-defaults", "trading-defaults"],
  ["/risk-management", "/risk-management", "risk-management"],
  ["/settings/risk-limits", "/settings/risk-limits", "settings-risk-limits"],
  ["/settings/alerts", "/settings/alerts", "settings-alerts"],
  ["/notifications", "/notifications", "notifications"],
  ["/settings/ai", "/settings/ai", "settings-ai"],
  ["/settings/appearance", "/settings/appearance", "settings-appearance"],
  ["/settings/help", "/settings/help", "settings-help"],
  ["/settings/advanced", "/settings/advanced", "settings-advanced"],
  // C. Search-only pages.
  ["/fundamentals", "/fundamentals", "fundamentals"],
  ["/quant", "/quant", "quant"],
  ["/instruments", "/instruments", "instruments"],
  ["/system-logs", "/system-logs", "system-logs"],
  ["/marketplace", "/marketplace", "marketplace"],
  // D. Old addresses that already forwarded (each still lands on its page in one step).
  ["/orders", "/positions/orders", "orders"],
  ["/portfolio", "/positions/money", "portfolio"],
  ["/strategy-builder", "/strategies/build", "strategy-builder"],
  ["/backtest", "/strategies/test", "backtest"],
  ["/journal", "/positions/results/journal", "journal"],
  ["/coach", "/copilot/coach", "copilot"],
  ["/journal/coach", "/copilot/coach", "copilot"],
  ["/scanner", "/screener/custom", "scanner"],
  ["/options-builder", "/option-chain/plan", "options-builder"],
  ["/account", "/settings/account", "account"],
];

const resolve = (address: string): string => v2RedirectFor(address, "", ctx) ?? address;

describe("M60: every old address resolves (the approved before / after table)", () => {
  it("covers the table: at least 57 addresses", () => {
    expect(TABLE.length).toBeGreaterThanOrEqual(57);
    expect(new Set(TABLE.map((r) => r[0])).size).toBe(TABLE.length);
  });

  it.each(TABLE)("%s ends at %s (%s) in one step", (from, to, key) => {
    const end = resolve(from);
    expect(end).toBe(to);
    expect(v2RedirectFor(end, "", ctx), `${end} forwards again`).toBeNull();
    expect(shellKeyFromPath(end)).toBe(key);
  });

  it("the older address forms keep working: /ai-copilot/<tab> and /settings#<part>", () => {
    expect(legacyCopilotPath("today")).toBe("/copilot/market-pulse");
    expect(settingsHashTarget("#ai", true)).toBe("/settings/ai");
    expect(settingsHashTarget("#alerts", true)).toBe("/settings/alerts");
  });

  it("a forward whose new home is behind a feature that is off keeps the old page (the Coach without the AI Copilot)", () => {
    const off = { signedIn: true, on: (f: string) => f !== "ai_copilot" && on(f) };
    expect(v2RedirectFor("/analytics/coach", "", off)).toBeNull();
    expect(shellKeyFromPath("/analytics/coach")).toBe("coach");
    expect(v2RedirectFor("/coach", "", off)).toBe("/analytics/coach");
    for (const m of V2_MOVED.filter((x) => !x.when)) expect(v2RedirectFor(m.from, "", off), m.from).toBe(m.to);
  });

  it("with ui_v2 off, every new address goes back to today's app (never 'This page does not exist')", () => {
    for (const [from, to] of TABLE) {
      if (from === to) continue;
      const back = flagOffFallback(to);
      // A Copilot tab and /signals are addresses today's app knows (no fallback needed).
      if (to.startsWith("/copilot") || to === "/signals") { expect(back, to).toBeNull(); continue; }
      expect(back, to).not.toBeNull();
    }
  });
});

describe("M60: search finds every page", () => {
  const operator: NavContext = { on, role: "SUPER_ADMIN" };
  it("each page in the navigation is found by its label and by the words of before", () => {
    for (const it of allNavItems().filter((i) => isItemVisible(i, operator))) {
      expect(searchPages(it.label, operator).map((i) => i.key), it.label).toContain(it.key);
    }
    const words: [string, ShellKey][] = [["performance", "analytics"], ["trading bot", "deployments"], ["broker", "broker"],
      ["brief", "copilot-read"], ["news", "news-events"], ["idea library", "ideas"], ["glossary", "learn-words"], ["charts", "charts"],
      ["watchlist", "watchlist"], ["option chain", "option-chain"], ["inbox", "notifications"], ["coach", "copilot-coach"]];
    for (const [q, key] of words) expect(searchPages(q, operator).map((i) => i.key), q).toContain(key);
  });
});
