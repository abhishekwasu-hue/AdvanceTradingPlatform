import type { TradeRecord } from "../types";

/** The highest stored stop that is the "one tick" placeholder of a bought option or future placed without a stop. */
export const PLACEHOLDER_STOP_MAX = 0.1;

/** Owner bug report 13 Nov ("Premium ceiling 0.00" on a sold option): the stop stored on a position is his own only
 * when it is a real level. DEF-0 D7 (backend paper/fno_ticket.py `position_stop`, trading/exit_logic.py): a short's
 * stop of 0 (or less) is "no stop set", and a bought option or future placed without one is stored with one tick (the
 * whole premium at risk). Null: no stop of his own - the row then shows the one "Set ..." chip, never a number. Pure. */
export function ownStop(p: Pick<TradeRecord, "stop_loss" | "direction" | "instrument_kind" | "option_position">): number | null {
  const v = p.stop_loss;
  if (v === null || v === undefined || !Number.isFinite(v) || v <= 0) return null;
  const short = p.direction === "SHORT" || p.option_position === "WRITE";
  const contract = p.instrument_kind === "OPTION" || p.instrument_kind === "FUTURE";
  return contract && !short && v <= PLACEHOLDER_STOP_MAX ? null : v;
}

/** An underlying level stored as 0 (or less) is "none set" (DEF-0 D7, trading/exit_logic.py `underlying_exit`). Pure. */
export function ownLevel(v: number | null | undefined): number | null {
  return v === null || v === undefined || !Number.isFinite(v) || v <= 0 ? null : v;
}
