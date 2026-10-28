import { describe, expect, it } from "vitest";
import { PAGES } from "../routes";
import {
  BOTTOM_BAR_V2, NAV_EXTRAS_V2, NAV_PINNED_V2, NAV_SECTIONS_V2, allNavItems, isItemVisible, moreButtonDot, moreSheetPinned,
  moreSheetSections, navDotFor, navEntryFor, navItemFor, searchPages, sectionOf, visibleBottomBar, visibleSections, visiblePinned, type NavContext,
} from "./navModel";
import { brokerDot } from "../broker/brokerState";
import type { BrokerConnection } from "../types";
import { shellKeyFromPath } from "./shellRoutes";

const flags = (...on: string[]) => (f: string) => on.includes(f);
const trader: NavContext = { on: flags(), role: "OWNER" };
const everything: NavContext = { on: flags("ai_copilot", "discipline_engine", "marketplace", "options_builder", "screener_v2"), role: "SUPER_ADMIN" };
// UX1 (audit P06): Find setups is in the navigation only while `screener_v2` is on.
const withScreener: NavContext = { on: flags("screener_v2"), role: "OWNER" };
const labels = (ctx: NavContext) => visibleSections(ctx).map((s) => [s.title, s.items.map((i) => i.label)]);

describe("UX3-9 navigation model: the 13-entry sidebar (UX audit section 5)", () => {
  const entries = (ctx: NavContext) => [...visiblePinned(ctx), ...visibleSections(ctx).flatMap((s) => s.items)].map((i) => i.label);

  it("lists the audit's 13 entries in task order, Broker first", () => {
    expect(entries({ on: flags("ai_copilot", "screener_v2"), role: "OWNER" })).toEqual([
      "Broker", "Today", "Charts", "Option chain", "Find setups", "Signals", "Paper bot", "My trades", "Performance",
      "Strategies", "AI Copilot", "News & events", "Settings",
    ]);
  });

  it("groups them for the More sheet and search captions; the admin area is its own group", () => {
    expect(labels(everything)).toEqual([
      ["Markets", ["Today", "Charts", "Option chain", "Find setups"]],
      ["Trading", ["Signals", "Paper bot", "My trades"]],
      ["Insights", ["Performance", "Strategies", "AI Copilot", "News & events"]],
      ["Account", ["Settings"]],
      ["Admin", ["Admin console"]],
    ]);
    // MO4 slice 2: Sectors & indices sits with the markets pages while `markets_view` is on.
    expect(entries({ on: flags("markets_view"), role: "OWNER" }).slice(0, 5)).toEqual(["Broker", "Today", "Charts", "Option chain", "Sectors & indices"]);
  });

  it("a trader with no feature on sees 10 entries; nothing technical", () => {
    expect(entries(trader)).toEqual([
      "Broker", "Today", "Charts", "Option chain", "Signals", "Paper bot", "My trades", "Performance", "Strategies",
      "News & events", "Settings",
    ].filter(Boolean));
    for (const gone of ["Factor Lab", "Contract specs", "Activity log", "Company research", "Team", "Notifications", "Risk", "Orders", "Portfolio"]) {
      expect(entries(everything), gone).not.toContain(gone);
    }
  });

  it("leaves no page unreachable: every routed page is a sidebar item or a searchable extra", () => {
    const keys = new Set(allNavItems().map((i) => i.key));
    for (const p of PAGES) expect(keys.has(p), p).toBe(true);
    expect(keys.has("charts")).toBe(true);
    expect(new Set(allNavItems().map((i) => i.key)).size).toBe(allNavItems().length);   // listed once each
  });

  it("every entry's address opens that entry in the v2 shell", () => {
    for (const it of allNavItems()) {
      const key = shellKeyFromPath(it.path);
      expect(key, it.path).toBe(it.key);
    }
  });

  it("finds the group of the page on screen", () => {
    expect(sectionOf("dashboard")).toBe("markets");
    expect(sectionOf("charts")).toBe("markets");
    expect(sectionOf("signals")).toBe("trading");
    expect(sectionOf("admin")).toBe("admin");
    expect(sectionOf("scanner")).toBe("markets");          // an extra keeps its caption
    expect(sectionOf("backtest")).toBe("strategies");
    expect(sectionOf(null)).toBeNull();
    expect(navItemFor("analytics")?.title).toBe("How am I doing?");
  });

  it("a merged page's tabs and Settings' sub-pages belong to their entry", () => {
    expect(["orders", "portfolio", "positions"].map((k) => navEntryFor(k as never))).toEqual(["positions", "positions", "positions"]);
    expect(["ideas", "strategy-builder", "backtest"].map((k) => navEntryFor(k as never))).toEqual(["strategies", "strategies", "strategies"]);
    expect(["journal", "coach"].map((k) => navEntryFor(k as never))).toEqual(["analytics", "analytics"]);
    expect(["risk-management", "team", "account", "setup", "settings-alerts"].map((k) => navEntryFor(k as never))).toEqual(
      ["settings", "settings", "settings", "settings", "settings"]);
    expect(navEntryFor("signals")).toBe("signals");
    expect(navEntryFor(null)).toBeNull();
    // Every entry they point at is a sidebar entry.
    const sidebar = new Set(NAV_SECTIONS_V2.flatMap((s) => s.items.map((i) => i.key)));
    for (const it of allNavItems()) expect(it.key === "broker" || sidebar.has(navEntryFor(it.key)!) || NAV_EXTRAS_V2.includes(it as never), it.key).toBe(true);
    // One title per merged page: the tab bar names the tab.
    expect(["positions", "orders", "portfolio"].map((k) => navItemFor(k as never)?.title)).toEqual(["My trades", "My trades", "My trades"]);
    expect(["strategies", "ideas", "strategy-builder", "backtest"].map((k) => navItemFor(k as never)?.title)).toEqual(Array(4).fill("Strategies"));
    expect(["analytics", "journal", "coach"].map((k) => navItemFor(k as never)?.title)).toEqual(Array(3).fill("How am I doing?"));
  });
});

