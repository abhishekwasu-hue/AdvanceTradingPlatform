// Z3-b: pure helpers for the progression ladder card. The server computes every value and threshold from the user's own
// PAPER trades; this only turns them into a bar and a line of text. Facts only - no advice wording.
import type { LadderCriterion } from "../types";
import { formatNumber } from "../ui/format";

/** 0..1 for the progress bar; null when the criterion is not measured yet. */
export function criterionProgress(c: LadderCriterion): number | null {
  if (c.value == null || c.target == null || c.comparison == null) return null;
  if (c.comparison === "at_least") return c.target <= 0 ? 1 : Math.max(0, Math.min(1, c.value / c.target));
  // at_most: full while within the limit, shrinking as the value goes past it
  if (c.value <= c.target) return 1;
  return c.value <= 0 ? 0 : Math.max(0, Math.min(1, c.target / c.value));
}

function num(n: number): string {
  return formatNumber(n, Number.isInteger(n) ? 0 : 2);
}

/** "3 of 20", "4.20 (limit 10)", or the server's note when not measured. */
export function criterionText(c: LadderCriterion): string {
  if (c.value == null || c.target == null || c.comparison == null) return c.note ?? "Not measured yet";
  return c.comparison === "at_least" ? `${num(c.value)} of ${num(c.target)}` : `${num(c.value)} (limit ${num(c.target)})`;
}

/** Signed money with two decimals, e.g. "+1,234.50" / "−20.00". */
export function signedMoney(n: number): string {
  const s = formatNumber(n, 2);
  return n > 0 && /[1-9]/.test(s) ? `+${s}` : s;
}

/** LIVE-1 (owner, 11 Nov - BINDING): `live_trading` is the ONE switch. The ladder's LIVE step only says what that switch
 * does now; the criteria above it are information and never gate LIVE. */
export const LIVE_STEP_OFF = "Live trading is switched off by the admin";
export const LIVE_STEP_ON = "Live trading available - choose LIVE on any order";
export function liveStepText(liveOn: boolean): string {
  return liveOn ? LIVE_STEP_ON : LIVE_STEP_OFF;
}

/** PAPER account tiles (owner, 12 Nov, D.3): a tile is at least as wide as its number on one line (`min-w-max`), the
 * label does not widen it (`w-0 min-w-full`, it wraps instead), and the row wraps to the next line when the numbers do
 * not fit side by side - so a number never breaks inside or runs past the card. */
export const TILE_ROW = "flex flex-wrap gap-3";
export const TILE = "min-w-max flex-1 rounded-xl border border-border bg-surface-1 shadow-card px-4 py-3";
export const TILE_LABEL = "w-0 min-w-full text-t13 font-semibold text-fg-muted";
export const TILE_VALUE = "font-tabular whitespace-nowrap text-t20 font-extrabold sm:text-t24";
