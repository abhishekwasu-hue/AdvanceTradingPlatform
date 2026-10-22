/**
 * Mobile B1 (docs/design/MOBILE_APP_PLAN.md §4.2): where the API is. The one place that spells `/api/v1`
 * (scripts/check-mobile-config.mjs refuses the literal anywhere else in src/).
 *
 * - Web build: API_ORIGIN is "", so every URL stays the relative `/api/v1/...` on the page's own origin, exactly as
 *   before (nginx / Caddy serve both; the dev server proxies /api).
 * - Android build: API_ORIGIN is `https://aialgodesk.tech` for internal and release builds unless VITE_API_BASE_URL
 *   overrides it at build time (MA-6), or a debug build's `.invalid` placeholder - a constant the build checked: https
 *   only, never an IP address (scripts/mobile-rules.mjs).
 */

/** The API's origin without a trailing slash; "" on the web. */
export const API_ORIGIN: string = __ATP_API_ORIGIN__;

/** The versioned API root: `/api/v1` on the web, `https://<domain>/api/v1` in the app. */
export const API_V1 = `${__ATP_API_ORIGIN__}/api/v1`;

/** The full URL of an API path (`path` starts with "/", e.g. "/status"). */
export function apiUrl(path: string): string {
  return `${API_V1}${path}`;
}
