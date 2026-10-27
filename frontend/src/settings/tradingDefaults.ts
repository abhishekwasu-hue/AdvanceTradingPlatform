/**
 * Settings redesign part C: "My trading defaults" - a PRE-FILL, never a hidden rule (docs/design/TRADING_DEFAULTS.md).
 *
 * The user's own starting values live on the server per user (`/api/me/prefs`, namespace `trading_defaults`, backend
 * app/me/prefs.py TradingDefaultsPrefs: every field empty by default). The server never reads them to trade. Only the
 * frontend form builders read them - the Paper bot's "Start on paper", the full deployment form ("More ways to trade")
 * and the chain order ticket - and each value they take is shown "from your defaults" and can be changed there.
 *
 * Rules kept here (pure functions, unit-tested):
 * - Only existing deployment / ExitRules / ticket fields (review R7). Never the mode, broker routing, the LIVE order style
 *   or any LIVE field: the types below have no such keys, so nothing can pre-fill them.
 * - Empty defaults change nothing: every builder returns today's object itself (byte-identical request bodies).
 * - A value is used only where the API accepts it (an option rule on an option, a structure's credit % on a structure,
 *   approval only while the `approval_only_entries` flag allows it); anything else is left out and not claimed.
 * - The time exits are available, empty unless the user sets them, and never suggested (SR-10).
 */
import { useEffect, useState } from "react";
import { request } from "../api/client";
import {
  DEBIT_STRUCTURES, PAYOFF_STRUCTURES, WIDTH_STRUCTURES, WINGED_STRUCTURES,
  type DeploymentCreateRequest, type ExitRules, type ExpiryRule, type InstrumentKind, type OptionPosition, type OptionStrategy,
  type StrikeRule,
} from "../types";

export const DEFAULTS_PATH = "/settings/trading-defaults";
export const RISK_LIMITS_PATH = "/risk-management";
export const NAMESPACE = "trading_defaults";
export const FROM_DEFAULTS = "from your defaults";

export type TicketProduct = "MIS" | "NRML";
export type TicketOrderType = "MARKET" | "LIMIT" | "SL" | "SL-M";
export type EntryMode = "AUTO" | "APPROVAL";
/** Every structure the deployment API takes except CUSTOM (its legs are drawn per deployment). */
export type DefaultStructure = Exclude<OptionStrategy, "CUSTOM">;

export interface ExitDefaults {
  trailing_stop_pct: number | null;
  break_even_at_r: number | null;
  time_exit_minutes: number | null;
  time_exit_at: string | null;
}

export interface TicketDefaults {
  lots: number | null;
  product: TicketProduct | null;
  order_type: TicketOrderType | null;
}

/** The namespace as the server stores it (app/me/prefs.py TradingDefaultsPrefs). */
export interface TradingDefaults {
  instrument_kind: InstrumentKind | null;
  option_position: OptionPosition | null;
  expiry_rule: ExpiryRule | null;
  strike_rule: StrikeRule | null;
  strike_offset: number | null;
  premium_stop_pct: number | null;
  max_lots: number | null;
  option_strategy: DefaultStructure | null;
  spread_width: number | null;
  target_credit_pct: number | null;
  stop_credit_pct: number | null;
  entry_mode: EntryMode | null;
  exit_rules: ExitDefaults | null;
  ticket: TicketDefaults | null;
  /** P1 (27 Oct): on an expiry day the chain and the builder open the next expiry. null = ON (the default); false = off. */
  expiry_day_next: boolean | null;
}

export const EMPTY_EXITS: ExitDefaults = { trailing_stop_pct: null, break_even_at_r: null, time_exit_minutes: null, time_exit_at: null };
export const EMPTY_TICKET: TicketDefaults = { lots: null, product: null, order_type: null };
export const EMPTY_DEFAULTS: TradingDefaults = {
  instrument_kind: null, option_position: null, expiry_rule: null, strike_rule: null, strike_offset: null, premium_stop_pct: null,
  max_lots: null, option_strategy: null, spread_width: null, target_credit_pct: null, stop_credit_pct: null, entry_mode: null,
  exit_rules: null, ticket: null, expiry_day_next: null,
};