describe("UI2-1 navigation model: what a trader may see", () => {
  it("hides a page while its feature flag is off (fail closed: unknown is off)", () => {
    const shown = allNavItems().filter((i) => isItemVisible(i, trader)).map((i) => i.label);
    for (const hidden of ["AI Copilot", "Journal", "Marketplace", "Find setups", "Sectors & indices", "Ideas"]) expect(shown, hidden).not.toContain(hidden);
    for (const always of ["Today", "Charts", "Signals", "My trades", "Factor Lab", "Contract specs", "Activity log", "Team"]) {
      expect(shown, always).toContain(always);
    }
    const withJournal = allNavItems().filter((i) => isItemVisible(i, { ...trader, on: flags("discipline_engine") })).map((i) => i.label);
    expect(withJournal).toContain("Journal");
    expect(withJournal).not.toContain("AI Copilot");
  });

  it("shows the Admin console to the platform operator (SUPER_ADMIN) only", () => {
    for (const role of ["OWNER", "ADMIN", "TRADER", "VIEWER", null]) {
      const items = visibleSections({ on: flags("marketplace"), role }).flatMap((s) => s.items.map((i) => i.label));
      expect(items, String(role)).not.toContain("Admin console");
    }
    expect(visibleSections({ on: flags(), role: "SUPER_ADMIN" }).flatMap((s) => s.items.map((i) => i.label))).toContain("Admin console");
    expect(isItemVisible({ role: "SUPER_ADMIN" }, { on: flags(), role: "super_admin" })).toBe(false);   // exact role only
  });

  it("drops a section left empty", () => {
    const admin = NAV_SECTIONS_V2.find((s) => s.id === "admin")!;
    expect(visibleSections(trader, [admin])).toEqual([]);
  });
});

