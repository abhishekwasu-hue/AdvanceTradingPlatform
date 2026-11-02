import { describe, expect, it } from "vitest";
import plansSource from "./TriggerPlansV2.tsx?raw";
import { EMPTY_EXITS, legOf } from "./plan";
import { EMPTY_TRIGGER, istInstant, planRequest, statusTone, triggerMissing, type TriggerDraft } from "./triggerPlans";

/**
 * M75 W-8 (owner order M75 CW-6; the PR plan's W-8 row): the trigger plan form - nothing pre-chosen (every field Not set,
 * "place at once" / "ask me first" left to him or his Trade settings), the request carries only his values, and the form
 * calls only the plan routes (no order route: only the worker fires a plan, through the ticket).
 * backend/tests/test_m75_w8_trigger_plans.py proves the server: fires once, exits follow, restart, approval-only.
 */
const INST = { spot: "24500", lotSize: "75", step: "50" };
const LEG = legOf({ direction: "BUY", optionType: "CE", strike: "24700", expiry: "2026-11-25", lots: "2", premium: "80" }, INST, "OPTIONS")!;
const FULL: TriggerDraft = { ...EMPTY_TRIGGER, level: "24650", side: "ABOVE", mode: "TOUCH", validUntil: "2026-11-05T15:00", orderType: "MARKET", product: "NRML" };

describe("W-8: nothing pre-chosen", () => {
  it("every field starts Not set and the form lists what is missing", () => {
    expect(Object.values(EMPTY_TRIGGER).every((v) => v === null || v === "")).toBe(true);
    expect(triggerMissing(EMPTY_TRIGGER, [])).toEqual(["A leg", "Level", "Above or below", "Touch or close", "Valid until", "Order type", "Product"]);
    expect(triggerMissing(FULL, [LEG])).toEqual([]);
    expect(triggerMissing({ ...FULL, mode: "CLOSE" }, [LEG])).toEqual(["Timeframe"]);
    expect(triggerMissing({ ...FULL, windowStart: "10:00" }, [LEG])).toEqual(["Window start and end"]);
  });
});

describe("W-8: the request is his own values", () => {
  it("condition, legs, his stop / target as premium levels, validity in IST; approval left Not set when he did not pick", () => {
    const body = planRequest("NIFTY 50", FULL, [LEG], { ...EMPTY_EXITS, stopKind: "POINTS", stop: "20", targetKind: "PCT", target: "50" }, "tp-abc12345");
    expect(body).toEqual({
      underlying: "NIFTY 50", client_ref: "tp-abc12345",
      condition: { level: 24650, side: "ABOVE", mode: "TOUCH", timeframe_minutes: null, window_start: null, window_end: null },
      orders: [{ expiry: "2026-11-25", strike: 24700, right: "CE", side: "BUY", lots: 2, order_type: "MARKET", product: "NRML", price: null, stop_loss: 60, target: 120 }],
      approval: null, valid_until: "2026-11-05T09:30:00.000Z",
    });
  });
  it("a limit uses the leg's own premium; a structure carries no per-leg stop / target", () => {
    const sell = { ...LEG, id: "x", direction: "SELL" as const, strike: 24900, premium: 30 };
    const body = planRequest("NIFTY 50", { ...FULL, orderType: "LIMIT" }, [LEG, sell], { ...EMPTY_EXITS, stopKind: "POINTS", stop: "20" }, "tp-abc12345");
    const orders = body.orders as { price: number | null; stop_loss: number | null }[];
    expect(orders.map((o) => o.price)).toEqual([80, 30]);
    expect(orders.every((o) => o.stop_loss === null)).toBe(true);
  });
  it("IST validity", () => { expect(istInstant("2026-11-05T15:00")).toBe("2026-11-05T09:30:00.000Z"); });
  it("states are coloured by meaning and always named", () => {
    expect(statusTone("ARMED")).toBe("up");
    expect(statusTone("TRIGGERED")).toBe("warn");
    expect(statusTone("CANCELLED")).toBe("muted");
    expect(plansSource).toContain("{p.status_words}");
  });
  it("the form calls only the plan routes - never an order route", () => {
    expect(plansSource).toContain('"/trigger-plans"');
    for (const route of ["/paper/fno/order", "/paper/ticket", "fnoApi", "basketApi", "useEffect(() => { void save"]) expect(plansSource).not.toContain(route);
  });
});
