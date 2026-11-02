import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { api as appApi, request } from "../../api/client";
import { itemKey } from "../../chart/watchlistsV2";
import type { LightweightEngine } from "../../charting/lightweight";
import { PA_COLORS } from "../../charting/priceAction";
import { cx } from "../../components/primitives/cx";
import { dataOf, usePolled } from "../../dashboard/useDashboardData";
import { CombinedExitBarV2, useCombinedExits } from "../../fno/CombinedExitV2";
import { positionItem } from "../../fno/contracts";
import { GroupExitSheetV2 } from "../../fno/GroupExitsV2";
import type { GroupExitAsk } from "../../fno/groupExits";
import { PositionActionSheetV2, WaitingOrdersV2, type PositionActionAsk } from "../../fno/PositionActionsV2";
import { positionActionsApi, type PositionActionsApi, type WaitingOrder } from "../../fno/positionActions";
import { useAuth } from "../../auth/AuthContext";
import { closedText, exitConfirmText, failedText, heldRows } from "../../trading/model";
import { useFnoPositions } from "../../trading/PositionsV2";
import type { TradeRecord } from "../../types";
import { useConfirm } from "../../ui/ConfirmDialog";
import { formatNumber, formatPrice } from "../../ui/format";
import { Signed } from "../../ui/Signed";
import { useFeatures } from "../../ui/useFeatures";
import { greeksPath } from "./RiskPreviewV2";
import { buildCards, exitLines, halfOf, positionsHere, spotOf, stopToEntry, type CardLeg, type GreeksRead, type PositionCard } from "./positionCards";

/**
 * M76 MP-10 (owner order M76 E-3 / E-4; flag `copilot_workspace`): his live positions on the chart's underlying, as
 * cards inside the workspace (same tab): P&L, the spot's distance to each sold strike (points and %), delta, days to
 * expiry; one-tap exits - Close all legs, Close half, Close leg, Stop to entry - each opening the EXISTING confirm and the
 * EXISTING exit path (Exit now / Exit the whole group / Exit this leg / Exit part / Modify of his waiting stop); his
 * combined exits (CX / SX) editable here with the existing bar, and his waiting stops / targets with their existing
 * Modify. Stops and targets on the underlying's own level are drawn on the chart as lines.
 *
 * ADR-0004: no exit is ever disabled or held back here, and nothing adds a check to an exit. The same components and
 * routes as Positions and the Terminal, so the path is the one path (PAPER positions; a LIVE one says, as there, that it
 * closes in the broker's app). ADR-0006: nothing here acts by itself; every exit is his tap and his confirm.
 */
const POLL_MS = 15_000;
const BTN = "inline-flex min-h-11 items-center justify-center rounded-control border border-border px-space-3 text-t13 font-semibold text-fg hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9";
const LAYER = "m76-position-exits";
const LINE_COLORS = { stop: PA_COLORS.down, target: PA_COLORS.up };

export interface PositionCardsApi {
  positions: () => Promise<TradeRecord[]>;
  greeks: (underlying: string) => Promise<GreeksRead>;
  exitNow: typeof appApi.exitNow;
  actions: PositionActionsApi;
}
export const positionCardsApi: PositionCardsApi = {
  positions: () => request<TradeRecord[]>("/positions?mine=true"),
  greeks: (underlying) => request<GreeksRead>(greeksPath(underlying)),
  exitNow: (id) => appApi.exitNow(id),
  actions: positionActionsApi,
};

function words(c: PositionCard): string[] {
  const out = c.distances.map((d) => `Spot to sold ${formatNumber(d.strike, 0)} ${d.right}: ${formatNumber(Math.abs(d.points), 2)} points `
    + `${d.points >= 0 ? "above" : "below"} (${formatNumber(Math.abs(d.pct), 2)} %)`);
  out.push(`Delta: ${c.delta === null ? "Not known" : formatNumber(c.delta, 2)}`);
  out.push(`Days to expiry: ${c.daysLeft === null ? "Not known" : c.daysLeft}`);
  return out;
}

