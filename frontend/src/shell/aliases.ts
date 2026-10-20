import type { Page } from "../components/Sidebar";

/**
 * UI2-1: the merged pages' new addresses (UI v2 proposal §3 M1, M2, M3, M4) and the page each shows until its tab
 * exists (UI2-8). A tiny module on purpose: App.tsx reads it with the flag off too (see flagOffFallback), so it must not
 * pull the v2 shell into the first download.
 */
export const V2_ALIASES: readonly { path: string; page: Page; old: string }[] = [
  { path: "/screener/custom", page: "scanner", old: "/scanner" },
  { path: "/option-chain/plan", page: "options-builder", old: "/options-builder" },
  { path: "/journal/coach", page: "coach", old: "/coach" },
  { path: "/settings/account", page: "account", old: "/account" },
];

/**
 * With `ui_v2` off (never turned on, turned off again as a kill switch, or the trader just signed out - flags are not
 * read while signed out): a v2-only address of a merged page goes back to today's address, so a bookmark or the page
 * a trader signed out on (Settings -> Account) never ends on "This page does not exist". Every address today's app
 * knows is left alone (this only answers for addresses that are not found with the flag off). Null: stay.
 */
export function flagOffFallback(pathname: string): string | null {
  const clean = `/${pathname.replace(/^\/+|\/+$/g, "")}`;
  return V2_ALIASES.find((a) => a.path === clean)?.old ?? null;
}
