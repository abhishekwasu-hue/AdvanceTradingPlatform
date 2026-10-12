import { describe, expect, it } from "vitest";
import type { TicketCostLine } from "../types";
import { EMPTY_FORM, costRows, gauge, lockoutLine, money, pendingLine, placedMessage, planFromForm } from "./paperTicket";

describe("plan-before-trade ticket helpers (Z5 / P1-d)", () => {
  it("builds the plan from the form, or says what is missing", () => {
    expect(planFromForm(EMPTY_FORM)).toEqual({ plan: null, missing: ["symbol", "entry", "stop", "target"] });
    const { plan } = planFromForm({ ...EMPTY_FORM, symbol: " reliance ", entry: "100", stop_loss: "98", target: "104", quantity: "", time_stop: " ",
                                    checklist: { trend_aligned: true }, override: false, override_reason: "ignored" });
    expect(plan).toEqual({ symbol: "RELIANCE", direction: "LONG", setup: "PULLBACK_AT_LEVEL", entry: 100, stop_loss: 98, target: 104, time_stop: null,
                           invalidation: null, checklist: { trend_aligned: true }, quantity: null, override: false, override_reason: null });
    expect(planFromForm({ ...EMPTY_FORM, symbol: "X", entry: "-1", stop_loss: "1", target: "2" }).missing).toEqual(["entry"]);
    const over = planFromForm({ ...EMPTY_FORM, symbol: "X", entry: "10", stop_loss: "9", target: "12", quantity: "50", override: true, override_reason: " because " });
    expect(over.plan?.quantity).toBe(50);
    expect(over.plan?.override_reason).toBe("because");
  });

  it("draws the gauge from the server's fraction and zone, never past a full arc", () => {
    expect(gauge(0, "ok")).toMatchObject({ path: "", tone: "ok", label: "0% of your cap" });
    expect(gauge(0.5, "ok").path).toBe("M 10 50 A 40 40 0 0 1 50.00 10.00");
    expect(gauge(1, "ok").path).toBe("M 10 50 A 40 40 0 0 1 90.00 50.00");
    expect(gauge(1.6, "override")).toMatchObject({ path: "M 10 50 A 40 40 0 0 1 90.00 50.00", tone: "warn", label: "160% of your cap" });
    expect(gauge(3, "over").tone).toBe("over");
    expect(gauge(null, "ok").label).toBe("0% of your cap");
  });

  it("shows the cost line in a fixed order with Indian grouping", () => {
    const costs = { items: { brokerage: 40, stt: 25.5, exchange: 1.2, sebi: 0.2, stamp: 3, gst: 7.4, slippage: 40.8 }, total: 118.1 } as TicketCostLine;
    expect(costRows(costs).map((r) => r.label)).toEqual(["Brokerage", "STT", "Exchange charges", "SEBI fee", "Stamp duty", "GST", "Slippage (estimate)"]);
    expect(costRows(costs)[0].value).toBe("40.00");
    expect(money(1234567.5)).toBe("12,34,567.50");
    expect(money(null)).toBe("-");
  });

  it("says until when a lockout holds and that exits stay available", () => {
    expect(lockoutLine({ lockout: null })).toBeNull();
    const line = lockoutLine({ lockout: { kind: "DAILY_LOSS", reason: "r", pnl: -1, limit: 1, locked_at: "2026-10-12T05:00:00Z", until: "2026-10-12T18:30:00Z" } });
    expect(line).toContain("13 Oct");
    expect(line).toContain("Exits stay available");
  });

  it("words the plan's outcome, including a proposal under approval-only", () => {
    expect(placedMessage({ status: "PROPOSED", trade_id: null, filled: null, proposal: { id: 42, status: "PROPOSED", result: null }, reasons: [] }))
      .toBe("Sent for approval: proposal #42. Approve it to place the PAPER order at the live price.");
    expect(placedMessage({ status: "PLACED", trade_id: 7, filled: { quantity: 50, price: 100.2, risk_amount: 110 }, proposal: null, reasons: [] }))
      .toBe("Placed (PAPER): position #7, 50 at 100.20.");
    expect(placedMessage({ status: "FAILED", trade_id: null, filled: null, proposal: null, reasons: ["Not placed: boom"] })).toBe("Not placed: Not placed: boom");
  });

  it("says when a looser limit starts", () => {
    expect(pendingLine({ pending: null, labels: {} as never })).toBeNull();
    const line = pendingLine({ pending: { values: { daily_loss_pct: 5 }, from: "2026-10-13" }, labels: { daily_loss_pct: "Daily loss limit, % of capital" } as never });
    expect(line).toBe("Looser limits take effect on 13 Oct (not during the day they protect): Daily loss limit, % of capital 5");
  });
});
