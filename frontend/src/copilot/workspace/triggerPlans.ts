/**
 * M75 W-8 (owner order M75 CW-6, flag `trigger_plans`): a trigger plan's request from the trade plan panel - pure, no
 * network. Every value is his own: the level, above / below, touch / close (+ his timeframe), his window, the validity,
 * the order type and product, and "place at once" / "ask me first" (Not set: his Trade settings, else the server asks).
 */
import type { Leg } from "../../optionsBuilder/model";
import { exitLevel, positive, whole, type ExitDraft } from "./plan";

export type PlanSide = "ABOVE" | "BELOW";
export type PlanMode = "TOUCH" | "CLOSE";
export type PlanApproval = "AUTO" | "APPROVAL";
/** M75 W-9: what the plan watches - his price level (W-8), his area + the reading's rejection candle, or a BOS / CHoCH. */
export type WatchKind = "PRICE" | "AREA" | "STRUCTURE";
export type StructureEvent = "BOS" | "CHOCH";
/** An area he can pick: one of the open chart's reading areas (its side, edges and own timeframe, as read). */
export interface PlanArea { side: "buyer" | "seller"; low: number; high: number; low_text: string; high_text: string; timeframe: string }
/** The candle timeframes the chart reading is offered on (minutes -> words). */
export const READ_TIMEFRAMES: { value: string; label: string }[] = [
  { value: "1", label: "1 minute" }, { value: "3", label: "3 minutes" }, { value: "5", label: "5 minutes" }, { value: "15", label: "15 minutes" },
  { value: "30", label: "30 minutes" }, { value: "60", label: "1 hour" }, { value: "1440", label: "Daily" },
];

export interface TriggerDraft {
  kind: WatchKind | null; area: PlanArea | null; event: StructureEvent | null; direction: "up" | "down" | null;
  level: string; side: PlanSide | null; mode: PlanMode | null; timeframe: string; windowStart: string; windowEnd: string;
  validUntil: string;                    // datetime-local, IST as he typed it
  orderType: "MARKET" | "LIMIT" | null; product: "MIS" | "NRML" | null; approval: PlanApproval | null;
}
export const EMPTY_TRIGGER: TriggerDraft = {
  kind: null, area: null, event: null, direction: null,
  level: "", side: null, mode: null, timeframe: "", windowStart: "", windowEnd: "", validUntil: "", orderType: null, product: null, approval: null,
};

export interface PlanView {
  id: number; name: string | null; underlying: string; status: "ARMED" | "PAUSED" | "TRIGGERED" | "DONE" | "EXPIRED" | "CANCELLED";
  status_words: string; condition_words?: string;
  condition: { kind?: WatchKind; level: number; side: PlanSide; mode: PlanMode; timeframe_minutes: number | null; window_start: string | null; window_end: string | null };
  orders: { expiry: string; strike: number | null; right: string; side: string; lots: number }[]; approval: PlanApproval;
  valid_until: string; note: string | null; last_price: number | null;
}

/** Which of his trigger fields are still Not set (the form's order); empty = the plan can be saved. */
export function triggerMissing(d: TriggerDraft, legs: Leg[]): string[] {
  const out: string[] = [];
  if (!legs.length) out.push("A leg");
  if (!d.kind) out.push("What the plan watches");
  if (d.kind === "PRICE") {
    if (positive(d.level) == null) out.push("Level");
    if (!d.side) out.push("Above or below");
    if (!d.mode) out.push("Touch or close");
    if (d.mode === "CLOSE" && whole(d.timeframe, 1440) == null) out.push("Timeframe");
  }
  if (d.kind === "AREA" && !d.area) out.push("Area");
  if (d.kind === "STRUCTURE") {
    if (!d.event) out.push("BOS or CHoCH");
    if (!d.direction) out.push("Up or down");
  }
  if ((d.kind === "AREA" || d.kind === "STRUCTURE") && !READ_TIMEFRAMES.some((t) => t.value === d.timeframe)) out.push("Candle timeframe");
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(d.validUntil)) out.push("Valid until");
  if (!d.orderType) out.push("Order type");
  if (!d.product) out.push("Product");
  if ((d.windowStart === "") !== (d.windowEnd === "")) out.push("Window start and end");
  return out;
}

/** "2026-11-05T15:00" typed in IST -> an ISO instant. */
export function istInstant(local: string): string {
  return new Date(`${local.length === 16 ? `${local}:00` : local}+05:30`).toISOString();
}

/** The request body for POST /api/trigger-plans. A single leg carries his stop / target as premium levels. */
export function planRequest(underlying: string, d: TriggerDraft, legs: Leg[], exits: ExitDraft, clientRef: string): Record<string, unknown> {
  const single = legs.length === 1 ? legs[0] : null;
  const level = (which: "stop" | "target") => {
    const kind = which === "stop" ? exits.stopKind : exits.targetKind;
    const v = positive(which === "stop" ? exits.stop : exits.target);
    if (!single || !kind || v == null) return null;
    return exitLevel({ direction: single.direction, price: single.premium, units: single.lots * single.lot_size }, kind, v, which)?.level ?? null;
  };
  return {
    underlying, client_ref: clientRef,
    condition: conditionRequest(d),
    orders: legs.map((l) => ({ expiry: l.expiry, strike: l.option_type === "FUT" ? null : l.strike, right: l.option_type, side: l.direction, lots: l.lots,
      order_type: d.orderType, product: d.product, price: d.orderType === "LIMIT" ? l.premium : null,
      stop_loss: single ? level("stop") : null, target: single ? level("target") : null })),
    approval: d.approval, valid_until: istInstant(d.validUntil),
  };
}

/** The condition part of the request: only the fields of the kind he chose. */
export function conditionRequest(d: TriggerDraft): Record<string, unknown> {
  const window = { window_start: d.windowStart || null, window_end: d.windowEnd || null };
  if (d.kind === "AREA") {
    return { kind: "AREA", timeframe_minutes: whole(d.timeframe, 1440), area_side: d.area?.side ?? null, area_low: d.area?.low ?? null,
      area_high: d.area?.high ?? null, area_timeframe: d.area?.timeframe ?? null, ...window };
  }
  if (d.kind === "STRUCTURE") return { kind: "STRUCTURE", timeframe_minutes: whole(d.timeframe, 1440), event: d.event, direction: d.direction, ...window };
  return { kind: "PRICE", level: positive(d.level), side: d.side, mode: d.mode, timeframe_minutes: d.mode === "CLOSE" ? whole(d.timeframe, 1440) : null, ...window };
}

/** The areas of the open chart's reading he can pick (one entry per area, never merged). */
export function areaChoices(areas: PlanArea[]): { value: string; label: string; area: PlanArea }[] {
  return areas.map((a, i) => ({ value: String(i), area: a,
    label: `${a.side === "buyer" ? "Buyer" : "Seller"} ${a.low_text} - ${a.high_text}${a.timeframe ? ` · ${a.timeframe}` : ""}` }));
}

/** The plan's condition in words for the list (the server's words; a W-8 plan without them: the level). */
export function conditionText(p: PlanView): string {
  if (p.condition_words) return p.condition_words;
  return `${p.underlying} ${p.condition.side === "ABOVE" ? "above" : "below"} ${p.condition.level}`;
}

/** The tone of a plan's state (colour by meaning; the word is always shown too). */
export function statusTone(s: PlanView["status"]): "up" | "down" | "warn" | "muted" {
  return s === "ARMED" || s === "DONE" ? "up" : s === "TRIGGERED" ? "warn" : s === "CANCELLED" || s === "EXPIRED" ? "muted" : "warn";
}
