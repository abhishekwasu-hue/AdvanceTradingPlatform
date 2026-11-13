import type { OHLCVBar } from "../types";
import { meaningOf, type Meaning } from "../ui/format";

/**
 * CEPE-1 (owner 13 Nov: "show CE and the same-strike PE as 2 charts, one below the other"): the other side of an option
 * contract and the figures of each chart's header. Pure; no reads.
 *
 * The other side has the same underlying, strike and expiry, so its trading symbol is the same text with the CE / PE
 * word swapped - in the broker's spaced form ("NIFTY 25050 CE 17 NOV 26") and in the exchange's compact form
 * ("NIFTY26N1725050CE", the right at the end, after the strike's digits).
 */
export type OptionRight = "CE" | "PE";

/** Exchanges whose contracts are options with a same-strike pair. */
const OPTION_EXCHANGES = new Set(["NFO", "BFO"]);

const SPACED = /(^|\s)(CE|PE)(?=\s|$)/;
const COMPACT = /(\d)(CE|PE)$/;

/** The option's right (CE / PE) on a derivatives exchange, or null (a stock, an index, a future). */
export function optionRightOf(symbol: string, exchange: string | null | undefined): OptionRight | null {
  if (!OPTION_EXCHANGES.has((exchange ?? "").trim().toUpperCase())) return null;
  const s = symbol.trim().toUpperCase();
  const m = SPACED.exec(s) ?? COMPACT.exec(s);
  return m ? (m[2] as OptionRight) : null;
}

/** The same strike and expiry on the other side: CE -> PE, PE -> CE; null when `symbol` is no option. */
export function pairSymbol(symbol: string, exchange: string | null | undefined): string | null {
  const right = optionRightOf(symbol, exchange);
  if (!right) return null;
  const other = right === "CE" ? "PE" : "CE";
  const s = symbol.trim().toUpperCase();
  return SPACED.test(s) ? s.replace(SPACED, `$1${other}`) : s.replace(COMPACT, `$1${other}`);
}

/** Both sides, CE first: the order the sheet stacks them in. */
export function sidesOf(symbol: string, exchange: string | null | undefined): { CE: string; PE: string } | null {
  const right = optionRightOf(symbol, exchange);
  const other = pairSymbol(symbol, exchange);
  if (!right || !other) return null;
  const s = symbol.trim().toUpperCase();
  return right === "CE" ? { CE: s, PE: other } : { CE: other, PE: s };
}

export type PairView = "CE" | "PE" | "both";

/** Which charts the sheet shows for a choice of the switch. */
export function shownSides(view: PairView): OptionRight[] {
  return view === "both" ? ["CE", "PE"] : [view];
}

const IST_MS = 5.5 * 3600 * 1000;
const istDay = (ts: string) => new Date(Date.parse(ts) + IST_MS).toISOString().slice(0, 10);

export interface HeaderFigures { last: number | null; change: number | null; pct: number | null; meaning: Meaning }

/**
 * A chart header's figures: the last price (the live one, else the last candle's close) and its change from the
 * previous close - the previous bar's close on a daily chart, else the last close of the trading day before the last
 * bar's. Nothing is made up: without that earlier close there is no change.
 */
export function headerFigures(candles: readonly OHLCVBar[], live: number | null | undefined, daily: boolean): HeaderFigures {
  const lastBar = candles[candles.length - 1];
  const last = live != null && Number.isFinite(live) ? live : lastBar ? lastBar.close : null;
  let prev: number | null = null;
  if (lastBar) {
    if (daily) prev = candles.length > 1 ? candles[candles.length - 2].close : null;
    else {
      const day = istDay(lastBar.timestamp);
      for (let i = candles.length - 2; i >= 0; i -= 1) if (istDay(candles[i].timestamp) < day) { prev = candles[i].close; break; }
    }
  }
  const change = last != null && prev != null ? last - prev : null;
  const pct = change != null && prev ? (change / prev) * 100 : null;
  return { last, change, pct, meaning: meaningOf(change) };
}
