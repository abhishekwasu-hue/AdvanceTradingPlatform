/**
 * SX1 / SX2 / SX5 (owner, 24 Oct, docs/owner-orders/ATP_ORDER_CX_SX.md): "Exits for this strategy" on the basket ticket
 * (the spread builder's "Review and place" opens the same ticket). The pure parts - tested in strategyExits.test.tsx.
 *
 * - Overall stop-loss and overall target, each as the combined ₹ profit / loss, a % of the net premium, or the
 *   underlying's spot level (spot is the default for a spread, T-D2). Optional trailing: lock a gain, trail step by step,
 *   lock and trail, or move to break-even. The stop only ever moves the trade's way.
 * - Every number is the user's own: the section starts empty, or from his own saved defaults (marked "from your
 *   defaults"); it can be left empty (then no strategy exit is set and each leg keeps its own stop).
 * - When the legs fill, the server sets ONE combined exit (origin SX) on the filled legs (backend
 *   app/execution/strategy_exits.py): it shows as the combined-exit chip on Positions, where it can be changed or removed.
 */
import { useEffect, useState } from "react";
import { request } from "../api/client";
import { formatINR, formatNumber } from "../ui/format";
import { signedParts } from "../ui/Signed";
import type { StrategyExitsAsk } from "./api";

export type StrategyMeasure = "MTM_INR" | "PREMIUM_PCT" | "SPOT";
export type StrategyTrail = "NONE" | "LOCK" | "TRAIL" | "LOCK_TRAIL" | "BREAK_EVEN";
export const EXITS_NAMESPACE = "strategy_exits";

/** The section as typed: "" = empty. */
export interface StrategyExitForm {
  /** M68 PR-3 (TS1-031): "" = not chosen (his own saved default starts it, else the section asks). */
  measure: StrategyMeasure | "";
  sl: string;
  target: string;
  trailKind: StrategyTrail;
  after: string;
  lock: string;
  step: string;
  by: string;
  /** True while every value is still the one from the user's own saved defaults. */
  fromDefaults: boolean;
}

/** The user's saved starting values (backend app/me/prefs.py StrategyExitPrefs); null = not set. */
export interface StrategyExitDefaults {
  measure: StrategyMeasure | null;
  sl_value: number | null;
  target_value: number | null;
  trail_kind: Exclude<StrategyTrail, "NONE"> | null;
  trail_after: number | null;
  trail_lock: number | null;
  trail_step: number | null;
  trail_by: number | null;
}

export const STRATEGY_MEASURES: { key: StrategyMeasure; label: string; hint: string }[] = [
  { key: "SPOT", label: "Spot level", hint: "The underlying's price" },
  { key: "MTM_INR", label: "Combined ₹ profit / loss", hint: "All legs together, in rupees" },
  { key: "PREMIUM_PCT", label: "% of net premium", hint: "Of the premium at the fill" },
];

export const STRATEGY_TRAILS: { key: StrategyTrail; label: string; hint: string }[] = [
  { key: "NONE", label: "No trailing", hint: "The stop stays where you set it" },
  { key: "LOCK", label: "Lock a gain", hint: "After a set gain, the stop moves to keep part of it" },
  { key: "TRAIL", label: "Trail step by step", hint: "Every set move your way shifts the stop" },
  { key: "LOCK_TRAIL", label: "Lock and trail", hint: "Lock part of a gain, then trail step by step" },
  { key: "BREAK_EVEN", label: "Trail to break-even", hint: "After a set gain, the stop moves to no loss" },
];

/** The legs the section reads: side and underlying only. */
export interface ExitLeg { side: "BUY" | "SELL"; underlying: string }

/** T-D2: a spread (two or more legs on one underlying with a sold leg). M68 PR-3: no longer picks the measure. Pure. */
export function isSpread(legs: readonly ExitLeg[]): boolean {
  return legs.length > 1 && new Set(legs.map((l) => l.underlying)).size === 1 && legs.some((l) => l.side === "SELL");
}

export function emptyForm(legs: readonly ExitLeg[]): StrategyExitForm {
  void legs;                                     // M68 PR-3 (TS1-031): nothing pre-chosen - the section asks
  return { measure: "", sl: "", target: "", trailKind: "NONE", after: "", lock: "", step: "", by: "",
    fromDefaults: false };
}

