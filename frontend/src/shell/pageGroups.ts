import { isItemVisible, navItemFor, type NavContext, type ShellKey } from "./navModel";

/**
 * UX3-9 (UX audit section 5 "Merge") and M60 (owner order, approved 30 Oct): the pages that are tabs of one sidebar
 * entry. Each tab is today's page, moved as it is (nothing is rebuilt or removed); the shell shows the tab bar above it.
 * The old addresses forward to the new ones while `ui_v2` is on (shellRoutes.ts v2RedirectFor) and the new ones go back
 * with it off (aliases.ts). Every tab row has at most 4 tabs (MAX_TABS); a tab may hold parts - a second, smaller row
 * of at most 4 links (M60 default 2: Journal is a part of Results).
 *
 * - My trades: Positions · Orders · Money · Results (parts Summary · Journal; Coach while the AI Copilot is off).
 * - Strategies: Library · Build · Test (Ideas moved to Learn).
 * - Learn: Lessons · Words explained · Ideas · News & events.
 * - Settings: Account & broker · Trading · AI · App (each a few small pages, as parts).
 * The AI Copilot draws its own row (Ask · Read the chart · Analyze a chart · Coach, src/copilot/tabs.ts) and the Terminal
 * its ready layouts.
 */

export type PageGroupId = "my-trades" | "strategies" | "learn" | "settings";

/** M60: at most four tabs in any row (and four parts under a tab). */
export const MAX_TABS = 4;

export interface PageTabV2 {
  /** The tab's own page (its address is where the tab opens, unless `parts` say otherwise). */
  key: ShellKey;
  label: string;
  /** A shorter name on a phone (below md), so four tabs fit a 360 px screen without a word being cut. */
  short?: string;
  /** The tab's parts: the tab opens at the first one this trader may see; each is a link in the second row. */
  parts?: readonly { key: ShellKey; label: string }[];
}

export interface PageGroupV2 {
  id: PageGroupId;
  /** The sidebar entry the group lives under. */
  home: ShellKey;
  /** The tab bar's accessible name. */
  label: string;
  tabs: readonly PageTabV2[];
  /** Pages that show the tab row without being a tab (the Settings index): no tab is marked there. */
  also?: readonly ShellKey[];
}

export const PAGE_GROUPS_V2: readonly PageGroupV2[] = [
  { id: "my-trades", home: "positions", label: "My trades", tabs: [
    { key: "positions", label: "Positions" }, { key: "orders", label: "Orders" }, { key: "portfolio", label: "Money" },
    { key: "analytics", label: "Results", parts: [
      { key: "analytics", label: "Summary" }, { key: "journal", label: "Journal" }, { key: "coach", label: "Coach" },
    ] },
  ] },
  { id: "strategies", home: "strategies", label: "Strategies", tabs: [
    { key: "strategies", label: "Library" }, { key: "strategy-builder", label: "Build" }, { key: "backtest", label: "Test" },
  ] },
  { id: "learn", home: "learn", label: "Learn", tabs: [
    { key: "learn", label: "Lessons" }, { key: "learn-words", label: "Words explained", short: "Words" }, { key: "ideas", label: "Ideas" },
    { key: "news-events", label: "News & events" },
  ] },
  { id: "settings", home: "settings", label: "Settings", also: ["settings"], tabs: [
    { key: "account", label: "Account & broker", short: "Account", parts: [
      { key: "account", label: "Account & plan" }, { key: "broker", label: "Broker accounts" }, { key: "team", label: "Team" },
      { key: "setup", label: "Setup" },
    ] },
    { key: "trading-defaults", label: "Trading", parts: [
      { key: "trading-defaults", label: "My trading defaults" }, { key: "risk-management", label: "Risk limits" },
      { key: "settings-alerts", label: "Alerts" }, { key: "notifications", label: "Inbox" },
    ] },
    { key: "settings-ai", label: "AI" },
    { key: "settings-appearance", label: "App", parts: [
      { key: "settings-appearance", label: "Appearance" }, { key: "settings-help", label: "Help & tips" },
      { key: "settings-advanced", label: "Advanced" },
    ] },
  ] },
];

const tabHolds = (t: PageTabV2, key: ShellKey): boolean => t.key === key || (t.parts ?? []).some((p) => p.key === key);

/** The group a page is a tab (or a part, or an `also` page) of, or null. */
export function pageGroupOf(key: ShellKey | null): PageGroupV2 | null {
  if (key === null) return null;
  return PAGE_GROUPS_V2.find((g) => g.tabs.some((t) => tabHolds(t, key)) || (g.also ?? []).includes(key)) ?? null;
}

const visibleAt = (key: ShellKey, ctx: NavContext): string | null => {
  const nav = navItemFor(key);
  return nav && isItemVisible(nav, ctx) ? nav.path : null;
};

/** The parts of a tab this trader may see, with their addresses. */
export function visibleParts(tab: PageTabV2, ctx: NavContext): { key: ShellKey; label: string; path: string }[] {
  return (tab.parts ?? []).flatMap((p) => {
    const path = visibleAt(p.key, ctx);
    return path ? [{ ...p, path }] : [];
  });
}

/** The tabs this trader may see (a tab whose feature is off is left out, like a sidebar entry), with their addresses: a
 * tab with parts opens at its first visible part, and is left out when none is visible. */
export function visibleTabs(group: PageGroupV2, ctx: NavContext): (PageTabV2 & { path: string })[] {
  return group.tabs.flatMap((t) => {
    if (t.parts) {
      const parts = visibleParts(t, ctx);
      return parts.length ? [{ ...t, path: parts[0].path }] : [];
    }
    const path = visibleAt(t.key, ctx);
    return path ? [{ ...t, path }] : [];
  });
}

/** The tab that holds the page on screen (null on an `also` page). */
export function tabOf(group: PageGroupV2, key: ShellKey | null): PageTabV2 | null {
  if (key === null) return null;
  return group.tabs.find((t) => tabHolds(t, key)) ?? null;
}
