import { Crosshair, ShoppingBasket } from "lucide-react";
import { useConfirm } from "../ui/ConfirmDialog";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "../api/client";
import { cx } from "../components/primitives/cx";
import { formatNumber } from "../ui/format";
import { useFeatures } from "../ui/useFeatures";
import { closedText, failedText } from "../trading/model";
import { daysText, formatIv, formatStrike, type ColumnPrefs } from "../optionChain/model";
import type { ChainExpiry, ChainRow, ChainSummary, LiveChain } from "../optionChain/types";
import { fnoApi, type FnoApi, type FnoContract, type HeldLeg, type Side } from "./api";
import { basket as basketStore, useBasket } from "./basket";
import { insightsApi, type InsightsApi, type SetupTrade } from "./insights";
import BasketPanelV2 from "./BasketPanelV2";
import ChainActionSheetV2, { type ActionTarget } from "./ChainActionSheetV2";
import OrderTicketV2 from "./OrderTicketV2";
import { heldKey, sideWord, type TicketTarget } from "./ticketModel";

/**
 * FNO-1: trading from the option chain (owner order section 3), mounted by the v2 chain page with a few lines so the
 * page itself barely changes. On only while the PAPER account is on (`paper_account`; the page itself is `ui_v2`):
 * its "Exit now" is how these positions are closed by hand, and the backend routes answer 404 without it. Off, the
 * chain is exactly OC1's (a row opens its details).
 *
 * - a tap on a call or put cell -> the action sheet; Buy / Sell -> the ONE order ticket (PAPER);
 * - the "P" markers from the user's own open PAPER positions on this underlying and expiry, re-read every 30 s and
 *   after an order or an exit; Exit = the existing "Exit now" (confirmed, never disabled - ADR-0004);
 * - the bottom strip: PCR, max pain, ATM IV, days to expiry (IV percentile is not available yet, so it is left out),
 *   tap ATM to bring the ATM row back to the middle, OI change as a count or in %, the basket badge;
 * - FNO-2: the badge opens the basket panel (src/fno/BasketPanelV2.tsx): the legs, the payoff, ready strategies and
 *   Execute basket (PAPER, hedge legs first).
 * - 8b-b "Trade this setup" (`tradeSetup`): the chain opened from one of the user's own signals opens the SAME ticket
 *   (one leg) or basket (several legs) pre-filled with the strikes his own deployment settings give. Nothing is placed:
 *   he reviews it and places it himself with the ticket's / basket's own buttons (PAPER). A basket he already has is
 *   replaced only after he says yes. Once he placed it, his signal is noted on the journal of those trades (8b-k,
 *   journal rows only); a failed note changes nothing.
 */
const HELD_EVERY_MS = 30_000;
type Note = { kind: "done" | "warn"; text: string } | null;

export interface ChainTrading {
  enabled: boolean;
  held: ReadonlySet<string>;
  onPickLeg?: (row: ChainRow, side: "call" | "put") => void;
  /** Added to the table's centre key: a new value centres the ATM row again. */
  centreSuffix: string;
  strip: ReactNode;
  layer: ReactNode;
  /** 8b-b: open the ticket / basket pre-filled (never places). Absent while the PAPER account is off. */
  tradeSetup?: (t: SetupTrade) => void;
  /** Trading Terminal T1: open the basket panel (the same panel as the strip's badge). Absent while the PAPER account is off. */
  openBasket?: () => void;
}

export function spotSymbolOf(underlying: string): string {
  const map: Record<string, string> = { NIFTY: "NIFTY 50", BANKNIFTY: "NIFTY BANK", FINNIFTY: "NIFTY FIN SERVICE", SENSEX: "SENSEX" };
  return map[underlying] ?? underlying;
}

