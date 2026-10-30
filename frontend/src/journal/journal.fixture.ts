import type { DisciplineScore, JournalEntryFull, JournalList, JournalListRow, JournalStatsResponse, JournalWeekV2, StatsGroup } from "./types";

/**
 * M62 JN-3: made-up journal answers for the unit tests and the e2e pictures (made-up symbols, not anyone's trades).
 * The statistics are the backend fixture of tests/test_m62_journal_stats.py, worked out by hand there.
 */

const row = (over: Partial<JournalListRow>): JournalListRow => ({
  kind: "trade", id: 1, unit: "t:1", source: "ATP", source_label: null, outside_atp: false, mode: "PAPER", symbol: "DEMOA", direction: "LONG",
  setup: "Buyer area", timeframe: "15min", entry_time: "2032-03-15T04:15:00Z", exit_time: "2032-03-15T06:00:00Z", entry_price: 2450.5,
  exit_price: 2478, quantity: 10, charges: 42.5, pnl: 232.5, r_multiple: 1.16, outcome: "win", exit_reason: "Closed by hand", tags: [],
  user_tags: [], notes: null, attachments: 0, chart: "/journal/trades/1/chart?at=entry", mae_r: 0.3, mfe_r: 1.8, leg_group_id: null, ...over,
});

export const DEMO_LIST: JournalList = {
  total: 4, truncated: false, setups: ["Buyer area", "Seller area"], tags: ["early_exit", "late entry"],
  entries: [
    row({ id: 4, unit: "m:4", kind: "manual", source: "OUTSIDE_ATP", source_label: "Manual - done outside ATP", outside_atp: true, mode: null,
      symbol: "DEMOD", setup: "Seller area", direction: "SHORT", entry_time: "2032-03-17T05:30:00Z", exit_time: "2032-03-17T07:00:00Z",
      pnl: -1250, r_multiple: -0.5, outcome: "loss", user_tags: ["late entry"], chart: "/journal/manual/4/chart?at=entry" }),
    row({ id: 3, unit: "t:3", symbol: "DEMOC", entry_time: "2032-03-16T08:45:00Z", exit_time: null, exit_price: null, pnl: null, r_multiple: null,
      outcome: "open", chart: "/journal/trades/3/chart?at=entry" }),
    row({ id: 2, unit: "t:2", symbol: "DEMOB", setup: "Seller area", direction: "SHORT", entry_time: "2032-03-16T04:00:00Z", pnl: -540.25,
      r_multiple: -1, outcome: "loss", tags: [{ id: 21, tag: "early_exit", status: "SUGGESTED", source: "AUTO" }],
      chart: "/journal/trades/2/chart?at=entry" }),
    row({ id: 1, unit: "t:1", pnl: 123456.75, r_multiple: 3.25, user_tags: ["late entry"] }),
  ],
};

const group = (over: Partial<StatsGroup>): StatsGroup => ({
  trades: 0, closed: 0, open: 0, wins: 0, losses: 0, flat: 0, outside_atp: 0, win_rate: null, net_pnl: 0, gross_win: 0, gross_loss: 0,
  profit_factor: null, expectancy: null, avg_win: null, avg_loss: null, avg_r: null, r_trades: 0, avg_mae_r: null, avg_mfe_r: null,
  avg_mae: null, avg_mfe: null, excursion_trades: 0, ...over,
});

/** test_summary_maths_by_hand: 6 trades, 5 closed (2 wins, 2 losses, 1 flat), net 350, PF 500 / 150, expectancy 70. */
export const DEMO_STATS: JournalStatsResponse = {
  totals: group({ trades: 6, closed: 5, open: 1, wins: 2, losses: 2, flat: 1, outside_atp: 1, win_rate: 40, net_pnl: 350, gross_win: 500,
    gross_loss: -150, profit_factor: 3.33, expectancy: 70, avg_win: 250, avg_loss: -75, avg_r: 0.25, r_trades: 4, avg_mae_r: 0.75,
    avg_mfe_r: 1.85, avg_mae: 75, avg_mfe: 185, excursion_trades: 2 }),
  by_setup: [
    { setup: "S1", ...group({ trades: 3, closed: 3, wins: 1, losses: 1, flat: 1, win_rate: 33.3, net_pnl: 200, profit_factor: 3, expectancy: 66.67, avg_r: 0.67 }) },
    { setup: "S2", ...group({ trades: 3, closed: 2, open: 1, wins: 1, losses: 1, win_rate: 50, net_pnl: 150, profit_factor: 4, expectancy: 75, avg_r: -1 }) },
  ],
  by_timeframe: [
    { timeframe: "15m", ...group({ trades: 3, closed: 2, net_pnl: 150 }) }, { timeframe: "5m", ...group({ trades: 2, closed: 2, net_pnl: 200 }) },
    { timeframe: "-", ...group({ trades: 1, closed: 1 }) },
  ],
  by_weekday: [
    { weekday: "Monday", ...group({ trades: 2, closed: 2, net_pnl: 200 }) }, { weekday: "Tuesday", ...group({ trades: 2, closed: 2, net_pnl: 200 }) },
    { weekday: "Wednesday", ...group({ trades: 1, closed: 1, net_pnl: -50 }) }, { weekday: "Thursday", ...group({ trades: 1, open: 1 }) },
  ],
  truncated: false, setups: ["S1", "S2"], tags: [],
};

