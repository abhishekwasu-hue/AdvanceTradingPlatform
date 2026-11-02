/**
 * M75 W-6 (owner order M75 CW-4): the trade plan panel's data - what he typed, and the pure rules that turn it into the
 * Options Builder's legs and into plain exit lines. No React, no network: every rule here is unit-tested.
 *
 * M68: every field starts Not set ("" or null) and stays so until he fills it - no side, type, strike, lots, price or
 * exit is ever chosen here. A leg reaches the builder's engine only once every one of its own fields is filled in.
 */
import { formatNumber } from "../../ui/format";
import { legId, money, type Direction, type Leg, type OptionKind } from "../../optionsBuilder/model";

export type PlanKind = "OPTIONS" | "FUTURE" | "EQUITY";
export const PLAN_KINDS: { value: PlanKind; label: string }[] = [
  { value: "OPTIONS", label: "Option strategy" }, { value: "FUTURE", label: "Future" }, { value: "EQUITY", label: "Equity" },
];

/** The instrument row of an option or future plan (the exchange's numbers, typed by him). */
export interface InstrumentDraft { spot: string; lotSize: string; step: string }
/** A new leg as typed, before it is added (every field Not set). */
export interface LegDraft { direction: Direction | null; optionType: OptionKind | null; strike: string; expiry: string; lots: string; premium: string }
/** An equity plan as typed. */
export interface EquityDraft { direction: Direction | null; quantity: string; price: string }

/** The M68 Exits fields (Settings › Trade settings › Exits), here for this one plan. */
export type ExitMeasure = "POINTS" | "PCT" | "RUPEES";
export type TrailKind = "POINTS" | "PCT" | "ATR";
export type CombinedMeasure = "MTM_INR" | "NET_PREMIUM" | "SPOT";
export interface ExitDraft {
  stopKind: ExitMeasure | null; stop: string;
  targetKind: ExitMeasure | null; target: string;
  trailKind: TrailKind | null; trail: string;
  timeMinutes: string; timeAt: string;
  combined: CombinedMeasure | null;
}

/** The choices with the registry's own words (backend app/me/trade_settings.py EXIT_MEASURE, trailing_kind, combined_measure). */
export const EXIT_MEASURES: { value: ExitMeasure; label: string }[] = [
  { value: "POINTS", label: "Points from entry" }, { value: "PCT", label: "% from entry" }, { value: "RUPEES", label: "Rupees on the trade" },
];
export const TRAIL_KINDS: { value: TrailKind; label: string }[] = [
  { value: "POINTS", label: "Points" }, { value: "PCT", label: "%" }, { value: "ATR", label: "ATR multiple" },
];
export const COMBINED_MEASURES: { value: CombinedMeasure; label: string }[] = [
  { value: "MTM_INR", label: "Profit or loss in rupees" }, { value: "NET_PREMIUM", label: "Net premium" }, { value: "SPOT", label: "The index or stock price" },
];

export const EMPTY_INSTRUMENT: InstrumentDraft = { spot: "", lotSize: "", step: "" };
export const EMPTY_LEG: LegDraft = { direction: null, optionType: null, strike: "", expiry: "", lots: "", premium: "" };
export const EMPTY_EQUITY: EquityDraft = { direction: null, quantity: "", price: "" };
export const EMPTY_EXITS: ExitDraft = { stopKind: null, stop: "", targetKind: null, target: "", trailKind: null, trail: "", timeMinutes: "", timeAt: "", combined: null };

/** A positive number as typed, else null (blank stays blank - nothing is assumed). */
export function positive(text: string): number | null {
  const t = text.replace(/,/g, "").trim();
  const n = Number(t);
  return t !== "" && Number.isFinite(n) && n > 0 ? n : null;
}

/** A whole number from 1 to `max` as typed, else null. */
export function whole(text: string, max = 100_000): number | null {
  const n = positive(text);
  return n != null && Number.isInteger(n) && n <= max ? n : null;
}

/** Which of a new leg's fields are still Not set (in the form's order); empty = the leg can be added. */
export function legMissing(d: LegDraft, inst: InstrumentDraft, kind: PlanKind): string[] {
  const out: string[] = [];
  if (whole(inst.lotSize) == null) out.push("Lot size");
  if (!d.direction) out.push("Side");
  const type = kind === "FUTURE" ? "FUT" : d.optionType;
  if (!type) out.push("Type");
  if (type !== "FUT" && positive(d.strike) == null) out.push("Strike");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(d.expiry)) out.push("Expiry");
  if (whole(d.lots, 1000) == null) out.push("Lots");
  if (positive(d.premium) == null) out.push(type === "FUT" ? "Price" : "Premium");
  return out;
}

/**
 * The builder's leg for a complete draft, else null. A future's strike is not used by the engine (its premium is its
 * entry price, options_builder/payoff.py), so the entry price is sent there only because the wire needs a number.
 */
export function legOf(d: LegDraft, inst: InstrumentDraft, kind: PlanKind): Leg | null {
  if (legMissing(d, inst, kind).length) return null;
  const type = (kind === "FUTURE" ? "FUT" : d.optionType) as OptionKind;
  const premium = positive(d.premium) as number;
  return {
    id: legId(), direction: d.direction as Direction, option_type: type, strike: type === "FUT" ? premium : (positive(d.strike) as number),
    premium, lots: whole(d.lots, 1000) as number, lot_size: whole(inst.lotSize) as number, expiry: d.expiry, iv: null, premium_source: "manual",
  };
}