export function PositionCardsV2({ symbol, engine = null, api = positionCardsApi }: { symbol: string; engine?: LightweightEngine | null; api?: PositionCardsApi }) {
  const { user } = useAuth();
  const features = useFeatures();
  const canTrade = !!user && user.role !== "VIEWER";
  const exitNowOn = features.on("approval_only_entries") || features.on("paper_account");
  const fnoOn = features.on("paper_account") && canTrade;
  const [positionsL, reloadPositions] = usePolled(api.positions, () => POLL_MS, !!user);
  const loadExposure = useCallback(() => appApi.portfolioExposure(true, true), []);
  const [exposureL, reloadExposure] = usePolled(loadExposure, () => POLL_MS, !!user);
  const loadGreeks = useCallback(() => api.greeks(symbol), [api, symbol]);
  const [greeksL, reloadGreeks] = usePolled(loadGreeks, () => POLL_MS, !!user);
  const [waitingL, reloadWaiting] = usePolled(api.actions.waiting, () => POLL_MS, fnoOn);
  const all = useMemo(() => dataOf(positionsL) ?? [], [positionsL]);
  const fno = useFnoPositions(all, fnoOn);
  const factsOf = useCallback((p: TradeRecord) => fno.facts.get(itemKey(positionItem(p))), [fno.facts]);
  const here = useMemo(() => positionsHere(all, symbol, factsOf), [all, symbol, factsOf]);
  const rows = useMemo(() => new Map(heldRows(here, dataOf(exposureL)).map((r) => [r.id, r])), [here, exposureL]);
  const greeks = dataOf(greeksL);
  const spot = spotOf(greeks, symbol);
  const cards = useMemo(() => buildCards(here, rows, fno.groups, factsOf, greeks, spot), [here, rows, fno.groups, factsOf, greeks, spot]);
  const waiting = useMemo(() => dataOf(waitingL) ?? [], [waitingL]);
  const combined = useCombinedExits(fnoOn);
  const [note, setNote] = useState<{ ok: boolean; text: ReactNode } | null>(null);
  const [groupExit, setGroupExit] = useState<GroupExitAsk | null>(null);
  const [partAsk, setPartAsk] = useState<PositionActionAsk | null>(null);
  const [waitingTick, setWaitingTick] = useState(0);
  const confirmBox = useConfirm();
  const reload = () => { reloadPositions(); reloadExposure(); reloadGreeks(); reloadWaiting(); setWaitingTick((n) => n + 1); };

  // His stops / targets on the underlying's own level, as lines on the open chart (removed when the cards go).
  const ids = useMemo(() => new Set(here.map((p) => p.id)), [here]);
  const lines = useMemo(() => exitLines(waiting, ids, LINE_COLORS), [waiting, ids]);
  useEffect(() => {
    if (!engine) return undefined;
    engine.setLayer(LAYER, lines.length ? { lines } : null);
    return () => engine.setLayer(LAYER, null);
  }, [engine, lines]);

  if (!here.length) return null;

  // The My trades / Terminal "Exit now", unchanged: its confirm, then the existing exit. Never disabled (ADR-0004).
  async function exitNow(p: TradeRecord) {
    if (!window.confirm(exitConfirmText(p))) return;
    try {
      const result = await api.exitNow(p.id);
      const first = result.closed[0];
      setNote({ ok: true, text: `${p.symbol}: ${closedText(first?.exit_price, first?.pnl, first?.exit_reason)}` });
      reload();
    } catch (e) {
      setNote({ ok: false, text: failedText(`${p.symbol} was not closed.`, e) });
    }
  }
  // "Stop to entry": Modify of his own waiting stop (its level only), after his confirm - the existing Modify route.
  async function stopAtEntry(p: TradeRecord) {
    const ask = stopToEntry(waiting, p);
    if (!ask) return;
    const ok = await confirmBox.ask("Move the stop to the entry price?",
      `The stop on ${p.symbol} moves from ${formatPrice(ask.order.trigger.level)} to ${formatPrice(ask.level)}. Nothing is sent now.`, "Move the stop");
    if (!ok) return;
    try {
      await api.actions.modify(ask.order.id, { level: ask.level });
      setNote({ ok: true, text: `${p.symbol}: stop at ${formatPrice(ask.level)}.` });
      reload();
    } catch (e) {
      setNote({ ok: false, text: failedText(`The stop on ${p.symbol} was not changed.`, e) });
    }
  }
  const closeAll = (c: PositionCard) => {
    const first = c.legs[0].trade;
    if (c.kind === "basket") setGroupExit({ kind: "group", tradeId: first.id, title: c.title, legs: c.legs.length });
    else void exitNow(first);                                 // a strategy structure closes whole with Exit now; a single position too
  };
  const exitsOn = exitNowOn && canTrade;

  return (
    <section aria-labelledby="workspace-positions-title" className="space-y-space-3 rounded-panel border border-subtle bg-surface-1 p-space-4" data-workspace-positions={cards.length}>
      <h3 id="workspace-positions-title" className="text-t14 font-semibold text-fg">Your positions · <span className="font-tabular">{symbol}</span></h3>
      {note && <p role="status" className={cx("text-t13", note.ok ? "text-fg" : "text-down")} data-positions-note>{note.text}</p>}
      <CardsGridV2 cards={cards} waiting={waiting} exitsOn={exitsOn} onCloseAll={closeAll}
        onHalf={(p, lots) => setPartAsk({ kind: "part", position: p, lots })}
        onLeg={(l) => setGroupExit({ kind: "leg", tradeId: l.trade.id, symbol: l.trade.symbol, hedge: l.hedge, warning: null })}
        onStopToEntry={(p) => void stopAtEntry(p)} />
      {fnoOn && (
        <CombinedExitBarV2 positions={here} facts={fno.facts} exits={combined.list}
          onChanged={() => { combined.reload(); reload(); }} onNote={(text, ok) => setNote({ ok, text })} />
      )}
      {fnoOn && <WaitingOrdersV2 refresh={waitingTick} onChanged={reload} />}
      <GroupExitSheetV2 ask={groupExit} onClose={() => setGroupExit(null)}
        onDone={(text, ok) => { setGroupExit(null); setNote({ ok, text }); reload(); }} />
      <PositionActionSheetV2 ask={partAsk} onClose={() => setPartAsk(null)} api={api.actions}
        onDone={(text, ok) => { setPartAsk(null); setNote({ ok, text }); reload(); }} />
      {confirmBox.dialog}
    </section>
  );
}

