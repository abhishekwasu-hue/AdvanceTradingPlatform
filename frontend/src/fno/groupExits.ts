/**
 * TT-8 (owner, 24 Oct): the legs of one basket are one group, with "Exit this leg" and "Exit the whole group" - the pure
 * parts (tested in groupExits.test.tsx) and the two calls (backend app/paper/fno_groups.py).
 *
 * Both are exits (ADR-0004): nothing on the page ever disables them, and no kill switch, limit or approval rule stops them
 * on the server. Exiting a hedge (a bought leg that protects a sold one) while its sold leg is open shows a plain warning
 * and asks again; it is never refused. "Exit the whole group" closes the sold legs first, then the hedges.
 */
import { friendlyError, request } from "../api/client";
import { formatSignedINR } from "../ui/format";

export interface GroupExitLeg { trade_id: number; symbol: string; closed: boolean; exit_price: number | null; pnl: number | null; warnings: string[] }
export interface GroupExitResult { status: "CLOSED" | "PARTIAL" | "CONFIRM"; warning?: string | null; group?: string | null; legs: GroupExitLeg[] }

export const groupExitApi = {
  exitLeg: (tradeId: number, confirm: boolean) =>
    request<GroupExitResult>(`/paper/fno/positions/${tradeId}/exit-leg`, { method: "POST", body: JSON.stringify({ confirm }) }),
  exitGroup: (tradeId: number) => request<GroupExitResult>(`/paper/fno/positions/${tradeId}/exit-group`, { method: "POST" }),
};
export type GroupExitApi = typeof groupExitApi;

/** What the confirm step is about: one leg (with the hedge warning once known) or the whole group. */
export type GroupExitAsk =
  | { kind: "leg"; tradeId: number; symbol: string; hedge: boolean; warning: string | null }
  | { kind: "group"; tradeId: number; title: string; legs: number };

export const HEDGE_NOTE = "This leg protects a sold leg that is still open. If you exit it first, the sold leg has no protection and "
  + "the possible loss is no longer capped. You can still exit it, or exit the whole group to close the sold legs first.";

/** The confirm sheet's words. Plain language, no internal terms. Pure. */
export function confirmCopy(ask: GroupExitAsk): { title: string; body: string; warning: string | null; confirm: string } {
  if (ask.kind === "group") {
    return {
      title: "Exit the whole group?",
      body: `All ${ask.legs} legs close at the current price: the sold legs first, then the legs that protect them.`,
      warning: null,
      confirm: "Exit the whole group",
    };
  }
  const warning = ask.warning ?? (ask.hedge ? HEDGE_NOTE : null);
  return {
    title: "Exit this leg?",
    body: `${ask.symbol} closes at the current price. The other legs of the group stay open.`,
    warning,
    confirm: warning ? "Exit this leg anyway" : "Exit this leg",
  };
}

/** The line shown after an exit. Pure. */
export function resultText(result: GroupExitResult): string {
  const closed = result.legs.filter((l) => l.closed);
  const open = result.legs.filter((l) => !l.closed);
  const pnl = closed.reduce((s, l) => s + (l.pnl ?? 0), 0);
  const money = formatSignedINR(Math.round(pnl * 100) / 100);
  const head = closed.length === 1 ? `${closed[0].symbol} closed` : `${closed.length} legs closed`;
  const rest = open.length ? ` ${open.length} ${open.length === 1 ? "leg is" : "legs are"} still open: ${open.map((l) => l.symbol).join(", ")}.` : "";
  return `${head}: ${money}.${rest}`;
}

export type ExitStep = { kind: "warn"; warning: string } | { kind: "done"; text: string; ok: boolean };

/** One tap on the sheet's confirm: a leg goes with `confirm` once the warning has been shown (by the page or the server);
 * the server's own warning keeps the sheet open and asks again. A failure is a plain line, never a crash. */
export async function runExit(ask: GroupExitAsk, warned: boolean, api: GroupExitApi = groupExitApi): Promise<ExitStep> {
  try {
    const result = ask.kind === "group" ? await api.exitGroup(ask.tradeId) : await api.exitLeg(ask.tradeId, ask.hedge || warned);
    if (result.status === "CONFIRM") return { kind: "warn", warning: result.warning || HEDGE_NOTE };
    return { kind: "done", text: resultText(result), ok: true };
  } catch (e) {
    const why = friendlyError(e).trim();
    return { kind: "done", text: `${ask.kind === "group" ? "The group" : ask.symbol} was not closed. ${why || "Try again in a moment."}`, ok: false };
  }
}