describe("UX3-9 bottom bar and More sheet (UX audit section 5)", () => {
  it("the bar is Today, Charts, Signals, My trades (More is the fifth button)", () => {
    expect(visibleBottomBar(trader).map((b) => b.label)).toEqual(["Today", "Charts", "Signals", "My trades"]);
    expect(visibleBottomBar().map((b) => b.label)).toEqual(["Today", "Charts", "Signals", "My trades"]);
    expect(BOTTOM_BAR_V2.map((b) => navItemFor(b.key)?.path)).toEqual(["/", "/charts", "/signals", "/positions"]);   // UI-28 U3.5
  });

  it("UI-28 U3.5: Watchlist is not on the bar (four tabs and More); with `watchlists` on it is in the More sheet", () => {
    const watch = { on: flags("watchlists"), role: "OWNER" };
    expect(visibleBottomBar(watch).map((b) => b.label)).toEqual(["Today", "Charts", "Signals", "My trades"]);
    expect(moreSheetSections(watch).flatMap((s) => s.items.map((i) => i.key))).toContain("watchlist");
    expect(moreSheetSections(trader).flatMap((s) => s.items.map((i) => i.key))).not.toContain("watchlist");
    expect(navItemFor("watchlist")).toMatchObject({ label: "Watchlist", path: "/watchlist", flag: "watchlists", section: "markets" });
    expect(isItemVisible(navItemFor("watchlist")!, trader)).toBe(false);
    expect(searchPages("watch", watch).map((i) => i.key)).toContain("watchlist");
    expect(searchPages("watch", trader).map((i) => i.key)).not.toContain("watchlist");
  });

  it("the sheet lists every other sidebar entry, grouped, Markets first and Admin last", () => {
    const sheet = moreSheetSections(trader);
    expect(sheet.map((s) => s.title)).toEqual(["Markets", "Trading", "Insights", "Account"]);
    expect(sheet[0].items.map((i) => i.label)).toEqual(["Option chain"]);                    // screener_v2 off
    expect(moreSheetSections(withScreener)[0].items.map((i) => i.label)).toEqual(["Option chain", "Find setups"]);
    expect(sheet[1].items.map((i) => i.label)).toEqual(["Paper bot"]);
    expect(sheet[2].items.map((i) => i.label)).toEqual(["Performance", "Strategies", "News & events"]);
    const all = sheet.flatMap((s) => s.items.map((i) => i.key));
    for (const inBar of BOTTOM_BAR_V2) expect(all).not.toContain(inBar.key);
    expect(all).not.toContain("admin");
    const withOperator = moreSheetSections(everything);
    expect(withOperator[withOperator.length - 1]).toMatchObject({ title: "Admin" });
    expect(withOperator[withOperator.length - 1].items.map((i) => i.label)).toEqual(["Admin console"]);
  });
});

describe("UI2-1 page search (More sheet and command bar)", () => {
  const find = (q: string, ctx = trader) => searchPages(q, ctx).map((i) => i.label);

  it("finds pages by their label, title, today's name or section", () => {
    expect(find("find", withScreener)[0]).toBe("Find setups");
    expect(find("screener", withScreener)[0]).toBe("Find setups");    // today's name
    expect(find("find setups")).not.toContain("Find setups");          // UX1: hidden while screener_v2 is off
    expect(find("backtesting")).toEqual(["Test a strategy"]);       // a merged page's tab is still found by its old name
    expect(find("portfolio")).toEqual(["Money"]);
    expect(find("orders")[0]).toBe("Orders");
    expect(find("how am i")).toEqual(["Performance", "Coach"]);        // the page title (Performance and its tabs)
    expect(find("market scanner")).toEqual(["Custom scan"]);           // an extra (merged page)
    expect(find("option chain")[0]).toBe("Option chain");
    expect(find("  OPTION   chain ")[0]).toBe("Option chain");
    expect(find("system logs")).toEqual(["Activity log"]);
  });

  it("ranks a label that starts with the query first", () => {
    const p = find("p");
    expect(p.indexOf("Paper bot")).toBeLessThan(p.indexOf("My trades"));      // label start before a keyword
    expect(p.indexOf("Performance")).toBeLessThan(p.indexOf("Money"));        // label start before a keyword
    expect(find("my t")[0]).toBe("My trades");
    expect(find("risk")[0]).toBe("Risk limits");
  });

  it("never offers a hidden page", () => {
    expect(find("journal")).toEqual([]);
    expect(find("journal", { ...trader, on: flags("discipline_engine") })).toEqual(["Journal"]);
    expect(find("admin")).toEqual([]);
    expect(find("admin", { on: flags(), role: "SUPER_ADMIN" })).toEqual(["Admin console"]);
    expect(find("plan an options")).toEqual([]);                       // options_builder off
  });

  it("an empty query lists every visible page; nonsense lists none", () => {
    expect(searchPages("", trader).length).toBe(allNavItems().filter((i) => isItemVisible(i, trader)).length);
    expect(find("zzzz")).toEqual([]);
  });

  it("the merged pages' extras live at their new addresses", () => {
    expect(NAV_EXTRAS_V2.map((e) => [e.key, e.path])).toEqual([
      ["scanner", "/screener/custom"], ["options-builder", "/option-chain/plan"], ["chart-analyze", "/chart-analyze"],
      // UX3-9: the merged pages' tabs.
      ["orders", "/positions/orders"], ["portfolio", "/positions/money"], ["ideas", "/ideas"], ["strategy-builder", "/strategies/build"],
      ["backtest", "/strategies/test"], ["journal", "/analytics/journal"], ["coach", "/analytics/coach"], ["fundamentals", "/fundamentals"],
      ["risk-management", "/risk-management"], ["team", "/team"], ["notifications", "/notifications"],
      ["account", "/settings/account"], ["setup", "/settings/setup"], ["trading-defaults", "/settings/trading-defaults"],
      // Settings redesign B: the Settings section pages.
      ["settings-ai", "/settings/ai"], ["settings-alerts", "/settings/alerts"], ["settings-appearance", "/settings/appearance"],
      ["settings-help", "/settings/help"], ["settings-advanced", "/settings/advanced"],
      // UX audit section 5 "Drop from the user navigation": found by search.
      ["quant", "/quant"], ["instruments", "/instruments"], ["marketplace", "/marketplace"], ["system-logs", "/system-logs"],
    ]);
  });

  it("UI2-7: Settings → Setup is found by search, not listed in the sidebar or the More sheet", () => {
    const ctx = { on: () => true, role: "OWNER" };
    expect(searchPages("setup", ctx)[0]).toMatchObject({ key: "setup", title: "Setup", section: "account" });
    expect(searchPages("go-live", ctx).map((i) => i.key)).toContain("setup");
    expect(moreSheetSections(ctx).flatMap((s) => s.items).map((i) => i.key)).not.toContain("setup");
  });
});

