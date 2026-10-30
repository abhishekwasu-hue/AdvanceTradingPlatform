import { Bot, ChevronDown, ChevronRight, LineChart, ShoppingBasket } from "lucide-react";
import { Fragment, useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router";
import { api } from "../api/client";
import { BROKER_PATH } from "../broker/paths";
import type { LevelLine } from "../chart/chartCore";
import EmbeddedChartV2 from "../chart/EmbeddedChartV2";
import { cx } from "../components/primitives/cx";
import { LoadingRowsV2 } from "../dashboard/CardV2";
import type { ChainTrading } from "../fno/ChainTradingV2";
import { basketOn } from "../fno/desk";
import { PracticeFrame, TicketBody, useOrderTicket, type OrderTicketState } from "../fno/OrderTicketV2";
import type { BasketLeg } from "../fno/basket";
import { heldKey, type TicketTarget } from "../fno/ticketModel";
import type { Side } from "../fno/api";
import type { ChainLeg, LiveChain } from "../optionChain/types";
import { chainAround } from "../fno/desk";
import { CombinedExitBarV2 } from "../fno/CombinedExitV2";
import type { CombinedExit } from "../fno/combinedExit";
import { GroupExitSheetV2 } from "../fno/GroupExitsV2";
import { PositionActionSheetV2, WaitingOrdersV2, type PositionActionAsk } from "../fno/PositionActionsV2";
import type { GroupExitAsk } from "../fno/groupExits";
import { PositionList, useFnoPositions, type RowActions } from "../trading/PositionsV2";
import type { RollAsk } from "../fno/roll";
import type { MyDayArea } from "../dashboard/myDay";
import { closedText, exitConfirmText, failedText, heldRows } from "../trading/model";
import { NoteV2 } from "../trading/PartsV2";
import type { PortfolioExposure, TradeRecord } from "../types";
import { EMPTY, formatCompactIN, formatDateIST, formatNumber, formatPrice, formatSignedCompactIN, formatTimeIST } from "../ui/format";
import { Chip } from "../ui/Chip";
import { Signed } from "../ui/Signed";
import { SubTabs } from "../ui/SubTabs";
import type { ChartLive, TerminalSpot } from "./spot";
import { orderGroup, statusWords, type OrderEvent, type OrderGroup, type OrderRow, type TerminalApi } from "./api";
import { Details } from "../ui/numbersFirst";

/**
 * Trading Terminal T1: the panels. Each one wraps an existing part of ATP - nothing is rewritten:
 * - Chart: the v2 embedded chart (src/chart/EmbeddedChartV2.tsx, the chart core inside).
 * - Option chain: a compact ladder around the ATM (or his strikes) whose call / put cells open the existing action sheet
 *   of the chain's trading layer (src/fno/ChainTradingV2.tsx useChainTrading); his own strikes carry "P".
 * - Order: the ONE order ticket's body and confirm step (src/fno/OrderTicketV2.tsx TicketBody / Review through
 *   useOrderTicket) - the same order path as the chain's ticket, PAPER in Phase 0.
 * - Positions: the My trades rows (src/trading/PositionsV2.tsx PositionRowV2) with their own Exit now (never blocked,
 *   ADR-0004).
 * - Orders: his own orders (read only), Open / Executed / Rejected, each with its trail.
 * - Basket: his basket's legs; "Review and place" opens the existing basket panel (src/fno/BasketPanelV2.tsx).
 * - Bots & signals, Payoff: plain-word placeholders until their parts are built.
 */

export type Read<T> = { status: "loading" } | { status: "error"; message?: string } | { status: "ok"; value: T };

const MUTED = "text-t13 text-fg-muted";
const LINK = "inline-flex min-h-11 items-center font-semibold text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand lg:min-h-9";

export function EmptyText({ children, testId }: { children: ReactNode; testId?: string }) {
  return <p className={cx(MUTED, "py-space-2")} data-empty={testId}>{children}</p>;
}

// --- Chart ---------------------------------------------------------------------------------------------------------------

export function ChartPanelV2({ symbol, onSymbol, live, levels }: {
  symbol: string; onSymbol?: (symbol: string | null) => void;
  /** TERM-2 step 2: his legs, breakevens and own spot stops drawn on the chart (terminal/chartLevels.ts). */
  levels?: readonly LevelLine[];
  /** T1-3: the page's one spot when this chart shows the underlying (undefined: the chart reads its own price). */
  live?: ChartLive | null;
}) {
  const [draft, setDraft] = useState(symbol);
  useEffect(() => setDraft(symbol), [symbol]);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const clean = draft.trim().toUpperCase().replace(/[^A-Z0-9&_\- ]/g, "").slice(0, 30);
    onSymbol?.(clean || null);
  };
  return (
    <div className="flex h-full min-h-0 flex-col gap-space-1" data-panel-body="chart">
      {onSymbol && (
        <form onSubmit={submit} className="flex items-center gap-space-1" aria-label="Chart symbol">
          <label className="sr-only" htmlFor={`sym-${symbol}`}>Symbol</label>
          <input id={`sym-${symbol}`} value={draft} onChange={(e) => setDraft(e.target.value)} onBlur={submit} spellCheck={false}
            className="h-11 min-w-0 flex-1 rounded-control border border-border bg-surface-2 px-space-2 text-t13 uppercase text-fg focus:border-brand focus:outline-none lg:h-9" />
        </form>
      )}
      <div className="min-h-0 flex-1 [&>section]:flex [&>section]:h-full [&>section]:flex-col">
        <EmbeddedChartV2 symbol={symbol} className="min-h-[12rem] flex-1" live={live} levels={levels} />
      </div>
    </div>
  );
}