export function useChainTrading({ symbol, live, prefs, onPrefs, fno = fnoApi, exitNow = api.exitNow, expiries = [], signal = null, insights = insightsApi, onTicket }: {
  symbol: string;
  /** Trading Terminal T1: Buy / Sell from the action sheet go to the Terminal's order panel instead of the modal ticket
   * (the same ticket state and the same order path, src/fno/OrderTicketV2.tsx useOrderTicket). Absent: as before. */
  onTicket?: (t: { target: TicketTarget; side: Side }) => void;
  /** 8b-b: the user's own signal the chain was opened from ("action:12"), for the journal note after he places. */
  signal?: string | null;
  insights?: Pick<InsightsApi, "linkJournal">;
  live: LiveChain | null;
  /** The expiries the page lists (a Calendar's far leg reads the next one). */
  expiries?: readonly ChainExpiry[];
  prefs: ColumnPrefs;
  onPrefs: (prefs: ColumnPrefs) => void;
  fno?: FnoApi;
  exitNow?: typeof api.exitNow;
}): ChainTrading {
  const features = useFeatures();
  const enabled = features.on("paper_account");
  const expiry = live?.expiry ?? null;
  const [held, setHeld] = useState<HeldLeg[]>([]);
  const [action, setAction] = useState<{ strike: number; side: "call" | "put" } | null>(null);
  const [ticket, setTicket] = useState<{ target: TicketTarget; side: Side } | null>(null);
  const [note, setNote] = useState<Note>(null);
  const [recentre, setRecentre] = useState(0);
  const [round, setRound] = useState(0);
  // FNO-3: the futures chain's basket badge links here with `?basket=open`, so the legs it added open at once.
  const [basketOpen, setBasketOpen] = useState(() => typeof window !== "undefined" && new URLSearchParams(window.location.search).get("basket") === "open");
  const legs = useBasket();
  // 8b-b: the signal the open ticket / basket was pre-filled from (null: opened by hand, nothing is noted).
  const [origin, setOrigin] = useState<string | null>(null);
  const confirmBox = useConfirm();   // UI-28 U3.3

  const reloadHeld = useCallback(() => setRound((r) => r + 1), []);
  useEffect(() => {
    if (!enabled || !expiry) { setHeld([]); return undefined; }
    let alive = true;
    const read = () => fno.held(symbol, expiry).then((h) => { if (alive) setHeld(h); }).catch(() => { /* the markers wait for the next read */ });
    void read();
    const timer = window.setInterval(read, HELD_EVERY_MS);
    return () => { alive = false; window.clearInterval(timer); };
  }, [enabled, symbol, expiry, round, fno]);

  const heldSet = useMemo(() => new Set(held.map((h) => heldKey(h.strike, h.right))), [held]);
  const row = action && live ? live.rows.find((r) => r.strike === action.strike) ?? null : null;
  const target: ActionTarget | null = row && action && expiry
    ? { underlying: symbol, expiry, strike: row.strike, right: action.side === "call" ? "CE" : "PE", leg: row[action.side], spotSymbol: spotSymbolOf(symbol) }
    : null;

  function trade(side: Side, contract: FnoContract | null) {
    if (!target) return;
    const picked = { side, target: { underlying: target.underlying, expiry: target.expiry, strike: target.strike, right: target.right,
      label: contract?.label ?? null, lotSize: contract?.lot_size ?? null, ltp: target.leg.ltp, bid: target.leg.bid, ask: target.leg.ask } };
    if (onTicket) onTicket(picked);
    else setTicket(picked);
    setAction(null);
  }

  async function exit(h: HeldLeg) {
    // The existing "Exit now" with its confirm; never disabled here (ADR-0004).
    if (!window.confirm(`Exit ${sideWord(h.side, true).toLowerCase()} ${formatNumber(h.lots, h.lots % 1 ? 2 : 0)} lot(s) of ${h.tradingsymbol} (PAPER) now, at the current price?`)) return;
    try {
      const result = await exitNow(h.trade_id);
      const first = result.closed[0];
      setNote({ kind: "done", text: `${h.tradingsymbol}: ${closedText(first?.exit_price, first?.pnl, first?.exit_reason)}` });
    } catch (e) {
      setNote({ kind: "warn", text: failedText(`${h.tradingsymbol} was not closed.`, e) });
    }
    setAction(null);
    reloadHeld();
  }

  // 8b-k: after he placed what "Trade this setup" opened, his signal is noted on those trades' journal (journal only).
  function noteSignal(tradeIds: (number | null | undefined)[]) {
    const ids = tradeIds.filter((id): id is number => typeof id === "number");
    if (!origin || !ids.length) return;
    insights.linkJournal(origin, ids).catch(() => { /* the journal note is a convenience; the trades are as placed */ });
  }

  async function tradeSetup(t: SetupTrade) {
    if (t.kind === "none" || !signal) return;
    setAction(null);
    setNote(null);
    if (t.kind === "ticket") {
      setOrigin(signal);
      setTicket({ side: t.side, target: t.target });
      // The contract's name and lot size, as the action sheet reads them (the ticket works without them).
      fno.contract(t.target.underlying, t.target.expiry, t.target.strike, t.target.right)
        .then((c) => setTicket((cur) => (cur && cur.target.strike === t.target.strike && cur.target.right === t.target.right
          ? { ...cur, target: { ...cur.target, label: c.label ?? null, lotSize: c.lot_size ?? null } } : cur)))
        .catch(() => { /* the ticket reads the lot size on its review step */ });
      return;
    }
    if (legs.length && !(await confirmBox.ask("Replace your basket?", `Your basket has ${legs.length} leg${legs.length === 1 ? "" : "s"}. This setup takes their place.`, "Replace basket"))) return;
    basketStore.replace(t.legs);
    setOrigin(signal);
    setBasketOpen(true);
  }

  if (!enabled) return { enabled: false, held: heldSet, centreSuffix: "", strip: null, layer: null };
  return {
    enabled,
    held: heldSet,
    onPickLeg: (r, side) => { setNote(null); setAction({ strike: r.strike, side }); },
    centreSuffix: recentre ? `|${recentre}` : "",
    tradeSetup,
    openBasket: () => setBasketOpen(true),
    strip: live ? (
      <ChainBottomStripV2 summary={live.summary} pct={prefs.pct === true} onPct={(pct) => onPrefs({ ...prefs, pct })}
        onRecentre={() => setRecentre((n) => n + 1)} basketCount={legs.length} onBasket={() => setBasketOpen(true)} />
    ) : null,
    layer: (
      <>
        {confirmBox.dialog}
        {note && (
          <p role="status" data-chain-trade-note={note.kind}
            className={cx("rounded-control px-space-3 py-space-2 text-t13", note.kind === "done" ? "bg-surface-2 text-fg" : "bg-warn-tint text-warn")}>
            {note.text}
          </p>
        )}
        <ChainActionSheetV2 target={target} held={held} onClose={() => setAction(null)} onTrade={trade} onExit={exit}
          watchlistsOn={features.on("watchlists")} alertsOn={features.on("screener_v2")} api={fno} />
        <OrderTicketV2 open={ticket !== null} target={ticket?.target ?? null} side={ticket?.side ?? "BUY"} api={fno}
          chainRows={live && ticket && ticket.target.expiry === live.expiry ? live.rows : null}
          onClose={() => { setTicket(null); setOrigin(null); }} onPlaced={(r) => { reloadHeld(); noteSignal([r.trade_id]); setOrigin(null); }} />
        <BasketPanelV2 open={basketOpen} onClose={() => { setBasketOpen(false); setOrigin(null); }}
          onPlaced={(r) => { reloadHeld(); noteSignal(r.legs.filter((l) => l.status === "PLACED").map((l) => l.trade_id)); setOrigin(null); }}
          chain={{ symbol, live, expiries, loadChain: (e) => api.optionChainLive(symbol, e) }} />
      </>
    ),
  };
}

