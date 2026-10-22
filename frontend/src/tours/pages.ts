import type { ShellKey } from "../shell/navModel";

/**
 * MO2 part C: the v2 pages that have a page tour, by tour id. Kept tiny (no content), because the shell reads it on every
 * page to decide whether to show the "?" button; the steps themselves live in src/tours/content/ and load with the tour
 * engine, on demand. The server allow-lists the same ids (backend app/me/prefs.py TOUR_IDS, a test keeps them equal).
 */
export const TOUR_IDS = ["dashboard", "broker", "charts", "option-chain", "signals", "positions", "portfolio", "risk", "settings", "copilot"] as const;

export type TourId = (typeof TOUR_IDS)[number];

/** The shell location each tour belongs to. */
const BY_KEY: Partial<Record<ShellKey, TourId>> = {
  dashboard: "dashboard",
  broker: "broker",
  charts: "charts",
  "option-chain": "option-chain",
  signals: "signals",
  positions: "positions",
  portfolio: "portfolio",
  "risk-management": "risk",
  settings: "settings",
  copilot: "copilot",
};

/**
 * The tour of the page the shell shows, or null (a page without one, or no page). MO2-2: the Copilot's tips belong to its
 * chat-first page (`/copilot`) only; its older tool pages (`/copilot/<tab>`) have none, so pass the path when it is known.
 */
export function tourIdFor(key: ShellKey | null, path?: string): TourId | null {
  if (key === "copilot" && path !== undefined && path.replace(/\/+$/, "") !== "/copilot") return null;
  return key ? BY_KEY[key] ?? null : null;
}

export function isTourId(value: unknown): value is TourId {
  return typeof value === "string" && (TOUR_IDS as readonly string[]).includes(value);
}
