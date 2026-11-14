import { isContractItem, isFnoPosition, known, type ContractFacts, type KnownContract } from "../fno/contracts";
import type { Side } from "../fno/api";
import type { TicketTarget } from "../fno/ticketModel";
import type { TradeRecord } from "../types";
import { itemKey, type WatchItem } from "./watchlistsV2";

/**
 * CTB-1 (owner 14 Nov: "Give Buy / Sell buttons; entry and exit must be possible right from the chart"): the pure parts
 * of the chart's trade bar (src/chart/ChartTradeBar.tsx). Pure; no reads.
 *
 * - Only an F&O contract (NFO / BFO) is traded from a chart: the ONE order ticket (src/fno/OrderTicketV2.tsx) takes an
 *   F&O contract. An index or a stock chart shows no trade buttons.
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
