import { describe, expect, it } from "vitest";
import { PAGES } from "../routes";
import {
  ADMIN_ROLE, BOTTOM_BAR_SIZE, NAV_EXTRAS_V2, NAV_PINNED_V2, NAV_SECTIONS_V2, allNavItems, isCurrentEntry, isItemVisible, moreButtonDot,
  moreSheetPinned, moreSheetSections, navDotFor, navEntryFor, navItemFor, searchPages, sectionOf, visibleBottomBar, visibleSections,
  type NavContext,
} from "./navModel";
import { brokerDot } from "../broker/brokerState";
import type { BrokerConnection } from "../types";
import { shellKeyFromPath } from "./shellRoutes";

/**
 * M60 (owner order, approved 30 Oct with the five defaults; docs/ui/M60_SIDEBAR.html): the sidebar is five short sections
 * - Trade, Find, Build, Learn & AI, Account - with 11 user entries, Admin at the end of Account for the platform operator
 * only; the phone bottom bar is the first four entries plus More; every page is still found by search.
 */
const flags = (...on: string[]) => (f: string) => on.includes(f);
const ALL_ON = ["terminal", "screener_v2", "markets_view", "ai_copilot", "learn", "discipline_engine", "idea_library", "watchlists",
  "options_builder", "chart_analyze", "marketplace"];
const trader: NavContext = { on: flags(), role: "OWNER" };
const full: NavContext = { on: flags(...ALL_ON), role: "OWNER" };
const operator: NavContext = { on: flags(...ALL_ON), role: ADMIN_ROLE };
const labels = (ctx: NavContext) => visibleSections(ctx).map((s) => [s.title, s.items.map((i) => i.label)]);

