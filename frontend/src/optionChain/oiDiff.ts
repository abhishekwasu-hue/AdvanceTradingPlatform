/**
 * M96 L1 (owner): the OI-difference table's pure parts (tested in oiDiff.test.ts), read from the /live rows only.
 * - Per strike: the call's OI change, the put's OI change, and the difference put minus call (null when either is
 *   missing). Nothing is estimated: a missing number stays missing.
 * - The interpretation word of a side is the standard fact from that side's own price change and OI change:
 *   price up + OI up = "Long buildup", price down + OI up = "Short buildup", price up + OI down = "Short covering",
 *   price down + OI down = "Long unwinding". Either change missing or zero: "–" (no word is made up).
 * - The difference's plain fact word: "Puts added more" / "Calls added more" (or, when both sides fell, "Calls cut more" /
 *   "Puts cut more"), "Even" for a zero difference. Words of what the numbers show - never a call to act.
 */
import { EMPTY } from "../ui/format";
import type { ChainLeg, ChainRow } from "./types";

export type Buildup = "Long buildup" | "Short buildup" | "Short covering" | "Long unwinding";
export type DiffWord = "Puts added more" | "Calls added more" | "Calls cut more" | "Puts cut more" | "Even";

export interface OiDiffRow {
  strike: number;
  call: number | null;
  put: number | null;
  /** Put OI change minus call OI change. */
  diff: number | null;
  callTag: Buildup | null;
  putTag: Buildup | null;
  word: DiffWord | null;
}

const num = (v: number | null | undefined): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** The standard fact word for one side; null (shown "–") when either change is missing or zero. */
export function buildupOf(priceChange: number | null | undefined, oiChange: number | null | undefined): Buildup | null {
  const p = num(priceChange);
  const o = num(oiChange);
  if (p === null || o === null || p === 0 || o === 0) return null;
  if (o > 0) return p > 0 ? "Long buildup" : "Short buildup";
  return p > 0 ? "Short covering" : "Long unwinding";
}

/** The difference's fact word (put minus call); null when either side is missing. */
export function diffWordOf(call: number | null, put: number | null): DiffWord | null {
  if (call === null || put === null) return null;
  const d = put - call;
  if (Math.round(d) === 0) return "Even";
  if (d > 0) return call < 0 && put <= 0 ? "Calls cut more" : "Puts added more";
  return put < 0 && call <= 0 ? "Puts cut more" : "Calls added more";
}

function tagOf(leg: ChainLeg): Buildup | null {
  return buildupOf(leg.ltp_change, leg.oi_change);
}

/** One row of the table per chain row, in the chain's order. */
export function oiDiffRows(rows: readonly ChainRow[]): OiDiffRow[] {
  return rows.map((r) => {
    const call = num(r.call.oi_change);
    const put = num(r.put.oi_change);
    return {
      strike: r.strike, call, put, diff: call === null || put === null ? null : put - call,
      callTag: tagOf(r.call), putTag: tagOf(r.put), word: diffWordOf(call, put),
    };
  });
}

/** A tag as shown: the word, or "–". */
export function tagText(tag: Buildup | null): string {
  return tag ?? EMPTY;
}
