import type { Page } from "../components/Sidebar";
import { isDataPath } from "../ui/dataPages";
import { settingsSectionAt } from "../settings/paths";

/**
 * UI2-1: the merged pages' new addresses (UI v2 proposal §3 M1, M2, M3, M4) and the page each shows until its tab
 * exists (UI2-8). A tiny module on purpose: App.tsx reads it with the flag off too (see flagOffFallback), so it must not
 * pull the v2 shell into the first download.
 */
export const V2_ALIASES: readonly { path: string; page: Page; old: string }[] = [
  { path: "/screener/custom", page: "scanner", old: "/scanner" },
  { path: "/option-chain/plan", page: "options-builder", old: "/options-builder" },
  { path: "/settings/account", page: "account", old: "/account" },
  // UX3-9 (UX audit section 5): the merged pages' tabs - My trades, Strategies, Performance.
  { path: "/positions/orders", page: "orders", old: "/orders" },
  { path: "/positions/money", page: "portfolio", old: "/portfolio" },
  { path: "/strategies/build", page: "strategy-builder", old: "/strategy-builder" },
  { path: "/strategies/test", page: "backtest", old: "/backtest" },
  { path: "/analytics/coach", page: "coach", old: "/coach" },
  // M60 (owner order, approved 30 Oct): Performance is My trades › Results, Journal a part of it; Broker accounts sit in
  // Settings; News & events is a Learn tab.
  { path: "/positions/results", page: "analytics", old: "/analytics" },
  { path: "/positions/results/journal", page: "journal", old: "/journal" },
  { path: "/settings/broker", page: "broker", old: "/broker" },
  { path: "/learn/news", page: "news-events", old: "/news-events" },
];

/**
 * Addresses an earlier v2 slice used and the v2 shell now forwards (UX3-9: Coach moved from Journal to Performance).
 * With `ui_v2` off they still go back to today's address, so an old bookmark never ends on "This page does not exist".
 */
export const V2_RETIRED_ALIASES: readonly { path: string; now: string; old: string }[] = [
  { path: "/journal/coach", now: "/analytics/coach", old: "/coach" },
  // M60: the Journal's UX3-9 address forwards to its new part of My trades › Results.
  { path: "/analytics/journal", now: "/positions/results/journal", old: "/journal" },
];

/**
 * M60 (owner order, approved 30 Oct with the five defaults): old addresses that forward to a new home while `ui_v2` is
 * on - inside the v2 shell only (with the flag off today's app keeps them). `when`: only while that feature is on (its
 * new home lives behind it); otherwise the old address keeps its page. The query string is kept.
 */
export const V2_MOVED: readonly { from: string; to: string; when?: string }[] = [
  // Default 3: the Coach is an AI Copilot tab (Review folds into it); Brief becomes Read the chart; Copilot Signals
  // (the Watchtower) opens the Signals page. The Watchtower itself keeps /copilot/watchtower under "Advanced".
  { from: "/analytics/coach", to: "/copilot/coach", when: "ai_copilot" },
  { from: "/coach", to: "/copilot/coach", when: "ai_copilot" },
  { from: "/journal/coach", to: "/copilot/coach", when: "ai_copilot" },
  { from: "/copilot/review", to: "/copilot/coach" },
  { from: "/copilot/brief", to: "/copilot/read" },
  { from: "/copilot/signals", to: "/signals" },
  // The idea library is a Learn tab.
  { from: "/ideas", to: "/learn/ideas" },
];

/** M60: Learn's v2-only tabs (with `ui_v2` off they go to the dashboard, like the Learn page itself has no old page). */
export const LEARN_V2_ONLY: readonly string[] = ["/learn/ideas", "/learn/words"];

/** UI2-7: Settings → Setup, a v2-only page (the setup wizard). With the flag off it goes back to Settings. */
export const SETUP_ADDRESS = "/settings/setup";

/** Settings redesign part C: Settings → My trading defaults, a v2-only page. With the flag off it goes back to Settings. */
export const TRADING_DEFAULTS_ADDRESS = "/settings/trading-defaults";

/** MO4 slice 2: the Sector & index view, a v2-only page (flag `markets_view`). With `ui_v2` off it goes to the dashboard. */
export const MARKETS_ADDRESS = "/markets";

/** Trading Terminal T1: the Terminal, a v2-only page (flag `terminal`). With `ui_v2` off it goes to the dashboard. */
export const TERMINAL_ADDRESS = "/terminal";