describe("M60 sidebar: five sections, one feature one home", () => {
  it("with every feature on: 11 user entries in Trade · Find · Build · Learn & AI · Account", () => {
    expect(labels(full)).toEqual([
      ["Trade", ["Today", "Terminal", "My trades"]],
      ["Find", ["Find setups", "Signals", "Sectors & indices"]],
      ["Build", ["Strategies", "AI Trading Bot"]],
      ["Learn & AI", ["AI Copilot", "Learn"]],
      ["Account", ["Settings"]],
    ]);
    expect(visibleSections(full).flatMap((s) => s.items)).toHaveLength(11);
  });

  it("Admin sits at the end of Account for the platform operator only (exact role; the server stays the gate)", () => {
    expect(labels(operator)[labels(operator).length - 1]).toEqual(["Account", ["Settings", "Admin"]]);
    for (const role of ["OWNER", "ADMIN", "TRADER", "VIEWER", "super_admin", null]) {
      const items = visibleSections({ on: flags(...ALL_ON), role }).flatMap((s) => s.items.map((i) => i.key));
      expect(items, String(role)).not.toContain("admin");
      expect(searchPages("admin", { on: flags(...ALL_ON), role }).map((i) => i.key), String(role)).not.toContain("admin");
    }
    expect(searchPages("admin console", operator)[0].key).toBe("admin");
  });

  it("a feature that is off leaves the menu; its stand-in keeps the page in the menu (Charts and Option chain without the Terminal)", () => {
    expect(labels(trader)).toEqual([
      ["Trade", ["Today", "Charts", "Option chain", "My trades"]],
      ["Find", ["Signals"]],
      ["Build", ["Strategies", "AI Trading Bot"]],
      ["Learn & AI", ["News & events"]],
      ["Account", ["Settings"]],
    ]);
    // Learn on: News & events is a Learn tab, so it leaves the menu; Terminal on: Charts and Option chain do.
    expect(labels({ on: flags("learn", "terminal"), role: "OWNER" })[0]).toEqual(["Trade", ["Today", "Terminal", "My trades"]]);
    expect(labels({ on: flags("learn"), role: "OWNER" }).find(([t]) => t === "Learn & AI")).toEqual(["Learn & AI", ["Learn"]]);
  });

  it("section titles are short and each section is listed once; an empty section is dropped", () => {
    for (const s of NAV_SECTIONS_V2) expect(s.title.length, s.title).toBeLessThanOrEqual(10);
    expect(new Set(NAV_SECTIONS_V2.map((s) => s.id)).size).toBe(NAV_SECTIONS_V2.length);
    expect(visibleSections(trader, [{ id: "account", title: "Account", items: [navItemFor("admin")!] }])).toEqual([]);
  });

  it("one feature, one home: every page is listed once (sidebar or search), and every routed page is reachable", () => {
    const keys = allNavItems().map((i) => i.key);
    expect(new Set(keys).size).toBe(keys.length);
    for (const p of PAGES) expect(keys, p).toContain(p);
    for (const k of ["charts", "terminal", "watchlist", "markets", "learn", "learn-words", "ideas", "setup", "trading-defaults"]) expect(keys, k).toContain(k);
    expect(NAV_PINNED_V2).toEqual([]);                       // nothing above the sections any more (Broker is in Settings)
    expect(moreSheetPinned(full)).toEqual([]);
  });

  it("every entry's address opens that page in the v2 shell (the Copilot's tabs open the Copilot)", () => {
    for (const it of allNavItems()) {
      const expected = it.key === "copilot-read" || it.key === "copilot-coach" ? "copilot" : it.key;
      expect(shellKeyFromPath(it.path), it.path).toBe(expected);
    }
  });

  it("a page lights up its home entry: My trades' tabs, the Terminal's parts, Learn's tabs, Settings' pages", () => {
    const home = (...keys: string[]) => keys.map((k) => navEntryFor(k as never));
    expect(home("orders", "portfolio", "analytics", "journal", "coach")).toEqual(Array(5).fill("positions"));
    expect(home("charts", "option-chain", "watchlist", "options-builder")).toEqual(Array(4).fill("terminal"));
    expect(home("learn-words", "ideas", "news-events")).toEqual(Array(3).fill("learn"));
    expect(home("copilot-read", "copilot-coach", "chart-analyze")).toEqual(Array(3).fill("copilot"));
    expect(home("strategy-builder", "backtest")).toEqual(["strategies", "strategies"]);
    expect(home("scanner")).toEqual(["screener"]);
    expect(home("broker", "account", "team", "setup", "trading-defaults", "risk-management", "settings-alerts", "notifications",
      "settings-ai", "settings-appearance", "settings-help", "settings-advanced")).toEqual(Array(12).fill("settings"));
    expect(navEntryFor(null)).toBeNull();
    // A stand-in lights up itself while its home is not listed (Charts while the Terminal is off).
    expect(isCurrentEntry("charts", "charts")).toBe(true);
    expect(isCurrentEntry("terminal", "charts")).toBe(true);
    expect(isCurrentEntry("positions", "charts")).toBe(false);
    // Every home is a sidebar entry.
    const sidebar = new Set(NAV_SECTIONS_V2.flatMap((s) => s.items.map((i) => i.key)));
    for (const it of allNavItems()) {
      const entry = navEntryFor(it.key)!;
      expect(sidebar.has(entry) || NAV_EXTRAS_V2.some((e) => e.key === it.key && e.section === "more"), it.key).toBe(true);
    }
  });

  it("captions: each page names its section", () => {
    expect(sectionOf("dashboard")).toBe("trade");
    expect(sectionOf("signals")).toBe("find");
    expect(sectionOf("deployments")).toBe("build");
    expect(sectionOf("copilot")).toBe("learn");
    expect(sectionOf("admin")).toBe("account");
    expect(sectionOf("analytics")).toBe("trade");
    expect(sectionOf(null)).toBeNull();
    expect(navItemFor("analytics")).toMatchObject({ label: "Results", title: "How am I doing?", path: "/positions/results" });
  });
});

describe("M60 phone bottom bar = the first four entries + More", () => {
  it("every feature on: Today, Terminal, My trades, Find setups", () => {
    expect(visibleBottomBar(full).map((b) => b.label)).toEqual(["Today", "Terminal", "My trades", "Find setups"]);
    expect(BOTTOM_BAR_SIZE).toBe(4);
  });

  it("follows the flags: without the Terminal its stand-ins come first; never the Admin console", () => {
    expect(visibleBottomBar(trader).map((b) => b.label)).toEqual(["Today", "Charts", "Chain", "My trades"]);
    expect(visibleBottomBar().map((b) => b.label)).toEqual(["Today", "Charts", "Chain", "My trades"]);
    expect(visibleBottomBar({ on: flags("terminal"), role: "OWNER" }).map((b) => b.label)).toEqual(["Today", "Terminal", "My trades", "Signals"]);
    expect(visibleBottomBar(operator).map((b) => b.key)).not.toContain("admin");
  });

  it("More lists the rest under the same section titles; the menu button lists every section", () => {
    const sheet = moreSheetSections(full);
    expect(sheet.map((s) => [s.title, s.items.map((i) => i.label)])).toEqual([
      ["Find", ["Signals", "Sectors & indices"]],
      ["Build", ["Strategies", "AI Trading Bot"]],
      ["Learn & AI", ["AI Copilot", "Learn"]],
      ["Account", ["Settings"]],
    ]);
    const bar = visibleBottomBar(full).map((b) => b.key);
    for (const k of bar) expect(sheet.flatMap((s) => s.items.map((i) => i.key))).not.toContain(k);
    expect(moreSheetSections(operator)[moreSheetSections(operator).length - 1].items.map((i) => i.label)).toEqual(["Settings", "Admin"]);
    const all = moreSheetSections(full, true);
    expect(all.flatMap((s) => s.items)).toHaveLength(11);
    expect(all[0].items.map((i) => i.label)).toEqual(["Today", "Terminal", "My trades"]);
  });
});

