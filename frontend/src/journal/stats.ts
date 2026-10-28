import type { JournalEntry } from "./types";

/**
 * UI-28 U2.4 / U7.6 (owner, 28 Oct): the Journal's numbers a trading journal shows first (Edgewonk-style) - expectancy
 * (the average net P&L of a closed trade), the average R, and the drawdown of the closed trades in exit order. Pure; only
 * the user's own closed trades of the week on screen, nothing invented when there are none.
 */
export interface JournalStats {
  closed: number;
  /** Net P&L per closed trade (₹), null with no closed trade. */
  expectancy: number | null;
  /** Average R multiple over the closed trades that have one, null when none has. */
  avgR: number | null;
  /** The running net P&L after each closed trade, in exit order (a sparkline). */
  equity: number[];
  /** The deepest fall from a running peak (₹, as a negative number; 0 when it never fell). */
  maxDrawdown: number;
}

const closedOf = (entries: readonly JournalEntry[]) =>
  entries.filter((e) => e.pnl !== null && Number.isFinite(e.pnl))
    .sort((a, b) => (a.exit_time ?? "").localeCompare(b.exit_time ?? ""));

export function journalStats(entries: readonly JournalEntry[]): JournalStats {
  const closed = closedOf(entries);
  const rs = closed.map((e) => e.r_multiple).filter((r): r is number => r !== null && Number.isFinite(r));
  let run = 0;
  let peak = 0;
  let maxDrawdown = 0;
  const equity: number[] = [];
  for (const e of closed) {
    run += e.pnl as number;
    equity.push(run);
    peak = Math.max(peak, run);
    maxDrawdown = Math.min(maxDrawdown, run - peak);
  }
  const total = closed.reduce((s, e) => s + (e.pnl as number), 0);
  return {
    closed: closed.length,
    expectancy: closed.length ? total / closed.length : null,
    avgR: rs.length ? rs.reduce((s, r) => s + r, 0) / rs.length : null,
    equity,
    maxDrawdown,
  };
}

const CSV_COLUMNS: [string, (e: JournalEntry) => string | number | null][] = [
  ["trade_id", (e) => e.trade_id], ["symbol", (e) => e.symbol], ["mode", (e) => e.mode], ["direction", (e) => e.direction],
  ["setup", (e) => e.setup], ["entry_time", (e) => e.entry_time], ["exit_time", (e) => e.exit_time],
  ["entry_price", (e) => e.execution.entry_price], ["exit_price", (e) => e.execution.exit_price],
  ["quantity", (e) => e.execution.quantity], ["charges", (e) => e.costs.charges], ["net_pnl", (e) => e.pnl],
  ["r_multiple", (e) => e.r_multiple], ["tags", (e) => e.tags.map((t) => t.tag).join(" ")], ["notes", (e) => e.notes],
];

function cell(v: string | number | null): string {
  if (v === null || v === undefined) return "";
  const s = String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** The week's journal as CSV (a header row, one row per trade), for "Export (CSV)". */
export function journalCsv(entries: readonly JournalEntry[]): string {
  return [CSV_COLUMNS.map(([h]) => h).join(","), ...entries.map((e) => CSV_COLUMNS.map(([, f]) => cell(f(e))).join(","))].join("\n") + "\n";
}