const text = (v: number | null | undefined) => (typeof v === "number" && Number.isFinite(v) ? String(v) : "");
const posNum = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) && v > 0 ? v : null);
const zeroNum = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) && v >= 0 ? v : null);

/** What the server stored, read defensively (an unknown or bad value is "not set"). Pure. */
export function readExitDefaults(raw: unknown): StrategyExitDefaults | null {
  const r = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const measure = STRATEGY_MEASURES.some((m) => m.key === r.measure) ? (r.measure as StrategyMeasure) : null;
  const kind = STRATEGY_TRAILS.some((t) => t.key === r.trail_kind && t.key !== "NONE") ? (r.trail_kind as Exclude<StrategyTrail, "NONE">) : null;
  const out: StrategyExitDefaults = {
    measure, sl_value: posNum(r.sl_value), target_value: posNum(r.target_value), trail_kind: kind,
    trail_after: posNum(r.trail_after), trail_lock: zeroNum(r.trail_lock), trail_step: posNum(r.trail_step), trail_by: posNum(r.trail_by),
  };
  return Object.values(out).some((v) => v !== null) ? out : null;
}

/** The section's start: the user's own defaults when he saved any (marked), else empty (spot for a spread). Pure. */
export function prefillForm(d: StrategyExitDefaults | null | undefined, legs: readonly ExitLeg[]): StrategyExitForm {
  const base = emptyForm(legs);
  if (!d) return base;
  return {
    measure: d.measure ?? base.measure, sl: text(d.sl_value), target: text(d.target_value), trailKind: d.trail_kind ?? "NONE",
    after: text(d.trail_after), lock: text(d.trail_lock), step: text(d.trail_step), by: text(d.trail_by), fromDefaults: true,
  };
}

/** The form as the user's defaults (what "Save as my defaults" stores). Pure. */
export function defaultsOfForm(f: StrategyExitForm): StrategyExitDefaults {
  const kind = f.trailKind === "NONE" ? null : f.trailKind;
  return {
    measure: f.measure || null, sl_value: amount(f.sl), target_value: amount(f.target), trail_kind: kind,
    trail_after: kind && kind !== "TRAIL" ? amount(f.after) : null,
    trail_lock: kind === "LOCK" || kind === "LOCK_TRAIL" ? zeroAmount(f.lock) : null,
    trail_step: kind === "TRAIL" || kind === "LOCK_TRAIL" ? amount(f.step) : null,
    trail_by: kind === "TRAIL" || kind === "LOCK_TRAIL" ? amount(f.by) : null,
  };
}

function amount(s: string): number | null {
  const v = Number(s.replace(/[₹,%\s]/g, ""));
  return s.trim() !== "" && Number.isFinite(v) && v > 0 ? v : null;
}
function zeroAmount(s: string): number | null {
  const v = Number(s.replace(/[₹,%\s]/g, ""));
  return s.trim() !== "" && Number.isFinite(v) && v >= 0 ? v : null;
}

/** True when the user asked for any exit (a stop-loss, a target or a trailing). Pure. */
export function hasExits(f: StrategyExitForm): boolean {
  return f.sl.trim() !== "" || f.target.trim() !== "" || f.trailKind !== "NONE";
}

/** The unit word of the measure ("₹", "%", "points"). */
export const unitOf = (m: StrategyMeasure) => (m === "MTM_INR" ? "₹" : m === "PREMIUM_PCT" ? "%" : "points");

/** The trailing's boxes (labels in the measure's unit) for the chosen kind. Pure. */
export function trailFields(kind: StrategyTrail, m: StrategyMeasure): { key: "after" | "lock" | "step" | "by"; label: string }[] {
  const u = unitOf(m);
  const after = { key: "after" as const, label: `Once the gain reaches (${u})` };
  const lock = { key: "lock" as const, label: `Move the stop to keep (${u})` };
  const by = { key: "by" as const, label: `Shift the stop by (${u})` };
  if (kind === "LOCK") return [after, lock];
  if (kind === "TRAIL") return [{ key: "step", label: `For every move your way of (${u})` }, by];
  if (kind === "LOCK_TRAIL") return [after, lock, { key: "step", label: `Then for every further move of (${u})` }, by];
  if (kind === "BREAK_EVEN") return [after];
  return [];
}