/** The cards themselves (pure view; every button is his tap into an existing exit, never disabled - ADR-0004). */
export function CardsGridV2({ cards, waiting, exitsOn, onCloseAll, onHalf, onLeg, onStopToEntry }: {
  cards: PositionCard[]; waiting: readonly WaitingOrder[]; exitsOn: boolean;
  onCloseAll: (c: PositionCard) => void; onHalf: (p: TradeRecord, lots: number) => void; onLeg: (l: CardLeg) => void;
  onStopToEntry: (p: TradeRecord) => void;
}) {
  return (
    <div className="grid gap-space-3 md:grid-cols-2">
      {cards.map((c) => {
        const single = c.legs.length === 1 ? c.legs[0].trade : null;
        const half = single ? halfOf(single) : null;
        return (
          <article key={c.key} className="min-w-0 space-y-space-2 rounded-panel border border-subtle p-space-3" data-position-card={c.kind}>
            <header className="flex flex-wrap items-baseline justify-between gap-space-2">
              <span className="min-w-0 text-t13 font-semibold text-fg">{c.title}</span>
              {c.pnl === null ? <span className="text-t12 text-fg-muted">P&amp;L: Not known</span> : <Signed value={c.pnl} kind="money" paise className="text-t15 font-semibold" />}
            </header>
            <ul className="space-y-0.5 text-t12 text-fg-muted font-tabular" data-card-facts>
              {words(c).map((w) => <li key={w}>{w}</li>)}
            </ul>
            {c.legs.length > 1 && (
              <ul className="divide-y divide-subtle text-t12" data-card-legs>
                {c.legs.map((l) => (
                  <li key={l.trade.id} className="flex flex-wrap items-center justify-between gap-space-2 py-space-1">
                    <span className="min-w-0">{l.sold ? "Sold" : "Bought"} {l.label} · {l.lots} lot{l.lots === 1 ? "" : "s"}</span>
                    {exitsOn && c.paper && c.kind === "basket" && (
                      <button type="button" className={BTN} data-card-action="close-leg"
                        onClick={() => onLeg(l)}>Close leg</button>
                    )}
                    {exitsOn && c.paper && stopToEntry(waiting, l.trade) && (
                      <button type="button" className={BTN} data-card-action="stop-to-entry" onClick={() => onStopToEntry(l.trade)}>Stop to entry</button>
                    )}
                  </li>
                ))}
              </ul>
            )}
            {exitsOn && c.paper ? (
              <div className="flex flex-wrap gap-space-2">
                <button type="button" className={BTN} data-card-action="close-all" onClick={() => onCloseAll(c)}>{c.legs.length > 1 ? "Close all legs" : "Exit now"}</button>
                {single && half !== null && (
                  <button type="button" className={BTN} data-card-action="close-half" onClick={() => onHalf(single, half)}>Close half</button>
                )}
                {single && stopToEntry(waiting, single) && (
                  <button type="button" className={BTN} data-card-action="stop-to-entry" onClick={() => onStopToEntry(single)}>Stop to entry</button>
                )}
              </div>
            ) : !c.paper ? <p className="text-t12 text-fg-muted">Close a live position in your broker&apos;s app.</p> : null}
          </article>
        );
      })}
    </div>
  );
}

export default PositionCardsV2;
