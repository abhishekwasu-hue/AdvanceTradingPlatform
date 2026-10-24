import { Bot, ChevronDown, ChevronRight, LineChart, ShoppingBasket } from "lucide-react";
import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router";
import { api } from "../api/client";
import { BROKER_PATH } from "../broker/paths";
import EmbeddedChartV2 from "../chart/EmbeddedChartV2";
import { cx } from "../components/primitives/cx";
import { LoadingRowsV2 } from "../dashboard/CardV2";
import type { ChainTrading } from "../fno/ChainTradingV2";
import { basketOn } from "../fno/desk";
import { PracticeFrame, TicketBody, useOrderTicket, type OrderTicketState } from "../fno/OrderTicketV2";
import type { BasketLeg } from "../fno/basket";
import { heldKey, type TicketTarget } from "../fno/ticketModel";
import type { Side } from "../fno/api";
import type { LiveChain } from "../optionChain/types";
import { chainAround } from "../fno/desk";
import { PositionRowV2, type RowActions } from "../trading/PositionsV2";
import { closedText, exitConfirmText, failedText, heldRows } from "../trading/model";
import { NoteV2 } from "../trading/PartsV2";
import type { TradeRecord } from "../types";
import { EMPTY, formatDateIST, formatNumber, formatPrice, formatTimeIST } from "../ui/format";
import { orderGroup, statusWords, type OrderEvent, type OrderGroup, type OrderRow, type TerminalApi } from "./api";

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

