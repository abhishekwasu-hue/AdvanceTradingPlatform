/**
 * B8 (docs/design/MOBILE_APP_PLAN.md §6.2, docs/design/MOBILE_PUSH.md): which links the app opens, and where.
 *
 * A link reaches the app three ways: a tap on a phone notification (its `data.path`, a bare path from the server), an
 * Android App Link (`https://aialgodesk.tech/...`, e.g. Telegram's "Open in app"), or the URL that launched the app.
 * All three go through `resolveDeepLink`, which:
 * - accepts only a bare path (`/signals?proposal=12`) or an https link on the app's own domain;
 * - matches the path against the allow-list below, with only that path's query keys, and ids as whole numbers;
 * - returns "/" (Home) for anything else - another site, an unknown page, an unknown key, a bad id.
 * A link never does anything by itself: it opens a screen, and the user still presses Approve (or anything else).
 *
 * Pure (no Capacitor import), so the web build and the tests use it as is; src/native/links.ts wires it to the app.
 */

/** The four phone notification kinds and the screen each opens (backend app/alerts/push_kinds.py). */
export const KIND_SCREENS = {
  signals: "/signals",            // the Signals item (?proposal=<id>)
  approvals: "/copilot/watchtower", // Approvals (?action=<id>)
  exits: "/positions",            // Positions (?trade=<id>)
  renew: "/broker",               // Broker (renew the login)
} as const;

/** Path -> the query keys it may carry (each a whole-number id). */
const ALLOWED: Record<string, readonly string[]> = {
  "/": [],
  "/signals": ["proposal"],
  "/copilot/watchtower": ["action"],
  "/positions": ["trade"],
  "/orders": ["order"],
  "/broker": [],
  "/notifications": ["n"],
  "/option-chain": [],
  "/settings": [],
};

export const HOME = "/";

/** The paths the app opens (the Android manifest's App Links filter lists the same ones). */
export const ALLOWED_PATHS: readonly string[] = Object.keys(ALLOWED);

/** The hosts whose https links the app opens: the API's own domain and its www name. */
export function appHosts(apiOrigin: string): string[] {
  try {
    const host = new URL(apiOrigin).hostname.toLowerCase();
    if (!host) return [];
    return host.startsWith("www.") ? [host, host.slice(4)] : [host, `www.${host}`];
  } catch {
    return [];
  }
}

const ID = /^[1-9][0-9]{0,17}$/;

/** The safe in-app path for a link, or "/" (Home) when it is not one the app opens. */
export function resolveDeepLink(raw: string | null | undefined, hosts: readonly string[]): string {
  const text = (raw ?? "").trim();
  if (!text || text.includes("\\")) return HOME;
  let url: URL;
  try {
    if (text.startsWith("/")) {
      if (text.startsWith("//")) return HOME;            // protocol-relative: another site
      url = new URL(text, "https://app.invalid");
    } else {
      url = new URL(text);
      if (url.protocol !== "https:" || !hosts.includes(url.hostname.toLowerCase()) || url.username || url.password || url.port) return HOME;
    }
  } catch {
    return HOME;
  }
  const path = url.pathname.length > 1 ? url.pathname.replace(/\/+$/, "") : url.pathname;
  const keys = ALLOWED[path];
  if (!keys) return HOME;
  const out = new URLSearchParams();
  for (const [key, value] of url.searchParams) {
    if (!keys.includes(key) || !ID.test(value) || out.has(key)) return HOME;
    out.set(key, value);
  }
  const query = out.toString();
  return query ? `${path}?${query}` : path;
}

/** Opens a path in the running app without a reload: react-router's BrowserRouter follows `popstate`. */
export function navigateInApp(path: string, win: Window = window): void {
  if (win.location.pathname + win.location.search === path) return;
  win.history.pushState({}, "", path);
  win.dispatchEvent(typeof PopStateEvent === "function" ? new PopStateEvent("popstate") : new Event("popstate"));
}

/** A notification tap's data block -> the path to open (`data.path` from the server, checked like any link). */
export function pathFromNotification(data: unknown, hosts: readonly string[]): string {
  const path = data && typeof data === "object" ? (data as Record<string, unknown>).path : undefined;
  return resolveDeepLink(typeof path === "string" ? path : null, hosts);
}
