import type { DailyReport, ReportRow } from "./types";

/** M88-R tests and stories only: a stored report's shape (made-up figures, never shown to a user). */
const row = (symbol: string, change: number, over: Partial<ReportRow> = {}): ReportRow => ({
  symbol, name: symbol, kind: "EQ", sector: "Financial Services", missing: false, close: 1000 + change * 10, change_pct: change,
  volume_ratio: 1.2, delivery_pct: null, range_position_pct: 60, new_recent_high: false, new_recent_low: false,
  structure_d: { direction: change >= 0 ? "up" : "down", words: "", swing_high: null, swing_low: null },
  structure_w: { direction: "range", words: "", swing_high: null, swing_low: null },
  fut_oi_change_pct: change * 2, pcr: null, read: `${symbol} closed with its structure unchanged.`, areas: [], ...over,
});

export const REPORT: DailyReport = {
  id: 7, trade_date: "2026-11-06", info_line: "Last close 6 Nov 2026 · AI-generated · information only, not advice", generated_at: "2026-11-06T13:00:00Z",
  index: [row("NIFTY 50", 0.62, { kind: "INDEX", sector: null, close: 24180.4, volume_ratio: null, read: null })],
  stocks: [row("TESTAAA", 1.5), row("TESTBBB", -0.8, { sector: "Information Technology" }), row("TESTCCC", 0.2, { pcr: 0.9 }),
    row("TESTDDD", 0, { missing: true, change_pct: null, read: null, fut_oi_change_pct: null })],
  breadth: { advances: 2, declines: 1, unchanged: 0, stocks: 3, new_recent_high: 1, new_recent_low: 0, new_recent_high_pct: 33.3, new_recent_low_pct: 0 },
  sectors: [{ sector: "Financial Services", stocks: 2, avg_change_pct: 0.85, advances: 2, declines: 0 },
    { sector: "Information Technology", stocks: 1, avg_change_pct: -0.8, advances: 0, declines: 1 }],
  leaders: ["TESTAAA"], laggards: ["TESTBBB"], missing: ["TESTDDD"],
  sections: { index_read: "The index closed higher; the Daily structure is up.", breadth_read: "Two stocks rose and one fell.",
    sectors_read: "Financial services led.", leaders_laggards: "TESTAAA led; TESTBBB lagged.", notable_structures: "TESTCCC is near a demand area.",
    conclusion: "A firm close with broad participation." },
};
