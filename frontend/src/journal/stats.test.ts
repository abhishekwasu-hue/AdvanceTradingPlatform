import { describe, expect, it } from "vitest";
import { journalCsv, journalStats } from "./stats";
import type { JournalEntry } from "./types";

const entry = (id: number, pnl: number | null, r: number | null, exit: string | null, over: Partial<JournalEntry> = {}): JournalEntry => ({
  trade_id: id, symbol: "NIFTY", mode: "PAPER", direction: "LONG", setup: "breakout", source: "manual", instrument_kind: "UNDERLYING",
  leg_group_id: null, entry_time: "2026-10-27T04:00:00Z", exit_time: exit, plan: {},
  execution: { entry_price: 100, quantity: 10, slippage: null, exit_price: exit ? 110 : null, exit_reason: null, stop_now: 95 },
  fills: [], costs: { charges: 20, source: "estimate" }, pnl, r_multiple: r, risk_amount: 50, holding_minutes: null,
  excursions: { mae: null, mfe: null, mae_r: null, mfe_r: null }, stop_history: [], chart_entry: null, chart_exit: null,
  greeks_entry: null, iv_entry: null, notes: null, tags: [], ...over,
});

describe("UI-28 U2.4 / U7.6 the Journal's numbers", () => {
  it("expectancy, average R and the drawdown of the closed trades in exit order; an open trade is left out", () => {
    const st = journalStats([
      entry(3, 300, 1.5, "2026-10-29T05:00:00Z"),
      entry(1, 500, 2, "2026-10-27T05:00:00Z"),
      entry(2, -800, -1, "2026-10-28T05:00:00Z"),
      entry(4, null, null, null),                                      // still open
    ]);
    expect(st.closed).toBe(3);
    expect(st.expectancy).toBe(0);
    expect(st.avgR).toBeCloseTo(0.8333, 3);
    expect(st.equity).toEqual([500, -300, 0]);
    expect(st.maxDrawdown).toBe(-800);
  });

  it("nothing invented with no closed trade; avg R is empty when no trade has an R", () => {
    expect(journalStats([entry(1, null, null, null)])).toEqual({ closed: 0, expectancy: null, avgR: null, equity: [], maxDrawdown: 0 });
    expect(journalStats([entry(1, 100, null, "2026-10-27T05:00:00Z")]).avgR).toBeNull();
  });

  it("CSV: a header row, one row per trade, quotes where a value needs them", () => {
    const csv = journalCsv([entry(7, 120.5, 1.2, "2026-10-27T05:00:00Z", { notes: 'late, "chased"' })]);
    const [head, row] = csv.trim().split("\n");
    expect(head).toBe("trade_id,symbol,mode,direction,setup,entry_time,exit_time,entry_price,exit_price,quantity,charges,net_pnl,r_multiple,tags,notes");
    expect(row).toBe('7,NIFTY,PAPER,LONG,breakout,2026-10-27T04:00:00Z,2026-10-27T05:00:00Z,100,110,10,20,120.5,1.2,,"late, ""chased"""');
  });
});
