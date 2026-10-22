import { isItemVisible, navItemFor, type NavContext, type ShellKey } from "./navModel";

/**
 * UX3-9 (UX audit section 5 "Merge"): the pages that became tabs of one sidebar entry. Each tab is today's page, moved
 * as it is (nothing is rebuilt or removed); the shell shows the tab bar above it. The old addresses forward to the new
 * ones while `ui_v2` is on (shellRoutes.ts v2RedirectFor) and the new ones go back with it off (aliases.ts).
 *
 * - My trades: Positions (open and closed trades) · Orders · Money (the portfolio).
 * - Strategies: Library · Ideas (flag `idea_library`) · Build · Test.
 * - Performance: Results · Journal (flag `discipline_engine`) · Coach.
 */

export type PageGroupId = "my-trades" | "strategies" | "performance";

export interface PageTabV2 {
  key: ShellKey;
  label: string;
}

export interface PageGroupV2 {
  id: PageGroupId;
  /** The sidebar entry the group lives under (its first tab). */
  home: ShellKey;
  /** The tab bar's accessible name. */
  label: string;
  tabs: readonly PageTabV2[];
}

export const PAGE_GROUPS_V2: readonly PageGroupV2[] = [
  { id: "my-trades", home: "positions", label: "My trades", tabs: [
    { key: "positions", label: "Positions" }, { key: "orders", label: "Orders" }, { key: "portfolio", label: "Money" },
  ] },
  { id: "strategies", home: "strategies", label: "Strategies", tabs: [
    { key: "strategies", label: "Library" }, { key: "ideas", label: "Ideas" }, { key: "strategy-builder", label: "Build" },
    { key: "backtest", label: "Test" },
  ] },
  { id: "performance", home: "analytics", label: "Performance", tabs: [
    { key: "analytics", label: "Results" }, { key: "journal", label: "Journal" }, { key: "coach", label: "Coach" },
  ] },
];

/** The group a page is a tab of, or null. */
export function pageGroupOf(key: ShellKey | null): PageGroupV2 | null {
  if (key === null) return null;
  return PAGE_GROUPS_V2.find((g) => g.tabs.some((t) => t.key === key)) ?? null;
}

/** The tabs this trader may see (a tab whose feature is off is left out, like a sidebar entry), with their addresses. */
export function visibleTabs(group: PageGroupV2, ctx: NavContext): (PageTabV2 & { path: string })[] {
  return group.tabs.flatMap((t) => {
    const nav = navItemFor(t.key);
    if (!nav || !isItemVisible(nav, ctx)) return [];
    return [{ ...t, path: nav.path }];
  });
}