// --- Option chain --------------------------------------------------------------------------------------------------------

/** The chain's error as a trader reads it: without a broker the chain has no prices to show. */
export function chainProblem(read: Read<LiveChain>): string | null {
  if (read.status !== "error") return null;
  return "Connect your broker to see live prices on the option chain.";
}

/** T1-3: one side's figures as traders read them; a missing figure is "–", never 0. */
export function legFigures(leg: ChainLeg): { oi: string; oiChange: string; iv: string; delta: string; ltp: string } {
  return {
    oi: leg.oi === null ? EMPTY : formatCompactIN(leg.oi),
    oiChange: leg.oi_change === null ? EMPTY : formatSignedCompactIN(leg.oi_change),
    iv: leg.iv === null ? EMPTY : formatNumber(leg.iv, 1),
    delta: leg.delta === null ? EMPTY : formatNumber(leg.delta, 2),
    ltp: leg.ltp === null ? EMPTY : formatPrice(leg.ltp),
  };
}

/** The chain's spot line: the page's one spot (T1-3, the chart's too) with its time, once. */
export function spotLine(spot: TerminalSpot | null | undefined, chain: LiveChain): string {
  const value = spot?.ltp ?? chain.summary.spot;
  if (value === null || value === undefined) return "";
  const word = (spot?.source ?? "chain") === "chain" && chain.summary.spot_source === "last_close" ? "last close" : "spot";
  const at = spot?.at ?? chain.data_as_of ?? null;
  return ` · ${word} ${formatPrice(value)}${at ? ` (as of ${formatTimeIST(at)})` : ""}`;
}

const SIDE_COLUMNS = [["oi", "OI"], ["oiChange", "Chg OI"], ["iv", "IV %"], ["delta", "Delta"]] as const;

