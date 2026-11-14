import { Suspense, lazy, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "../api/client";
import { useOptionalAuth } from "../auth/AuthContext";
import { cx } from "../components/primitives/cx";
import { LOOKUP_MAX, contractsApi, type ContractFacts } from "../fno/contracts";
import type { Side } from "../fno/api";
import { closedText, exitConfirmText, failedText } from "../trading/model";
import type { TradeRecord } from "../types";
import { formatPrice } from "../ui/format";
import { useFeatures } from "../ui/useFeatures";
import { chartTicketTarget, heldOnChart, heldWords, positionItems, tradableOnChart, type ChartTicket } from "./chartTrade";
import { itemKey } from "./watchlistsV2";

const OrderTicketV2 = lazy(() => import("../fno/OrderTicketV2"));

const focus = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";
const BTN = cx("inline-flex min-h-11 min-w-11 items-center justify-center rounded-control px-space-3 text-t13 font-semibold md:min-h-8", focus);

export interface ChartTradeBarProps {
  /** The chart's instrument (an F&O contract shows the bar; anything else shows nothing). */
  symbol: string;
  exchange: string;
  /** The chart's own last price for this contract (the ticket's price helper); null while not known. */
  ltp?: number | null;
  /** The held chip (side, qty, avg) - off where the host already shows the position (the position chart's strip). */
  heldChip?: boolean;
  /** Given, Buy / Sell hand the ticket to the host (the Terminal's order panel, the same ticket state); absent, the ONE
   * order ticket opens as its own sheet. */
  onTicket?: (t: ChartTicket) => void;
  className?: string;
}

/**
 * CTB-1 (owner 14 Nov: "Give Buy / Sell buttons; entry and exit must be possible right from the chart"): Buy / Sell (and
 * Exit while he holds the contract) on a chart window's header.
 *
 * - Buy / Sell open the ONE order ticket (src/fno/OrderTicketV2.tsx) on this chart's contract - the contract and its
 *   last price only; his own lots / product / order type come from his settings, or the ticket asks. Review, Place, the
 *   PAPER / LIVE choice and every check are the ticket's own, unchanged. Shown with the PAPER account on, as on the chain.
 * - Exit runs the existing "Exit now" of that position (its confirm, then the existing exit; PAPER). Never disabled here
 *   (ADR-0004). A LIVE position is closed in his broker's app, as on My trades.
 * - Keyboard: none of its own (the ticket keeps its B / S keys once open). 44 px targets on a phone.
 * Nothing here places an order by itself; the AI never opens it (ADR-0006).
 */
export default function ChartTradeBar({ symbol, exchange, ltp = null, heldChip = true, onTicket, className }: ChartTradeBarProps) {
  const user = useOptionalAuth()?.user ?? null;   // no sign-in (or no provider): no bar
  const features = useFeatures();
  const canTrade = !!user && user.role !== "VIEWER";
  const entryOn = features.on("ui_v2") && features.on("paper_account");
  const exitOn = features.on("ui_v2") && (features.on("approval_only_entries") || features.on("paper_account"));
  const chart = useMemo(() => ({ symbol: symbol.trim().toUpperCase(), exchange: exchange.trim().toUpperCase() }), [symbol, exchange]);
  const on = canTrade && (entryOn || exitOn) && !!chart.symbol && tradableOnChart(chart.exchange);

  const [facts, setFacts] = useState<ReadonlyMap<string, ContractFacts>>(new Map());
  const [positions, setPositions] = useState<TradeRecord[]>([]);
  const [round, setRound] = useState(0);
  const [ticket, setTicket] = useState<ChartTicket | null>(null);
  const [note, setNote] = useState<{ kind: "done" | "warn"; text: string } | null>(null);

  useEffect(() => {
    if (!on) { setFacts(new Map()); setPositions([]); return undefined; }
    let live = true;
    (async () => {
      // A failed read of his positions only leaves Exit out (Buy / Sell stay); a failed lookup shows nothing.
      const held = await api.listPositions().catch(() => [] as TradeRecord[]);
      const items = [chart, ...positionItems(held).filter((i) => itemKey(i) !== itemKey(chart))].slice(0, LOOKUP_MAX);
      const read = await contractsApi.lookup(items).catch(() => ({ items: [] as ContractFacts[] }));
      if (!live) return;
      setPositions(held);
      setFacts(new Map(read.items.map((f) => [itemKey(f), f])));
    })();
    return () => { live = false; };
  }, [on, chart, round]);

  if (!on) return null;
  const own = facts.get(itemKey(chart));
  const target = chartTicketTarget(own, ltp);
  const held = heldOnChart(chart, positions, facts);
  const paperHeld = exitOn ? held.filter((p) => p.mode === "PAPER") : [];
  const liveHeld = held.some((p) => p.mode === "LIVE");
  if (!(entryOn && target) && !paperHeld.length && !liveHeld && !note) return null;

  const open = (side: Side) => {
    if (!target) return;
    const t = { target, side };
    setNote(null);
    if (onTicket) onTicket(t); else setTicket(t);
  };
  // The My trades "Exit now", unchanged: its confirm, then the existing exit (UI-11). Never disabled (ADR-0004).
  async function exit(p: TradeRecord) {
    if (!window.confirm(exitConfirmText(p))) return;
    try {
      const result = await api.exitNow(p.id);
      const first = result.closed[0];
      setNote({ kind: "done", text: `${p.symbol}: ${closedText(first?.exit_price, first?.pnl, first?.exit_reason)}` });
      setRound((r) => r + 1);
    } catch (e) {
      setNote({ kind: "warn", text: failedText(`${p.symbol} was not closed.`, e) });
    }
  }
  return (
    <ChartTradeButtons chart={chart.symbol} name={target?.label ?? chart.symbol} entry={entryOn && !!target} held={heldChip ? held : []}
      paperHeld={paperHeld} liveHeld={liveHeld} note={note} className={className} onSide={open} onExit={(p) => void exit(p)}>
      {ticket && (
        <Suspense fallback={null}>
          <OrderTicketV2 open target={ticket.target} side={ticket.side} onClose={() => setTicket(null)} onPlaced={() => setRound((r) => r + 1)} />
        </Suspense>
      )}
    </ChartTradeButtons>
  );
}

/** The bar itself, without state (rendered directly by the tests): the held chip(s), Buy / Sell, Exit per PAPER
 * position (never disabled, ADR-0004), the LIVE line and the outcome of an exit. */
export function ChartTradeButtons({ chart, name, entry, held, paperHeld, liveHeld, note, className, onSide, onExit, children }: {
  chart: string; name: string; entry: boolean; held: readonly TradeRecord[]; paperHeld: readonly TradeRecord[]; liveHeld: boolean;
  note: { kind: "done" | "warn"; text: string } | null; className?: string;
  onSide: (side: Side) => void; onExit: (p: TradeRecord) => void; children?: ReactNode;
}) {
  const many = paperHeld.length > 1;
  return (
    <div data-chart-trade={chart} className={cx("flex min-w-0 flex-wrap items-center justify-end gap-space-1", className)}>
      {held.map((p) => {
        const w = heldWords(p);
        return (
          <span key={p.id} data-chart-trade-held={p.id} className="inline-flex items-center gap-space-1 rounded-pill bg-surface-2 px-space-2 py-space-1 font-tabular text-t12 text-fg-muted">
            <span className={cx("font-semibold", w.side === "Long" ? "text-up" : "text-down")}>{w.side}</span>
            <span className="text-fg">{w.qty}</span>
            <span>Avg {formatPrice(p.entry_price)}</span>
            {held.length > 1 && p.account_label && <span>{p.account_label}</span>}
          </span>
        );
      })}
      {entry && (
        <>
          <button type="button" data-chart-trade-action="buy" aria-label={`Buy ${name}`} onClick={() => onSide("BUY")} className={cx(BTN, "bg-up text-on-brand")}>Buy</button>
          <button type="button" data-chart-trade-action="sell" aria-label={`Sell ${name}`} onClick={() => onSide("SELL")} className={cx(BTN, "bg-down text-on-brand")}>Sell</button>
        </>
      )}
      {paperHeld.map((p) => (
        <button key={p.id} type="button" data-chart-trade-action="exit" data-position={p.id} aria-label={`Exit ${p.symbol}${p.account_label ? ` (${p.account_label})` : ""}`}
          onClick={() => onExit(p)} className={cx(BTN, "border border-border bg-surface-1 text-fg hover:bg-surface-2")}>
          {many && p.account_label ? `Exit ${p.account_label}` : "Exit"}
        </button>
      ))}
      {liveHeld && <span className="text-t12 text-fg-muted" data-chart-trade-live>Close a live position in your broker&apos;s app.</span>}
      {note && (
        <p role="status" data-chart-trade-note={note.kind} className={cx("basis-full text-right text-t12", note.kind === "warn" ? "text-down" : "text-fg")}>{note.text}</p>
      )}
      {children}
    </div>
  );
}