/** The structures, in the full form's order, with plain names. */
export const STRUCTURES: { value: DefaultStructure; label: string }[] = [
  { value: "SINGLE", label: "Single option" },
  { value: "BULL_PUT_SPREAD", label: "Bull put spread" },
  { value: "BEAR_CALL_SPREAD", label: "Bear call spread" },
  { value: "IRON_CONDOR", label: "Iron condor" },
  { value: "IRON_BUTTERFLY", label: "Iron butterfly" },
  { value: "SHORT_STRADDLE", label: "Short straddle" },
  { value: "SHORT_STRANGLE", label: "Short strangle" },
  { value: "LONG_STRADDLE", label: "Long straddle" },
  { value: "LONG_STRANGLE", label: "Long strangle" },
  { value: "CALENDAR_SPREAD", label: "Calendar spread" },
  { value: "CALL_RATIO_SPREAD", label: "Call ratio spread 1:2" },
  { value: "PUT_RATIO_SPREAD", label: "Put ratio spread 1:2" },
  { value: "LONG_BUTTERFLY", label: "Long butterfly 1:2:1" },
];
export const KINDS: { value: InstrumentKind; label: string }[] = [
  { value: "OPTION", label: "Options" }, { value: "FUTURE", label: "Futures" }, { value: "UNDERLYING", label: "The symbol itself" },
];
export const EXPIRIES: { value: ExpiryRule; label: string }[] = [
  { value: "NEAREST", label: "Nearest" }, { value: "NEXT", label: "Next" }, { value: "MONTHLY", label: "Monthly" },
];
export const STRIKES: { value: StrikeRule; label: string }[] = [
  { value: "ATM", label: "At the money" }, { value: "ITM", label: "In the money" }, { value: "OTM", label: "Out of the money" },
];
export const POSITIONS: { value: OptionPosition; label: string }[] = [
  { value: "BUY", label: "Buy the option" }, { value: "WRITE", label: "Write the option" },
];
export const TICKET_ORDER_TYPES: { value: TicketOrderType; label: string }[] = [
  { value: "MARKET", label: "Market" }, { value: "LIMIT", label: "Limit" }, { value: "SL", label: "SL" }, { value: "SL-M", label: "SL-M" },
];
export const TICKET_PRODUCTS: { value: TicketProduct; label: string }[] = [
  { value: "MIS", label: "MIS (intraday)" }, { value: "NRML", label: "NRML (carry forward)" },
];

const oneOf = <T extends string>(v: unknown, allowed: readonly { value: T }[]): T | null =>
  typeof v === "string" && allowed.some((a) => a.value === v) ? (v as T) : null;
const numIn = (v: unknown, lo: number, hi: number, { int = false, above = false } = {}): number | null => {
  if (typeof v !== "number" || !Number.isFinite(v)) return null;
  if (int && !Number.isInteger(v)) return null;
  if (above ? v <= lo : v < lo) return null;
  return v <= hi ? v : null;
};
const TIME = /^([01]\d|2[0-3]):[0-5]\d$/;

/** What the server returned, read defensively: an unknown key or a value out of range is dropped (taken as "not set"). */
export function readDefaults(raw: unknown): TradingDefaults {
  const r = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const e = (r.exit_rules && typeof r.exit_rules === "object" ? r.exit_rules : null) as Record<string, unknown> | null;
  const t = (r.ticket && typeof r.ticket === "object" ? r.ticket : null) as Record<string, unknown> | null;
  const exits: ExitDefaults | null = e ? {
    trailing_stop_pct: numIn(e.trailing_stop_pct, 0, 50, { above: true }),
    break_even_at_r: numIn(e.break_even_at_r, 0, 10, { above: true }),
    time_exit_minutes: numIn(e.time_exit_minutes, 1, 375, { int: true }),
    time_exit_at: typeof e.time_exit_at === "string" && TIME.test(e.time_exit_at) ? e.time_exit_at : null,
  } : null;
  const ticket: TicketDefaults | null = t ? {
    lots: numIn(t.lots, 1, 500, { int: true }),
    product: oneOf(t.product, TICKET_PRODUCTS),
    order_type: oneOf(t.order_type, TICKET_ORDER_TYPES),
  } : null;
  return {
    instrument_kind: oneOf(r.instrument_kind, KINDS),
    option_position: oneOf(r.option_position, POSITIONS),
    expiry_rule: oneOf(r.expiry_rule, EXPIRIES),
    strike_rule: oneOf(r.strike_rule, STRIKES),
    strike_offset: numIn(r.strike_offset, 0, 10, { int: true }),
    premium_stop_pct: numIn(r.premium_stop_pct, 5, 95),
    max_lots: numIn(r.max_lots, 1, 500, { int: true }),
    option_strategy: oneOf(r.option_strategy, STRUCTURES),
    spread_width: numIn(r.spread_width, 1, 20, { int: true }),
    target_credit_pct: numIn(r.target_credit_pct, 5, 95),
    stop_credit_pct: numIn(r.stop_credit_pct, 10, 500),
    entry_mode: oneOf(r.entry_mode, [{ value: "AUTO" }, { value: "APPROVAL" }] as const),
    exit_rules: exits && Object.values(exits).some((v) => v !== null) ? exits : null,
    ticket: ticket && Object.values(ticket).some((v) => v !== null) ? ticket : null,
    expiry_day_next: r.expiry_day_next === false ? false : null,
  };
}