/** P1-8 (2): the Watchlist page, a v2-only page (flag `watchlists`). With `ui_v2` off it goes to the dashboard. */
export const WATCHLIST_ADDRESS = "/watchlist";

/**
 * With `ui_v2` off (never turned on, turned off again as a kill switch, or the trader just signed out - flags are not
 * read while signed out): a v2-only address of a merged page goes back to today's address, so a bookmark or the page
 * a trader signed out on (Settings -> Account) never ends on "This page does not exist". Every address today's app
 * knows is left alone (this only answers for addresses that are not found with the flag off). Null: stay.
 */
export function flagOffFallback(pathname: string): string | null {
  const { path, malformed } = routerPath(pathname);
  if (malformed) return null;
  const clean = `/${path.replace(/^\/+|\/+$/g, "")}`;
  if (clean === SETUP_ADDRESS || clean === TRADING_DEFAULTS_ADDRESS) return "/settings";
  if (clean === MARKETS_ADDRESS || clean === TERMINAL_ADDRESS || clean === WATCHLIST_ADDRESS) return "/";
  if (LEARN_V2_ONLY.includes(clean)) return "/";
  // Settings redesign B: a section page goes back to today's Settings page, at the part that holds it.
  const section = settingsSectionAt(clean);
  if (section) return section.old;
  return (V2_ALIASES.find((a) => a.path === clean) ?? V2_RETIRED_ALIASES.find((a) => a.path === clean))?.old ?? null;
}

/**
 * Settings redesign B: the whole address the flag-off fallback goes to - the query string before the hash
 * (`/settings?x=1#alerts`). The fallback's own hash wins (it names the part of today's page); without one, the
 * address's hash is kept. Null: stay.
 */
export function fallbackAddress(pathname: string, search: string, hash: string): string | null {
  const to = flagOffFallback(pathname);
  if (to === null) return null;
  const at = to.indexOf("#");
  const path = at === -1 ? to : to.slice(0, at);
  const ownHash = at === -1 ? "" : to.slice(at);
  const query = search && search !== "?" ? (search.startsWith("?") ? search : `?${search}`) : "";
  return `${path}${query}${ownHash || hash}`;
}

/**
 * The path as React Router matches it (UI2-1 review M1): each segment percent-decoded, an encoded "/" kept as %2F
 * (react-router's decodePath). `/%70ositions` and `/option%2Dchain` ARE the Positions and Option chain pages to the
 * router, so every shell decision (gate, redirect, title) must read this, never the raw address. A malformed escape
 * (`/%E0`) cannot be decoded: the router then matches the raw text, and the shell treats it as unknown - `malformed`
 * tells the caller to fail closed.
 */
export function routerPath(pathname: string): { path: string; malformed: boolean } {
  try {
    return { path: pathname.split("/").map((v) => decodeURIComponent(v).replace(/\//g, "%2F")).join("/"), malformed: false };
  } catch {
    return { path: pathname, malformed: true };
  }
}

/** True when the shell must put the broker gate in front of `pathname`: a data page as the router sees it, or an
 * address that cannot be decoded (fail closed - never a page's data by mistake). */
export function needsBrokerGate(pathname: string): boolean {
  const { path, malformed } = routerPath(pathname);
  return malformed || isDataPath(path);
}

/**
 * Which shell to render (App.tsx), from what is known so far (UI2-1 review M3):
 * - "v2": the flag is known and on;
 * - "wait": not known yet (auth still checking, or a signed-in user's flags not answered) on a data page - a neutral
 *   loading screen, so a ui_v2 organisation never glimpses today's page (which may show sample data) before its
 *   flags arrive; at most SHELL_WAIT_MS (`capped`), and a failed call answers "off" (today's shell, the kill switch);
 * - "fallback": the flag is off and this is a v2-only address (see flagOffFallback);
 * - "today": today's shell, exactly as before.
 */
export type ShellMode = "v2" | "wait" | "fallback" | "today";
export function shellMode(s: {
  v2: boolean; authLoading: boolean; signedIn: boolean; featuresLoaded: boolean; pathname: string;
  /** The wait has lasted SHELL_WAIT_MS (shellWait.ts): treat "not known" as "off", like a failed flags read. */
  capped?: boolean;
}): ShellMode {
  if (s.v2) return "v2";
  const unknown = !s.capped && (s.authLoading || (s.signedIn && !s.featuresLoaded));
  if (flagOffFallback(s.pathname)) return unknown ? "wait" : "fallback";
  if (unknown && needsBrokerGate(s.pathname)) return "wait";
  return "today";
}
