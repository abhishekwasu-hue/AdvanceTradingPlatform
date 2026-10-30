import { errorSentence } from "../api/errors";
import * as RD from "@radix-ui/react-dialog";
import { Bell, ChartCandlestick, ListPlus, ShoppingBasket, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { request } from "../api/client";
import { applyEdit, watchlistsV2Api, type WatchlistV2 } from "../chart/watchlistsV2";
import { cx } from "../components/primitives/cx";
import { formatStrike } from "../optionChain/model";
import type { ChainLeg } from "../optionChain/types";
import { chartTabPath, chartsPath } from "../shell/shellRoutes";
import { EMPTY, formatDateIST, formatPrice, formatSignedINR } from "../ui/format";
import { fnoApi, type FnoApi, type FnoContract, type HeldLeg, type Right, type Side } from "./api";
import { MAX_LEGS, basket as defaultBasket, type BasketStore } from "./basket";
import { heldPnl, heldText } from "./ticketModel";
import { PaperBadge } from "../ui/PaperBadge";

/**
 * FNO-1 (owner order section 3): tap a strike's call or put on the chain -> this sheet.
 * - Buy / Sell open the order ticket (the user's own manual order, PAPER);
 * - Add to basket (a Buy or a Sell leg of one lot, kept in this browser; the basket panel is src/fno/BasketPanelV2.tsx);
 * - Chart of this strike (the contract's chart; the underlying's while the contract is not known);
 * - Add to watchlist (the contract, on the exchange it trades on; behind the `watchlists` flag);
 * - Price alert (an instrument rule on the contract's price; behind the `screener_v2` flag - a rule only notifies);
 * - "You hold" with the P&L and Add / Exit when the user holds this contract in PAPER. Exit uses the existing
 *   "Exit now" path and is never disabled here (ADR-0004).
 * Nothing here places an order by itself, and no word tells the user what to trade.
 * `ActionSheetBody` is the sheet's content without state (rendered directly by the tests); the actions are plain
 * functions (`basketAdd`, `watchlistAdd`, `alertSave`) tested on their own.
 */
export interface ActionTarget {
  underlying: string;
  expiry: string;
  strike: number;
  right: Right;
  leg: ChainLeg;
  /** The underlying's chart symbol ("NIFTY 50"), for the chart link while the contract is not known. */
  spotSymbol: string;
}

export type Panel = null | "basket" | "watchlist" | "alert";
export type Note = { tone: "done" | "warn"; text: string } | null;
export type Lists = Pick<typeof watchlistsV2Api, "list" | "create" | "save">;
export type CreateAlert = (body: Record<string, unknown>) => Promise<unknown>;

export interface ChainActionSheetProps {
  target: ActionTarget | null;
  held: HeldLeg[];
  onClose: () => void;
  onTrade: (side: Side, contract: FnoContract | null) => void;
  onExit: (held: HeldLeg) => void;
  watchlistsOn: boolean;
  alertsOn: boolean;
  api?: Pick<FnoApi, "contract">;
  lists?: Lists;
  createAlert?: CreateAlert;
  basketStore?: BasketStore;
}

const ROW = "flex min-h-11 w-full items-center gap-space-3 rounded-control px-space-3 text-left text-t14 font-medium text-fg hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand disabled:opacity-50";
const CHIP = "min-h-11 rounded-control border border-border px-space-3 text-t14 font-semibold text-fg hover:bg-surface-2 disabled:opacity-50";

const createAlertRule: CreateAlert = (body) => request<unknown>("/alerts/rules", { method: "POST", body: JSON.stringify(body) });

// --- the actions ------------------------------------------------------------------------------------------------------

export function labelOf(target: ActionTarget, contract: FnoContract | null): string {
  return contract?.label ?? `${target.underlying} ${formatDateIST(target.expiry).replace(/ \d{4}$/, "")} ${formatStrike(target.strike)} ${target.right}`;
}

/** The chart of this strike: the contract on its exchange, else the underlying. */
export function chartLink(target: ActionTarget, contract: FnoContract | null): string {
  return contract ? chartTabPath(contract.tradingsymbol, contract.exchange) : chartsPath(target.spotSymbol);
}

export function basketAdd(store: BasketStore, target: ActionTarget, contract: FnoContract | null, side: Side): Note {
  const ok = store.add({ underlying: target.underlying, expiry: target.expiry, strike: target.strike, right: target.right, side, lots: 1,
    price: target.leg.ltp, label: contract?.label ?? null, iv: target.leg.iv });
  return ok ? { tone: "done", text: `Added to the basket: ${side === "BUY" ? "Buy" : "Sell"} 1 lot of ${labelOf(target, contract)}.` }
    : { tone: "warn", text: `The basket is full (${MAX_LEGS} legs). Remove a leg first.` };
}

/** Adds the contract to `list` (or to a new list "F&O" when the user has none). */
export async function watchlistAdd(lists: Lists, contract: FnoContract, list: WatchlistV2 | null): Promise<Note> {
  const item = { symbol: contract.tradingsymbol, exchange: contract.exchange };
  try {
    if (list === null) {
      await lists.create("F&O", [item]);
      return { tone: "done", text: `Added ${contract.label} to a new list, F&O.` };
    }
    if (list.items.some((i) => i.symbol.toUpperCase() === item.symbol.toUpperCase() && i.exchange.toUpperCase() === item.exchange.toUpperCase())) {
      return { tone: "done", text: `${contract.label} is already in ${list.name}.` };
    }
    await lists.save(applyEdit(list, { kind: "add", item }));
    return { tone: "done", text: `Added ${contract.label} to ${list.name}.` };
  } catch (e) {
    return { tone: "warn", text: errorSentence(e) };
  }
}

/** The ScreenQL condition of a price alert: reaching `level` from where the price is now. */
export function alertCondition(level: number, ltp: number | null): { condition: string; words: string; sign: string } {
  const above = ltp === null || level >= ltp;
  return { condition: `close ${above ? ">=" : "<="} ${level}`, words: above ? "at or above" : "at or below", sign: above ? "≥" : "≤" };
}

export async function alertSave(createAlert: CreateAlert, contract: FnoContract, levelText: string, ltp: number | null): Promise<Note> {
  const level = Number(levelText.replace(/,/g, ""));
  if (levelText.trim() === "" || !Number.isFinite(level) || level <= 0) return { tone: "warn", text: "Enter a price for the alert." };
  const { condition, words, sign } = alertCondition(level, ltp);
  try {
    // UI-28 U1.4: the alert reads in plain words ("Alert when NIFTY 24500 CE ≥ 120"); the condition code stays server-side.
    await createAlert({ name: `Alert when ${contract.label} ${sign} ${level}`.slice(0, 120), kind: "instrument", symbol: contract.tradingsymbol,
      exchange: contract.exchange, condition, base_tf: "1m", priority: "normal", cooldown_minutes: 60, mode: "instant", digest_every: "hourly" });
    return { tone: "done", text: `Alert saved: you will be told when ${contract.label} is ${words} ${formatPrice(level)}.` };
  } catch (e) {
    return { tone: "warn", text: errorSentence(e) };
  }
}

// --- the sheet --------------------------------------------------------------------------------------------------------

export default function ChainActionSheetV2({
  target, held, onClose, onTrade, onExit, watchlistsOn, alertsOn, api = fnoApi, lists = watchlistsV2Api, createAlert = createAlertRule,
  basketStore = defaultBasket,
}: ChainActionSheetProps) {
  const [contract, setContract] = useState<FnoContract | null>(null);
  const [contractProblem, setContractProblem] = useState<string | null>(null);
  const [panel, setPanel] = useState<Panel>(null);
  const [note, setNote] = useState<Note>(null);
  const [watchlists, setWatchlists] = useState<WatchlistV2[] | null>(null);
  const [level, setLevel] = useState("");
  const key = target ? `${target.underlying}|${target.expiry}|${target.strike}|${target.right}` : "";

  useEffect(() => {
    setContract(null); setContractProblem(null); setPanel(null); setNote(null); setWatchlists(null);
    setLevel(target?.leg.ltp ? target.leg.ltp.toFixed(2) : "");
    if (!target) return undefined;
    let live = true;
    api.contract(target.underlying, target.expiry, target.strike, target.right)
      .then((c) => { if (live) setContract(c); })
      .catch((e: unknown) => { if (live) setContractProblem(errorSentence(e)); });
    return () => { live = false; };
  }, [key]);   // one read per contract (the target object changes with every chain refresh)

  if (!target) return null;
  const done = (n: Note) => { setNote(n); if (n?.tone === "done") setPanel(null); };
  const handlers: BodyHandlers = {
    onTrade: (side) => onTrade(side, contract),
    onExit,
    onPanel: (p) => {
      setPanel(p);
      if (p === "watchlist" && watchlists === null) lists.list().then(setWatchlists).catch((e: unknown) => setNote({ tone: "warn", text: errorSentence(e) }));
    },
    onBasket: (side) => done(basketAdd(basketStore, target, contract, side)),
    onWatchlist: (list) => { if (contract) void watchlistAdd(lists, contract, list).then(done); },
    onLevel: setLevel,
    onAlert: () => { if (contract) void alertSave(createAlert, contract, level, target.leg.ltp).then(done); },
    onChart: onClose,
  };
  return (
    <RD.Root open onOpenChange={(o) => { if (!o) onClose(); }}>
      <RD.Portal>
        <RD.Overlay className="fixed inset-0 z-40 bg-scrim/50" />
        <RD.Content aria-describedby={undefined} data-chain-actions data-right={target.right}
          className="fixed inset-x-0 bottom-0 z-50 max-h-[88dvh] overflow-y-auto rounded-t-panel border-t border-subtle bg-surface-1 p-space-4 pb-[calc(1rem+env(safe-area-inset-bottom))] text-fg shadow-raised focus:outline-none md:inset-x-auto md:bottom-auto md:left-1/2 md:top-1/2 md:w-[26rem] md:-translate-x-1/2 md:-translate-y-1/2 md:rounded-panel md:border">
          <div className="flex items-start justify-between gap-space-3">
            <RD.Title className="min-w-0 truncate text-t18 font-semibold text-fg">{labelOf(target, contract)}</RD.Title>
            <RD.Close aria-label="Close" className="-mr-space-2 -mt-space-2 flex h-11 w-11 shrink-0 items-center justify-center rounded-control text-fg-muted hover:bg-surface-2 hover:text-fg">
              <X size={18} aria-hidden />
            </RD.Close>
          </div>
          <ActionSheetBody target={target} contract={contract} contractProblem={contractProblem} held={held} panel={panel} note={note}
            watchlists={watchlists} level={level} watchlistsOn={watchlistsOn} alertsOn={alertsOn} {...handlers} />
        </RD.Content>
      </RD.Portal>
    </RD.Root>
  );
}

export interface BodyHandlers {
  onTrade: (side: Side) => void;
  onExit: (held: HeldLeg) => void;
  onPanel: (panel: Panel) => void;
  onBasket: (side: Side) => void;
  onWatchlist: (list: WatchlistV2 | null) => void;
  onLevel: (level: string) => void;
  onAlert: () => void;
  onChart: () => void;
}

/** The sheet's content (no state, no hooks). */
export function ActionSheetBody({ target, contract, contractProblem, held, panel, note, watchlists, level, watchlistsOn, alertsOn, ...on }: {
  target: ActionTarget; contract: FnoContract | null; contractProblem: string | null; held: HeldLeg[]; panel: Panel; note: Note;
  watchlists: WatchlistV2[] | null; level: string; watchlistsOn: boolean; alertsOn: boolean;
} & BodyHandlers) {
  const leg = target.leg;
  const mine = held.filter((h) => h.strike === target.strike && h.right === target.right);
  const toggle = (p: Exclude<Panel, null>) => () => on.onPanel(panel === p ? null : p);
  return (
    <div data-action-body>
      <p className="text-t13 text-fg-muted font-tabular">LTP {formatPrice(leg.ltp)} · Bid {formatPrice(leg.bid)} · Ask {formatPrice(leg.ask)}</p>
      <p className="mt-space-1"><PaperBadge /></p>

      {mine.length > 0 && (
        <section aria-label="You hold" className="mt-space-3 rounded-control border border-subtle p-space-3" data-held>
          <h3 className="text-t12 font-semibold uppercase text-fg-muted">You hold (practice)</h3>
          {mine.map((h) => {
            const pnl = heldPnl(h, leg.ltp);
            return (
              <div key={h.trade_id} className="mt-space-2 flex flex-wrap items-center gap-space-2" data-held-trade={h.trade_id}>
                <div className="min-w-0 flex-1 text-t14">
                  <div className="text-fg">{heldText(h)}</div>
                  <div className={cx("font-tabular text-t13", pnl === null ? "text-fg-muted" : pnl >= 0 ? "text-up" : "text-down")} data-held-pnl>
                    P&amp;L {pnl === null ? EMPTY : formatSignedINR(pnl)}
                  </div>
                </div>
                <button type="button" className={CHIP} onClick={() => on.onTrade(h.side)} data-action="held-add">Add</button>
                <button type="button" className={CHIP} onClick={() => on.onExit(h)} data-action="held-exit">Exit</button>
              </div>
            );
          })}
        </section>
      )}

      <div className="mt-space-4 grid grid-cols-2 gap-space-2">
        <button type="button" onClick={() => on.onTrade("BUY")} data-action="buy"
          className="min-h-12 rounded-control bg-up px-space-4 text-t15 font-semibold text-on-brand">Buy</button>
        <button type="button" onClick={() => on.onTrade("SELL")} data-action="sell"
          className="min-h-12 rounded-control bg-down px-space-4 text-t15 font-semibold text-on-brand">Sell</button>
      </div>

      <div className="mt-space-3 space-y-space-1">
        <button type="button" className={ROW} onClick={toggle("basket")} aria-expanded={panel === "basket"} data-action="basket">
          <ShoppingBasket size={18} aria-hidden className="text-fg-muted" />Add to basket
        </button>
        {panel === "basket" && (
          <Inline>
            <button type="button" className={CHIP} onClick={() => on.onBasket("BUY")} data-action="basket-buy">Buy leg, 1 lot</button>
            <button type="button" className={CHIP} onClick={() => on.onBasket("SELL")} data-action="basket-sell">Sell leg, 1 lot</button>
          </Inline>
        )}
        <Link to={chartLink(target, contract)} className={ROW} data-action="chart" onClick={on.onChart}>
          <ChartCandlestick size={18} aria-hidden className="text-fg-muted" />Chart of this strike
        </Link>
        {watchlistsOn && (
          <>
            <button type="button" className={ROW} onClick={toggle("watchlist")} disabled={!contract} aria-expanded={panel === "watchlist"} data-action="watchlist">
              <ListPlus size={18} aria-hidden className="text-fg-muted" />Add to watchlist
            </button>
            {panel === "watchlist" && (
              <Inline>
                {watchlists === null ? <span className="text-t13 text-fg-muted">Loading your lists…</span> : (
                  <>
                    {watchlists.map((w) => <button key={w.id} type="button" className={CHIP} onClick={() => on.onWatchlist(w)} data-watchlist={w.id}>{w.name}</button>)}
                    {watchlists.length === 0 && <button type="button" className={CHIP} onClick={() => on.onWatchlist(null)} data-watchlist="new">New list "F&amp;O"</button>}
                  </>
                )}
              </Inline>
            )}
          </>
        )}
        {alertsOn && (
          <>
            <button type="button" className={ROW} onClick={toggle("alert")} disabled={!contract} aria-expanded={panel === "alert"} data-action="alert">
              <Bell size={18} aria-hidden className="text-fg-muted" />Price alert
            </button>
            {panel === "alert" && (
              <Inline>
                <label className="flex items-center gap-space-2 text-t13 text-fg-muted">
                  Alert at
                  <input inputMode="decimal" value={level} onChange={(e) => on.onLevel(e.target.value)} data-alert-level
                    className="h-11 w-28 rounded-control border border-border bg-surface-2 px-space-3 text-t14 text-fg font-tabular focus:border-brand focus:outline-none" />
                </label>
                <button type="button" className={CHIP} onClick={on.onAlert} data-action="alert-save">Save alert</button>
              </Inline>
            )}
          </>
        )}
      </div>
      {contractProblem && <p className="mt-space-2 text-t13 text-fg-muted" data-contract-problem>{contractProblem}</p>}
      {note && (
        <p role="status" data-action-note={note.tone}
          className={cx("mt-space-3 rounded-control px-space-3 py-space-2 text-t13", note.tone === "done" ? "bg-surface-2 text-fg" : "bg-warn-tint text-warn")}>
          {note.text}
        </p>
      )}
    </div>
  );
}

function Inline({ children }: { children: ReactNode }) {
  return <div className="flex flex-wrap items-center gap-space-2 pb-space-2 pl-space-10">{children}</div>;
}
