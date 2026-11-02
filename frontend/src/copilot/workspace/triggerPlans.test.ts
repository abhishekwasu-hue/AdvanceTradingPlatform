import { describe, expect, it } from "vitest";
import plansSource from "./TriggerPlansV2.tsx?raw";
import { EMPTY_EXITS, legOf } from "./plan";
import {
  EMPTY_TRIGGER, READ_TIMEFRAMES, areaChoices, conditionRequest, conditionText, istInstant, planRequest, statusTone, triggerMissing,
  type PlanArea, type PlanView, type TriggerDraft,
} from "./triggerPlans";

/**
 * M75 W-8 (owner order M75 CW-6; the PR plan's W-8 row): the trigger plan form - nothing pre-chosen (every field Not set,
 * "place at once" / "ask me first" left to him or his Trade settings), the request carries only his values, and the form
 * calls only the plan routes (no order route: only the worker fires a plan, through the ticket).
 * backend/tests/test_m75_w8_trigger_plans.py proves the server: fires once, exits follow, restart, approval-only.
 */
const INST = { spot: "24500", lotSize: "75", step: "50" };
const LEG = legOf({ direction: "BUY", optionType: "CE", strike: "24700", expiry: "2026-11-25", lots: "2", premium: "80" }, INST, "OPTIONS")!;
const FULL: TriggerDraft = { ...EMPTY_TRIGGER, kind: "PRICE", level: "24650", side: "ABOVE", mode: "TOUCH", validUntil: "2026-11-05T15:00", orderType: "MARKET", product: "NRML" };

describe("W-8: nothing pre-chosen", () => {
  it("every field starts Not set and the form lists what is missing", () => {
    expect(Object.values(EMPTY_TRIGGER).every((v) => v === null || v === "")).toBe(true);
    expect(triggerMissing(EMPTY_TRIGGER, [])).toEqual(["A leg", "What the plan watches", "Valid until", "Order type", "Product"]);
    expect(triggerMissing({ ...EMPTY_TRIGGER, kind: "PRICE" }, [])).toEqual(["A leg", "Level", "Above or below", "Touch or close", "Valid until", "Order type", "Product"]);
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
      condition: { kind: "PRICE", level: 24650, side: "ABOVE", mode: "TOUCH", timeframe_minutes: null, window_start: null, window_end: null },
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

/**
 * M75 W-9 (the PR plan's W-9 row): method conditions - his area from the chart's reading + the reading's rejection candle,
 * or a BOS / CHoCH; his candle timeframe, no preset. backend/tests/test_m75_w9_method_conditions.py proves the server:
 * one reading source, no look-ahead, parity with a bar-by-bar replay, fires once.
 */
const AREA: PlanArea = { side: "buyer", low: 24500, high: 24550, low_text: "24,500", high_text: "24,550", timeframe: "1h" };

describe("W-9: method conditions are his own", () => {
  it("the area and structure kinds ask for their own fields, and the timeframe is one the reading offers", () => {
    const base = { ...FULL, kind: "AREA" as const, level: "", side: null, mode: null };
    expect(triggerMissing(base, [LEG])).toEqual(["Area", "Candle timeframe"]);
    expect(triggerMissing({ ...base, area: AREA, timeframe: "7" }, [LEG])).toEqual(["Candle timeframe"]);
    expect(triggerMissing({ ...base, area: AREA, timeframe: "15" }, [LEG])).toEqual([]);
    const st = { ...base, kind: "STRUCTURE" as const };
    expect(triggerMissing(st, [LEG])).toEqual(["BOS or CHoCH", "Up or down", "Candle timeframe"]);
    expect(triggerMissing({ ...st, event: "CHOCH", direction: "down", timeframe: "1440" }, [LEG])).toEqual([]);
    expect(READ_TIMEFRAMES.map((t) => t.value)).toEqual(["1", "3", "5", "15", "30", "60", "1440"]);
  });
  it("the request carries only the chosen kind's fields", () => {
    const area = conditionRequest({ ...FULL, kind: "AREA", area: AREA, timeframe: "15" });
    expect(area).toEqual({ kind: "AREA", timeframe_minutes: 15, area_side: "buyer", area_low: 24500, area_high: 24550, area_timeframe: "1h",
      window_start: null, window_end: null });
    const st = conditionRequest({ ...FULL, kind: "STRUCTURE", event: "BOS", direction: "up", timeframe: "60", windowStart: "09:30", windowEnd: "15:00" });
    expect(st).toEqual({ kind: "STRUCTURE", timeframe_minutes: 60, event: "BOS", direction: "up", window_start: "09:30", window_end: "15:00" });
  });
  it("one choice per reading area (never merged) and the list shows the server's words", () => {
    const seller = { ...AREA, side: "seller" as const, low: 24800, high: 24850, low_text: "24,800", high_text: "24,850", timeframe: "15m" };
    expect(areaChoices([AREA, seller]).map((c) => c.label)).toEqual(["Buyer 24,500 - 24,550 · 1h", "Seller 24,800 - 24,850 · 15m"]);
    const view = { underlying: "NIFTY", condition_words: "NIFTY: break of structure up on the 15-minute chart", condition: { side: "ABOVE", level: 0 } } as unknown as PlanView;
    expect(conditionText(view)).toBe("NIFTY: break of structure up on the 15-minute chart");
    expect(plansSource).toContain('data-trigger="no-areas"');
  });
});