describe("P2b Broker first, with its status dot", () => {
  const conn = (status: BrokerConnection["status"], data_only = false): BrokerConnection =>
    ({ broker: "upstox", account_label: "primary", name: "Upstox", status, message: "", data_only, renew: true });

  it("Broker is the first entry: pinned above the sections, for every role, at /broker", () => {
    expect(NAV_PINNED_V2.map((i) => [i.key, i.label, i.path])).toEqual([["broker", "Broker", "/broker"]]);
    for (const ctx of [trader, everything, { on: flags(), role: "VIEWER" }]) expect(visiblePinned(ctx).map((i) => i.key)).toEqual(["broker"]);
    expect(allNavItems()[0].key).toBe("broker");
    expect(shellKeyFromPath("/broker")).toBe("broker");
    expect(navItemFor("broker")?.title).toBe("Broker");
    expect(sectionOf("broker")).toBeNull();                    // always shown: no section to open
  });

  it("on a phone it is the first tile of the More sheet, above every section", () => {
    expect(moreSheetPinned(trader).map((i) => i.key)).toEqual(["broker"]);
    expect(moreSheetSections(trader).flatMap((s) => s.items.map((i) => i.key))).not.toContain("broker");
  });

  it("search finds it first by name and by today's words; Settings no longer claims 'brokers'", () => {
    expect(searchPages("broker", trader)[0].key).toBe("broker");
    expect(searchPages("connect broker", trader).map((i) => i.key)).toEqual(["broker"]);
    expect(searchPages("renew", trader)[0].key).toBe("broker");
  });

  it("the dot: green connected, amber needs login, red not working - on Broker only; More carries amber and red", () => {
    const cases: [BrokerConnection[] | null, string | null, string | null][] = [
      [null, null, null],                                         // not known: nothing
      [[], null, null],                                           // none: no dot (the prompt asks instead)
      [[conn("connected")], "connected", null],
      [[conn("needs_login", true)], "connected", null],           // analytics-only Upstox: connected
      [[conn("needs_login")], "needs_login", "needs_login"],
      [[conn("connected"), conn("not_working")], "not_working", "not_working"],
    ];
    for (const [rows, dot, more] of cases) {
      const d = brokerDot(rows);
      expect(navDotFor("broker", d), JSON.stringify(rows)).toBe(dot);
      expect(navDotFor("dashboard", d)).toBeNull();
      expect(moreButtonDot(d)).toBe(more);
    }
  });
});
