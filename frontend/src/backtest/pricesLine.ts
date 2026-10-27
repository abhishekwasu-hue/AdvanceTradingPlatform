import type { OHLCVBar } from "../types";

/** Owner order 23 Oct: every backtest result says where its prices came from and the date range, in plain words. */

export const STORED_HISTORY_NOTE = "No broker connected: Daily backtests use ATP's stored end-of-day history. Intraday backtests need your broker.";

/** Day-one fix 3: the broker is connected but not logged in today - say that, not "No broker connected". */
export function storedHistoryLoginNote(broker: string): string {
  return `Not logged in to ${broker} today: Daily backtests use ATP's stored end-of-day history. Intraday backtests need today's login.`;
}

/** Day-one fix 3: the stored history a Daily backtest reads (the server reads at least about a year of Daily bars). */
export const STORED_HISTORY_CHOICES: readonly { days: number; label: string }[] = [
  { days: 400, label: "About 1 year" },
  { days: 730, label: "2 years" },
];
export const STORED_HISTORY_DEFAULT_DAYS = 400;

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** A bar's day in India time ("2 Jan 2026"), the same words the server uses. */
export function dayWords(timestamp: string): string {
  const t = new Date(timestamp);
  if (Number.isNaN(t.getTime())) return timestamp;
  const ist = new Date(t.getTime() + 330 * 60_000);
  return `${ist.getUTCDate()} ${MONTHS[ist.getUTCMonth()]} ${ist.getUTCFullYear()}`;
}

function brokerName(label: string): string {
  const name = label.slice("broker:".length).split(":")[0];
  return name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/** "Prices from your broker (Upstox), 2 Jan 2026 to 22 Oct 2026." for broker candles; the server's own line for stored
 * history (`serverLine`); nothing for sample data (a testing tool, never shown as prices). */
export function pricesLine(label: string, candles: OHLCVBar[], serverLine?: string): string | null {
  if (serverLine) return serverLine;
  if (!label.startsWith("broker:") || candles.length === 0) return null;
  const name = brokerName(label);
  const who = name && name.toLowerCase() !== "auto" ? `your broker (${name})` : "your broker";
  return `Prices from ${who}, ${dayWords(candles[0].timestamp)} to ${dayWords(candles[candles.length - 1].timestamp)}.`;
}
