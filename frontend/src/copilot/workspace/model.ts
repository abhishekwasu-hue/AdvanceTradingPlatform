import { readTimeframe } from "../../chart/readChart";
import type { TimeframeId } from "../../chart/timeframes";
import type { PickedSymbol } from "../../ui/SymbolPicker";

/**
 * M75 W-2 (owner order M75 CW-1; docs/design/M75_REVIEW.md, the PR plan's W-2 row): the Copilot's chart workspace, its
 * pure parts. Nothing here reads the network; the component (ChartWorkspaceV2) wires them.
 */

/** Exchanges whose instruments are futures / option contracts (a chart of its own, where the data has its prices). */
const CONTRACT_EXCHANGES = new Set(["NFO", "BFO", "MCX", "CDS", "BCD"]);

export function isContract(exchange: string): boolean {
  return CONTRACT_EXCHANGES.has(exchange.trim().toUpperCase());
}

/** The chart the workspace opens on: the symbol handed over (`?symbol=`, the Ask AI hand-off), else the trader's own
 * most recent pick, else none - the workspace never picks a symbol for him (the picker asks). Pure. */
export function openingSymbol(handed: string | null | undefined, recent: readonly PickedSymbol[]): PickedSymbol | null {
  const s = (handed ?? "").trim().toUpperCase();
  if (s) {
    const known = recent.find((r) => r.symbol === s);
    return { symbol: s, exchange: known?.exchange ?? "NSE" };
  }
  return recent[0] ? { symbol: recent[0].symbol, exchange: recent[0].exchange } : null;
}

/** What the reading is for: one key per (exchange, symbol, server timeframe). The reading runs once per key - opening
 * the chart reads it, a new symbol or timeframe reads again, nothing else does. Null: nothing to read (no symbol, or
 * a timeframe the reading is not offered on: week, month). Pure. */
export function readingKey(pick: PickedSymbol | null, tf: TimeframeId): { instrument: string; tf: string } | null {
  const serverTf = readTimeframe(tf);
  if (!pick || !pick.symbol.trim() || !serverTf) return null;
  return { instrument: `${pick.exchange.trim().toUpperCase()}:${pick.symbol.trim().toUpperCase()}`, tf: serverTf };
}

/** The empty chart's words for a contract (the chart's own words for an index or share). */
export function contractEmptyNote(symbol: string): string {
  return `No prices for the contract ${symbol} on this timeframe yet. Pick another timeframe, or another contract.`;
}
