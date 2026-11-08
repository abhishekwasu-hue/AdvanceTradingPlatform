/**
 * M91 C1: the address parameter that carries a watchlist's chosen screen to a chart (`?wscreen=<his screen's id>`). The
 * chart reads it and draws that screen; a symbol picked any other way opens without it. Tiny on purpose: the in-place
 * panel registry imports it.
 */
export const SCREEN_PARAM = "wscreen";

/** A screen id from the address, or null (anything that is not a whole number above 0). */
export function screenIdOf(value: string | null | undefined): string | null {
  return value && /^[1-9][0-9]{0,11}$/.test(value) ? value : null;
}

/** A chart address (`/charts/X?exchange=..`) with the screen added. */
export function withScreen(path: string, from?: { screenId: number } | null): string {
  if (!from) return path;
  return `${path}${path.includes("?") ? "&" : "?"}${SCREEN_PARAM}=${encodeURIComponent(String(from.screenId))}`;
}

/** A query string (`""` or `?a=b`) with the screen added. */
export function screenParam(query: string, from?: { screenId: number } | null): string {
  if (!from) return query;
  return `${query}${query ? "&" : "?"}${SCREEN_PARAM}=${encodeURIComponent(String(from.screenId))}`;
}
