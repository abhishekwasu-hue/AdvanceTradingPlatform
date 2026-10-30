import { useState } from "react";
import { api } from "../api/client";
import { groupRows, type PositionGroup } from "../fno/contracts";
import { groupExitApi } from "../fno/groupExits";
import type { TradeRecord } from "../types";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { formatINR } from "../ui/format";
import type { HeldRow } from "./model";

/**
 * CT-1 T4 / TERM-2 step 2 (owner 30 Oct): "Exit all" lives in the one position list, so My trades and the Terminal
 * behave the same (one code path). Moved here from the Terminal's positions panel (T1-6), unchanged: one confirm naming
 * the open P&L it books, then the existing exits one after another - each basket group closes its sold legs before its
 * hedges (exit-group), then the single sold positions, then the bought ones (Exit now). Nothing here is ever disabled
 * and no check stops it (ADR-0004); a leg that does not close is named, the rest still go.
 */
export function exitAllPlan(rows: readonly HeldRow[], groups: readonly PositionGroup[]): { groups: number[]; sells: number[]; buys: number[] } {
  const { grouped, single } = groupRows(rows, groups);
  return {
    groups: grouped.filter((g) => g.group.kind === "basket").map((g) => g.legs[0].id),
    sells: [...grouped.filter((g) => g.group.kind !== "basket").flatMap((g) => g.legs), ...single].filter((r) => r.side === "Sell").map((r) => r.id),
    buys: [...grouped.filter((g) => g.group.kind !== "basket").flatMap((g) => g.legs), ...single].filter((r) => r.side === "Buy").map((r) => r.id),
  };
}

/** The rows Exit all closes: the PAPER positions only (a LIVE one closes in the broker's app, as its row says). Pure. */
export function exitableRows<R extends { id: number }>(rows: readonly R[], byId: Map<number, Pick<TradeRecord, "mode">>): R[] {
  return rows.filter((r) => byId.get(r.id)?.mode === "PAPER");
}

export function exitAllConfirmText(count: number): string {
  return `Exit all ${count} open ${count === 1 ? "position" : "positions"} at the current price? Sold legs close first, then the legs that protect them.`;
}

/** UI-28 U3.4 (NN/g H5): the Exit all confirm names the rupee impact - the open P&L it books - when every price is known. */
export function exitAllConsequence(count: number, openPnl: number | null): string {
  const base = exitAllConfirmText(count).replace(/\?/, ".");
  return openPnl === null ? base : `${base} Open P&L now: ${openPnl >= 0 ? "+" : "−"}${formatINR(Math.abs(openPnl))}.`;
}

export interface ExitAllHooks {
  onChanged: () => void;
  onNote: (text: string, ok: boolean) => void;
}

export function ExitAllV2({ rows, byId, groups, hooks }: {
  rows: readonly HeldRow[]; byId: Map<number, TradeRecord>; groups: readonly PositionGroup[]; hooks: ExitAllHooks;
}) {
  const [exitingAll, setExitingAll] = useState(false);
  const [askExitAll, setAskExitAll] = useState(false);
  const known = rows.filter((r) => r.pnl !== null);
  const total = known.length ? known.reduce((a, r) => a + (r.pnl ?? 0), 0) : null;
  async function exitAll() {
    setExitingAll(true);
    const plan = exitAllPlan(rows, groups);
    const failed: string[] = [];
    for (const id of plan.groups) {
      try { const r = await groupExitApi.exitGroup(id); r.legs.filter((l) => !l.closed).forEach((l) => failed.push(l.symbol)); }
      catch { failed.push(byId.get(id)?.symbol ?? "a group"); }
    }
    for (const id of [...plan.sells, ...plan.buys]) {
      try { await api.exitNow(id); } catch { failed.push(byId.get(id)?.symbol ?? "a position"); }
    }
    setExitingAll(false);
    hooks.onChanged();
    hooks.onNote(failed.length
      ? `Not closed: ${failed.join(", ")}. Exit ${failed.length === 1 ? "it" : "them"} one by one below.`
      : "Every open position was sent to close at the current price.", failed.length === 0);
  }
  return (
    <div className="flex justify-end" data-exit-all>
      {/* A neutral button, never disabled (ADR-0004); "Exiting…" only while the exits are on their way. */}
      <button type="button" onClick={() => setAskExitAll(true)} data-terminal="exit-all" aria-busy={exitingAll}
        className="inline-flex min-h-11 items-center rounded-control border border-border px-space-3 text-t14 font-semibold text-fg hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand lg:min-h-9">
        {exitingAll ? "Exiting…" : `Exit all (${rows.length})`}
      </button>
      <ConfirmDialog open={askExitAll} onOpenChange={setAskExitAll} title={`Exit all ${rows.length} positions?`}
        consequence={exitAllConsequence(rows.length, known.length === rows.length ? total : null)} confirmLabel="Exit all"
        busy={exitingAll} onConfirm={() => { setAskExitAll(false); void exitAll(); }} />
    </div>
  );
}