/** The bottom strip of the chain (FNO-1): the key figures, tap ATM to recentre, the change toggle, the basket badge. */
export function ChainBottomStripV2({ summary, pct, onPct, onRecentre, basketCount, onBasket }: {
  summary: ChainSummary; pct: boolean; onPct: (pct: boolean) => void; onRecentre: () => void; basketCount: number; onBasket?: () => void;
}) {
  const figures: [string, string, string][] = [
    ["pcr", "PCR", formatNumber(summary.pcr_oi, 2)],
    ["max-pain", "Max pain", formatStrike(summary.max_pain)],
    ["atm-iv", "ATM IV", formatIv(summary.atm_iv)],
    ["dte", "Expiry in", daysText(summary.days_to_expiry)],
  ];
  const seg = "min-h-9 rounded-control px-space-2 text-t12 font-semibold";
  return (
    <section aria-label="Chain figures" data-block="chain-bottom-strip"
      className="sticky bottom-[calc(4rem+env(safe-area-inset-bottom))] z-[3] -mx-space-4 flex items-center gap-space-3 overflow-x-auto border-t border-subtle bg-surface-1 px-space-4 py-space-2 shadow-raised md:bottom-0 md:mx-0 md:rounded-panel md:border">
      <button type="button" onClick={onRecentre} data-strip="atm" aria-label={`Show the ATM strike ${formatStrike(summary.atm_strike)} in the middle`}
        className="inline-flex min-h-11 shrink-0 items-center gap-space-1 rounded-pill border border-border px-space-3 text-t13 font-semibold text-fg hover:bg-surface-2">
        <Crosshair size={14} aria-hidden />ATM {formatStrike(summary.atm_strike)}
      </button>
      <dl className="flex shrink-0 items-center gap-space-4">
        {figures.map(([key, label, value]) => (
          <div key={key} data-strip={key} className="flex flex-col">
            <dt className="text-t12 text-fg-muted">{label}</dt>
            <dd className="font-tabular text-t14 font-semibold text-fg">{value}</dd>
          </div>
        ))}
      </dl>
      <div role="radiogroup" aria-label="OI change as" className="ml-auto flex shrink-0 gap-space-1 rounded-control bg-surface-2 p-space-1" data-strip="change">
        <button type="button" role="radio" aria-checked={!pct} onClick={() => onPct(false)} className={cx(seg, !pct ? "bg-surface-1 text-fg" : "text-fg-muted")}>Count</button>
        <button type="button" role="radio" aria-checked={pct} onClick={() => onPct(true)} className={cx(seg, pct ? "bg-surface-1 text-fg" : "text-fg-muted")}>%</button>
      </div>
      {/* FNO-2: the badge opens the basket panel; it looks as before (FNO-1's baselines), now a button. */}
      <button type="button" data-strip="basket" onClick={onBasket} title="Open the basket"
        className="inline-flex min-h-11 shrink-0 items-center gap-space-1 rounded-control text-t13 font-semibold text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
        aria-label={`Basket: ${basketCount} leg${basketCount === 1 ? "" : "s"}`}>
        <ShoppingBasket size={16} aria-hidden />
        <span className={cx("rounded-pill px-space-2 text-t12", basketCount ? "bg-brand text-on-brand" : "bg-surface-2 text-fg-muted")}>{basketCount}</span>
      </button>
    </section>
  );
}