export function ChainPanelV2({ underlying, read, trading, rows = 6, spot, compact = false }: {
  underlying: string; read: Read<LiveChain>; trading: ChainTrading; rows?: number;
  /** T1-3: the page's one spot (the chart's too); without it the chain's own. */
  spot?: TerminalSpot | null;
  /** Phone / tablet: LTP + OI per side, a row opens to the rest (T1-3). */
  compact?: boolean;
}) {
  const [open, setOpen] = useState<number | null>(null);
  if (!trading.enabled) {
    return <EmptyText testId="chain-off">Trading from the chain needs your practice account. Turn it on in Settings to place practice orders.</EmptyText>;
  }
  if (read.status === "loading") return <LoadingRowsV2 rows={4} />;
  if (read.status === "error") {
    return (
      <div data-empty="chain-error" className="space-y-space-2 py-space-2">
        <p className={MUTED}>{chainProblem(read)}</p>
        <Link to={BROKER_PATH} className={LINK}>Connect your broker</Link>
      </div>
    );
  }
  const chain = read.value;
  const heldStrikes = chain.rows.filter((r) => trading.held.has(heldKey(r.strike, "CE")) || trading.held.has(heldKey(r.strike, "PE"))).map((r) => r.strike);
  const shown = chainAround(chain.rows, heldStrikes, chain.summary.atm_strike, rows);
  if (!shown.length) return <EmptyText testId="chain-empty">The chain on this expiry has no strikes to show yet.</EmptyText>;
  const atm = chain.summary.atm_strike;
  const ltpCell = (r: (typeof shown)[number], side: "call" | "put") => {
    const right = side === "call" ? "CE" : "PE";
    const mine = trading.held.has(heldKey(r.strike, right));
    const ltp = r[side].ltp;
    return (
      <td className="p-0">
        <button type="button" onClick={() => trading.onPickLeg?.(r, side)} data-cell={`${side}-${r.strike}`}
          aria-label={`${underlying} ${formatNumber(r.strike, 0)} ${right}${ltp === null ? "" : `, ${formatPrice(ltp)}`}${mine ? ", you hold it" : ""}`}
          className={cx("flex min-h-11 w-full items-center justify-end gap-space-1 whitespace-nowrap px-space-1 text-right font-tabular text-t13 hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand lg:min-h-9",
            mine ? "font-semibold text-brand" : "text-fg")}>
          {mine && <span className="rounded-pill bg-brand px-space-1 text-t12 text-on-brand" aria-hidden>P</span>}
          {ltp === null ? EMPTY : formatPrice(ltp)}
        </button>
      </td>
    );
  };
  const fig = (v: string, key: string) => <td key={key} className="whitespace-nowrap px-0.5 text-right font-tabular text-t13 text-fg-muted" data-col={key}>{v}</td>;
  const strikeCell = (r: (typeof shown)[number]) => {
    const label = formatNumber(r.strike, 0);
    const tone = r.strike === atm ? "text-brand" : "text-fg";
    if (!compact) return <td className={cx("whitespace-nowrap px-space-1 text-center font-tabular font-semibold", tone)}>{label}</td>;
    const expanded = open === r.strike;
    return (
      <td className="p-0">
        <button type="button" aria-expanded={expanded} aria-controls={`chain-more-${r.strike}`} onClick={() => setOpen(expanded ? null : r.strike)}
          aria-label={`${label}: ${expanded ? "hide" : "show"} change in OI, IV and delta`} data-expand={r.strike}
          className={cx("flex min-h-11 w-full items-center justify-center gap-space-1 font-tabular font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand", tone)}>
          {label}
          <ChevronDown size={14} aria-hidden className={cx("text-fg-muted transition-transform", expanded && "rotate-180")} />
        </button>
      </td>
    );
  };
  const head = "whitespace-nowrap px-0.5 font-medium";
  return (
    <div className="space-y-space-2" data-panel-body="chain" data-chain-view={compact ? "compact" : "full"}>
      <p className={MUTED} data-chain-spot>
        {underlying}{chain.expiry ? ` · ${formatDateIST(chain.expiry)} expiry` : ""}{spotLine(spot, chain)}
      </p>
      <div className="overflow-x-auto">
        <table className={cx("w-full text-t13", compact && "table-fixed")} data-chain-ladder>
          <thead>
            <tr className="text-t12 text-fg-muted">
              {compact
                ? <><th className={cx(head, "text-right")}>Call OI</th><th className={cx(head, "text-right")}>Call</th></>
                : <>{[...SIDE_COLUMNS].map(([k, l]) => <th key={`c-${k}`} className={cx(head, "text-right")}>{l}</th>)}<th className={cx(head, "text-right")}>Call LTP</th></>}
              <th className={cx(head, "text-center")}>Strike</th>
              {compact
                ? <><th className={cx(head, "text-right")}>Put</th><th className={cx(head, "text-right")}>Put OI</th></>
                : <><th className={cx(head, "text-right")}>Put LTP</th>{[...SIDE_COLUMNS].reverse().map(([k, l]) => <th key={`p-${k}`} className={cx(head, "text-right")}>{l}</th>)}</>}
            </tr>
          </thead>
          <tbody className="divide-y divide-subtle">
            {shown.map((r) => {
              const c = legFigures(r.call);
              const p = legFigures(r.put);
              return (
                <Fragment key={r.strike}>
                  <tr data-strike={r.strike} data-atm={r.strike === atm ? "" : undefined}>
                    {compact ? fig(c.oi, "call-oi") : [...SIDE_COLUMNS].map(([k]) => fig(c[k], `call-${k}`))}
                    {ltpCell(r, "call")}
                    {strikeCell(r)}
                    {ltpCell(r, "put")}
                    {compact ? fig(p.oi, "put-oi") : [...SIDE_COLUMNS].reverse().map(([k]) => fig(p[k], `put-${k}`))}
                  </tr>
                  {compact && open === r.strike && (
                    <tr id={`chain-more-${r.strike}`} data-more={r.strike} className="bg-surface-2">
                      <td colSpan={5} className="px-space-2 py-space-2">
                        <dl className="grid grid-cols-2 gap-x-space-3 gap-y-space-1 font-tabular text-t13">
                          <div><dt className="text-fg-muted">Call · Chg OI / IV % / Delta</dt><dd className="text-fg">{c.oiChange} · {c.iv} · {c.delta}</dd></div>
                          <div><dt className="text-fg-muted">Put · Chg OI / IV % / Delta</dt><dd className="text-fg">{p.oiChange} · {p.iv} · {p.delta}</dd></div>
                        </dl>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      {/* UX-11 U1: how to use the chain, on demand. */}
      <Details id="terminal.chain" className="mt-space-2">
        <p>Tap a price to buy, sell or add it to your basket. P: a contract you hold. The ATM strike is in colour.{compact ? " Tap a strike for change in OI, IV and delta." : ""}</p>
      </Details>
    </div>
  );
}

// --- Order ---------------------------------------------------------------------------------------------------------------

/** The order panel's ticket state, held by the page (so a draft survives the panel being closed, moved or shown as a tab). */
export function useTerminalTicket(ticket: { target: TicketTarget; side: Side } | null, chainRows: LiveChain["rows"] | null, onPlaced: () => void): OrderTicketState {
  return useOrderTicket({ open: ticket !== null, target: ticket?.target ?? null, side: ticket?.side ?? "BUY", chainRows, onPlaced: () => onPlaced(), shortcuts: false });
}

export function OrderPanelV2({ ticket, state: t, onClear, paperBadge = true }: {
  ticket: { target: TicketTarget; side: Side } | null; state: OrderTicketState; onClear: () => void;
  /** TERM-2 step 5 / CT-1 C8: false where the page already shows its one PAPER badge. */
  paperBadge?: boolean;
}) {
  if (!ticket) {
    return (
      <EmptyText testId="order-empty">
        Tap a price on the chain to start an order.
      </EmptyText>
    );
  }
  const target = ticket.target;
  const label = target.label ?? (target.right === "FUT" ? `${target.underlying} ${target.expiry} FUT` : `${target.underlying} ${target.strike} ${target.right}`);
  return (
    <div className="-mx-space-3 -mb-space-3 flex flex-col md:h-full md:min-h-0 md:overflow-hidden" data-panel-body="order" data-step={t.step.kind}>
      <div className="px-space-4 pt-space-2">
        <div className="flex min-w-0 items-center gap-space-2">
          <p className="truncate text-t15 font-semibold text-fg">{label}</p>
          {paperBadge && <PracticeFrame />}
        </div>
        <p className="text-t13 font-tabular text-fg-muted">LTP {formatPrice(target.ltp)} · Bid {formatPrice(target.bid)} · Ask {formatPrice(target.ask)}</p>
      </div>
      <TicketBody target={target} draft={t.draft} step={t.step} busy={t.busy} problem={t.problem} idPrefix={t.idPrefix} defaults={t.defaults}
        budget={t.budget} events={t.events} liquidity={t.liquidity} keysHint={false} stickyActions
        onDraft={t.onDraft} onReview={() => void t.review()} onPlace={() => void t.place()} onReviewWith={(patch) => void t.review(patch)} onBack={t.back} onClose={onClear} />
    </div>
  );
}

// --- Positions -----------------------------------------------------------------------------------------------------------

// CT-1 T4: Exit all moved into the one position list (trading/ExitAllV2.tsx); re-exported for older callers.
export { exitAllConfirmText, exitAllConsequence, exitAllPlan } from "../trading/ExitAllV2";

export function PositionsPanelV2({ read, exposure = null, canTrade, exitNowOn, fnoOn = false, combined, onChanged, roll, areas }: {
  read: Read<TradeRecord[]>; exposure?: PortfolioExposure | null; canTrade: boolean; exitNowOn: boolean;
  /** The PAPER account on: basket legs grouped with "Exit the whole group" (TT-8), as on My trades. */
  fnoOn?: boolean;
  /** CX6: his combined exits (the page reads them once for this panel and the status bar); absent: no button, no chip. */
  combined?: { list: readonly CombinedExit[]; reload: () => void };
  onChanged: () => void;
  /** TERM-2 R1 (flag term_roll): "Roll" on F&O PAPER positions and groups (the page holds the roll's sheet, so a panel
   *  that re-renders never drops it half way). */
  roll?: { onRoll: (ask: RollAsk) => void };
  /** TERM-2 R3 (flag term_strike_area): each short strike's nearest area, by trade id. */
  areas?: ReadonlyMap<number, MyDayArea>;
}) {
  const [note, setNote] = useState<{ kind: "done" | "info" | "warn"; text: ReactNode } | null>(null);
  const [groupExit, setGroupExit] = useState<GroupExitAsk | null>(null);
  const [partAsk, setPartAsk] = useState<PositionActionAsk | null>(null);   // T2 (TT-9): Exit part / Reverse
  const [waitingTick, setWaitingTick] = useState(0);
  const list = useMemo(() => (read.status === "ok" ? read.value : []), [read]);
  const rows = useMemo(() => heldRows(list, exposure), [list, exposure]);
  const fno = useFnoPositions(list, fnoOn && canTrade);
  if (read.status === "loading") return <LoadingRowsV2 rows={3} />;
  if (read.status === "error") return <EmptyText testId="positions-error">We could not load your positions. We will try again in a moment.</EmptyText>;
  if (!rows.length) {
    return (
      <div className="space-y-space-2" data-panel-body="positions">
        <EmptyText testId="positions-empty">Your trades show here as soon as an order fills.</EmptyText>
        {fnoOn && canTrade && <WaitingOrdersV2 />}
      </div>
    );
  }
  // The My trades "Exit now", unchanged: its confirm, then the existing exit. Never disabled here (ADR-0004).
  async function exitNow(p: TradeRecord) {
    if (!window.confirm(exitConfirmText(p))) return;
    try {
      const result = await api.exitNow(p.id);
      const first = result.closed[0];
      setNote({ kind: "done", text: `${p.symbol}: ${closedText(first?.exit_price, first?.pnl, first?.exit_reason)}` });
      onChanged();
    } catch (e) {
      setNote({ kind: "warn", text: failedText(`${p.symbol} was not closed.`, e) });
    }
  }
  // With "Exit now" off, a price checked by hand closes the position if it reaches its stop or target (as on My trades).
  async function checkPrice(p: TradeRecord, price: number) {
    try {
      const result = await api.markPrice(p.id, price);
      setNote(result.closed
        ? { kind: "done", text: `${p.symbol}: ${closedText(result.exit_price, result.pnl, result.exit_reason)}` }
        : { kind: "info", text: `${p.symbol} is still open: ${formatPrice(price)} does not reach its stop or target.` });
      if (result.closed) onChanged();
    } catch (e) {
      setNote({ kind: "warn", text: failedText(`We could not check the price of ${p.symbol}.`, e) });
    }
  }
  const byId = new Map(list.map((p) => [p.id, p]));
  const actions: RowActions = {
    exitNowOn, canTrade, onExit: (p) => void exitNow(p), onCheck: (p, price) => void checkPrice(p, price),
    group: fnoOn && canTrade ? {
      onLeg: (p, hedge) => setGroupExit({ kind: "leg", tradeId: p.id, symbol: p.symbol, hedge, warning: null }),
      onGroup: (tradeId, title, legs) => setGroupExit({ kind: "group", tradeId, title, legs }),
    } : undefined,
    part: fnoOn && canTrade ? {
      onPart: (p) => setPartAsk({ kind: "part", position: p }), onReverse: (p) => setPartAsk({ kind: "reverse", position: p }),
    } : undefined,
    roll: roll && fnoOn && canTrade ? roll : undefined,
    areas,
  };
  // UX-15 (owner, 28 Oct): total P&L first (the broker-standard header); Exit all only with two or more positions - each
  // position keeps its own Exit, never hidden or blocked (ADR-0004).
  const known = rows.filter((r) => r.pnl !== null);
  const total = known.length ? known.reduce((a, r) => a + (r.pnl ?? 0), 0) : null;
  return (
    <div className="space-y-space-2" data-panel-body="positions">
      {note && <NoteV2 kind={note.kind} testId="terminal-exit">{note.text}</NoteV2>}
      <div className="flex flex-wrap items-end justify-between gap-space-2" data-positions-total>
        <div>
          <div className="text-t12 text-fg-muted">Total P&amp;L</div>
          {total === null
            ? <Chip tone="warn" variant="meaning" icon="!" name="no-price">Waiting for a live price</Chip>
            : <Signed value={total} kind="money" paise className="text-t20 font-semibold" />}
          {total !== null && known.length < rows.length && <Chip tone="warn" variant="meaning" icon="!" name="some-prices" className="ml-space-2">{rows.length - known.length} waiting for a price</Chip>}
        </div>
        <span className="text-t12 text-fg-muted">{rows.length} open</span>
      </div>
      {combined && fnoOn && canTrade && (
        <CombinedExitBarV2 positions={list} facts={fno.facts} exits={combined.list}
          onChanged={() => { combined.reload(); onChanged(); }} onNote={(text, ok) => setNote({ kind: ok ? "done" : "warn", text })} />
      )}
      <PositionList rows={rows} byId={byId} actions={actions} prices groups={fno.groups}
        exitAll={{ onChanged, onNote: (text, ok) => setNote({ kind: ok ? "done" : "warn", text }) }} />
      <GroupExitSheetV2 ask={groupExit} onClose={() => setGroupExit(null)}
        onDone={(text, ok) => { setGroupExit(null); setNote({ kind: ok ? "done" : "warn", text }); onChanged(); }} />
      <PositionActionSheetV2 ask={partAsk} onClose={() => setPartAsk(null)}
        onDone={(text, ok) => { setPartAsk(null); setNote({ kind: ok ? "done" : "warn", text }); setWaitingTick((n) => n + 1); onChanged(); }} />
      {fnoOn && canTrade && <WaitingOrdersV2 refresh={waitingTick} onChanged={onChanged} />}
    </div>
  );
}

// --- Orders --------------------------------------------------------------------------------------------------------------

const GROUPS: { key: OrderGroup; label: string }[] = [{ key: "open", label: "Open" }, { key: "executed", label: "Executed" }, { key: "rejected", label: "Rejected" },
  { key: "cancelled", label: "Cancelled" }];

export function OrdersPanelV2({ read, terminal }: { read: Read<OrderRow[]>; terminal: Pick<TerminalApi, "events"> }) {
  const [group, setGroup] = useState<OrderGroup>("executed");
  const [openId, setOpenId] = useState<number | null>(null);
  const [trail, setTrail] = useState<Read<OrderEvent[]>>({ status: "loading" });
  useEffect(() => {
    if (openId === null) return undefined;
    let live = true;
    setTrail({ status: "loading" });
    terminal.events(openId).then((value) => { if (live) setTrail({ status: "ok", value }); }, () => { if (live) setTrail({ status: "error" }); });
    return () => { live = false; };
  }, [openId, terminal]);
  if (read.status === "loading") return <LoadingRowsV2 rows={3} />;
  if (read.status === "error") return <EmptyText testId="orders-error">We could not load your orders. We will try again in a moment.</EmptyText>;
  const counts = { open: 0, executed: 0, rejected: 0, cancelled: 0 } as Record<OrderGroup, number>;
  for (const o of read.value) counts[orderGroup(o.status)] += 1;
  const shown = read.value.filter((o) => orderGroup(o.status) === group);
  return (
    <div className="space-y-space-2" data-panel-body="orders">
      {/* UI-H: the shared SubTabs (a choice inside the panel), as every tab row of the app. */}
      <SubTabs label="Orders" value={group} onChange={setGroup} size="sm"
        items={GROUPS.map((g) => ({ key: g.key, label: <>{g.label}<span className="ml-space-1 font-tabular">{counts[g.key]}</span></>, ariaLabel: `${g.label} ${counts[g.key]}`, data: { "orders-tab": g.key } }))} />
      {shown.length === 0 ? (
        <EmptyText testId={`orders-${group}-empty`}>
          {group === "open" ? "All orders are filled." : group === "executed" ? "Filled orders show here." : group === "cancelled" ? "No order was cancelled." : "Every order went through."}
        </EmptyText>
      ) : (
        <ul className="divide-y divide-subtle" data-orders-list={group}>
          {shown.map((o) => (
            <li key={o.id} className="py-space-1" data-order={o.id}>
              <button type="button" onClick={() => setOpenId(openId === o.id ? null : o.id)} aria-expanded={openId === o.id}
                className="flex min-h-11 w-full items-center gap-space-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand lg:min-h-9">
                {openId === o.id ? <ChevronDown size={14} aria-hidden className="shrink-0 text-fg-muted" /> : <ChevronRight size={14} aria-hidden className="shrink-0 text-fg-muted" />}
                <span className="min-w-0 flex-1 truncate text-t13 text-fg">
                  <span className={o.direction === "LONG" ? "text-up" : "text-down"}>{o.direction === "LONG" ? "Buy" : "Sell"}</span> {o.symbol}
                </span>
                <span className="shrink-0 font-tabular text-t13 text-fg-muted">{formatNumber(o.quantity, o.quantity % 1 ? 2 : 0)}</span>
                <span className="shrink-0 text-t12 text-fg-muted">{formatTimeIST(o.created_at)}</span>
              </button>
              <p className="pl-space-5 text-t12 text-fg-muted">{statusWords(o.status)}{o.mode === "PAPER" ? "" : " · Live"}</p>
              {group === "rejected" && o.reasons.length > 0 && <p className="pl-space-5 text-t12 text-warn">{o.reasons[0]}</p>}
              {openId === o.id && (
                <div className="pl-space-5" data-order-trail={o.id}>
                  {trail.status === "loading" ? <p className={MUTED}>Loading the order&apos;s steps…</p>
                    : trail.status === "error" ? <p className={MUTED}>The order&apos;s steps could not be read just now.</p>
                      : trail.value.length === 0 ? <p className={MUTED}>Each step shows here as it happens.</p> : (
                        <ol className="space-y-px text-t12 text-fg-muted">
                          {trail.value.map((e) => <li key={e.id}><span className="font-tabular">{formatTimeIST(e.created_at)}</span> · {statusWords(e.to_status)}</li>)}
                        </ol>
                      )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// --- Basket --------------------------------------------------------------------------------------------------------------

export function BasketPanelBodyV2({ underlying, legs, onOpen }: { underlying: string; legs: readonly BasketLeg[]; onOpen?: () => void }) {
  const mine = basketOn(legs, underlying);
  return (
    <div className="space-y-space-2" data-panel-body="basket">
      {mine.length === 0 ? (
        <EmptyText testId="basket-empty">Your basket on {underlying} is empty. Tap a price on the option chain and choose Add to basket to build a multi-leg order.</EmptyText>
      ) : (
        <ul className="divide-y divide-subtle text-t13" data-basket-legs>
          {mine.map((l) => (
            <li key={l.id} className="flex items-baseline justify-between gap-space-2 py-space-1">
              <span className="min-w-0 truncate text-fg">
                <span className={l.side === "SELL" ? "text-down" : "text-up"}>{l.side === "SELL" ? "Sell" : "Buy"}</span> {l.label ?? `${formatNumber(l.strike, 0)} ${l.right}`}
              </span>
              <span className="shrink-0 font-tabular text-fg-muted">{l.lots} lot{l.lots === 1 ? "" : "s"}</span>
            </li>
          ))}
        </ul>
      )}
      {onOpen && (
        <button type="button" onClick={onOpen} data-basket-open
          className="inline-flex min-h-11 items-center gap-space-2 rounded-control border border-border px-space-3 text-t14 font-semibold text-fg hover:bg-surface-2 lg:min-h-9">
          <ShoppingBasket size={16} aria-hidden />{mine.length ? "Review and place" : "Open the basket"}
        </button>
      )}
    </div>
  );
}

// --- Later parts ---------------------------------------------------------------------------------------------------------

export function PlaceholderPanelV2({ kind }: { kind: "bots" | "payoff" }) {
  if (kind === "bots") {
    return (
      <div className="space-y-space-2" data-panel-body="bots">
        <p className="flex items-center gap-space-2 text-t14 font-semibold text-fg"><Bot size={16} aria-hidden className="text-brand" />Coming in a later update</p>
        <p className={MUTED}>Your running bots and the signals waiting for you will show here, with pause and exit for each bot. Until then, use the Signals and Trading bot pages.</p>
        <div className="flex flex-wrap gap-x-space-4">
          <Link to="/signals" className={LINK}>Open Signals</Link>
          <Link to="/deployments" className={LINK}>Open Trading bot</Link>
        </div>
      </div>
    );
  }
  return (
    <div className="space-y-space-2" data-panel-body="payoff">
      <p className="flex items-center gap-space-2 text-t14 font-semibold text-fg"><LineChart size={16} aria-hidden className="text-brand" />Coming in a later update</p>
      <p className={MUTED}>The payoff of your basket and your open positions, with what-if, will show here. Until then, the basket shows its payoff before you place it.</p>
    </div>
  );
}