describe("M60 search finds every page", () => {
  const find = (q: string, ctx = full) => searchPages(q, ctx).map((i) => i.key);

  it("every page a trader may see is found by its own name", () => {
    for (const it of allNavItems().filter((i) => isItemVisible(i, operator))) {
      expect(find(it.label, operator), it.label).toContain(it.key);
    }
  });

  it("moved pages are found by their old names", () => {
    expect(find("performance")[0]).toBe("analytics");
    expect(find("trading bot")[0]).toBe("deployments");
    expect(find("broker")[0]).toBe("broker");
    expect(find("connect broker")).toEqual(["broker"]);
    expect(find("charts")).toContain("charts");                     // the Charts page stays, found by search (Terminal on)
    expect(find("option chain")[0]).toBe("option-chain");
    expect(find("watchlist")).toContain("watchlist");               // /watchlist keeps its page until the Terminal panel
    expect(find("brief")).toContain("copilot-read");
    expect(find("analyze chart")).toContain("chart-analyze");      // default 1: the standalone page stays, searchable
    expect(find("glossary")).toContain("learn-words");
    expect(find("notifications")).toContain("notifications");
    expect(find("backtesting")).toEqual(["backtest"]);
    expect(find("system logs")).toEqual(["system-logs"]);
  });

  it("one Coach: the Copilot's while it is on, today's page while it is off", () => {
    expect(find("coach")).toContain("copilot-coach");
    expect(find("coach")).not.toContain("coach");
    const off = { on: flags(), role: "OWNER" };
    expect(find("coach", off)).toContain("coach");
    expect(find("coach", off)).not.toContain("copilot-coach");
  });

  it("never offers a hidden page", () => {
    expect(find("journal", trader)).toEqual([]);
    expect(find("journal", { ...trader, on: flags("discipline_engine") })).toEqual(["journal"]);
    expect(find("plan an options", trader)).toEqual([]);
    expect(find("zzzz")).toEqual([]);
    expect(searchPages("", trader).length).toBe(allNavItems().filter((i) => isItemVisible(i, trader)).length);
  });
});

describe("M60 the broker's connection dot sits on the Settings row", () => {
  const conn = (status: BrokerConnection["status"], data_only = false): BrokerConnection =>
    ({ broker: "upstox", account_label: "primary", name: "Upstox", status, message: "", data_only, renew: true });

  it("Broker accounts live in Settings, at /settings/broker", () => {
    expect(navItemFor("broker")).toMatchObject({ label: "Broker accounts", path: "/settings/broker", section: "account" });
    expect(shellKeyFromPath("/settings/broker")).toBe("broker");
    expect(shellKeyFromPath("/broker")).toBe("broker");
  });

  it("green connected, amber needs login, red not working - on Settings (and the Broker accounts tile); More carries amber and red", () => {
    const cases: [BrokerConnection[] | null, string | null, string | null][] = [
      [null, null, null], [[], null, null], [[conn("connected")], "connected", null], [[conn("needs_login", true)], "connected", null],
      [[conn("needs_login")], "needs_login", "needs_login"], [[conn("connected"), conn("not_working")], "not_working", "not_working"],
    ];
    for (const [rows, dot, more] of cases) {
      const d = brokerDot(rows);
      expect(navDotFor("settings", d), JSON.stringify(rows)).toBe(dot);
      expect(navDotFor("broker", d)).toBe(dot);
      expect(navDotFor("dashboard", d)).toBeNull();
      expect(moreButtonDot(d)).toBe(more);
    }
  });
});
