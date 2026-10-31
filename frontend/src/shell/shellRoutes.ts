import { pageFromPath } from "../routes";
import { MARKETS_ADDRESS, SETUP_ADDRESS, TERMINAL_ADDRESS, TRADING_DEFAULTS_ADDRESS, V2_ALIASES, V2_MOVED, WATCHLIST_ADDRESS, V2_RETIRED_ALIASES, needsBrokerGate, routerPath } from "./aliases";
import { LEARN_IDEAS_ADDRESS, LEARN_WORDS_ADDRESS, navItemFor, type ShellKey } from "./navModel";
import { settingsSectionAt } from "../settings/paths";
import { IDEAS_ADDRESS } from "../ideas/model";
import { LEARN_ADDRESS } from "../learn/model";

export { V2_ALIASES, V2_MOVED, V2_RETIRED_ALIASES, flagOffFallback, needsBrokerGate, routerPath } from "./aliases";

/**
 * UI2-1 (UI v2 proposal §2 "Standalone and special routes", §12 UI2-1): the v2 shell's addresses. All of this applies
 * only while `ui_v2` is on; with the flag off the app keeps today's router untouched.
 *
 * - `/charts` and `/charts/:symbol`: the new Charts page (it wraps today's chart for now; UI2-4 rebuilds it).
 * - The merged pages' new addresses (§3 M1, M2, M4) show today's page: `/screener/custom` (Market scanner),
 *   `/option-chain/plan` (Options builder), `/settings/account` (Account, signed in).
 * - UX3-9 (UX audit section 5): the tabs of My trades (`/positions/orders`, `/positions/money`), Strategies
 *   (`/strategies/build`, `/strategies/test`) and Performance (`/analytics/journal`, `/analytics/coach`).
 * - The old addresses forward there (§2: "Redirect to their new tabs while ui_v2 is on").
 */

const clean = (pathname: string) => `/${pathname.replace(/^\/+|\/+$/g, "")}`;

/** The symbol in `/charts/<symbol>` (decoded, upper case), or null on `/charts`; undefined when not a charts address. */
export function chartsSymbolFromPath(pathname: string): string | null | undefined {
  const m = /^\/charts(?:\/([^/]+))?\/?$/.exec(pathname);
  if (!m) return undefined;
  if (!m[1]) return null;
  let raw = m[1];
  try { raw = decodeURIComponent(raw); } catch { /* a malformed escape: use it as written */ }
  const symbol = raw.trim().toUpperCase();
  return symbol || null;
}

/** The address of a chart: `/charts/NIFTY%2050`. */
export function chartsPath(symbol?: string | null): string {
  const s = (symbol ?? "").trim().toUpperCase();
  return s ? `/charts/${encodeURIComponent(s)}` : "/charts";
}

/** UI2-4e: a chart on the Chart tab with its timeframe: `/charts/NIFTY%2050?tf=15min` (`&exchange=BSE` off NSE) - an
 * embedded chart's "Open in Chart tab", Signals' "Open chart". The Charts page takes the timeframe (when it is one it
 * knows), then drops it from the address. */
export function chartTabPath(symbol: string, exchange?: string | null, timeframe?: string | null): string {
  const s = symbol.trim().toUpperCase();
  const q = new URLSearchParams();
  if (timeframe) q.set("tf", timeframe);
  const x = (exchange ?? "").trim().toUpperCase();
  if (s && x && x !== "NSE") q.set("exchange", x);
  const query = q.toString();
  return query ? `${chartsPath(s)}?${query}` : chartsPath(s);
}

/** What the v2 shell shows at `pathname`: the Charts page, Settings → Setup, a merged page's new address, or today's
 * page id. */
