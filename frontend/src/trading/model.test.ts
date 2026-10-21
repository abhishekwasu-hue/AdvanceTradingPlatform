import { describe, expect, it } from "vitest";
import { ApiError } from "../api/errors";
import {
  allocationRows, bookedCurve, checkStatus, closedRows, closedText, drawdownOrderProblem, eventAction, eventRemoveConsequence, eventScope, eventWindow, exitConfirmText,
  failedText, fieldProblem, guardianLine, heldRows, holdingTiles, limitScopeLabel, limitValueText, longShortLine, openCaption, portfolioTiles,
  positionLevels, structureLabel, todayRisk,
} from "./model";
import { CONFIG, exposure, guardianMode, marketEvent, trade } from "./trading.fixture";

/** UI2-9a: what My positions, My portfolio and Risk limits show, from made-up API answers. */

describe("My positions rows", () => {
  it("size in lots, the average, the live price and P&L from the exposure", () => {
    const [row] = heldRows([trade()], exposure());
    expect(row.detail).toBe("Buy · 2 lots (150) · avg 100.00");
    expect(row.mode).toBe("Paper");
    expect(row.ltp).toBeCloseTo(110);
    expect(row.pnl).toBeCloseTo(1_500);
    expect(row.pnlPct).toBeCloseTo(10);
    expect(row.strategy).toBe("Demo breakout");
    expect(row.chartSymbol).toBe("DEMO A");
  });

  it("no live price: no LTP and no P&L (never a figure at the entry price)", () => {
    const [row] = heldRows([trade()], exposure({ price_source: "entry" }));
    expect(row.ltp).toBeNull();
    expect(row.pnl).toBeNull();
    expect(heldRows([trade()], null)[0].pnl).toBeNull();
  });

  it("stop in red and target in green; an option's levels are its underlying's, with the premium floor", () => {
    expect(positionLevels(trade())).toEqual({ on: null, levels: [
      { label: "Stop", value: "95.00", tone: "down" }, { label: "Target", value: "110.00", tone: "up" }] });
    const opt = trade({ instrument_kind: "OPTION", symbol: "DEMO 24800 CE", underlying_symbol: "DEMO IDX", underlying_direction: "LONG",
      underlying_stop_loss: 24_700, underlying_target1: 24_900, stop_loss: 60 });
    const l = positionLevels(opt);
    expect(l.on).toBe("On DEMO IDX (Long):");
    expect(l.levels.map((x) => `${x.label} ${x.value}`)).toEqual(["Stop 24,700.00", "Target 24,900.00", "Premium floor 60.00"]);
    expect(positionLevels({ ...opt, option_position: "WRITE" }).levels[2].label).toBe("Premium ceiling");
    expect(heldRows([opt], null)[0].chartSymbol).toBe("DEMO IDX");
  });

  it("a structure leg names the structure, its credit, worst case and exit levels", () => {
    const leg = trade({ leg_group_id: "g1", leg_role: "SHORT_PUT", option_strategy: "IRON_CONDOR",
      group_meta: { net_credit: 45, max_loss: 155, target_value: 20, stop_value: 90 } as TradeRecordMeta });
    expect(structureLabel(leg)).toEqual({ label: "Iron condor · short put leg",
      note: "Credit 45.00 · most it can lose 155.00 a unit · closes at or below 20.00, or at or above 90.00" });
    expect(structureLabel(trade())).toEqual({ label: null, note: null });
  });

  it("tiles: count with paper / live split, open P&L only when every symbol is priced, booked today, risk at stops", () => {
    const t = holdingTiles([trade(), trade({ id: 2, mode: "LIVE", symbol: "DEMO B" })], exposure());
    expect(t).toMatchObject({ open: 2, paper: 1, live: 1, openPnl: 1_500, booked: 2_540, riskAtStops: 12_000, riskPct: 1.2 });
    expect(openCaption(t)).toBe("1 paper · 1 live");
    expect(holdingTiles([trade()], exposure({ price_source: "entry" })).openPnl).toBeNull();
    expect(openCaption(holdingTiles([], null))).toBe("No open positions");
  });

  it("Exit now keeps today's confirm text exactly (UI-11)", () => {
    expect(exitConfirmText(trade())).toBe("Exit LONG DEMO A x150 (PAPER) now, at the current price?");
  });

  it("an exit's result and a failure, in plain words", () => {
    expect(closedText(108, 1_200, "EXIT_NOW")).toBe("Closed (Exit now) at 108.00: +₹1,200.00.");
    expect(closedText(90, -750)).toBe("Closed at 90.00: −₹750.00.");
    expect(failedText("DEMO A was not closed.", new ApiError(409, "This conflicts with the current state. Refresh and try again.")))
      .toBe("DEMO A was not closed. This conflicts with the current state. Refresh and try again.");
  });
});

