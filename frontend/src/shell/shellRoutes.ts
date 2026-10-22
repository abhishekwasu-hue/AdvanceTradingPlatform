import { pageFromPath } from "../routes";
import { SETUP_ADDRESS, TRADING_DEFAULTS_ADDRESS, V2_ALIASES, needsBrokerGate, routerPath } from "./aliases";
import { navItemFor, type ShellKey } from "./navModel";
import { settingsSectionAt } from "../settings/paths";

export { V2_ALIASES, flagOffFallback, needsBrokerGate, routerPath } from "./aliases";

/**
 * UI2-1 (UI v2 proposal §2 "Standalone and special routes", §12 UI2-1): the v2 shell's addresses. All of this applies
 * only while `ui_v2` is on; with the flag off the app keeps today's router untouched.
 *
 * - `/charts` and `/charts/:symbol`: the new Charts page (it wraps today's chart for now; UI2-4 rebuilds it).
 * - The merged pages' new addresses (§3 M1, M2, M3, M4) show today's page until UI2-8 adds the tabs:
 *   `/screener/custom` (Market scanner), `/option-chain/plan` (Options builder), `/journal/coach` (Coach and guide),
 *   `/settings/account` (Account, signed in).
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
  // Settings redesign B: the Settings section pages (v2 only).
  const section = settingsSectionAt(clean(pathname));
  if (section) return section.key;
  const alias = V2_ALIASES.find((a) => a.path === clean(pathname));
  if (alias) return alias.page;
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
 * the flag off. `/coach` moves under Journal only while Journal exists (flag `discipline_engine`); `/account` moves to
 * Settings only for a signed-in trader, and never with a verify / reset / invite link (that page handles those).
 */
export function v2RedirectFor(pathname: string, search: string, ctx: RedirectContext): string | null {
  const p = clean(pathname);
  const keep = search && search !== "?" ? (search.startsWith("?") ? search : `?${search}`) : "";
  if (p === "/scanner") return `/screener/custom${keep}`;
  if (p === "/options-builder") return `/option-chain/plan${keep}`;
  if (p === "/coach" && ctx.on("discipline_engine")) return `/journal/coach${keep}`;
  if (p === "/account" && ctx.signedIn) {
    const params = new URLSearchParams(search);
    if (params.has("invite") || params.has("reset") || params.has("verify")) return null;
    return `/settings/account${keep}`;
  }
  return null;
}

/** True when the v2 shell puts the broker gate in front of this address (src/ui/dataPages.ts, owner brief: no sample
 * data). `/charts/<symbol>`, `/screener/custom` and `/option-chain/plan` are sub-paths of data pages, so they are gated
 * too; `/journal/coach` and `/settings/account` are not data pages. */
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