export const DEMO_ENTRY: JournalEntryFull = {
  kind: "trade", id: 2, manual: false, source: "ATP", symbol: "DEMOB", mode: "PAPER", direction: "SHORT", setup: "Seller area",
  entry_time: "2032-03-16T04:00:00Z", exit_time: "2032-03-16T05:10:00Z",
  plan_view: { setup: "Seller area", timeframe: "15min", area: ["Pullback into the seller area", "Weakening momentum"], invalidation: 1012.5,
    target: 985, entry: 1005, entry_source: "signal" },
  execution: { entry_price: 1005.25, quantity: 50, slippage: 0.25, exit_price: 1012.5, exit_reason: "Stop hit" },
  fills: [], costs: { charges: 40, source: "ESTIMATED" }, pnl: -402.5, r_multiple: -1.11, risk_amount: 362.5, holding_minutes: 70,
  excursions: { mae: 7.5, mfe: 3.1, mae_r: 1.03, mfe_r: 0.43 }, notes: "Entered before the candle closed.",
  tags: [{ id: 21, trade_id: 2, tag: "early_exit", source: "AUTO", status: "SUGGESTED", evidence: {}, detected_at: null, decided_at: null }],
  user_tags: ["late entry"], checklist: [{ rule: "Trend agrees", followed: true, retired: false }, { rule: "Waited for the close", followed: false, retired: false },
    { rule: "Size from my settings", followed: null, retired: false }],
  attachments: [{ id: 7, trade_id: 2, manual_trade_id: null, size_bytes: 12000, width: 640, height: 360, created_at: "2032-03-16T06:00:00Z" }],
  charts: { entry: "/journal/trades/2/chart?at=entry", exit: "/journal/trades/2/chart?at=exit" },
};

export const DEMO_SCORE: DisciplineScore = {
  score: 78, decisions: 9, trades: 9, count_unconfirmed: false, note: "(1 open · 8 closed)", open: 1, closed: 8,
  components: [
    { name: "stop_discipline", tags: ["no_stop", "moved_stop_against"], weight: 30, weight_share: 0.3, decisions: 9, flagged: 1, clean_pct: 88.9, points: 26.7, max_points: 30, by_tag: { no_stop: 1 } },
    { name: "plan_adherence", tags: ["fomo_entry", "early_exit"], weight: 25, weight_share: 0.25, decisions: 9, flagged: 2, clean_pct: 77.8, points: 19.4, max_points: 25, by_tag: { early_exit: 2 } },
    { name: "emotional_control", tags: ["revenge_trade", "averaging_loser"], weight: 25, weight_share: 0.25, decisions: 9, flagged: 1, clean_pct: 88.9, points: 22.2, max_points: 25, by_tag: { revenge_trade: 1 } },
    { name: "frequency_control", tags: ["overtrading"], weight: 10, weight_share: 0.1, decisions: 9, flagged: 0, clean_pct: 100, points: 10, max_points: 10, by_tag: {} },
    { name: "event_awareness", tags: ["held_through_event"], weight: 10, weight_share: 0.1, decisions: 9, flagged: 0, clean_pct: 100, points: 10, max_points: 10, by_tag: {} },
  ],
};

export const DEMO_WEEK: JournalWeekV2 = {
  week: "2032-W11", start: "2032-03-14T18:30:00Z", end: "2032-03-21T18:30:00Z", score: DEMO_SCORE,
  entries: [
    { trade_id: 1, symbol: "DEMOA", mode: "PAPER", direction: "LONG", setup: "Buyer area", source: "MANUAL", instrument_kind: "UNDERLYING", leg_group_id: null,
      entry_time: "2032-03-15T04:15:00Z", exit_time: "2032-03-15T06:00:00Z", plan: {}, execution: { entry_price: 2450.5, quantity: 10, slippage: null, exit_price: 2478, exit_reason: null, stop_now: 2440 },
      fills: [], costs: { charges: 42.5, source: "ESTIMATED" }, pnl: 232.5, r_multiple: 1.16, risk_amount: 105, holding_minutes: 105,
      excursions: { mae: null, mfe: null, mae_r: null, mfe_r: null }, stop_history: [], chart_entry: null, chart_exit: null, greeks_entry: null, iv_entry: null,
      notes: null, tags: [{ id: 30, trade_id: 1, tag: "early_exit", source: "AUTO", status: "CONFIRMED", evidence: {}, detected_at: null, decided_at: null }],
      user_tags: ["late entry"] },
    { trade_id: 2, symbol: "DEMOB", mode: "PAPER", direction: "SHORT", setup: "Seller area", source: "MANUAL", instrument_kind: "UNDERLYING", leg_group_id: null,
      entry_time: "2032-03-16T04:00:00Z", exit_time: "2032-03-16T05:10:00Z", plan: {}, execution: { entry_price: 1005.25, quantity: 50, slippage: 0.25, exit_price: 1012.5, exit_reason: null, stop_now: 1012.5 },
      fills: [], costs: { charges: 40, source: "ESTIMATED" }, pnl: -402.5, r_multiple: -1.11, risk_amount: 362.5, holding_minutes: 70,
      excursions: { mae: null, mfe: null, mae_r: null, mfe_r: null }, stop_history: [], chart_entry: null, chart_exit: null, greeks_entry: null, iv_entry: null,
      notes: null, tags: [{ id: 21, trade_id: 2, tag: "early_exit", source: "AUTO", status: "SUGGESTED", evidence: {}, detected_at: null, decided_at: null },
        { id: 22, trade_id: 2, tag: "fomo_entry", source: "AUTO", status: "DISMISSED", evidence: {}, detected_at: null, decided_at: null }],
      user_tags: ["late entry"] },
  ],
  manual_entries: [{ ...DEMO_ENTRY, kind: "manual", id: 4, manual: true, source: "OUTSIDE_ATP", symbol: "DEMOD", tags: [], user_tags: ["late entry"],
    entry_time: "2032-03-17T05:30:00Z", pnl: -1250, r_multiple: -0.5 }],
};