describe("closed trades", () => {
  it("only closed trades, the latest first, with charges marked as estimate or actual", () => {
    const rows = closedRows([
      trade({ id: 1, exit_time: "2026-10-07T09:00:00", exit_price: 108, pnl: 1_200, exit_reason: "TARGET_1" }),
      trade({ id: 2 }),
      trade({ id: 3, exit_time: "2026-10-08T06:00:00", exit_price: 95, pnl: -750, charges_source: "CONTRACT_NOTE", direction: "SHORT" }),
    ]);
    expect(rows.map((r) => r.id)).toEqual([3, 1]);
    expect(rows[0]).toMatchObject({ side: "Short", chargesActual: true, prices: "100.00 → 95.00", when: "8 Oct 2026" });
    expect(rows[1]).toMatchObject({ reason: "Target 1", charges: "₹40.00", chargesActual: false, size: "2 lots (150)" });
  });
});

describe("My portfolio", () => {
  it("allocation from the exposure, the largest first, the concentrated one marked", () => {
    const rows = allocationRows(null, exposure());
    expect(rows.map((r) => r.symbol)).toEqual(["DEMO B", "DEMO A"]);
    expect(rows[1]).toMatchObject({ pct: 45, pctOf: "capital", concentrated: true, unrealised: 1_500, strategies: "Demo breakout" });
    expect(allocationRows(null, exposure({ price_source: "entry" }))[0].unrealised).toBeNull();
  });

  it("without the exposure: shares of the money in open positions at the entry price, no P&L", () => {
    const rows = allocationRows([trade(), trade({ id: 2, symbol: "DEMO B", entry_price: 50, quantity: 100 })], null);
    expect(rows.map((r) => [r.symbol, Math.round(r.pct)])).toEqual([["DEMO A", 75], ["DEMO B", 25]]);
    expect(rows[0]).toMatchObject({ pctOf: "invested", unrealised: null, risk: null });
  });

  it("tiles, the booked curve and the long / short line", () => {
    const closed = [trade({ id: 1, exit_time: "2026-10-07T09:00:00", pnl: 1_000 }), trade({ id: 2, exit_time: "2026-10-06T09:00:00", pnl: -400 })];
    expect(portfolioTiles([trade()], closed, exposure())).toMatchObject({ invested: 300_000, investedPct: 30, openPnl: 1_500, booked: 600 });
    expect(portfolioTiles([trade()], null, null)).toMatchObject({ invested: 15_000, investedPct: null, openPnl: null, booked: null });
    expect(bookedCurve(closed)).toEqual([-400, 600]);
    expect(longShortLine(exposure())).toBe("Long ₹2 L · Short ₹1 L · Net ₹1 L");
    expect(longShortLine(exposure({ open_positions: 0 }))).toBeNull();
  });
});