/** The one entry an exit is measured from: a single leg (its premium x its units) or an equity plan; null for a structure. */
export interface SingleEntry { direction: Direction; price: number; units: number }

export function singleEntry(kind: PlanKind | null, legs: Leg[], equity: EquityDraft): SingleEntry | null {
  if (kind === "EQUITY") {
    const price = positive(equity.price);
    const units = whole(equity.quantity, 10_000_000);
    return equity.direction && price != null && units != null ? { direction: equity.direction, price, units } : null;
  }
  if (legs.length !== 1) return null;
  const l = legs[0];
  return { direction: l.direction, price: l.premium, units: l.lots * l.lot_size };
}

/** A stop (loss side) or a target (profit side) as a price level and the rupees on the trade, from his own numbers. */
export function exitLevel(entry: SingleEntry, measure: ExitMeasure, value: number, which: "stop" | "target"): { level: number; rupees: number } | null {
  if (!(value > 0) || !(entry.units > 0)) return null;
  const perUnit = measure === "POINTS" ? value : measure === "PCT" ? (entry.price * value) / 100 : value / entry.units;
  // A bought position loses below its entry; a sold one above it. The target is on the other side.
  const down = (entry.direction === "BUY") === (which === "stop");
  const level = down ? entry.price - perUnit : entry.price + perUnit;
  if (!(level > 0)) return null;
  return { level: Math.round(level * 100) / 100, rupees: Math.round(perUnit * entry.units * 100) / 100 };
}

/** The plain lines of the exits he set (nothing for a field he left Not set). */
export function exitLines(x: ExitDraft, entry: SingleEntry | null): { key: string; label: string; text: string }[] {
  const out: { key: string; label: string; text: string }[] = [];
  const word = (m: ExitMeasure, v: number) => (m === "POINTS" ? `${formatNumber(v, 2)} points` : m === "PCT" ? `${formatNumber(v, 2)} %` : money(v));
  for (const [key, label, kind, text] of [["stop", "Stop-loss", x.stopKind, x.stop], ["target", "Target", x.targetKind, x.target]] as const) {
    const v = positive(text);
    if (!kind || v == null) continue;
    const at = entry ? exitLevel(entry, kind, v, key) : null;
    out.push({ key, label, text: at ? `${word(kind, v)} · at ${formatNumber(at.level, 2)} · ${money(key === "stop" ? -at.rupees : at.rupees)}` : word(kind, v) });
  }
  const trail = positive(x.trail);
  if (x.trailKind && trail != null) {
    out.push({ key: "trail", label: "Trailing stop", text: x.trailKind === "ATR" ? `${formatNumber(trail, 2)} x ATR` : x.trailKind === "PCT" ? `${formatNumber(trail, 2)} %` : `${formatNumber(trail, 2)} points` });
  }
  const minutes = whole(x.timeMinutes, 100_000);
  if (minutes != null) out.push({ key: "time", label: "Close after", text: `${minutes} minutes` });
  if (/^\d{2}:\d{2}$/.test(x.timeAt)) out.push({ key: "timeAt", label: "Close at a time", text: x.timeAt });
  if (x.combined) out.push({ key: "combined", label: "Combined exit measured on", text: COMBINED_MEASURES.find((c) => c.value === x.combined)?.label ?? "" });
  return out;
}

/** What a field reads while it is empty. */
export const NOT_SET_TEXT = "Not set";

/** His saved Exits from Trade settings (only the fields he saved), for "Use my saved exits" - his own tap, never by itself. */
export function savedExits(fields: { key: string; value: unknown; saved: boolean }[]): Partial<ExitDraft> {
  const v = (k: string) => fields.find((f) => f.key === `exits.${k}` && f.saved)?.value;
  const num = (x: unknown) => (typeof x === "number" && Number.isFinite(x) && x > 0 ? String(x) : undefined);
  const of = <T extends string>(x: unknown, allowed: readonly { value: T }[]) => allowed.find((c) => c.value === x)?.value;
  const out: Partial<ExitDraft> = {};
  const put = <K extends keyof ExitDraft>(k: K, value: ExitDraft[K] | undefined) => { if (value !== undefined) out[k] = value; };
  put("stopKind", of(v("stop_loss_kind"), EXIT_MEASURES));
  put("stop", num(v("stop_loss_value")));
  put("targetKind", of(v("target_kind"), EXIT_MEASURES));
  put("target", num(v("target_value")));
  put("trailKind", of(v("trailing_kind"), TRAIL_KINDS));
  put("trail", num(v("trailing_value")));
  put("timeMinutes", num(v("time_exit_minutes")));
  const at = v("time_exit_at");
  put("timeAt", typeof at === "string" && /^\d{2}:\d{2}/.test(at) ? at.slice(0, 5) : undefined);
  put("combined", of(v("combined_measure"), COMBINED_MEASURES));
  return out;
}

/**
 * The margin line of an equity plan: always a labelled estimate. Delivery holds the whole value; anything else only the
 * broker can say (ATP never invents a leverage).
 */
export function equityValue(entry: SingleEntry | null): number | null {
  return entry ? Math.round(entry.price * entry.units * 100) / 100 : null;
}
