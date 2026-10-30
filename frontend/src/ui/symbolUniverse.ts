import { FNO_STOCKS } from "../utils/fnoSymbols";

/**
 * CT-1 C6 (owner order 30 Oct): the ONE symbol universe behind every symbol picker.
 * - "all": every index and share of the instrument list (`GET /api/instrument-master/search`), as the Charts page;
 * - "optionable": only the underlyings that have options (the option chain page, the option strikes) - the option
 *   indices and the F&O stocks, searched here with no server call. The option chain page's list (optionChain/model.ts
 *   INDEX_SYMBOLS / STOCK_SYMBOLS) is this list.
 */
export type SymbolUniverse = "all" | "optionable";

export interface UniverseSymbol { symbol: string; exchange: string; name?: string | null }

/** The indices with options, as the option chain names them. */
export const OPTION_INDICES = ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"] as const;
/** The F&O stocks (NSE). */
export const OPTION_STOCKS: readonly string[] = FNO_STOCKS;

const INDEX_NAMES: Record<string, string> = { NIFTY: "Nifty 50", BANKNIFTY: "Nifty Bank", FINNIFTY: "Nifty Financial Services", SENSEX: "BSE Sensex" };
const exchangeOf = (s: string) => (s === "SENSEX" ? "BSE" : "NSE");

/** Every symbol with options: the indices first, then the stocks. */
export const OPTIONABLE: readonly UniverseSymbol[] = [
  ...OPTION_INDICES.map((s) => ({ symbol: s, exchange: exchangeOf(s), name: INDEX_NAMES[s] })),
  ...OPTION_STOCKS.map((s) => ({ symbol: s, exchange: "NSE" })),
];

const OPTIONABLE_SET = new Set(OPTIONABLE.map((r) => r.symbol));

/** Whether a symbol has options (an exact name of the list). Pure. */
export function isOptionable(symbol: string): boolean {
  return OPTIONABLE_SET.has(symbol.trim().toUpperCase());
}

/** The optionable symbols matching a text: names that start with it first, then names that contain it (symbol or
 * index name), at most `limit`. Pure. */
export function searchOptionable(q: string, limit = 12): UniverseSymbol[] {
  const t = q.trim().toUpperCase();
  if (!t) return [];
  const starts = OPTIONABLE.filter((r) => r.symbol.startsWith(t));
  const has = OPTIONABLE.filter((r) => !r.symbol.startsWith(t) && (r.symbol.includes(t) || (r.name ?? "").toUpperCase().includes(t)));
  return [...starts, ...has].slice(0, limit);
}
