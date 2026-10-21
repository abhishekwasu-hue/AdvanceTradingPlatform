import { describe, expect, it } from "vitest";
import { PAGES } from "../routes";
import {
  BOTTOM_BAR_V2, NAV_EXTRAS_V2, NAV_SECTIONS_V2, allNavItems, isItemVisible, moreSheetSections, navItemFor, searchPages,
  sectionOf, visibleSections, type NavContext,
} from "./navModel";
import { shellKeyFromPath } from "./shellRoutes";

const flags = (...on: string[]) => (f: string) => on.includes(f);
const trader: NavContext = { on: flags(), role: "OWNER" };
const everything: NavContext = { on: flags("ai_copilot", "discipline_engine", "marketplace", "options_builder"), role: "SUPER_ADMIN" };
const labels = (ctx: NavContext) => visibleSections(ctx).map((s) => [s.title, s.items.map((i) => i.label)]);

describe("UI2-1 navigation model: sections (proposal §2)", () => {
  it("has the five sections plus More, in order, with 4 / 5 / 5 / 4 / 3 items - 21 primary items", () => {
    expect(NAV_SECTIONS_V2.map((s) => s.title)).toEqual(["Markets", "Strategies", "Trading", "Insights", "Account", "More"]);
    expect(NAV_SECTIONS_V2.slice(0, 5).map((s) => s.items.length)).toEqual([4, 5, 5, 4, 3]);
    expect(NAV_SECTIONS_V2.slice(0, 5).flatMap((s) => s.items)).toHaveLength(21);
    expect(NAV_SECTIONS_V2[5].items.map((i) => i.label)).toEqual(["Factor Lab", "Contract specs", "Marketplace", "Activity log", "Admin console"]);
  });

  it("uses the plain-language labels (§3 renames) and puts Signals in Trading (UI-2)", () => {
    expect(labels(everything)).toEqual([
      ["Markets", ["Dashboard", "Charts", "Option chain", "Find setups"]],
      ["Strategies", ["Library", "Build a strategy", "Test a strategy", "AI Copilot", "Autopilot"]],
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
    expect(sheet[0].items.map((i) => i.label)).toEqual(["Option chain", "Find setups"]);
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
    expect(find("find")[0]).toBe("Find setups");
    expect(find("screener")[0]).toBe("Find setups");                  // today's name
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
      ["scanner", "/screener/custom"], ["options-builder", "/option-chain/plan"], ["coach", "/coach"], ["account", "/settings/account"],
      ["setup", "/settings/setup"],
    ]);
  });

  it("UI2-7: Settings → Setup is found by search, not listed in the sidebar or the More sheet", () => {
    const ctx = { on: () => true, role: "OWNER" };
    expect(searchPages("setup", ctx)[0]).toMatchObject({ key: "setup", title: "Setup", section: "account" });
    expect(searchPages("go-live", ctx).map((i) => i.key)).toContain("setup");
    expect(moreSheetSections(ctx).flatMap((s) => s.items).map((i) => i.key)).not.toContain("setup");
  });
});
