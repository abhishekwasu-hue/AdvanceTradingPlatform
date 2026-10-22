import type { ShellKey } from "../shell/navModel";

/**
 * MO2 part C: the v2 pages that have a page tour, by tour id. Kept tiny (no content), because the shell reads it on every
 * page to decide whether to show the "?" button; the steps themselves live in src/tours/content/ and load with the tour
 * engine, on demand. The server allow-lists the same ids (backend app/me/prefs.py TOUR_IDS, a test keeps them equal).
 */
export const TOUR_IDS = ["dashboard", "broker", "charts", "option-chain", "signals", "positions", "portfolio", "risk", "settings"] as const;

export type TourId = (typeof TOUR_IDS)[number];

/** The shell location each tour belongs to. The AI Copilot has no v2 page yet, so no tour (MO2-2). */
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
};

/** The tour of the page the shell shows, or null (a page without one, or no page). */
export function tourIdFor(key: ShellKey | null): TourId | null {
  return key ? BY_KEY[key] ?? null : null;
}

export function isTourId(value: unknown): value is TourId {
  return typeof value === "string" && (TOUR_IDS as readonly string[]).includes(value);
}
