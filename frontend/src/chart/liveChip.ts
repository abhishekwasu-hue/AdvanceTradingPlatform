/**
 * UI2-4e (Charts page plan §3, 1(k)): the small chip a v2 chart shows about its live price, without React.
 * - "Live" while the last price is fresh (hover: "Updated 3 s ago");
 * - "Price delayed · 4 min" when the newest quote is older than the app's staleness rule: the server marks a quote
 *   `stale` past `QUOTE_MAX_STALE_SECONDS` (backend `market_data/freshness.quote_is_stale`), and a price this chart has
 *   not had renewed for as long (the poll keeps failing) counts the same;
 * - nothing while the market is closed, or while the chart has no live price at all.
 * The age is the server's (measured at the exchange time when it read the quote) plus the time since this browser got
 * the answer - never this device's clock against the server's, which may differ.
 */

/** The backend's `QUOTE_MAX_STALE_SECONDS` default (app/core/config.py): a quote older than this is stale. */
export const QUOTE_MAX_STALE_SECONDS = 120;

/** The parts of a `/market-data/ltp` answer the chip reads. */
export interface LiveQuote { stale?: boolean; age_seconds?: number | null }

export type LiveChipState =
  | { kind: "live"; ageSeconds: number }
  | { kind: "delayed"; ageSeconds: number | null };

/** The chip for a quote received at `receivedAt` (this browser's clock, ms); null shows no chip. Pure. */
export function liveChipState(quote: LiveQuote | null | undefined, receivedAt: number | null, marketOpen: boolean | null | undefined,
                              nowMs: number): LiveChipState | null {
  if (marketOpen === false || !quote || receivedAt === null) return null;
  const since = Math.max(0, (nowMs - receivedAt) / 1000);
  const known = typeof quote.age_seconds === "number" && Number.isFinite(quote.age_seconds);
  const age = (known ? Math.max(0, quote.age_seconds as number) : 0) + since;
  if (quote.stale) return { kind: "delayed", ageSeconds: known ? age : null };
  if (age > QUOTE_MAX_STALE_SECONDS) return { kind: "delayed", ageSeconds: age };
  return { kind: "live", ageSeconds: age };
}

/** "3 s", "4 min", "2 h". */
export function ageText(seconds: number): string {
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))} s`;
  if (seconds < 3_600) return `${Math.floor(seconds / 60)} min`;
  return `${Math.floor(seconds / 3_600)} h`;
}

/** The chip's words and its hover text. */
export function liveChipText(state: LiveChipState): { text: string; title: string } {
  if (state.kind === "live") return { text: "Live", title: state.ageSeconds < 1 ? "Updated just now" : `Updated ${ageText(state.ageSeconds)} ago` };
  if (state.ageSeconds === null) return { text: "Price delayed", title: "Your broker did not say when this price was traded." };
  return { text: `Price delayed · ${ageText(state.ageSeconds)}`, title: `The newest price is ${ageText(state.ageSeconds)} old.` };
}