export function ChartPanelV2({ symbol, onSymbol }: { symbol: string; onSymbol?: (symbol: string | null) => void }) {
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
        <EmbeddedChartV2 symbol={symbol} className="min-h-[12rem] flex-1" />
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

export function ChainPanelV2({ underlying, read, trading, rows = 6 }: {
  underlying: string; read: Read<LiveChain>; trading: ChainTrading; rows?: number;
}) {
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
  const cell = (r: (typeof shown)[number], side: "call" | "put") => {
    const right = side === "call" ? "CE" : "PE";
    const mine = trading.held.has(heldKey(r.strike, right));
    const ltp = r[side].ltp;
    return (
      <td className="p-0">
        <button type="button" onClick={() => trading.onPickLeg?.(r, side)} data-cell={`${side}-${r.strike}`}
          aria-label={`${underlying} ${formatNumber(r.strike, 0)} ${right}${ltp === null ? "" : `, ${formatPrice(ltp)}`}${mine ? ", you hold it" : ""}`}
          className={cx("flex min-h-11 w-full items-center justify-end gap-space-1 px-space-2 text-right font-tabular text-t13 hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand lg:min-h-9",
            mine ? "font-semibold text-brand" : "text-fg")}>
          {mine && <span className="rounded-pill bg-brand px-space-1 text-t12 text-on-brand" aria-hidden>P</span>}
          {ltp === null ? EMPTY : formatPrice(ltp)}
        </button>
      </td>
    );
  };
  return (
    <div className="space-y-space-2" data-panel-body="chain">
      <p className={MUTED}>
        {underlying}{chain.expiry ? ` · ${formatDateIST(chain.expiry)} expiry` : ""}{chain.summary.spot !== null ? ` · spot ${formatPrice(chain.summary.spot)}` : ""}
      </p>
      <table className="w-full table-fixed text-t13" data-chain-ladder>
        <thead>
          <tr className="text-t12 text-fg-muted">
            <th className="px-space-2 text-right font-medium">Call</th>
            <th className="px-space-2 text-center font-medium">Strike</th>
            <th className="px-space-2 text-right font-medium">Put</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-subtle">
          {shown.map((r) => (
            <tr key={r.strike} data-strike={r.strike} data-atm={r.strike === atm ? "" : undefined}>
              {cell(r, "call")}
              <td className={cx("px-space-2 text-center font-tabular font-semibold", r.strike === atm ? "text-brand" : "text-fg")}>{formatNumber(r.strike, 0)}</td>
              {cell(r, "put")}
            </tr>
          ))}
        </tbody>
      </table>
      <p className={MUTED}>Tap a price to buy, sell or add it to your basket. P: a contract you hold. The ATM strike is in colour.</p>
    </div>
  );
}

// --- Order ---------------------------------------------------------------------------------------------------------------

/** The order panel's ticket state, held by the page (so a draft survives the panel being closed, moved or shown as a tab). */
export function useTerminalTicket(ticket: { target: TicketTarget; side: Side } | null, chainRows: LiveChain["rows"] | null, onPlaced: () => void): OrderTicketState {
  return useOrderTicket({ open: ticket !== null, target: ticket?.target ?? null, side: ticket?.side ?? "BUY", chainRows, onPlaced: () => onPlaced(), shortcuts: false });
}

export function OrderPanelV2({ ticket, state: t, onClear }: {
  ticket: { target: TicketTarget; side: Side } | null; state: OrderTicketState; onClear: () => void;
}) {
  if (!ticket) {
    return (
      <EmptyText testId="order-empty">
        Pick a price on the option chain to start an order. The order is checked and shown to you before anything is placed.
      </EmptyText>
    );
  }
  const target = ticket.target;
  const label = target.label ?? (target.right === "FUT" ? `${target.underlying} ${target.expiry} FUT` : `${target.underlying} ${target.strike} ${target.right}`);
  return (
    <div className="-mx-space-3 -mb-space-3 flex h-full min-h-0 flex-col overflow-hidden" data-panel-body="order" data-step={t.step.kind}>
      <PracticeFrame />
      <div className="px-space-4 pt-space-2">
        <p className="truncate text-t15 font-semibold text-fg">{label}</p>
        <p className="text-t13 font-tabular text-fg-muted">LTP {formatPrice(target.ltp)} · Bid {formatPrice(target.bid)} · Ask {formatPrice(target.ask)}</p>
      </div>
      <TicketBody target={target} draft={t.draft} step={t.step} busy={t.busy} problem={t.problem} idPrefix={t.idPrefix} defaults={t.defaults}
        budget={t.budget} events={t.events} liquidity={t.liquidity} keysHint={false}
        onDraft={t.onDraft} onReview={() => void t.review()} onPlace={() => void t.place()} onBack={t.back} onClose={onClear} />
    </div>
  );
}

// --- Positions -----------------------------------------------------------------------------------------------------------

export function PositionsPanelV2({ read, canTrade, exitNowOn, onChanged }: {
  read: Read<TradeRecord[]>; canTrade: boolean; exitNowOn: boolean; onChanged: () => void;
}) {
  const [note, setNote] = useState<{ kind: "done" | "info" | "warn"; text: string } | null>(null);
  const list = read.status === "ok" ? read.value : [];
  const rows = useMemo(() => heldRows(list, null), [list]);
  if (read.status === "loading") return <LoadingRowsV2 rows={3} />;
  if (read.status === "error") return <EmptyText testId="positions-error">We could not load your positions. We will try again in a moment.</EmptyText>;
  if (!rows.length) return <EmptyText testId="positions-empty">No open positions. Your trades show here as soon as an order fills.</EmptyText>;
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
  const actions: RowActions = { exitNowOn, canTrade, onExit: (p) => void exitNow(p), onCheck: (p, price) => void checkPrice(p, price) };
  const byId = new Map(list.map((p) => [p.id, p]));
  return (
    <div className="space-y-space-2" data-panel-body="positions">
      {note && <NoteV2 kind={note.kind} testId="terminal-exit">{note.text}</NoteV2>}
      <ul className="divide-y divide-subtle">
        {rows.map((r) => {
          const p = byId.get(r.id);
          return p ? <PositionRowV2 key={r.id} row={r} position={p} actions={actions} prices={false} /> : null;
        })}
      </ul>
      <p className={MUTED}>Profit or loss of each position is on the status bar and on My trades.</p>
    </div>
  );
}

// --- Orders --------------------------------------------------------------------------------------------------------------

const GROUPS: { key: OrderGroup; label: string }[] = [{ key: "open", label: "Open" }, { key: "executed", label: "Executed" }, { key: "rejected", label: "Rejected" }];

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
  const counts = { open: 0, executed: 0, rejected: 0 } as Record<OrderGroup, number>;
  for (const o of read.value) counts[orderGroup(o.status)] += 1;
  const shown = read.value.filter((o) => orderGroup(o.status) === group);
  return (
    <div className="space-y-space-2" data-panel-body="orders">
      <div role="tablist" aria-label="Orders" className="flex gap-space-1 rounded-control bg-surface-2 p-space-1">
        {GROUPS.map((g) => (
          <button key={g.key} type="button" role="tab" aria-selected={group === g.key} onClick={() => setGroup(g.key)} data-orders-tab={g.key}
            className={cx("min-h-11 flex-1 rounded-control px-space-2 text-t13 font-semibold lg:min-h-9", group === g.key ? "bg-surface-1 text-fg shadow-card" : "text-fg-muted hover:text-fg")}>
            {g.label} <span className="font-tabular text-fg-muted">{counts[g.key]}</span>
          </button>
        ))}
      </div>
      {shown.length === 0 ? (
        <EmptyText testId={`orders-${group}-empty`}>
          {group === "open" ? "No orders waiting. Practice orders fill at once at the current price." : group === "executed" ? "No orders filled yet." : "No refused orders."}
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
              <p className="pl-space-5 text-t12 text-fg-muted">{statusWords(o.status)}{o.mode === "PAPER" ? " · Practice" : ""}</p>
              {group === "rejected" && o.reasons.length > 0 && <p className="pl-space-5 text-t12 text-warn">{o.reasons[0]}</p>}
              {openId === o.id && (
                <div className="pl-space-5" data-order-trail={o.id}>
                  {trail.status === "loading" ? <p className={MUTED}>Loading the order&apos;s steps…</p>
                    : trail.status === "error" ? <p className={MUTED}>The order&apos;s steps could not be read just now.</p>
                      : trail.value.length === 0 ? <p className={MUTED}>No steps recorded for this order.</p> : (
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
        <p className={MUTED}>Your running bots and the signals waiting for you will show here, with pause and exit for each bot. Until then, use the Signals and Paper bot pages.</p>
        <div className="flex flex-wrap gap-x-space-4">
          <Link to="/signals" className={LINK}>Open Signals</Link>
          <Link to="/deployments" className={LINK}>Open Paper bot</Link>
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
