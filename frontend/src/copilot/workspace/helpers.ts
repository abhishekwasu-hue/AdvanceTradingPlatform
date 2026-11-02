/**
 * M76 MP-8 (owner order M76 E-1; flag `copilot_workspace`): the trade plan's one-tap helpers - "Reverse", "Mirror this
 * spread", "Roll strikes" (one listed step up / down), "Roll expiry" (to the date he picks), "Add hedge" (his own
 * distance) and "Use my saved lots". Pure rules, no React, no network: each one only PREPARES the plan's legs; nothing is
 * sent - he reviews them and presses Place through the one ticket himself (ADR-0006).
 *
 * Nothing is filled from outside his settings or the object on screen (M68): a strike comes from his own legs, the
 * chart's spot and his strike step; a distance from his saved `options.hedge_offset` or what he types; lots from his own
 * legs or his saved `position_size.lots`. A leg whose contract changes (another strike, type or expiry) gets the builder's
 * model price at the IV the engine solved for the leg it came from, marked "model" until he types the real one (the
 * Options Builder's own rule, `repriceModelLegs`). A helper that lacks a value says which one, and does nothing.
 */
import { legId, type Leg } from "../../optionsBuilder/model";

export type HelperResult = { legs: Leg[]; note: string } | { missing: string };

/** The listed strike nearest the spot on his step; null while either is Not set. Pure. */
export function atmOf(spot: number | null, step: number | null): number | null {
  if (spot == null || step == null || !(spot > 0) || !(step > 0)) return null;
  return Math.round(spot / step) * step;
}

const round2 = (v: number) => Math.round(v * 100) / 100;

/** A leg for another contract, priced by the model at the IV solved for `from` (none solved yet: null). */
function moved(from: Leg, patch: Partial<Leg>, iv: number | null): Leg | null {
  if (iv == null || !(iv > 0)) return null;
  const next: Leg = { ...from, ...patch, id: legId(), iv, premium_source: "model" };
  delete next.priced_for;                     // a new contract: the model prices it on the next reply
  return next;
}

/** Every leg's side turned (same contracts, same prices, same lots). */
export function reverseLegs(legs: readonly Leg[]): HelperResult {
  if (!legs.length) return { missing: "a leg" };
  return { legs: legs.map((l) => ({ ...l, id: legId(), direction: l.direction === "BUY" ? "SELL" : "BUY" })), note: "Every leg's side is turned." };
}

/** The same structure on the other side of the money: CE <-> PE, each strike as far below the ATM strike as it was above. */
export function mirrorLegs(legs: readonly Leg[], atm: number | null, ivById: Readonly<Record<string, number>>): HelperResult {
  if (!legs.length) return { missing: "a leg" };
  if (legs.some((l) => l.option_type === "FUT")) return { missing: "option legs only (a future has no other side)" };
  if (atm == null) return { missing: "the spot and the strike step" };
  const out: Leg[] = [];
  for (const l of legs) {
    const strike = round2(2 * atm - l.strike);
    if (!(strike > 0)) return { missing: "a strike above zero on the other side" };
    const next = moved(l, { option_type: l.option_type === "CE" ? "PE" : "CE", strike }, ivById[l.id] ?? null);
    if (!next) return { missing: "the plan's numbers (wait for them)" };
    out.push(next);
  }
  return { legs: out, note: `Mirrored around ${atm}. Model prices until you type the real ones.` };
}

/** Every option leg one listed step up (+1) or down (-1). */
export function rollStrikes(legs: readonly Leg[], steps: 1 | -1, step: number | null, ivById: Readonly<Record<string, number>>): HelperResult {
  if (!legs.some((l) => l.option_type !== "FUT")) return { missing: "an option leg" };
  if (step == null || !(step > 0)) return { missing: "the strike step" };
  const out: Leg[] = [];
  for (const l of legs) {
    if (l.option_type === "FUT") { out.push(l); continue; }
    const strike = round2(l.strike + steps * step);
    if (!(strike > 0)) return { missing: "a strike above zero" };
    const next = moved(l, { strike }, ivById[l.id] ?? null);
    if (!next) return { missing: "the plan's numbers (wait for them)" };
    out.push(next);
  }
  return { legs: out, note: `Strikes ${steps > 0 ? "up" : "down"} one step. Model prices until you type the real ones.` };
}