export function hasAnyDefault(d: TradingDefaults | null | undefined): boolean {
  if (!d) return false;
  return Object.entries(d).some(([k, v]) => (k === "exit_rules" || k === "ticket")
    ? !!v && Object.values(v as object).some((x) => x !== null) : v !== null);
}

const isStructure = (s: OptionStrategy | null | undefined): s is DefaultStructure => !!s && s !== "SINGLE";

/**
 * What the page saves: only the values that apply to what is chosen (an option's rules only with options or with no
 * kind chosen, a single option's premium stop only without a structure, ...), so nothing hidden is ever stored.
 */
export function cleanForSave(d: TradingDefaults): TradingDefaults {
  const out: TradingDefaults = { ...EMPTY_DEFAULTS, entry_mode: d.entry_mode, max_lots: d.instrument_kind === "UNDERLYING" ? null : d.max_lots,
    expiry_day_next: d.expiry_day_next === false ? false : null };
  const kind = d.instrument_kind;
  out.instrument_kind = kind;
  if (kind !== "UNDERLYING") out.expiry_rule = d.expiry_rule;
  if (kind === "OPTION" || kind === null) {
    out.option_strategy = d.option_strategy;
    out.strike_rule = d.strike_rule;
    out.strike_offset = d.strike_rule === "ITM" || d.strike_rule === "OTM" ? d.strike_offset : null;
    if (isStructure(d.option_strategy)) {
      out.spread_width = WIDTH_STRUCTURES.includes(d.option_strategy) ? d.spread_width : null;
      out.target_credit_pct = d.target_credit_pct;
      out.stop_credit_pct = DEBIT_STRUCTURES.includes(d.option_strategy) && (d.stop_credit_pct ?? 0) > 100 ? null : d.stop_credit_pct;
    } else {
      out.option_position = d.option_position;
      out.premium_stop_pct = d.premium_stop_pct;
    }
  }
  const e = d.exit_rules;
  out.exit_rules = e && Object.values(e).some((v) => v !== null) ? { ...EMPTY_EXITS, ...e } : null;
  const t = d.ticket;
  out.ticket = t && Object.values(t).some((v) => v !== null) ? { ...EMPTY_TICKET, ...t } : null;
  return out;
}

// ---------------------------------------------------------------- the words of a pre-filled value

const EXPIRY_WORD: Record<ExpiryRule, string> = { NEAREST: "nearest", NEXT: "next", MONTHLY: "monthly" };
const lotsWord = (n: number) => `${n} lot${n === 1 ? "" : "s"}`;
export const structureLabel = (s: OptionStrategy) => STRUCTURES.find((x) => x.value === s)?.label ?? "Custom legs";
const strikeWord = (rule: StrikeRule, offset: number) => rule === "ATM" ? "at the money strike"
  : `strike ${offset} step${offset === 1 ? "" : "s"} ${rule === "ITM" ? "in" : "out of"} the money`;

