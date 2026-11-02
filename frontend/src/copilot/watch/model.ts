import { request } from "../../api/client";

/**
 * M72 PR-7 (CP-7): the Copilot watch (backend app/ai/watch.py, flag `copilot_watch`). Every type is off and every number
 * Not set until the user saves his own; a type cannot be on without its number (ATP chooses none, M68). Cards are
 * information about his own positions, strikes and watchlists, with "Ask why"; nothing is placed, closed or blocked.
 */
export interface WatchPrefs {
  telegram: boolean;
  near_levels: boolean;
  near_levels_room_left_pct: number | null;
  strike_distance: boolean;
  strike_distance_pct: number | null;
  greeks_jump: boolean;
  iv_jump_points: number | null;
  delta_jump: number | null;
  oi_shift: boolean;
  oi_shift_pct: number | null;
  watchlist_signal: boolean;
}

export interface WatchSettings {
  prefs: WatchPrefs;
  version: number;
  telegram: { connected: boolean; reaches_you: boolean };
  risk_free_rate_set: boolean;
}

export const EMPTY_PREFS: WatchPrefs = {
  telegram: false, near_levels: false, near_levels_room_left_pct: null, strike_distance: false, strike_distance_pct: null,
  greeks_jump: false, iv_jump_points: null, delta_jump: null, oi_shift: false, oi_shift_pct: null, watchlist_signal: false,
};

export type WatchKind = "near_levels" | "strike_distance" | "greeks_jump" | "oi_shift" | "watchlist_signal";
type NumberKey = "near_levels_room_left_pct" | "strike_distance_pct" | "iv_jump_points" | "delta_jump" | "oi_shift_pct";

export interface WatchNumber { key: NumberKey; label: string; unit: string; max: number; step: string }
export interface WatchChoice { kind: WatchKind; label: string; line: string; numbers: WatchNumber[] }

export const WATCH_CHOICES: readonly WatchChoice[] = [
  { kind: "near_levels", label: "Near my own levels", line: "An open position has this much of the room to its invalidation or profit level left.",
    numbers: [{ key: "near_levels_room_left_pct", label: "Room left", unit: "%", max: 100, step: "any" }] },
  { kind: "strike_distance", label: "Short strike getting close", line: "The price is within this distance of a strike you are short.",
    numbers: [{ key: "strike_distance_pct", label: "Distance", unit: "% of the strike", max: 50, step: "any" }] },
  { kind: "greeks_jump", label: "IV or delta jump", line: "Between two chain readings, at your strike.",
    numbers: [{ key: "iv_jump_points", label: "IV change", unit: "IV points", max: 100, step: "any" },
              { key: "delta_jump", label: "Delta change", unit: "", max: 1, step: "any" }] },
  { kind: "oi_shift", label: "Open interest shift at my strike", line: "Between two chain readings.",
    numbers: [{ key: "oi_shift_pct", label: "OI change", unit: "%", max: 10000, step: "any" }] },
  { kind: "watchlist_signal", label: "A setup on my watchlist", line: "From your own strategies and scans, in structure words.", numbers: [] },
];

/** A type can be on only with its own number (IV or delta jump: at least one of the two). */
export function canTurnOn(prefs: WatchPrefs, choice: WatchChoice): boolean {
  return choice.numbers.length === 0 || choice.numbers.some((n) => prefs[n.key] !== null);
}

/** A number field's text -> his number, or null (Not set). Out of range or not a number: null. */
export function parseNumber(text: string, max: number): number | null {
  const t = text.trim();
  if (!t) return null;
  const v = Number(t);
  return Number.isFinite(v) && v > 0 && v <= max ? v : null;
}

/** Drops a type whose number was cleared (the server would refuse it). */
export function normalise(prefs: WatchPrefs): WatchPrefs {
  const out = { ...prefs };
  for (const c of WATCH_CHOICES) if (out[c.kind] && !canTurnOn(out, c)) out[c.kind] = false;
  return out;
}

export const watchApi = {
  settings: () => request<WatchSettings>("/copilot-watch/settings"),
  save: (prefs: WatchPrefs) => request<WatchSettings>("/copilot-watch/settings", { method: "PUT", body: JSON.stringify(normalise(prefs)) }),
};
