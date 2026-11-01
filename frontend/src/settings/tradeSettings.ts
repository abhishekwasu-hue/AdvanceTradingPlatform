/**
 * M68 PR-0 (owner order M68, TS-2): Settings › Trade settings - every trading choice in one place, each "Not set" until the
 * trader saves his own. ATP pre-fills nothing. The server holds the one registry (backend/app/me/trade_settings.py) and
 * sends every field with its words, kind, choices and bounds (GET /api/me/trade-settings), so this page never keeps its
 * own copy. A field that nothing reads yet ("not wired") is not sent to a trader at all; a platform admin sees it under
 * Advanced. Facts (set by the exchange or the broker) are shown as information, never as a control.
 */
import { useEffect, useState } from "react";
import { request } from "../api/client";
import { GLOSSARY, type GlossaryKey } from "../ui/glossary";
import { formatNumber } from "../ui/format";

export const TRADE_SETTINGS_PATH = "/settings/trade-settings";
export const NOT_SET = "Not set";

export type FieldKind = "choice" | "number" | "whole" | "switch" | "time" | "legs" | "fact";

export interface TradeField {
  key: string; label: string; kind: FieldKind; help: string; unit: string; choices: { value: string; label: string }[];
  low: number | null; high: number | null; low_open: boolean; term: string | null; fact: boolean; wired: boolean;
  scope: "user" | "organisation"; value: unknown; saved: boolean;
}

export interface TradeSection { id: string; title: string; fields: TradeField[]; today: { label: string; path: string } | null }

export interface TradeSettingsView { sections: TradeSection[]; admin: boolean; page: string }

/** A field's text in the form: "" = Not set; a switch is "on" / "off". */
export type Draft = Record<string, string>;

export function loadTradeSettings(fetcher: typeof request = request): Promise<TradeSettingsView> {
  return fetcher<TradeSettingsView>("/me/trade-settings");
}

/** Saves only the fields in `values` (null: back to Not set); the server leaves every other field as it was. */
export async function saveTradeSettings(values: Record<string, unknown>, fetcher: typeof request = request): Promise<TradeSettingsView> {
  const view = await fetcher<TradeSettingsView>("/me/trade-settings", { method: "PUT", body: JSON.stringify({ values }) });
  cached = Promise.resolve(view);          // the next ticket / form starts from what he just saved
  return view;
}

// ---------------------------------------------------------------- M68 PR-1: Order defaults for the ticket and the forms

export const PRODUCT_KEY = "order_defaults.product";
export const ORDER_TYPE_KEY = "order_defaults.order_type";
export const HOLDING_KEY = "order_defaults.holding";
export const PROTECTION_KEY = "order_defaults.price_protection_pct";
const TICKET_TYPES = ["MARKET", "LIMIT", "SL", "SL-M"] as const;
const BOT_STYLES = ["MARKET", "PROTECTED_LIMIT"] as const;

export interface OrderDefaults {
  product?: "MIS" | "NRML";
  orderType?: (typeof TICKET_TYPES)[number];
  holding?: "INTRADAY" | "SWING";
  botOrderStyle?: (typeof BOT_STYLES)[number];
  protectionPct?: number;
}

/** The trader's own saved Order defaults (only the ones he saved; nothing is ever filled in by ATP). */
export function orderDefaultsOf(view: TradeSettingsView | null | undefined): OrderDefaults {
  const out: OrderDefaults = {};
  const field = (key: string) => view?.sections.flatMap((s) => s.fields).find((f) => f.key === key && f.saved);
  const v = (key: string) => field(key)?.value;
  const product = v(PRODUCT_KEY);
  if (product === "MIS" || product === "NRML") out.product = product;
  const type = v(ORDER_TYPE_KEY);
  if (TICKET_TYPES.includes(type as never)) out.orderType = type as OrderDefaults["orderType"];
  if (BOT_STYLES.includes(type as never)) out.botOrderStyle = type as OrderDefaults["botOrderStyle"];
  const holding = v(HOLDING_KEY);
  if (holding === "INTRADAY" || holding === "SWING") out.holding = holding;
  const pct = v(PROTECTION_KEY);
  if (typeof pct === "number" && Number.isFinite(pct) && pct > 0) out.protectionPct = pct;
  return out;
}

// ---------------------------------------------------------------- M68 PR-6: Automation for the bot forms

export const APPROVAL_MODE_KEY = "automation.approval_mode";
export type EntryModeChoice = "AUTO" | "APPROVAL";
/** "New entries": nothing pre-selected; the bot form asks when his Trade settings has none. */
export const ENTRY_MODE_CHOICES: { value: EntryModeChoice; label: string }[] = [
  { value: "AUTO", label: "Placed automatically" }, { value: "APPROVAL", label: "Ask me first" },
];

/** The trader's own saved "New entries" choice (undefined: Not set - the form asks). */
export function approvalModeOf(view: TradeSettingsView | null | undefined): EntryModeChoice | undefined {
  const field = view?.sections.flatMap((s) => s.fields).find((f) => f.key === APPROVAL_MODE_KEY && f.saved);
  return field?.value === "AUTO" || field?.value === "APPROVAL" ? field.value : undefined;
}

let cached: Promise<TradeSettingsView | null> | null = null;

/** Test hook: forget the cached read. */
export function resetTradeSettingsCache(): void { cached = null; }

/** The trader's Trade settings, read once per page load (null while loading, when off, or when the read failed). */
export function useTradeSettings(enabled: boolean, fetcher: typeof request = request): TradeSettingsView | null {
  const [value, setValue] = useState<TradeSettingsView | null>(null);
  useEffect(() => {
    if (!enabled) { setValue(null); return undefined; }
    let live = true;
    if (!cached) cached = loadTradeSettings(fetcher).catch(() => { cached = null; return null; });
    void cached.then((v) => { if (live) setValue(v); });
    return () => { live = false; };
  }, [enabled, fetcher]);
  return value;
}

