/**
 * UI2-4e (Charts page plan §3, 1(k)): the small chip a v2 chart shows about its live price, without React.
 * - "Live" while the last price is fresh (hover: "Updated 3 s ago");
 * - "Price delayed · 4 min" when the server says the quote is stale (`/market-data/ltp` `stale`: the backend's
 *   `QUOTE_MAX_STALE_SECONDS` rule, set per deployment, which may also turn staleness off), or when this browser has
 *   had no renewed answer for `RENEW_LIMIT_SECONDS` while the tab was shown (the poll keeps failing);
 * - nothing while the market is closed, or while the chart has no live price at all.
 * The server's own age is only shown, never compared here: the server decides about its quote. The local time counts
 * from when this browser got the answer, on its own clock, and stops while the tab is hidden (LiveChip).
 */

/** No renewed answer for this long (the tab shown) reads as delayed: the backend's default staleness limit. */
export const RENEW_LIMIT_SECONDS = 120;

/** The parts of a `/market-data/ltp` answer the chip reads. */
export interface LiveQuote { stale?: boolean; age_seconds?: number | null }

export type LiveChipState =
  | { kind: "live"; ageSeconds: number }
  | { kind: "delayed"; ageSeconds: number | null };

/** The chip for a quote received at `receivedAt` (this browser's clock, ms, hidden time left out); null shows no
 * chip. `ageSeconds`: for Live, the time since the answer; for delayed, the price's age when known. Pure. */
export function liveChipState(quote: LiveQuote | null | undefined, receivedAt: number | null, marketOpen: boolean | null | undefined,
                              nowMs: number): LiveChipState | null {
  if (marketOpen === false || !quote || receivedAt === null) return null;
  const since = Math.max(0, (nowMs - receivedAt) / 1000);
  const known = typeof quote.age_seconds === "number" && Number.isFinite(quote.age_seconds);
  const age = (known ? Math.max(0, quote.age_seconds as number) : 0) + since;
  if (quote.stale) return { kind: "delayed", ageSeconds: known ? age : null };
  if (since > RENEW_LIMIT_SECONDS) return { kind: "delayed", ageSeconds: age };
  return { kind: "live", ageSeconds: since };
}

/** AUD-G2.2 (flag `live_push`): the chip from the age of the symbol's last tick while the push is live - "Live" up to
 * the server's tick-age limit (`staleAfterMs`, setting LIVE_PUSH_STALE_SECONDS), then "Price delayed · <age>"; nothing
 * while the market is closed. `tickAt`: when this browser got the tick. Pure. */
export function tickAgeChipState(tickAt: number, marketOpen: boolean | null | undefined, nowMs: number, staleAfterMs: number): LiveChipState | null {
  if (marketOpen === false) return null;
  const age = Math.max(0, (nowMs - tickAt) / 1000);
  return age * 1000 > staleAfterMs ? { kind: "delayed", ageSeconds: age } : { kind: "live", ageSeconds: age };
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