/** Every option leg to the expiry he picked (a future keeps its own: its price for another expiry is not known here). */
export function rollExpiry(legs: readonly Leg[], expiry: string, ivById: Readonly<Record<string, number>>): HelperResult {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(expiry)) return { missing: "the new expiry" };
  if (!legs.some((l) => l.option_type !== "FUT")) return { missing: "an option leg" };
  if (legs.every((l) => l.option_type === "FUT" || l.expiry === expiry)) return { missing: "an expiry different from the plan's" };
  const out: Leg[] = [];
  for (const l of legs) {
    if (l.option_type === "FUT" || l.expiry === expiry) { out.push(l); continue; }
    const next = moved(l, { expiry }, ivById[l.id] ?? null);
    if (!next) return { missing: "the plan's numbers (wait for them)" };
    out.push(next);
  }
  return { legs: out, note: "Option legs rolled to the new expiry. Model prices until you type the real ones." };
}

/** A bought leg further out for every sold option: same type, expiry and lots, `strikesOut` listed steps away (CE above,
 *  PE below). The distance is his: saved in Trade settings, or typed here. */
export function addHedge(legs: readonly Leg[], strikesOut: number | null, step: number | null, ivById: Readonly<Record<string, number>>): HelperResult {
  const sold = legs.filter((l) => l.direction === "SELL" && l.option_type !== "FUT");
  if (!sold.length) return { missing: "a sold option leg" };
  if (strikesOut == null || !Number.isInteger(strikesOut) || strikesOut < 1) return { missing: "how many strikes further out" };
  if (step == null || !(step > 0)) return { missing: "the strike step" };
  const key = (l: Leg) => [l.direction, l.option_type, l.strike, l.expiry].join("|");      // the same contract and side
  const have = new Set(legs.map(key));
  const added: Leg[] = [];
  for (const l of sold) {
    const strike = round2(l.option_type === "CE" ? l.strike + strikesOut * step : l.strike - strikesOut * step);
    if (!(strike > 0)) return { missing: "a strike above zero" };
    const next = moved(l, { direction: "BUY", strike }, ivById[l.id] ?? null);
    if (!next) return { missing: "the plan's numbers (wait for them)" };
    if (!have.has(key(next))) added.push(next);
  }
  if (!added.length) return { missing: "a hedge not in the plan already" };
  return { legs: [...added, ...legs], note: `${added.length} bought leg${added.length === 1 ? "" : "s"} added. Model prices until you type the real ones.` };
}

/** The IV the engine solved for each leg of the last reply (by leg id), for the helpers' model prices. Pure. */
export function solvedIvs(sent: readonly Leg[], replyLegs: readonly { iv: number }[] | undefined): Record<string, number> {
  const out: Record<string, number> = {};
  if (!replyLegs || replyLegs.length !== sent.length) return out;
  sent.forEach((l, i) => { const iv = replyLegs[i]?.iv; if (typeof iv === "number" && Number.isFinite(iv) && iv > 0) out[l.id] = iv; });
  return out;
}

/** His saved values the helpers may use (only the fields he saved). Pure. */
export function savedHelpers(fields: { key: string; value: unknown; saved: boolean }[] | undefined): { hedgeOffset: number | null; lots: number | null } {
  const v = (k: string) => fields?.find((f) => f.key === k && f.saved)?.value;
  const whole = (x: unknown) => (typeof x === "number" && Number.isInteger(x) && x >= 1 ? x : null);
  return {
    hedgeOffset: whole(v("options.hedge_offset")),
    lots: v("position_size.mode") === "FIXED_LOTS" ? whole(v("position_size.lots")) : null,
  };
}
