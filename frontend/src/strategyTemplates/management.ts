/**
 * M99 P2: the trade-management options of an intraday template bot (the server's app/strategy_templates/management.py),
 * as the user's own switches in the v2 Trading bot form - every one OFF by default; a number field starts empty and shows
 * the reference value only as a hint ("Reference - edit before use"). All off sends nothing (the bot is stored as before).
 * Pure functions only (unit-tested); the server checks the choice again.
 */
import type { ManagementRequest } from "../types";
import { formatNumber } from "../ui/format";

export type { ManagementRequest };
export type Trail = "SWING" | "ATR" | "MA";

export interface ManagementChoices {
  /** The instrument classes the options run on (e.g. ["UNDERLYING"]). */
  classes: string[];
  trails: { value: Trail; label: string }[];
  averages: string[];
  bounds: { trail_degree: [number, number]; trail_n: [number, number]; trail_k: [number, number]; bars: [number, number] };
  reference: Record<string, number | string>;
  reference_label: string;
}

/** What the form holds (numbers as typed text). */
export interface ManagementPick {
  trail: Trail | "";
  degree: string;
  trailN: string;
  trailK: string;
  average: string;
  stall: boolean;
  stallBars: string;
  maxBars: boolean;
  maxBarsN: string;
}

export const EMPTY_MANAGEMENT: ManagementPick = {
  trail: "", degree: "", trailN: "", trailK: "", average: "",
  stall: false, stallBars: "", maxBars: false, maxBarsN: "",
};

function num(text: string): number | null {
  const t = text.trim();
  if (!/^\d+(\.\d+)?$/.test(t)) return null;
  const v = Number(t);
  return Number.isFinite(v) ? v : null;
}

function int(text: string): number | null {
  const v = num(text);
  return v !== null && Number.isInteger(v) ? v : null;
}

const within = (v: number | null, [lo, hi]: [number, number]) => v !== null && v >= lo && v <= hi;
const range = ([lo, hi]: [number, number]) => `${formatNumber(lo, 1, { trim: true })} to ${formatNumber(hi, 1, { trim: true })}`;

/** The reference value of a setting as a hint ("Reference - edit before use: 50"), or "" when there is none. */
export function referenceHint(choices: ManagementChoices, key: string): string {
  const v = choices.reference[key];
  if (v === undefined) return "";
  return `${choices.reference_label}: ${typeof v === "number" ? formatNumber(v, 1, { trim: true }) : v}`;
}

/** Whether the options can run on the instrument the bot trades. */
export function managementOffered(choices: ManagementChoices | null | undefined, cls: string): boolean {
  return !!choices && choices.classes.includes(cls);
}

/** A plain message when a switched-on option is missing a number (or it is out of range), else null. `premiumStopOnly`: a
 * single option whose stop is the premium stop only - it has no stop on the underlying for the trail to move (the
 * server's same check, app/strategy_templates/exits.py). */
export function managementProblem(pick: ManagementPick, choices: ManagementChoices, premiumStopOnly = false): string | null {
  const b = choices.bounds;
  if (pick.trail && premiumStopOnly) {
    return "The trailing stop moves your stop on the underlying; with only your premium stop there is none to move - pick the template's own level as the stop, or turn the trail off.";
  }
  if (pick.trail === "SWING" && !within(int(pick.degree), b.trail_degree)) return `Pick the swing size the stop trails (${range(b.trail_degree)}).`;
  if ((pick.trail === "ATR" || pick.trail === "MA") && !within(int(pick.trailN), b.trail_n)) return `Type the bars of the trail's average (${range(b.trail_n)}).`;
  if (pick.trail === "ATR" && !within(num(pick.trailK), b.trail_k)) return `Type how many average true ranges the stop trails by (${range(b.trail_k)}).`;
  if (pick.trail === "MA" && !choices.averages.includes(pick.average)) return "Pick the kind of average the stop trails.";
  if (pick.stall && !within(int(pick.stallBars), b.bars)) return `Type the bars without a new high or low (${range(b.bars)}).`;
  if (pick.maxBars && !within(int(pick.maxBarsN), b.bars)) return `Type the bars to stay in the trade (${range(b.bars)}).`;
  return null;
}

/** The request's `management`: null when every option is off (stored as before). */
export function managementBody(pick: ManagementPick): ManagementRequest | null {
  const body: ManagementRequest = {};
  if (pick.trail) {
    body.trail = pick.trail;
    if (pick.trail === "SWING") body.trail_degree = int(pick.degree);
    else body.trail_n = int(pick.trailN);
    if (pick.trail === "ATR") body.trail_k = num(pick.trailK);
    if (pick.trail === "MA") body.trail_ma = pick.average;
  }
  if (pick.stall) body.stall_bars = int(pick.stallBars);
  if (pick.maxBars) body.max_bars = int(pick.maxBarsN);
  return Object.keys(body).length ? body : null;
}

/** The short notes under the options (what every option keeps). */
export const MANAGEMENT_NOTES = [
  "Every option is off until you turn it on; the numbers are yours.",
  "A stop only ever moves closer, never further away. Nothing here holds back an exit.",
  "Read on each closed bar after the entry; acted on at the next price.",
  "To book part of the position, use the scale-out in your exit settings.",
];
