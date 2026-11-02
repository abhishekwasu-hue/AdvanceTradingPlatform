/**
 * M75 W-7 (owner order M75 CW-5): what the trade plan hands to the EXISTING ticket, basket and equity ticket - pure, no
 * network. Only his own numbers from the plan go across (marked "from your trade plan"); a field he left Not set stays
 * empty there and that ticket asks for it. Nothing here places anything.
 */
import type { TicketForm } from "../../components/paperTicket";
import type { NewLeg } from "../../fno/basket";
import type { ContractRight, OrderResult, Side } from "../../fno/api";
import type { Leg } from "../../optionsBuilder/model";
import type { TicketPrefill } from "../ticket/intent";
import { exitLevel, positive, singleEntry, type EquityDraft, type ExitDraft, type PlanKind } from "./plan";

/** The plan as it stands in the panel. */
export interface PlanState { underlying: string; kind: PlanKind | null; legs: Leg[]; equity: EquityDraft; exits: ExitDraft }

const round2 = (n: number) => Math.round(n * 100) / 100;

/** The stop-loss level of a single entry from his own stop (null when he set none). */
function stopLevel(plan: PlanState, entryPrice?: number): number | null {
  const entry = singleEntry(plan.kind, plan.legs, plan.equity);
  const v = positive(plan.exits.stop);
  if (!entry || !plan.exits.stopKind || v == null) return null;
  return exitLevel({ ...entry, price: entryPrice ?? entry.price }, plan.exits.stopKind, v, "stop")?.level ?? null;
}

/** One option or future leg: the ONE ticket's contract, side and prefill. */
export function singleLegTicket(plan: PlanState): { underlying: string; expiry: string; strike: number | null; right: ContractRight; side: Side; prefill: TicketPrefill } | null {
  if ((plan.kind !== "OPTIONS" && plan.kind !== "FUTURE") || plan.legs.length !== 1) return null;
  const l = plan.legs[0];
  const stop = stopLevel(plan);
  const prefill: TicketPrefill = { lots: l.lots, price: String(l.premium), from: { lots: "plan", price: "plan" } };
  if (stop != null) { prefill.stopLoss = String(stop); prefill.from.stopLoss = "plan"; }
  return { underlying: plan.underlying, expiry: l.expiry, strike: l.option_type === "FUT" ? null : l.strike, right: l.option_type, side: l.direction, prefill };
}

/** Several legs: the basket's legs (hedge order kept; the basket asks a sold leg's stop itself). */
export function basketLegsOf(plan: PlanState): NewLeg[] | null {
  if ((plan.kind !== "OPTIONS" && plan.kind !== "FUTURE") || plan.legs.length < 2) return null;
  return plan.legs.map((l) => ({ underlying: plan.underlying, expiry: l.expiry, strike: l.option_type === "FUT" ? 0 : l.strike, right: l.option_type,
    side: l.direction, lots: l.lots, price: l.premium, label: null, stopLoss: null, iv: null }));
}

/** Equity: the equity ticket's form from the plan (its own stop and target come from his exits, else empty). */
export function equityTicketOf(plan: PlanState): Partial<TicketForm> | null {
  if (plan.kind !== "EQUITY") return null;
  const entry = singleEntry("EQUITY", [], plan.equity);
  if (!entry) return null;
  const target = positive(plan.exits.target);
  const tgt = plan.exits.targetKind && target != null ? exitLevel(entry, plan.exits.targetKind, target, "target")?.level ?? null : null;
  const stop = stopLevel(plan);
  return { symbol: plan.underlying, direction: entry.direction === "BUY" ? "LONG" : "SHORT", entry: String(entry.price),
    quantity: String(entry.units), stop_loss: stop != null ? String(stop) : "", target: tgt != null ? String(tgt) : "" };
}

/** After a fill of a single leg: his target as a level from the FILL price (the real entry), else null. */
export function targetArm(plan: PlanState, result: OrderResult): { level: number; rupees: number } | null {
  if (result.status !== "PLACED" || !result.trade_id || !result.filled) return null;
  const entry = singleEntry(plan.kind, plan.legs, plan.equity);
  const v = positive(plan.exits.target);
  if (plan.kind === "EQUITY" || !entry || !plan.exits.targetKind || v == null) return null;
  const at = exitLevel({ ...entry, price: result.filled.price }, plan.exits.targetKind, v, "target");
  return at ? { level: round2(at.level), rupees: at.rupees } : null;
}
