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

export interface TriggerDraft {
  level: string; side: PlanSide | null; mode: PlanMode | null; timeframe: string; windowStart: string; windowEnd: string;
  validUntil: string;                    // datetime-local, IST as he typed it
  orderType: "MARKET" | "LIMIT" | null; product: "MIS" | "NRML" | null; approval: PlanApproval | null;
}
export const EMPTY_TRIGGER: TriggerDraft = {
  level: "", side: null, mode: null, timeframe: "", windowStart: "", windowEnd: "", validUntil: "", orderType: null, product: null, approval: null,
};

export interface PlanView {
  id: number; name: string | null; underlying: string; status: "ARMED" | "PAUSED" | "TRIGGERED" | "DONE" | "EXPIRED" | "CANCELLED";
  status_words: string; condition: { level: number; side: PlanSide; mode: PlanMode; timeframe_minutes: number | null; window_start: string | null; window_end: string | null };
  orders: { expiry: string; strike: number | null; right: string; side: string; lots: number }[]; approval: PlanApproval;
  valid_until: string; note: string | null; last_price: number | null;
}

/** Which of his trigger fields are still Not set (the form's order); empty = the plan can be saved. */
export function triggerMissing(d: TriggerDraft, legs: Leg[]): string[] {
  const out: string[] = [];
  if (!legs.length) out.push("A leg");
  if (positive(d.level) == null) out.push("Level");
  if (!d.side) out.push("Above or below");
  if (!d.mode) out.push("Touch or close");
  if (d.mode === "CLOSE" && whole(d.timeframe, 1440) == null) out.push("Timeframe");
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
    condition: { level: positive(d.level), side: d.side, mode: d.mode, timeframe_minutes: d.mode === "CLOSE" ? whole(d.timeframe, 1440) : null,
      window_start: d.windowStart || null, window_end: d.windowEnd || null },
    orders: legs.map((l) => ({ expiry: l.expiry, strike: l.option_type === "FUT" ? null : l.strike, right: l.option_type, side: l.direction, lots: l.lots,
      order_type: d.orderType, product: d.product, price: d.orderType === "LIMIT" ? l.premium : null,
      stop_loss: single ? level("stop") : null, target: single ? level("target") : null })),
    approval: d.approval, valid_until: istInstant(d.validUntil),
  };
}

/** The tone of a plan's state (colour by meaning; the word is always shown too). */
export function statusTone(s: PlanView["status"]): "up" | "down" | "warn" | "muted" {
  return s === "ARMED" || s === "DONE" ? "up" : s === "TRIGGERED" ? "warn" : s === "CANCELLED" || s === "EXPIRED" ? "muted" : "warn";
}
