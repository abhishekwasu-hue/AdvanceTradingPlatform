import { describe, expect, it } from "vitest";
import { tourIdFor } from "../tours/pages";
import { navEntryFor, navItemFor, type NavContext } from "./navModel";
import { PAGE_GROUPS_V2, pageGroupOf, visibleTabs } from "./pageGroups";
import { shellKeyFromPath, v2RedirectFor } from "./shellRoutes";

const flags = (...on: string[]) => (f: string) => on.includes(f);
const trader: NavContext = { on: flags(), role: "OWNER" };
const tabs = (id: string, ctx: NavContext = trader) => visibleTabs(PAGE_GROUPS_V2.find((g) => g.id === id)!, ctx).map((t) => [t.label, t.path]);

describe("UX3-9 merged pages (UX audit section 5)", () => {
  it("My trades: Positions · Orders · Money", () => {
    expect(tabs("my-trades")).toEqual([["Positions", "/positions"], ["Orders", "/positions/orders"], ["Money", "/positions/money"]]);
  });

  it("Strategies: Library · Ideas · Build · Test (Ideas while idea_library is on)", () => {
    expect(tabs("strategies")).toEqual([["Library", "/strategies"], ["Build", "/strategies/build"], ["Test", "/strategies/test"]]);
    expect(tabs("strategies", { ...trader, on: flags("idea_library") }).map((t) => t[0])).toEqual(["Library", "Ideas", "Build", "Test"]);
  });

  it("Performance: Results · Journal · Coach (Journal while discipline_engine is on)", () => {
    expect(tabs("performance")).toEqual([["Results", "/analytics"], ["Coach", "/analytics/coach"]]);
    expect(tabs("performance", { ...trader, on: flags("discipline_engine") }).map((t) => t[0])).toEqual(["Results", "Journal", "Coach"]);
  });

  it("each tab's address opens that tab, and its group's first tab is its sidebar entry", () => {
    for (const g of PAGE_GROUPS_V2) {
      expect(g.tabs[0].key).toBe(g.home);
      for (const t of g.tabs) {
        expect(shellKeyFromPath(navItemFor(t.key)!.path), t.key).toBe(t.key);
        expect(pageGroupOf(t.key)?.id).toBe(g.id);
        expect(navEntryFor(t.key)).toBe(g.home);
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
  });
});
