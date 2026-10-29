/**
 * AUD-T4 (owner audit 28 Oct; flag `paper_report_card`): the paper report card's pure parts - the figures as text and
 * the share image's card. The server (backend app/paper/report_card.py) computes every figure from the user's own
 * closed PAPER trades; nothing here computes a result.
 *
 * The share image is made on the device only when he taps Share (growth's card drawing, `cardSvgOf`). It carries ratios
 * and counts only: no stock or strategy name, no amount, no account or bot id, and the fixed paper / not-a-forecast line.
 */
import { hasCallWords } from "../chartAnalyze/signalLanguage";
import type { CardFact, DisciplineCard } from "../growth/types";
import { EMPTY, formatDateIST, formatNumber, formatPct } from "../ui/format";
import { strategyDisplayName } from "../ui/strategyName";

export interface ReportFigures {
  closed: number; wins: number; win_pct: number | null;
  avg_r: number | null; r_trades: number; no_stop_trades: number;
  net_pnl: number; max_drawdown: number;
  capture_pct: number | null; credit_trades: number;
  plan_pct: number | null; plan_trades: number; plan_flagged: number;
  first_close: string | null; last_close: string | null;
}

export interface ReportRow extends ReportFigures {
  key: string;
  kind: "bot" | "strategy";
  /** The server's name (a custom strategy's own name); the page shows the shared display name when it knows one. */
  name: string;
  /** null for the trades placed without a bot. */
  strategy_id: string | null;
  symbol: string | null;
}

export interface PaperReportCard {
  bots: ReportRow[];
  strategies: ReportRow[];
  total: ReportFigures;
  plan_note: string | null;
  calibration: { shown: boolean; brier: number | null; trades: number; min_trades: number; note: string | null };
}

/** The row's name: a custom strategy's own name from the server, else the one shared display rule - never an id. */
export function rowName(row: ReportRow, names?: ReadonlyMap<string, string> | null): string {
  if (!row.strategy_id) return row.name;
  if (/^custom:\d+$/.test(row.strategy_id) && row.name !== row.strategy_id) return names?.get(row.strategy_id) ?? row.name;
  return strategyDisplayName(row.strategy_id, names) || row.name;
}

export const SHARE_FILE = "my-paper-report-card.png";
export const SHARE_TITLE = "My paper report card";
export const SHARE_FOOTER = "Paper trading with simulated fills. Past results only: not a forecast, not advice.";

/** 62.5 -> "63%". */
export const winText = (v: number | null): string => (v == null ? EMPTY : `${formatNumber(v, 0)}%`);
/** 1.234 -> "+1.23R", -0.5 -> "-0.50R" (ASCII minus: the image and the share text read the same). */
export const rText = (v: number | null): string => (v == null ? EMPTY : `${v > 0 ? "+" : v < 0 ? "-" : ""}${Math.abs(v).toFixed(2)}R`);
/** Signed whole percent: credit kept can be below zero. */
export const capText = (v: number | null): string => (v == null ? EMPTY : formatPct(v, 0));

/** "3 of 4 trades" when some closed trades carry no invalidation level (they are counted but not in the average). */
export function rBasis(f: ReportFigures): string | null {
  return f.no_stop_trades > 0 ? `${f.r_trades} of ${f.closed} trades` : null;
}

/** "1 Oct 2026 - 28 Oct 2026" (one day: that day). */
export function periodText(f: ReportFigures): string {
  if (!f.first_close || !f.last_close) return "";
  const a = formatDateIST(f.first_close);
  const b = formatDateIST(f.last_close);
  return a === b ? a : `${a} - ${b}`;
}

const CURRENCY = /₹|\bRs\.?|\bINR\b|\$/;

/** True when a text may not be on the share image (a call word or a currency). */
export function shareProblem(text: string): boolean {
  return !!text && (hasCallWords(text) || CURRENCY.test(text));
}

/** The share image's card for one row: counts and ratios only, never the row's name, symbol, key or amounts. */
export function shareCard(row: ReportFigures): DisciplineCard {
  const facts: CardFact[] = [
    { key: "closed", label: "Closed paper trades", value: String(row.closed) },
    { key: "win", label: "Win %", value: winText(row.win_pct) },
  ];
  if (row.avg_r != null) facts.push({ key: "r", label: "Average R", value: rText(row.avg_r) });
  if (row.capture_pct != null) facts.push({ key: "capture", label: "Credit kept", value: capText(row.capture_pct) });
  else if (row.plan_pct != null) facts.push({ key: "plan", label: "Plan followed", value: winText(row.plan_pct) });
  const lines: string[] = [];
  if (row.capture_pct != null && row.plan_pct != null) lines.push(`Plan followed on ${winText(row.plan_pct)} of journaled trades.`);
  const basis = rBasis(row);
  if (row.avg_r != null && basis) lines.push(`Average R over ${basis}.`);
  const card: DisciplineCard = {
    title: SHARE_TITLE, week_label: periodText(row), facts, lines, footer: SHARE_FOOTER, empty: row.closed === 0, hints: [],
  };
  return {
    ...card,
    facts: card.facts.filter((f) => !shareProblem(`${f.label} ${f.value}`)),
    lines: card.lines.filter((l) => !shareProblem(l)),
  };
}

/** The share sheet's text: the same facts and lines, plain. */
export function shareText(card: DisciplineCard): string {
  return [card.week_label ? `${card.title} (${card.week_label})` : card.title, ...card.facts.map((f) => `${f.label}: ${f.value}`),
    ...card.lines, card.footer].join("\n");
}