/** The exit rules a form takes from the defaults (only the ones set), or null. */
export function exitsFrom(d: TradingDefaults | null | undefined): ExitRules | null {
  const e = d?.exit_rules;
  if (!e) return null;
  const r: ExitRules = {};
  if (e.trailing_stop_pct !== null) r.trailing_stop_pct = e.trailing_stop_pct;
  if (e.break_even_at_r !== null) r.break_even_at_r = e.break_even_at_r;
  if (e.time_exit_minutes !== null) r.time_exit_minutes = e.time_exit_minutes;
  if (e.time_exit_at !== null) r.time_exit_at = e.time_exit_at;
  return Object.keys(r).length ? r : null;
}

function exitWords(r: ExitRules): string[] {
  const out: string[] = [];
  if (r.trailing_stop_pct != null) out.push(`trailing stop ${r.trailing_stop_pct}%`);
  if (r.break_even_at_r != null) out.push(`break-even at ${r.break_even_at_r}R`);
  if (r.time_exit_minutes != null) out.push(`time exit after ${r.time_exit_minutes} min`);
  if (r.time_exit_at != null) out.push(`flat at ${r.time_exit_at}`);
  return out;
}

// ---------------------------------------------------------------- the Paper bot's "Start on paper"

export interface BotContext {
  /** An index symbol (no cash leg: the quick bot trades its option, or its future when that is the default). */
  index: boolean;
  /** A daily strategy (held for days): single positions only, options bought (the API's swing rules). */
  swing: boolean;
  /** The organisation's `approval_only_entries` flag is on, so APPROVAL may be asked for. */
  approvalAllowed: boolean;
}

/**
 * The quick bot's request with the user's defaults, and the words of each value taken. Nothing to take (no defaults,
 * or none that applies) returns `base` itself, so the body is byte-identical to today's. Never touches the mode, the
 * symbol, the strategy, the timeframe or the holding (the strategy's candles decide it).
 */
export function botWithDefaults<T extends DeploymentCreateRequest>(base: T, d: TradingDefaults | null | undefined, ctx: BotContext):
  { request: T; applied: string[] } {
  if (!d || !hasAnyDefault(d)) return { request: base, applied: [] };
  const applied: string[] = [];
  const out: DeploymentCreateRequest = { ...base };
  let changed = false;

  if (ctx.index && base.instrument_kind === "OPTION") {
    if (d.instrument_kind === "FUTURE") {
      for (const k of ["option_position", "strike_rule", "strike_offset", "premium_stop_pct"] as const) delete out[k];
      out.instrument_kind = "FUTURE";
      out.expiry_rule = d.expiry_rule ?? base.expiry_rule ?? "NEAREST";
      out.max_lots = d.max_lots ?? base.max_lots ?? 1;
      applied.push("futures");
      changed = true;
    } else {
      const structure = !ctx.swing && isStructure(d.option_strategy) ? d.option_strategy : null;
      if (structure) {
        delete out.option_position;
        out.option_strategy = structure;
        applied.push(structureLabel(structure));
        if (d.spread_width !== null && WIDTH_STRUCTURES.includes(structure)) {
          out.spread_width = d.spread_width;
          applied.push(`${WINGED_STRUCTURES.includes(structure) || structure === "LONG_BUTTERFLY" ? "wing width" : "short strike distance"} ${d.spread_width} steps`);
        }
        const basis = PAYOFF_STRUCTURES.includes(structure) ? "of the risk basis" : DEBIT_STRUCTURES.includes(structure) ? "of the debit" : "of the credit";
        if (d.target_credit_pct !== null) { out.target_credit_pct = d.target_credit_pct; applied.push(`target ${d.target_credit_pct}% ${basis}`); }
        if (d.stop_credit_pct !== null && !(DEBIT_STRUCTURES.includes(structure) && d.stop_credit_pct > 100)) {
          out.stop_credit_pct = d.stop_credit_pct; applied.push(`stop ${d.stop_credit_pct}% ${basis}`);
        }
        changed = true;
      } else {
        if (d.option_position === "WRITE" && !ctx.swing) { out.option_position = "WRITE"; applied.push("option writing"); changed = true; }
        if (d.premium_stop_pct !== null) {
          out.premium_stop_pct = d.premium_stop_pct;
          applied.push(`premium ${out.option_position === "WRITE" ? "ceiling" : "stop"} ${d.premium_stop_pct}%`);
          changed = true;
        }
      }
      if (d.strike_rule !== null && d.strike_rule !== "ATM") {
        out.strike_rule = d.strike_rule;
        out.strike_offset = d.strike_offset && d.strike_offset >= 1 ? d.strike_offset : 1;
        applied.push(strikeWord(d.strike_rule, out.strike_offset));
        changed = true;
      }
    }
    if (d.instrument_kind !== "FUTURE") {
      if (d.expiry_rule !== null && d.expiry_rule !== base.expiry_rule) {
        out.expiry_rule = d.expiry_rule; applied.push(`${EXPIRY_WORD[d.expiry_rule]} expiry`); changed = true;
      }
      if (d.max_lots !== null && d.max_lots !== base.max_lots) { out.max_lots = d.max_lots; applied.push(lotsWord(d.max_lots)); changed = true; }
    } else {
      if (d.expiry_rule !== null) applied.push(`${EXPIRY_WORD[d.expiry_rule]} expiry`);
      if (d.max_lots !== null) applied.push(lotsWord(d.max_lots));
    }
  }

  const exits = exitsFrom(d);
  if (exits) { out.exit_rules = exits; applied.push(...exitWords(exits)); changed = true; }
  if (d.entry_mode === "APPROVAL" && ctx.approvalAllowed) {
    out.entry_mode = "APPROVAL"; applied.push("each entry waits for your Approve"); changed = true;
  }
  return changed ? { request: out as T, applied } : { request: base, applied: [] };
}

