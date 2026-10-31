import { request } from "../api/client";
import { V2_ALIASES } from "../shell/aliases";
import { formatNumber } from "../ui/format";

/**
 * AUD-T6 (owner audit 28 Oct; BR-M3 / BR-R1; flags `ui_v2` + `setup_recall`): the user's own past closed trades whose
 * context stored at entry matches exactly (setup, side, holding, bot timeframe), closed before this entry - and, on a
 * closed journal row, what the stored entry context said next to what happened. Every word and number comes from the
 * server (`/api/setup-recall/*`, 404 while the flag is off); nothing here re-reads a chart.
 */

export interface RecallContext { setup: string; side: string; holding: string; timeframe: string | null }

export interface RecallMatch {
  trade_id: number;
  entered_at: string | null;
  closed_at: string | null;
  journal_week: string | null;
  mode: string;
  r: number | null;
  result: "gain" | "loss" | "flat" | null;
  outcome: string;
  how_closed: string;
}

export interface Recall {
  context: RecallContext;
  fields: string[];
  as_of: string;
  count: number;
  min_sample: number;
  enough: boolean;
  line: string;
  wins: number | null;
  losses: number | null;
  flat: number | null;
  avg_r: number | null;
  r_trades: number;
  common_loss_exit: string | null;
  matches: RecallMatch[];
}

export interface Reflection {
  closed: boolean;
  said: RecallContext & { planned_rr: number | null; reasons: string[]; source: string | null; captured_at: string | null };
  happened: { how_closed: string; r: number | null; result: string | null; outcome: string; mfe_r: number | null; mae_r: number | null } | null;
  line: string;
}

export interface TradeRecall { recall: Recall; reflection: Reflection }
export interface ProposalRecall { recall: Recall }

export const recallApi = {
  trade: (tradeId: number) => request<TradeRecall>(`/setup-recall/trades/${tradeId}`),
  proposal: (actionId: number) => request<ProposalRecall>(`/setup-recall/proposals/${actionId}`),
};

/** The one line that stays open: how many similar closed trades, and the aggregate only from the minimum sample. */
export function recallSummary(r: Recall): string {
  if (r.count === 0) return "No similar closed trades of yours yet";
  const noun = r.count === 1 ? "similar past trade" : "similar past trades";
  if (!r.enough) return `${r.count} ${noun} · not enough history yet (${r.count} of ${r.min_sample})`;
  const avg = r.avg_r !== null ? ` · average ${rWords(r.avg_r)}` : "";
  return `${r.count} ${noun}: ${r.wins ?? 0} with a gain, ${r.losses ?? 0} with a loss${avg}`;
}

export function rWords(r: number | null): string {
  if (r === null || !Number.isFinite(r)) return "-";
  return `${r > 0 ? "+" : ""}${formatNumber(r, 2)} R`;
}

export const SIDE_WORDS: Record<string, string> = { LONG: "Long", SHORT: "Short" };
export const HOLDING_WORDS: Record<string, string> = { INTRADAY: "Intraday", SWING: "Swing" };

/** The stored context in words: "Long · Intraday · 15min chart" (the timeframe only when a bot recorded one). */
export function contextWords(c: RecallContext): string {
  const parts = [SIDE_WORDS[c.side] ?? c.side, HOLDING_WORDS[c.holding] ?? c.holding];
  if (c.timeframe) parts.push(`${c.timeframe} chart`);
  return parts.join(" · ");
}

export const JOURNAL_ADDRESS = V2_ALIASES.find((a) => a.page === "journal")?.path ?? "/journal";

/** The journal row of a match: its week, and the row opened (`#trade-<id>`). */
export function journalLink(m: Pick<RecallMatch, "trade_id" | "journal_week">): string {
  return `${JOURNAL_ADDRESS}${m.journal_week ? `?week=${encodeURIComponent(m.journal_week)}` : ""}#trade-${m.trade_id}`;
}

/** The week and trade a journal address asks for (`?week=2026-W40#trade-12`), or nulls. */
export function journalTarget(search: string, hash: string): { week: string | null; tradeId: number | null } {
  const week = new URLSearchParams(search).get("week");
  const m = /^#trade-(\d+)$/.exec(hash);
  return { week: week && /^\d{4}-W\d{2}$/.test(week) ? week : null, tradeId: m ? Number(m[1]) : null };
}