/** Plain-word reasons the exits cannot be sent (the server checks again). Empty form: none (no exit is set). Pure. */
export function exitErrors(f: StrategyExitForm): string[] {
  if (!hasExits(f)) return [];
  const out: string[] = [];
  const sl = amount(f.sl);
  const target = amount(f.target);
  if (!f.measure) out.push("Choose what the strategy exit watches.");              // M68 PR-3: never pre-chosen
  if (f.sl.trim() && sl === null) out.push("The overall stop-loss must be a number above zero.");
  if (f.target.trim() && target === null) out.push("The overall target must be a number above zero.");
  const locking = f.trailKind === "LOCK" || f.trailKind === "LOCK_TRAIL" || f.trailKind === "BREAK_EVEN";
  if (sl === null && target === null && !locking) out.push("Enter an overall stop-loss, a target, or both.");
  if (f.measure === "SPOT" && sl === null && target === null && locking) {
    out.push("A spot lock needs a stop-loss or a target too, so the exit knows which way is yours.");
  }
  const after = amount(f.after);
  const keep = zeroAmount(f.lock);
  const step = amount(f.step);
  const by = amount(f.by);
  if (f.trailKind === "TRAIL" && sl === null) out.push("A trailing stop needs a stop-loss to start from.");
  if (f.trailKind !== "NONE" && f.trailKind !== "TRAIL" && after === null) out.push("Enter the gain after which the stop moves (more than zero).");
  if (f.trailKind === "LOCK" || f.trailKind === "LOCK_TRAIL") {
    if (keep === null) out.push("Enter how much of the gain to keep (zero or more).");
    if (after !== null && keep !== null && keep >= after) out.push("The gain kept must be less than the gain that moves the stop.");
  }
  if (f.trailKind === "TRAIL" || f.trailKind === "LOCK_TRAIL") {
    if (step === null) out.push("Enter the move that shifts the stop (more than zero).");
    if (by === null) out.push("Enter how far the stop shifts each time (more than zero).");
    if (step !== null && by !== null && by > step) out.push("The stop cannot shift further than the move itself.");
  }
  return out;
}

/** The request's `exits` (null when the section is empty: no strategy exit). Pure. */
export function exitsAsk(f: StrategyExitForm): StrategyExitsAsk | null {
  if (!hasExits(f)) return null;
  const k = f.trailKind;
  const trailing = k === "NONE" ? null : {
    kind: k,
    ...(k !== "TRAIL" ? { after: amount(f.after) ?? undefined } : {}),
    ...(k === "LOCK" || k === "LOCK_TRAIL" ? { lock: zeroAmount(f.lock) ?? undefined } : {}),
    ...(k === "TRAIL" || k === "LOCK_TRAIL" ? { step: amount(f.step) ?? undefined, by: amount(f.by) ?? undefined } : {}),
  };
  return { measure: f.measure || "MTM_INR", sl_value: amount(f.sl), target_value: amount(f.target), ...(trailing ? { trailing } : {}) };   // sent only once exitErrors is empty
}

function money(m: StrategyMeasure, v: number): string {
  if (m === "MTM_INR") return formatINR(v);
  if (m === "PREMIUM_PCT") return `${formatNumber(v, v % 1 ? 1 : 0)}%`;
  return `${formatNumber(v, 2)} points`;
}

/** SX2 the trailing in plain words ("" when none or not complete). Pure. */
export function trailText(f: StrategyExitForm): string {
  if (f.trailKind === "NONE" || !f.measure || exitErrors({ ...f, sl: f.sl || "1" }).length) return "";
  const m = f.measure;
  const a = amount(f.after) ?? 0;
  const keep = zeroAmount(f.lock) ?? 0;
  const step = amount(f.step) ?? 0;
  const by = amount(f.by) ?? 0;
  if (f.trailKind === "LOCK") return `once the gain reaches ${money(m, a)}, the stop moves to keep ${money(m, keep)}`;
  if (f.trailKind === "TRAIL") return `every ${money(m, step)} your way moves the stop ${money(m, by)} with it`;
  if (f.trailKind === "LOCK_TRAIL") {
    return `once the gain reaches ${money(m, a)}, the stop moves to keep ${money(m, keep)}, then every further ${money(m, step)} moves it ${money(m, by)}`;
  }
  return `once the gain reaches ${money(m, a)}, the stop moves to break-even`;
}