/** "From your defaults: next expiry, 2 lots." (empty when nothing was taken). */
export function appliedLine(applied: readonly string[]): string {
  if (!applied.length) return "";
  const text = applied.join(", ");
  return `From your defaults: ${text}.`;
}

// ---------------------------------------------------------------- the full deployment form ("More ways to trade")

/** The full form's field values (its own state shapes: numbers it types as text stay text). Only the keys set. */
export interface FormPrefill {
  kind?: InstrumentKind;
  structure?: DefaultStructure;
  position?: OptionPosition;
  expiryRule?: ExpiryRule;
  strikeRule?: StrikeRule;
  strikeOffset?: number;
  premiumStop?: string;
  maxLots?: string;
  spreadWidth?: number;
  targetCredit?: string;
  stopCredit?: string;
  exitTrail?: string;
  exitBreakEven?: string;
  exitMinutes?: string;
  exitAt?: string;
  approval?: boolean;
}

/** The values the full form starts with. Every field stays visible and editable on the form, and is marked there. */
export function formPrefill(d: TradingDefaults | null | undefined, approvalAllowed: boolean): FormPrefill {
  const p: FormPrefill = {};
  if (!d || !hasAnyDefault(d)) return p;
  if (d.instrument_kind !== null) p.kind = d.instrument_kind;
  if (d.option_strategy !== null) p.structure = d.option_strategy;
  if (d.option_position !== null) p.position = d.option_position;
  if (d.expiry_rule !== null) p.expiryRule = d.expiry_rule;
  if (d.strike_rule !== null) p.strikeRule = d.strike_rule;
  if (d.strike_offset !== null && d.strike_offset >= 1) p.strikeOffset = d.strike_offset;
  if (d.premium_stop_pct !== null) p.premiumStop = String(d.premium_stop_pct);
  if (d.max_lots !== null) p.maxLots = String(d.max_lots);
  if (d.spread_width !== null) p.spreadWidth = d.spread_width;
  if (d.target_credit_pct !== null) p.targetCredit = String(d.target_credit_pct);
  if (d.stop_credit_pct !== null) p.stopCredit = String(d.stop_credit_pct);
  const e = d.exit_rules;
  if (e?.trailing_stop_pct != null) p.exitTrail = String(e.trailing_stop_pct);
  if (e?.break_even_at_r != null) p.exitBreakEven = String(e.break_even_at_r);
  if (e?.time_exit_minutes != null) p.exitMinutes = String(e.time_exit_minutes);
  if (e?.time_exit_at != null) p.exitAt = e.time_exit_at;
  if (d.entry_mode === "APPROVAL" && approvalAllowed) p.approval = true;
  return p;
}

/** True while a field still shows the value it took from the defaults (a changed field loses its mark). */
export function stillFromDefaults<K extends keyof FormPrefill>(prefill: FormPrefill, key: K, value: FormPrefill[K]): boolean {
  return prefill[key] !== undefined && prefill[key] === value;
}

