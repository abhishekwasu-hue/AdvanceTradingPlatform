/**
 * M68 PR-4 (TS1-035 to 040, 042): the trader's own Options settings for the bot form and the backtest form, and the one
 * check of what an option / future choice still needs. ATP pre-selects no expiry, strike, distance, side, width, ratio or
 * custom leg: a form starts "Not set"; his own Settings › Trade settings › Options pre-fill it (marked "From your
 * defaults"); a field still Not set is asked for at the field ("Needs your setting: ...") before anything is sent.
 * A structure's own definition is not a choice (a straddle sells at the money, a credit structure writes).
 */
import type { TradeSettingsView } from "./tradeSettings";
import type { CustomLeg, ExpiryRule, InstrumentKind, OptionPosition, OptionStrategy, StrikeRule } from "../types";
import { WIDTH_STRUCTURES } from "../types";

export const OPTIONS_WHERE = "Settings › Trade settings › Options";
export const NEEDS = "Needs your setting: ";
/** The structures whose main strike follows the form's own strike rule (the others name theirs). Kept equal to the API's
 * RULE_STRIKE_STRUCTURES (backend app/instruments/contracts.py). */
export const RULE_STRIKE_STRUCTURES: OptionStrategy[] = ["SINGLE", "BULL_PUT_SPREAD", "BEAR_CALL_SPREAD", "CALL_RATIO_SPREAD", "PUT_RATIO_SPREAD"];
export const RATIO_STRUCTURES: OptionStrategy[] = ["CALL_RATIO_SPREAD", "PUT_RATIO_SPREAD"];
/** A condor / strangle sells out of the money by its definition; how many strikes out is his own (the API's OTM_OFFSET_STRUCTURES). */
export const OTM_OFFSET_STRUCTURES: OptionStrategy[] = ["IRON_CONDOR", "SHORT_STRANGLE", "LONG_STRANGLE"];

/** A custom leg while it is being drawn: nothing in it is chosen for the trader. */
export interface LegDraft {
  right: CustomLeg["right"] | "";
  role: CustomLeg["role"] | "";
  strike_rule: StrikeRule | "";
  strike_offset: number;
  ratio: number;
}
export const EMPTY_LEG: LegDraft = { right: "", role: "", strike_rule: "", strike_offset: 0, ratio: 1 };

export interface OptionDefaults {
  expiryRule?: ExpiryRule;
  strikeRule?: StrikeRule;
  strikeOffset?: number;
  position?: OptionPosition;
  spreadWidth?: number;
  ratio?: number;
  customLegs?: LegDraft[];
}

const KEY = (name: string) => `options.${name}`;

/** His own saved Options (only the ones he saved; nothing is ever filled in by ATP). */
export function optionDefaultsOf(view: TradeSettingsView | null | undefined): OptionDefaults {
  const out: OptionDefaults = {};
  const v = (name: string) => view?.sections.flatMap((s) => s.fields).find((f) => f.key === KEY(name) && f.saved)?.value;
  const whole = (x: unknown, low: number) => (typeof x === "number" && Number.isInteger(x) && x >= low ? x : undefined);
  const expiry = v("expiry_rule");
  if (expiry === "NEAREST" || expiry === "NEXT" || expiry === "MONTHLY") out.expiryRule = expiry;
  const rule = v("strike_rule");
  if (rule === "ATM" || rule === "ITM" || rule === "OTM") out.strikeRule = rule;
  const side = v("option_position");
  if (side === "BUY" || side === "WRITE") out.position = side;
  const offset = whole(v("strike_offset"), 0);
  if (offset !== undefined) out.strikeOffset = offset;
  const width = whole(v("spread_width"), 1);
  if (width !== undefined) out.spreadWidth = width;
  const ratio = whole(v("ratio"), 1);
  if (ratio !== undefined) out.ratio = ratio;
  const legs = v("custom_legs");
  if (Array.isArray(legs) && legs.length) out.customLegs = legs.flatMap((l) => (legOfSaved(l) ? [legOfSaved(l)!] : []));
  return out;
}

/** A leg as Trade settings stores it ({right, side, strike_rule, offset, ratio}) as the form draws it. */
function legOfSaved(raw: unknown): LegDraft | null {
  if (!raw || typeof raw !== "object") return null;
  const l = raw as Record<string, unknown>;
  if ((l.right !== "CE" && l.right !== "PE") || (l.side !== "BUY" && l.side !== "SELL")) return null;
  if (l.strike_rule !== "ATM" && l.strike_rule !== "ITM" && l.strike_rule !== "OTM") return null;
  const offset = typeof l.offset === "number" ? l.offset : 0;
  const ratio = typeof l.ratio === "number" ? l.ratio : 1;
  return { right: l.right, role: l.side === "SELL" ? "SHORT" : "LONG", strike_rule: l.strike_rule, strike_offset: offset, ratio };
}