/** The glossary key for a field's (?), only when the glossary has it. */
export function termOf(field: TradeField): GlossaryKey | null {
  return field.term && field.term in GLOSSARY ? (field.term as GlossaryKey) : null;
}

/** A field the trader can change here (a fact and a team ceiling are not his to set on this page). */
export const editable = (f: TradeField): boolean => !f.fact && f.scope === "user" && f.kind !== "legs";

/**
 * What a section shows: the trader's own controls (wired), the facts, and - for a platform admin only - the fields that
 * nothing reads yet, under Advanced. The server already leaves the unwired ones out for a trader; this keeps it so.
 */
export function partsOf(section: TradeSection, admin: boolean): { main: TradeField[]; facts: TradeField[]; advanced: TradeField[] } {
  return {
    main: section.fields.filter((f) => !f.fact && f.wired),
    facts: section.fields.filter((f) => f.fact),
    advanced: admin ? section.fields.filter((f) => !f.fact && !f.wired) : [],
  };
}

/** "2 of 7 set" over the fields this viewer may set in the section, or null when there are none. */
export function setCount(section: TradeSection, admin: boolean): string | null {
  const { main, advanced } = partsOf(section, admin);
  const own = [...main, ...advanced].filter((f) => f.scope === "user");
  if (!own.length) return null;
  return `${own.filter((f) => f.saved).length} of ${own.length} set`;
}

function textOf(f: TradeField): string {
  const v = f.value;
  if (v === null || v === undefined) return "";
  if (f.kind === "switch") return v === true ? "on" : "off";
  return typeof v === "number" || typeof v === "string" ? String(v) : "";
}

export function draftOf(section: TradeSection): Draft {
  return Object.fromEntries(section.fields.filter(editable).map((f) => [f.key, textOf(f)]));
}

/** A field's value in plain words: "Not set", a choice's label, "On" / "Off", or the number with its unit. */
export function valueWords(f: TradeField): string {
  const v = f.value;
  if (v === null || v === undefined) return NOT_SET;
  if (f.kind === "switch") return v === true ? "On" : "Off";
  if (f.kind === "choice") return f.choices.find((c) => c.value === v)?.label ?? NOT_SET;
  if (f.kind === "legs") return Array.isArray(v) ? `${v.length} ${v.length === 1 ? "leg" : "legs"}` : NOT_SET;
  if (typeof v === "number") return f.unit === "₹" ? `₹${formatNumber(v, 3, { trim: true })}` : `${v}${f.unit ? (f.unit === "%" ? "%" : ` ${f.unit}`) : ""}`;
  return String(v);
}

const TIME = /^([01]\d|2[0-3]):[0-5]\d$/;

/** The value of one field's text, or the problem in plain words. "" = Not set (null). */
export function parseField(f: TradeField, text: string): { value: unknown; problem: string | null } {
  const t = text.trim();
  if (t === "") return { value: null, problem: null };
  if (f.kind === "switch") return t === "on" || t === "off" ? { value: t === "on", problem: null } : { value: null, problem: `${f.label}: choose On or Off.` };
  if (f.kind === "choice") {
    return f.choices.some((c) => c.value === t) ? { value: t, problem: null } : { value: null, problem: `${f.label}: choose one of the options.` };
  }
  if (f.kind === "time") return TIME.test(t) ? { value: t, problem: null } : { value: null, problem: `${f.label}: enter a time like 09:30.` };
  const n = Number(t.replace(/,/g, ""));
  if (!Number.isFinite(n)) return { value: null, problem: `${f.label}: enter a number.` };
  if (f.kind === "whole" && !Number.isInteger(n)) return { value: null, problem: `${f.label}: enter a whole number.` };
  if (f.low !== null && (f.low_open ? n <= f.low : n < f.low)) {
    return { value: null, problem: `${f.label}: enter ${f.low_open ? "more than" : "at least"} ${f.low}.` };
  }
  if (f.high !== null && n > f.high) return { value: null, problem: `${f.label}: enter at most ${f.high}.` };
  return { value: n, problem: null };
}

/**
 * The save body of one section: only the fields whose text changed (a cleared field that was saved goes back to Not set,
 * null); a field never touched is never sent, so nothing is filled for the trader. The first problem stops the save.
 */
export function changesOf(section: TradeSection, draft: Draft, keys: readonly string[]): { values: Record<string, unknown>; problem: string | null } {
  const values: Record<string, unknown> = {};
  for (const f of section.fields) {
    if (!keys.includes(f.key) || !editable(f)) continue;
    const text = draft[f.key] ?? "";
    if (text === textOf(f)) continue;
    const { value, problem } = parseField(f, text);
    if (problem) return { values: {}, problem };
    if (value === null && !f.saved) continue;
    values[f.key] = value;
  }
  return { values, problem: null };
}

/**
 * TS-3 "Save as my default": writes ONE field of the trader's Trade settings (used by the order ticket and the bot form
 * from PR-1 / PR-2). An empty value is never written - nothing is saved for him that he did not choose.
 */
export async function saveAsDefault(key: string, value: unknown, fetcher: typeof request = request): Promise<boolean> {
  if (value === null || value === undefined || (typeof value === "string" && value.trim() === "")) return false;
  if (typeof value === "number" && !Number.isFinite(value)) return false;
  await saveTradeSettings({ [key]: value }, fetcher);
  return true;
}