export function shellKeyFromPath(pathname: string): ShellKey | null {
  if (chartsSymbolFromPath(pathname) !== undefined) return "charts";
  if (clean(pathname) === SETUP_ADDRESS) return "setup";
  if (clean(pathname) === TRADING_DEFAULTS_ADDRESS) return "trading-defaults";
  if (clean(pathname) === IDEAS_ADDRESS || clean(pathname) === LEARN_IDEAS_ADDRESS) return "ideas";
  if (clean(pathname) === LEARN_WORDS_ADDRESS) return "learn-words";
  if (clean(pathname) === MARKETS_ADDRESS) return "markets";
  if (clean(pathname) === LEARN_ADDRESS) return "learn";
  if (clean(pathname) === TERMINAL_ADDRESS) return "terminal";
  if (clean(pathname) === WATCHLIST_ADDRESS) return "watchlist";
  // Settings redesign B: the Settings section pages (v2 only).
  const section = settingsSectionAt(clean(pathname));
  if (section) return section.key;
  const alias = V2_ALIASES.find((a) => a.path === clean(pathname));
  if (alias) return alias.page;
  const retired = V2_RETIRED_ALIASES.find((a) => a.path === clean(pathname));
  if (retired) return shellKeyFromPath(retired.now);
  return pageFromPath(pathname);
}

/** The title the top bar and the browser tab show for a location (§2 "v2 page title"). */
export function shellTitle(key: ShellKey | null): string {
  if (key === null) return "Page not found";
  return navItemFor(key)?.title ?? "";
}

export interface RedirectContext {
  signedIn: boolean;
  /** True only when the flag is known and on. */
  on: (flag: string) => boolean;
}

/**
 * Where an old address forwards while `ui_v2` is on (the query string is kept), or null to stay. Never called with
 * the flag off. `/account` moves to Settings only for a signed-in trader, and never with a verify / reset / invite link
 * (that page handles those). UX3-9: the merged pages' old addresses go to their tabs (`/orders` -> My trades › Orders);
 * a tab whose feature is off says "Coming soon" there, as its old address did.
 */
export function v2RedirectFor(pathname: string, search: string, ctx: RedirectContext): string | null {
  const p = clean(pathname);
  const keep = search && search !== "?" ? (search.startsWith("?") ? search : `?${search}`) : "";
  // M60: a moved page's old address goes to its new home (only while the feature behind that home is on).
  const moved = V2_MOVED.find((m) => m.from === p && (!m.when || ctx.on(m.when)));
  if (moved) return `${moved.to}${keep}`;
  const merged = V2_ALIASES.find((a) => a.old === p && a.page !== "account")
    ?? V2_RETIRED_ALIASES.find((a) => a.path === p);
  if (merged) return `${"now" in merged ? merged.now : merged.path}${keep}`;
  if (p === "/account" && ctx.signedIn) {
    const params = new URLSearchParams(search);
    if (params.has("invite") || params.has("reset") || params.has("verify")) return null;
    return `/settings/account${keep}`;
  }
  return null;
}

/** True when the v2 shell puts the broker gate in front of this address (src/ui/dataPages.ts, owner brief: no sample
 * data). `/charts/<symbol>`, `/screener/custom` and `/option-chain/plan` are sub-paths of data pages, so they are gated
 * too, and so are `/positions/money` (the portfolio) and `/strategies/test` (backtests); `/positions/orders`,
 * `/strategies/build`, `/analytics/*` and `/settings/account` are not data pages. */
export function isGatedPath(pathname: string): boolean {
  return needsBrokerGate(pathname);
}

/** UI2-2: true when the shell puts the broker gate in front of this address *and* the page there is not one that shows
 * the broker states itself (`ownStates`, src/shell/pagesV2.ts: the v2 dashboard). An address that cannot be decoded
 * stays gated (fail closed). */
export function shellGates(pathname: string, ownStates: readonly string[]): boolean {
  if (!isGatedPath(pathname)) return false;
  const { path, malformed } = routerPath(pathname);
  if (malformed) return true;
  const clean = `/${path.replace(/^\/+|\/+$/g, "")}`;
  return !ownStates.includes(clean);
}