// ---------------------------------------------------------------- the chain order ticket

/** The ticket's starting lots, product and order type from the defaults (only the ones set). */
export function ticketPrefill(d: TradingDefaults | null | undefined): { lots?: number; product?: TicketProduct; orderType?: TicketOrderType } {
  const t = d?.ticket;
  const out: { lots?: number; product?: TicketProduct; orderType?: TicketOrderType } = {};
  if (!t) return out;
  if (t.lots !== null) out.lots = t.lots;
  if (t.product !== null) out.product = t.product;
  if (t.order_type !== null) out.orderType = t.order_type;
  return out;
}

// ---------------------------------------------------------------- reading and saving

interface PrefsRead { prefs?: Record<string, unknown>; versions?: Record<string, number> }

export async function loadDefaults(fetcher: typeof request = request): Promise<{ value: TradingDefaults; version: number }> {
  const r = await fetcher<PrefsRead>("/me/prefs");
  return { value: readDefaults(r.prefs?.[NAMESPACE]), version: r.versions?.[NAMESPACE] ?? 0 };
}

export async function saveDefaults(value: TradingDefaults, version: number | null, fetcher: typeof request = request):
  Promise<{ value: TradingDefaults; version: number }> {
  const q = version === null ? "" : `?version=${version}`;
  const r = await fetcher<{ value: unknown; version: number }>(`/me/prefs/${NAMESPACE}${q}`, {
    method: "PUT", body: JSON.stringify(cleanForSave(value)),
  });
  const saved = { value: readDefaults(r.value), version: r.version };
  cached = Promise.resolve(saved.value);
  return saved;
}

let cached: Promise<TradingDefaults | null> | null = null;

/** For tests: forget the copy this tab read. */
export function resetDefaultsCache(): void {
  cached = null;
}

/** The user's defaults for a form (read once per tab, refreshed by a save). Null while not known, when off, or on a
 * failed read: a form then starts exactly as today. */
export function useTradingDefaults(enabled: boolean, fetcher: typeof request = request): TradingDefaults | null {
  const [value, setValue] = useState<TradingDefaults | null>(null);
  useEffect(() => {
    if (!enabled) { setValue(null); return undefined; }
    let live = true;
    if (!cached) cached = loadDefaults(fetcher).then((r) => r.value).catch(() => { cached = null; return null; });
    void cached.then((v) => { if (live) setValue(v); });
    return () => { live = false; };
  }, [enabled, fetcher]);
  return value;
}

// ---------------------------------------------------------------- the page's own form (text fields as typed)

/** The page's fields as typed: "" = not set. */
export interface DefaultsForm {
  instrument_kind: InstrumentKind | "";
  option_strategy: DefaultStructure | "";
  option_position: OptionPosition | "";
  expiry_rule: ExpiryRule | "";
  strike_rule: StrikeRule | "";
  strike_offset: string;
  premium_stop_pct: string;
  max_lots: string;
  spread_width: string;
  target_credit_pct: string;
  stop_credit_pct: string;
  approval: boolean;
  trailing_stop_pct: string;
  break_even_at_r: string;
  time_exit_minutes: string;
  time_exit_at: string;
  ticket_lots: string;
  ticket_product: TicketProduct | "";
  ticket_order_type: TicketOrderType | "";
  expiry_day_next: boolean;
}

const txt = (v: number | null | undefined) => (v === null || v === undefined ? "" : String(v));

export function formOf(d: TradingDefaults): DefaultsForm {
  return {
    instrument_kind: d.instrument_kind ?? "", option_strategy: d.option_strategy ?? "", option_position: d.option_position ?? "",
    expiry_rule: d.expiry_rule ?? "", strike_rule: d.strike_rule ?? "", strike_offset: txt(d.strike_offset),
    premium_stop_pct: txt(d.premium_stop_pct), max_lots: txt(d.max_lots), spread_width: txt(d.spread_width),
    target_credit_pct: txt(d.target_credit_pct), stop_credit_pct: txt(d.stop_credit_pct), approval: d.entry_mode === "APPROVAL",
    trailing_stop_pct: txt(d.exit_rules?.trailing_stop_pct), break_even_at_r: txt(d.exit_rules?.break_even_at_r),
    time_exit_minutes: txt(d.exit_rules?.time_exit_minutes), time_exit_at: d.exit_rules?.time_exit_at ?? "",
    ticket_lots: txt(d.ticket?.lots), ticket_product: d.ticket?.product ?? "", ticket_order_type: d.ticket?.order_type ?? "",
    expiry_day_next: d.expiry_day_next !== false,
  };
}

