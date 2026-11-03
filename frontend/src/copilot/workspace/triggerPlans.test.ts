import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import cardSource from "./DryRunCardV2.tsx?raw";
import { DryRunCardV2 } from "./DryRunCardV2";
import plansSource from "./TriggerPlansV2.tsx?raw";
import { EMPTY_EXITS, legOf } from "./plan";
import {
  EMPTY_TRIGGER, MAX_DRY_SESSIONS, READ_TIMEFRAMES, areaChoices, conditionRequest, conditionText, dryLines, dryMissing, dryOrderLine,
  dryRunRequest, drySessions, dryTone, istInstant, planRequest, statusTone, triggerMissing,
  type DryRow, type DryRunView, type PlanArea, type PlanView, type TriggerDraft,
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

/**
 * M76 MP-12 (owner order M76 D-2): the dry run - his N (Not set until he types it, bounded as the server), only his
 * condition needed, his legs sent once the order fields are set, ONE compact card (triggered sessions first, at most three
 * lines visible, the rest behind More) and no action on it: the form never reaches an order route.
 * backend/tests/test_m76_mp12_dry_run.py proves the server: nothing written or sent, no look-ahead, the live evaluator.
 */
const row = (session: string, state: DryRow["state"], at: string | null = null): DryRow => ({
  session, state, state_words: state === "TRIGGERED" ? "Would have triggered" : "Not met", armed_at: `${session}T00:00:00+05:30`, at,
  price: at ? 24701.5 : null, source: "bars", source_words: "the stored one-minute candles", words: "", candles: 10,
});
const VIEW: DryRunView = {
  dry_run: true, placed: 0, condition_words: "NIFTY above 24700 (on a touch)", timeframe: "1m", sessions_asked: 5,
  sessions: [row("2026-10-26", "NOT_MET"), row("2026-10-27", "TRIGGERED", "2026-10-27T04:00:00+00:00"), row("2026-10-28", "NOT_MET"),
    row("2026-10-29", "TRIGGERED", "2026-10-29T05:00:00+00:00"), row("2026-10-30", "NOT_MET")],
  triggered: 2, reads: 0, cut: false, note: null, once: "A plan fires once.",
  on_trigger: { choice: "AUTO", ask_first: false, words: "Would place at once.", orders: [
    { label: "NIFTY 24700 CE", side: "BUY", lots: 2, fill_price: 80, can_place: true, errors: [], refusals: [], warnings: ["This contract expires today."] }] },
};

describe("MP-12: the dry run", () => {
  it("N is his own: Not set until typed, a whole number within the server's bound", () => {
    expect(drySessions("")).toBeNull();
    expect(drySessions("0")).toBeNull();
    expect(drySessions("2.5")).toBeNull();
    expect(drySessions(String(MAX_DRY_SESSIONS + 1))).toBeNull();
    expect(drySessions("5")).toBe(5);
  });
  it("needs only his condition and N - no leg, validity, order type or product", () => {
    expect(dryMissing(EMPTY_TRIGGER, "")).toEqual(["What the plan watches", "Sessions"]);
    expect(dryMissing({ ...EMPTY_TRIGGER, kind: "PRICE", level: "24700", side: "ABOVE", mode: "TOUCH" }, "3")).toEqual([]);
    expect(dryMissing({ ...EMPTY_TRIGGER, kind: "STRUCTURE" }, "3")).toEqual(["BOS or CHoCH", "Up or down", "Candle timeframe"]);
  });
  it("the request carries his condition, his legs once the order fields are set, and his own choice", () => {
    const bare = dryRunRequest("NIFTY 50", { ...FULL, orderType: null }, [LEG], EMPTY_EXITS, 3);
    expect(bare).toEqual({ sessions: 3, underlying: "NIFTY 50", condition: conditionRequest(FULL), orders: [], approval: null });
    const full = dryRunRequest("NIFTY 50", { ...FULL, validUntil: "" }, [LEG], EMPTY_EXITS, 3);
    expect(full.orders).toEqual(planRequest("NIFTY 50", FULL, [LEG], EMPTY_EXITS, "x").orders);
    expect(full).not.toHaveProperty("client_ref");
    expect(full).not.toHaveProperty("valid_until");
  });
  it("one compact card: triggered sessions first, three lines visible, the rest behind More; colour by meaning", () => {
    expect(dryLines(VIEW).map((r) => r.session)).toEqual(["2026-10-29", "2026-10-27", "2026-10-30", "2026-10-28", "2026-10-26"]);
    expect([dryTone("TRIGGERED"), dryTone("NOT_MET"), dryTone("NO_READING")]).toEqual(["up", "muted", "warn"]);
    const html = renderToStaticMarkup(DryRunCardV2({ view: VIEW }));
    expect(html.match(/data-trigger="dry-card"/g)).toHaveLength(1);
    expect(html).toContain("2 would have triggered");
    expect(html).toContain("Nothing placed (PAPER)");
    expect(html).toContain('data-details="copilot.trigger.dry"');
    expect(html.indexOf("Would have triggered")).toBeLessThan(html.indexOf("Not met"));
  });
  it("an order line is the ticket's own first refusal, else Ready with its warning count", () => {
    expect(dryOrderLine(VIEW.on_trigger!.orders[0])).toEqual({ ok: true, text: "Ready · 1 warning" });
    expect(dryOrderLine({ ...VIEW.on_trigger!.orders[0], can_place: false, refusals: ["Entries are paused."] })).toEqual({ ok: false, text: "Entries are paused." });
  });
  it("the card has no action and the form calls only the plan routes", () => {
    expect(cardSource).not.toContain("<button");
    expect(cardSource).not.toContain("request(");
    expect(plansSource).toContain('"/trigger-plans/dry-run"');
    for (const route of ["/paper/fno/order", "/paper/ticket", "fnoApi", "basketApi"]) expect(cardSource + plansSource).not.toContain(route);
  });
});
