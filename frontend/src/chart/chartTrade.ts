import { isContractItem, isFnoPosition, known, type ContractFacts, type KnownContract } from "../fno/contracts";
import type { Side } from "../fno/api";
import type { TicketTarget } from "../fno/ticketModel";
import type { TicketForm } from "../components/paperTicket";
import type { TradeRecord } from "../types";
import { itemKey, type WatchItem } from "./watchlistsV2";

/**
 * CTB-1 (owner 14 Nov: "Give Buy / Sell buttons; entry and exit must be possible right from the chart"): the pure parts
 * of the chart's trade bar (src/chart/ChartTradeBar.tsx). Pure; no reads.
 *
 * - An F&O contract (NFO / BFO) is traded from a chart through the ONE order ticket (src/fno/OrderTicketV2.tsx).
 * - Follow-up 15 Nov (PR #1072 defaults the owner accepted): an NSE share's chart (equity cash) gets Buy / Sell too,
 *   opening the existing equity ticket (src/components/PaperTicketCard.tsx) with the symbol and the side only - his own
 *   entry, stop, target and quantity are his to type there. An index chart shows no trade buttons (not tradable): a
 *   chart on NSE is a share only when the instrument list has that exact symbol as an NSE "EQ" row.
 * - The ticket's target is the contract and its last price ONLY - never lots, an order type, a product or a stop-loss
 *   (the ticket fills those from his own saved settings, or asks; M68).
 * - A held position is matched by the server's reading of both symbols (underlying, right, strike, expiry), so the
 *   broker's spaced name and the exchange's compact name of one contract are the same contract.
 */

/** Whether a chart's instrument can be traded from the chart (an F&O contract). */
export const tradableOnChart = (exchange: string | null | undefined): boolean => isContractItem({ exchange: exchange ?? "" });

/** The ticket's target for a chart's contract; null while it is not known, not trading (expired) or has no expiry. */
export function chartTicketTarget(facts: ContractFacts | undefined, ltp: number | null | undefined): TicketTarget | null {
  if (!known(facts) || facts.expired || !facts.expiry) return null;
  return { underlying: facts.underlying, expiry: facts.expiry, strike: facts.right === "FUT" ? null : facts.strike, right: facts.right,
    label: facts.label, lotSize: facts.lot_size ?? null, ltp: typeof ltp === "number" && Number.isFinite(ltp) && ltp > 0 ? ltp : null, bid: null, ask: null };
}

const sameContract = (a: KnownContract, b: KnownContract): boolean =>
  a.underlying === b.underlying && a.right === b.right && (a.strike ?? null) === (b.strike ?? null) && (a.expiry ?? null) === (b.expiry ?? null);

/** The open F&O positions the chart's contract lookup also reads (their own symbols, on their exchange). */
export function positionItems(positions: readonly TradeRecord[]): WatchItem[] {
  return positions.filter((p) => !p.exit_time && isFnoPosition(p)).map((p) => ({ symbol: p.symbol.toUpperCase(), exchange: (p.exchange ?? "NFO").toUpperCase() }));
}

/** His open positions in the chart's contract (the same symbol, or the same contract as the server reads both). */
export function heldOnChart(chart: WatchItem, positions: readonly TradeRecord[], facts: ReadonlyMap<string, ContractFacts>): TradeRecord[] {
  const own = facts.get(itemKey(chart));
  return positions.filter((p) => {
    if (p.exit_time || !isFnoPosition(p)) return false;
    const item = { symbol: p.symbol.toUpperCase(), exchange: (p.exchange ?? "NFO").toUpperCase() };
    if (itemKey(item) === itemKey(chart)) return true;
    const theirs = facts.get(itemKey(item));
    return known(own) && known(theirs) && sameContract(own, theirs);
  });
}

/** The held chip: "Long · Qty 75 · Avg 120.50" (his own record; no figure is computed here). */
export function heldWords(p: Pick<TradeRecord, "direction" | "quantity">): { side: "Long" | "Short"; qty: string } {
  return { side: p.direction.toUpperCase() === "SHORT" ? "Short" : "Long", qty: `Qty ${p.quantity}` };
}

export type ChartTicket = { target: TicketTarget; side: Side };

/** Follow-up 15 Nov: a chart that may be an NSE share (the instrument list then decides; an index is never one). */
export const maybeShareChart = (exchange: string | null | undefined): boolean => (exchange ?? "").trim().toUpperCase() === "NSE";

/** One row of `GET /api/instrument-master/search` (the fields read here). */
export interface InstrumentRow { tradingsymbol: string; exchange: string; instrument_type: string }

/** Whether the instrument list has this exact symbol as an NSE share ("EQ"); an index ("INDEX") or a near name is not. */
export function isNseShare(symbol: string, rows: readonly InstrumentRow[]): boolean {
  const s = symbol.trim().toUpperCase();
  return !!s && rows.some((r) => r.tradingsymbol.trim().toUpperCase() === s && r.exchange.trim().toUpperCase() === "NSE"
    && r.instrument_type.trim().toUpperCase() === "EQ");
}

/** The equity ticket's form from a chart: the symbol and the side only (never a price, stop, target or quantity). */
export function equityTicketInitial(symbol: string, side: Side): Partial<TicketForm> {
  return { symbol: symbol.trim().toUpperCase(), direction: side === "BUY" ? "LONG" : "SHORT" };
}

/** Follow-up 15 Nov (B1): a LIVE position gets Exit on the chart when his organisation may go LIVE through ATP (the
 * Terminal's `live_allowed`), through the existing LIVE exit (all lots of "Exit part", at the broker account that holds
 * it). A leg of a strategy structure is not closed that way, so it keeps "Close in your broker's app". Pure. */
export function liveExitOnChart(p: Pick<TradeRecord, "mode" | "leg_group_id">, liveAllowed: boolean): boolean {
  return liveAllowed && p.mode === "LIVE" && !p.leg_group_id;
}

export type EquityTicket = { initial: Partial<TicketForm>; side: Side };