/** The form's legs as Trade settings stores them (for "Save as my default"); null while a leg is not complete. */
export function savedLegsOf(legs: readonly LegDraft[]): Array<Record<string, unknown>> | null {
  const done = completeLegs(legs);
  return done && done.length ? done.map((l) => ({ right: l.right, side: l.role === "SHORT" ? "SELL" : "BUY", strike_rule: l.strike_rule,
    offset: l.strike_rule === "ATM" ? 0 : l.strike_offset, ratio: l.ratio })) : null;
}

/** The legs, once every leg names its side, right and strike (and a distance away from ATM); else null. */
export function completeLegs(legs: readonly LegDraft[]): CustomLeg[] | null {
  const out: CustomLeg[] = [];
  for (const l of legs) {
    if (!l.right || !l.role || !l.strike_rule || (l.strike_rule !== "ATM" && l.strike_offset < 1) || l.ratio < 1) return null;
    out.push({ right: l.right, role: l.role, strike_rule: l.strike_rule, strike_offset: l.strike_rule === "ATM" ? 0 : l.strike_offset, ratio: l.ratio });
  }
  return out;
}

export interface OptionChoice {
  kind: InstrumentKind | "";
  structure: OptionStrategy;
  position: OptionPosition | "";
  expiryRule: ExpiryRule | "";
  strikeRule: StrikeRule | "";
  strikeOffset: number;
  spreadWidth: number;
  ratio: number;
  legs: readonly LegDraft[];
}

/** The first choice the form still needs, in plain words, with the id of the field to focus; null when nothing is missing. */
export function optionsMissing(c: OptionChoice, ids: { [K in "kind" | "position" | "expiry" | "strike" | "offset" | "width" | "ratio" | "legs"]: string }):
  { text: string; field: string } | null {
  const need = (label: string, field: string) => ({ text: `${NEEDS}${label} (${OPTIONS_WHERE}, or choose it here).`, field });
  if (!c.kind) return { text: "Choose what to trade (Trade as).", field: ids.kind };
  if (c.kind === "UNDERLYING") return null;
  if (!c.expiryRule) return need("Expiry", ids.expiry);
  if (c.kind === "FUTURE") return null;
  if (c.structure === "SINGLE" && !c.position) return need("Option side", ids.position);
  if (RULE_STRIKE_STRUCTURES.includes(c.structure)) {
    if (!c.strikeRule) return need("Strike", ids.strike);
    if (c.strikeRule !== "ATM" && c.strikeOffset < 1) return need("Strikes away from ATM", ids.offset);
  }
  if (OTM_OFFSET_STRUCTURES.includes(c.structure) && c.strikeOffset < 1) return need("Strikes away from ATM", ids.offset);
  if (WIDTH_STRUCTURES.includes(c.structure) && c.spreadWidth < 1) return need("Spread width", ids.width);
  if (RATIO_STRUCTURES.includes(c.structure) && c.ratio < 1) return need("Ratio spread", ids.ratio);
  if (c.structure === "CUSTOM") {
    if (c.legs.length < 2) return { text: "Add at least two legs to the custom structure.", field: ids.legs };
    if (!completeLegs(c.legs)) return { text: "Choose the side, CE / PE and strike of every leg (and how far from ATM).", field: ids.legs };
  }
  return null;
}

/** A one-tap bot request with his own saved Options filled where it names none (side of a single option, expiry, strike);
 * a request that already names one keeps it. Nothing of ATP's own is ever added. */
export function withOwnOptions<T extends { instrument_kind?: InstrumentKind; option_strategy?: OptionStrategy; option_position?: OptionPosition | null;
  expiry_rule?: ExpiryRule | null; strike_rule?: StrikeRule | null; strike_offset?: number }>(req: T, own: OptionDefaults): T {
  if (req.instrument_kind !== "OPTION" && req.instrument_kind !== "FUTURE") return req;
  const out: T = { ...req };
  if (!out.expiry_rule && own.expiryRule) out.expiry_rule = own.expiryRule;
  if (req.instrument_kind === "OPTION" && (out.option_strategy ?? "SINGLE") === "SINGLE") {
    if (!out.option_position && own.position) out.option_position = own.position;
    if (!out.strike_rule && own.strikeRule) {
      out.strike_rule = own.strikeRule;
      out.strike_offset = own.strikeRule === "ATM" ? 0 : own.strikeOffset ?? 0;
    }
  }
  return out;
}