describe("Risk limits", () => {
  it("today: the daily loss limit and risk per trade in rupees; the paper limit in use from the server's share", () => {
    const t = todayRisk(CONFIG, { today: { used: 0.39, limit: 3_000, pnl: -1_170 }, lockout: null } as never);
    expect(t).toMatchObject({ limit: 3_000, limitPct: 3, perTrade: 1_000, paperLimit: 3_000 });
    expect(t.paperUsed).toBeCloseTo(1_170);
    expect(t.paperUsedPct).toBeCloseTo(39);
    expect(todayRisk(CONFIG, null)).toMatchObject({ paperLimit: null, paperUsed: null, lockout: null });
    const locked = todayRisk(CONFIG, { today: { used: 1, limit: 3_000 }, lockout: { reason: "Daily loss limit reached." } } as never);
    expect(locked.lockout).toBe("New paper trades are paused for today: Daily loss limit reached. Exits keep working.");
  });

  it("the guardian's state in words; amber only when it is not normal", () => {
    const line = guardianLine("PAPER", guardianMode());
    expect(line).toMatchObject({ mode: "Paper", state: "Size cut to 50%", tone: "warn" });
    expect(line.detail[0]).toBe("Drawdown 5.8% from the peak of ₹1.04 L, over 12 closed trades");
    expect(line.detail).toContain("Waiting after a stop-out: DEMO A (12 min left)");
    expect(guardianLine("LIVE", guardianMode({ state: "paused" })).state).toBe("New entries paused");
    expect(guardianLine("LIVE", guardianMode({ state: "normal", size_multiplier: 1 }))).toMatchObject({ state: "Normal size", tone: "neutral" });
  });

  it("market events: scope, window, action, and what removing one changes", () => {
    expect(eventScope(null)).toBe("All symbols");
    expect(eventScope("INDEX")).toBe("NIFTY, BANK NIFTY and SENSEX");
    expect(eventScope("demo a")).toBe("DEMO A");
    expect(eventWindow(null, null)).toBe("All day");
    expect(eventWindow("09:15", null)).toBe("09:15 to 23:59");
    expect(eventAction(marketEvent(), 50)).toBe("No new entries");
    expect(eventAction(marketEvent({ action: "SIZE_CUT" }), 50)).toBe("Size cut by 50%");
    expect(eventRemoveConsequence(marketEvent())).toBe("New entries in NIFTY, BANK NIFTY and SENSEX will no longer be blocked on 2 Nov 2026.");
    expect(eventRemoveConsequence(marketEvent({ action: "SIZE_CUT", underlying: null }))).toBe("Trades in any symbol will use their full size on 2 Nov 2026.");
  });

  it("organisation limits and checks in plain words", () => {
    expect(limitValueText("MAX_DAILY_LOSS", 5_000)).toBe("₹5,000.00");
    expect(limitValueText("MAX_SYMBOL_CONCENTRATION_PCT", 25)).toBe("25%");
    expect(limitValueText("MAX_OPEN_POSITIONS", 4)).toBe("4");
    expect(limitScopeLabel("INSTRUMENT", "DEMO A")).toBe("One symbol: DEMO A");
    expect(limitScopeLabel("GLOBAL", "")).toBe("Platform");
    expect(checkStatus("BLOCK")).toEqual({ label: "Blocked", tone: "warn" });
    expect(checkStatus("PASS")).toEqual({ label: "Passed", tone: "neutral" });
  });

  it("a field's problem: blank, not a number, below zero, above the ceiling", () => {
    expect(fieldProblem("")).toBe("Enter a number.");
    expect(fieldProblem("abc")).toBe("Enter a number.");
    expect(fieldProblem("-1")).toBe("Enter 0 or more.");
    expect(fieldProblem("3", 2)).toBe("The most allowed is 2.");
    expect(fieldProblem("2", 2)).toBeNull();
    expect(fieldProblem("1.5")).toBeNull();
    expect(fieldProblem("5", null, 15)).toBe("The least allowed is 15.");
    expect(fieldProblem("15", null, 15)).toBeNull();
    expect(fieldProblem("5", null, 0)).toBeNull();
  });

  it("the drawdown ladder halves the size before it pauses entries (an added check only)", () => {
    expect(drawdownOrderProblem("12", "10")).toBe("Halve the size at a drawdown no larger than the one that pauses new entries (10%).");
    expect(drawdownOrderProblem("10", "10")).toBeNull();
    expect(drawdownOrderProblem("5", "10")).toBeNull();
    expect(drawdownOrderProblem("", "10")).toBeNull();                  // the blank field has its own message
  });
});

type TradeRecordMeta = NonNullable<import("../types").TradeRecord["group_meta"]>;
