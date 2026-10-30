import { describe, expect, it } from "vitest";
import { COPILOT_V2_TABS } from "../copilot/tabs";
import { tourIdFor } from "../tours/pages";
import { navEntryFor, navItemFor, type NavContext } from "./navModel";
import { MAX_TABS, PAGE_GROUPS_V2, pageGroupOf, tabOf, visibleParts, visibleTabs } from "./pageGroups";
import { shellKeyFromPath, v2RedirectFor } from "./shellRoutes";

const flags = (...on: string[]) => (f: string) => on.includes(f);
const trader: NavContext = { on: flags(), role: "OWNER" };
const full: NavContext = { on: flags("ai_copilot", "learn", "discipline_engine", "idea_library"), role: "OWNER" };
const group = (id: string) => PAGE_GROUPS_V2.find((g) => g.id === id)!;
const tabs = (id: string, ctx: NavContext = trader) => visibleTabs(group(id), ctx).map((t) => [t.label, t.path]);

describe("M60 tab rows: at most four tabs each (and four parts under a tab)", () => {
  it("no row has more than four tabs - the Copilot's own row included", () => {
    for (const g of PAGE_GROUPS_V2) {
      expect(g.tabs.length, g.id).toBeLessThanOrEqual(MAX_TABS);
      for (const t of g.tabs) expect((t.parts ?? []).length, `${g.id} ${t.key}`).toBeLessThanOrEqual(MAX_TABS);
    }
    expect(COPILOT_V2_TABS.length).toBeLessThanOrEqual(MAX_TABS);
  });

  it("My trades: Positions · Orders · Money · Results; Results holds Summary · Journal (default 2)", () => {
    expect(tabs("my-trades", full)).toEqual([["Positions", "/positions"], ["Orders", "/positions/orders"], ["Money", "/positions/money"],
      ["Results", "/positions/results"]]);
    const results = tabOf(group("my-trades"), "journal")!;
    expect(results.label).toBe("Results");
    expect(visibleParts(results, full).map((p) => [p.label, p.path])).toEqual([["Summary", "/positions/results"], ["Journal", "/positions/results/journal"]]);
    // While the AI Copilot is off, the Coach stays a part of Results (its home there is not on).
    expect(visibleParts(results, trader).map((p) => p.label)).toEqual(["Summary", "Coach"]);
  });

  it("Strategies: Library · Build · Test (Ideas moved to Learn)", () => {
    expect(tabs("strategies", full)).toEqual([["Library", "/strategies"], ["Build", "/strategies/build"], ["Test", "/strategies/test"]]);
  });

  it("Learn: Lessons · Words explained · Ideas · News & events (each behind its own feature)", () => {
    expect(tabs("learn", full)).toEqual([["Lessons", "/learn"], ["Words explained", "/learn/words"], ["Ideas", "/learn/ideas"],
      ["News & events", "/learn/news"]]);
    expect(tabs("learn", trader)).toEqual([["News & events", "/learn/news"]]);
  });

  it("Settings: Account & broker · Trading · AI · App, the index showing the row with no tab marked", () => {
    const settings = group("settings");
    expect(tabs("settings", full).map((t) => t[0])).toEqual(["Account & broker", "Trading", "AI", "App"]);
    expect(visibleParts(tabOf(settings, "broker")!, full).map((p) => p.label)).toEqual(["Account & plan", "Broker accounts", "Team", "Setup"]);
    // Default 5: the alert channels in Settings › Trading, with the Inbox link beside them.
    expect(visibleParts(tabOf(settings, "notifications")!, full).map((p) => [p.label, p.path])).toEqual([
      ["My trading defaults", "/settings/trading-defaults"], ["Risk limits", "/risk-management"], ["Alerts", "/settings/alerts"],
      ["Inbox", "/notifications"]]);
    expect(visibleParts(tabOf(settings, "settings-help")!, full).map((p) => p.label)).toEqual(["Appearance", "Help & tips", "Advanced"]);
    expect(pageGroupOf("settings")?.id).toBe("settings");
    expect(tabOf(settings, "settings")).toBeNull();
  });

  it("each tab's (and part's) address opens it, and lights up the group's sidebar entry", () => {
    for (const g of PAGE_GROUPS_V2) {
      for (const t of g.tabs) {
        for (const k of [t.key, ...(t.parts ?? []).map((p) => p.key)]) {
          expect(shellKeyFromPath(navItemFor(k)!.path), k).toBe(k);
          expect(pageGroupOf(k)?.id, k).toBe(g.id);
          expect(navEntryFor(k), k).toBe(g.home);
        }
      }
    }
    expect(pageGroupOf("signals")).toBeNull();
    expect(pageGroupOf(null)).toBeNull();
  });

  it("the page tours keep their pages: the old addresses forward to the tab that holds the same page", () => {
    const at = (old: string) => shellKeyFromPath(v2RedirectFor(old, "", { signedIn: true, on: () => true }) ?? old);
    expect(tourIdFor(at("/portfolio"), "/positions/money")).toBe("portfolio");
    expect(tourIdFor(at("/positions"), "/positions")).toBe("positions");
    expect(tourIdFor(at("/risk-management"), "/risk-management")).toBe("risk");
    expect(tourIdFor(at("/broker"), "/settings/broker")).toBe("broker");
  });
});
