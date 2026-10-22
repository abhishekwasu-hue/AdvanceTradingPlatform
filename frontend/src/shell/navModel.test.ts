import { describe, expect, it } from "vitest";
import { PAGES } from "../routes";
import {
  BOTTOM_BAR_V2, NAV_EXTRAS_V2, NAV_PINNED_V2, NAV_SECTIONS_V2, allNavItems, isItemVisible, moreButtonDot, moreSheetPinned,
  moreSheetSections, navDotFor, navItemFor, searchPages, sectionOf, visibleSections, visiblePinned, type NavContext,
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

describe("UI2-1 navigation model: sections (proposal §2)", () => {
  it("has the five sections plus More, in order, with 4 / 6 / 5 / 4 / 3 items - 22 primary items (Ideas: idea library slice 1)", () => {
    expect(NAV_SECTIONS_V2.map((s) => s.title)).toEqual(["Markets", "Strategies", "Trading", "Insights", "Account", "More"]);
    expect(NAV_SECTIONS_V2.slice(0, 5).map((s) => s.items.length)).toEqual([4, 6, 5, 4, 3]);
    expect(NAV_SECTIONS_V2.slice(0, 5).flatMap((s) => s.items)).toHaveLength(22);
    expect(NAV_SECTIONS_V2[5].items.map((i) => i.label)).toEqual(["Factor Lab", "Contract specs", "Marketplace", "Activity log", "Admin console"]);
  });

  it("uses the plain-language labels (§3 renames) and puts Signals in Trading (UI-2)", () => {
    expect(labels(everything)).toEqual([
      ["Markets", ["Dashboard", "Charts", "Option chain", "Find setups"]],
      ["Strategies", ["Library", "Build a strategy", "Test a strategy", "AI Copilot", "Paper bot"]],
      ["Trading", ["Signals", "My positions", "Orders", "Portfolio", "Risk"]],
      ["Insights", ["Performance", "Journal", "News & events", "Company research"]],
      ["Account", ["Settings", "Team", "Notifications"]],
      ["More", ["Factor Lab", "Contract specs", "Marketplace", "Activity log", "Admin console"]],
    ]);
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
      // /coach is Coach and guide; /copilot opens the Copilot's default tab.
      expect(key, it.path).toBe(it.key);
    }
  });

  it("finds the section of the page on screen", () => {
    expect(sectionOf("dashboard")).toBe("markets");
    expect(sectionOf("charts")).toBe("markets");
    expect(sectionOf("signals")).toBe("trading");
    expect(sectionOf("admin")).toBe("more");
    expect(sectionOf("scanner")).toBe("markets");          // an extra keeps its future section
    expect(sectionOf(null)).toBeNull();
    expect(navItemFor("analytics")?.title).toBe("How am I doing?");
  });
});

describe("UI2-1 navigation model: what a trader may see", () => {
  it("hides a page while its feature flag is off (fail closed: unknown is off)", () => {
    const shown = visibleSections(trader).flatMap((s) => s.items.map((i) => i.label));
    for (const hidden of ["AI Copilot", "Journal", "Marketplace"]) expect(shown, hidden).not.toContain(hidden);
    for (const always of ["Dashboard", "Charts", "Signals", "My positions", "Factor Lab", "Contract specs", "Activity log", "Team"]) {
      expect(shown, always).toContain(always);
    }
    const withJournal = visibleSections({ ...trader, on: flags("discipline_engine") }).flatMap((s) => s.items.map((i) => i.label));
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
    const sections = visibleSections(trader, [{ id: "more", title: "More", items: [NAV_SECTIONS_V2[5].items[4]] }]);
    expect(sections).toEqual([]);
  });
});

describe("UI2-1 bottom bar and More sheet (proposal §4)", () => {
  it("the bar is Home, Charts, Signals, Positions (More is the fifth button)", () => {
    expect(BOTTOM_BAR_V2.map((b) => b.label)).toEqual(["Home", "Charts", "Signals", "Positions"]);
    expect(BOTTOM_BAR_V2.map((b) => navItemFor(b.key)?.path)).toEqual(["/", "/charts", "/signals", "/positions"]);
  });

  it("the sheet lists every other visible page, grouped by section, Markets first and More last", () => {
    const sheet = moreSheetSections(trader);
    expect(sheet.map((s) => s.title)).toEqual(["Markets", "Strategies", "Trading", "Insights", "Account", "More"]);
    expect(sheet[0].items.map((i) => i.label)).toEqual(["Option chain"]);                    // screener_v2 off
    expect(moreSheetSections(withScreener)[0].items.map((i) => i.label)).toEqual(["Option chain", "Find setups"]);
    expect(sheet[2].items.map((i) => i.label)).toEqual(["Orders", "Portfolio", "Risk"]);
    const all = sheet.flatMap((s) => s.items.map((i) => i.key));
    for (const inBar of BOTTOM_BAR_V2) expect(all).not.toContain(inBar.key);
    expect(all).not.toContain("admin");
    const withOperator = moreSheetSections(everything);
    expect(withOperator[withOperator.length - 1].items.map((i) => i.label)).toContain("Admin console");
  });
});

describe("UI2-1 page search (More sheet and command bar)", () => {
  const find = (q: string, ctx = trader) => searchPages(q, ctx).map((i) => i.label);

  it("finds pages by their label, title, today's name or section", () => {
    expect(find("find", withScreener)[0]).toBe("Find setups");
    expect(find("screener", withScreener)[0]).toBe("Find setups");    // today's name
    expect(find("find setups")).not.toContain("Find setups");          // UX1: hidden while screener_v2 is off
    expect(find("backtesting")).toEqual(["Test a strategy"]);
    expect(find("how am i")).toEqual(["Performance"]);                 // the page title
    expect(find("market scanner")).toEqual(["Custom scan"]);           // an extra (merged page)
    expect(find("option chain")[0]).toBe("Option chain");
    expect(find("  OPTION   chain ")[0]).toBe("Option chain");
    expect(find("system logs")).toEqual(["Activity log"]);
  });

  it("ranks a label that starts with the query first", () => {
    const p = find("p");
    expect(p.indexOf("Portfolio")).toBeLessThan(p.indexOf("My positions"));   // label start before a later word
    expect(find("port")[0]).toBe("Portfolio");
    expect(find("risk")[0]).toBe("Risk");
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
      ["scanner", "/screener/custom"], ["options-builder", "/option-chain/plan"], ["chart-analyze", "/chart-analyze"], ["coach", "/coach"],
      ["account", "/settings/account"], ["setup", "/settings/setup"], ["trading-defaults", "/settings/trading-defaults"],
      // Settings redesign B: the Settings section pages.
      ["settings-ai", "/settings/ai"], ["settings-alerts", "/settings/alerts"], ["settings-appearance", "/settings/appearance"],
      ["settings-help", "/settings/help"], ["settings-advanced", "/settings/advanced"],
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