type NumRule = { label: string; lo: number; hi: number; int?: boolean; above?: boolean };
export const NUMBER_RULES: Record<string, NumRule> = {
  strike_offset: { label: "Strike steps", lo: 1, hi: 10, int: true },
  premium_stop_pct: { label: "Premium stop", lo: 5, hi: 95 },
  max_lots: { label: "Lots for a new bot", lo: 1, hi: 500, int: true },
  spread_width: { label: "Width", lo: 1, hi: 20, int: true },
  target_credit_pct: { label: "Target", lo: 5, hi: 95 },
  stop_credit_pct: { label: "Stop", lo: 10, hi: 500 },
  trailing_stop_pct: { label: "Trailing stop", lo: 0, hi: 50, above: true },
  break_even_at_r: { label: "Break-even at R", lo: 0, hi: 10, above: true },
  time_exit_minutes: { label: "Time exit", lo: 1, hi: 375, int: true },
  ticket_lots: { label: "Ticket lots", lo: 1, hi: 500, int: true },
};

function parseNum(key: keyof typeof NUMBER_RULES, raw: string, problems: string[]): number | null {
  const t = raw.replace(/,/g, "").trim();
  if (!t) return null;
  const rule = NUMBER_RULES[key];
  const v = Number(t);
  const ok = Number.isFinite(v) && (!rule.int || Number.isInteger(v)) && (rule.above ? v > rule.lo : v >= rule.lo) && v <= rule.hi;
  if (!ok) {
    problems.push(`${rule.label}: ${rule.int ? "a whole number" : "a number"} ${rule.above ? "above" : "from"} ${rule.lo} ${rule.above ? "up to" : "to"} ${rule.hi}.`);
    return null;
  }
  return v;
}

/** The form as defaults, with a plain sentence per value that cannot be saved. Then cleaned like a save. */
export function defaultsOf(f: DefaultsForm): { value: TradingDefaults; problems: string[] } {
  const problems: string[] = [];
  const n = (k: keyof typeof NUMBER_RULES, raw: string) => parseNum(k, raw, problems);
  const at = f.time_exit_at.trim();
  if (at && !TIME.test(at)) problems.push("Flat at: a time like 15:10.");
  const value: TradingDefaults = {
    instrument_kind: f.instrument_kind || null, option_strategy: f.option_strategy || null, option_position: f.option_position || null,
    expiry_rule: f.expiry_rule || null, strike_rule: f.strike_rule || null,
    strike_offset: f.strike_rule === "ITM" || f.strike_rule === "OTM" ? n("strike_offset", f.strike_offset) : null,
    premium_stop_pct: n("premium_stop_pct", f.premium_stop_pct), max_lots: n("max_lots", f.max_lots),
    spread_width: n("spread_width", f.spread_width), target_credit_pct: n("target_credit_pct", f.target_credit_pct),
    stop_credit_pct: n("stop_credit_pct", f.stop_credit_pct), entry_mode: f.approval ? "APPROVAL" : null,
    exit_rules: {
      trailing_stop_pct: n("trailing_stop_pct", f.trailing_stop_pct), break_even_at_r: n("break_even_at_r", f.break_even_at_r),
      time_exit_minutes: n("time_exit_minutes", f.time_exit_minutes), time_exit_at: at && TIME.test(at) ? at : null,
    },
    ticket: { lots: n("ticket_lots", f.ticket_lots), product: f.ticket_product || null, order_type: f.ticket_order_type || null },
    expiry_day_next: f.expiry_day_next ? null : false,
  };
  const structure = value.option_strategy;
  if (structure && DEBIT_STRUCTURES.includes(structure) && (value.stop_credit_pct ?? 0) > 100) {
    problems.push("Stop: a debit structure cannot lose more than its debit, so at most 100.");
  }
  return { value: cleanForSave(value), problems };
}