/** A sentence with signed values in it (F3 / UX-11 U2): plain words, and the profit / loss figures that render as the
 * shared signed value (+ ▲ / − ▼). */
export type SentencePart = string | { signed: number; kind: "money" | "pct" };

const signedText = (v: number, kind: "money" | "pct") => signedParts({ value: v, kind, decimals: kind === "pct" ? (v % 1 ? 1 : 0) : undefined }).text;

/** The sentence as plain text (the signed figures as their text). Pure. */
export function sentenceText(parts: readonly SentencePart[]): string {
  return parts.map((p) => (typeof p === "string" ? p : signedText(p.signed, p.kind))).join("");
}

/** SX5: the Review screen's sentence, in parts ("Exit all 2 legs if loss reaches −₹X or profit reaches +₹Y; trailing:
 * …" - F3: the ₹ / % figures as the shared signed value). Pure. */
export function exitSentence(f: StrategyExitForm, legCount: number, underlying: string | null): SentencePart[] {
  if (!hasExits(f)) return ["No strategy exit: each leg keeps its own stop."];
  const sl = amount(f.sl);
  const target = amount(f.target);
  const legs = `${legCount} leg${legCount === 1 ? "" : "s"}`;
  const out: SentencePart[] = [];
  if (f.measure === "SPOT") {
    const what = `${underlying ?? "the"} spot`;
    const parts: string[] = [];
    if (sl !== null) parts.push(`${what} reaches ${formatNumber(sl, 2)} (stop-loss)`);
    if (target !== null) parts.push(`${what} reaches ${formatNumber(target, 2)} (target)`);
    out.push(parts.length ? `Exit all ${legs} if ${parts.join(" or ")}` : `Exit all ${legs} when the trailing stop is reached`);
  } else if (sl === null && target === null) {
    out.push(`Exit all ${legs} when the trailing stop is reached`);
  } else {
    const kind = f.measure === "PREMIUM_PCT" ? "pct" : "money";
    const of = f.measure === "PREMIUM_PCT" ? " of the net premium" : "";
    out.push(`Exit all ${legs} if `);
    if (sl !== null) out.push("loss reaches ", { signed: -sl, kind }, of);
    if (sl !== null && target !== null) out.push(" or ");
    if (target !== null) out.push("profit reaches ", { signed: target, kind }, of);
  }
  const trailing = trailText(f);
  out.push(`${trailing ? `; trailing: ${trailing}. The stop only moves your way` : ""}. Sold legs exit first, then the hedges.`);
  return out;
}

/** SX5: the Review screen's sentence as plain text. Pure. */
export function exitWords(f: StrategyExitForm, legCount: number, underlying: string | null): string {
  return sentenceText(exitSentence(f, legCount, underlying));
}

// ---------------------------------------------------------------- the user's own defaults (read once per tab)

interface PrefsRead { prefs?: Record<string, unknown>; versions?: Record<string, number> }
type Fetcher = typeof request;

export async function loadExitDefaults(fetcher: Fetcher = request): Promise<{ value: StrategyExitDefaults | null; version: number }> {
  const r = await fetcher<PrefsRead>("/me/prefs");
  return { value: readExitDefaults(r.prefs?.[EXITS_NAMESPACE]), version: r.versions?.[EXITS_NAMESPACE] ?? 0 };
}

export async function saveExitDefaults(value: StrategyExitDefaults, version: number, fetcher: Fetcher = request):
  Promise<{ value: StrategyExitDefaults | null; version: number }> {
  const r = await fetcher<{ value: unknown; version: number }>(`/me/prefs/${EXITS_NAMESPACE}?version=${version}`, {
    method: "PUT", body: JSON.stringify(value),
  });
  return { value: readExitDefaults(r.value), version: r.version };
}

/** The user's saved defaults for the section (null while unknown, when off, or on a failed read: then it starts empty). */
export function useExitDefaults(enabled: boolean, fetcher: Fetcher = request): { value: StrategyExitDefaults | null; version: number } | null {
  const [state, setState] = useState<{ value: StrategyExitDefaults | null; version: number } | null>(null);
  useEffect(() => {
    if (!enabled) { setState(null); return undefined; }
    let live = true;
    loadExitDefaults(fetcher).then((r) => { if (live) setState(r); }).catch(() => { if (live) setState(null); });
    return () => { live = false; };
  }, [enabled, fetcher]);
  return state;
}
