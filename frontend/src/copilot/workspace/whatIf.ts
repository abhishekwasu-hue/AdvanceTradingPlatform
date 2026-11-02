/**
 * M76 MP-6 (owner order M76 S-1 / S-2; flag `copilot_workspace`): what-if for the trade plan. Pure rules, no React, no
 * network. Every figure is the Options Builder's engine (`evaluate`) for the plan's own legs - this file only builds the
 * request with HIS shifts and reads the engine's curves at a price:
 *   - S-1: spot move (points), IV change (IV points, the builder's absolute `iv_shift`), days forward - each one blank =
 *     not applied (nothing is chosen for him, M68); any of them together = the combined scenario. The scenario's curve
 *     is the engine's value on that date with that IV (its T+0 when no days are typed), read at spot + move; its delta
 *     line is the slope of that same curve (rupees per point), so it is the engine's own number, never a second model.
 *   - S-2: the plan's P&L at both edges of the next areas of the open chart's reading (his own chart may show the range,
 *     CLAUDE.md section 3) - today and at expiry. Margin at those levels is not known (no SPAN in PAPER).
 * Saved scenarios live in his prefs (`what_if`, at most 8), each with his own name.
 */
import { wireLegs } from "../../optionsBuilder/api";
import { formatNumber } from "../../ui/format";
import { money, rangePctFor, seriesAt, type Evaluation, type Leg } from "../../optionsBuilder/model";
import type { AnnotationArea } from "./annotations";

export interface ScenarioDraft { spotMove: string; ivChange: string; days: string }
export const EMPTY_SCENARIO: ScenarioDraft = { spotMove: "", ivChange: "", days: "" };
export interface SavedScenario { name: string; spot_move: number | null; iv_change: number | null; days: number | null }
export const MAX_SCENARIOS = 8;
export const SCENARIO_NAME = /^[A-Za-z0-9][A-Za-z0-9 %()+\-/.,:<>=]{0,39}$/;

/** A signed number as typed ("+50", "-2.5"), else null - blank stays blank. */
export function signed(text: string): number | null {
  const t = text.replace(/,/g, "").replace(/^\+/, "").replace(/−/g, "-").trim();
  if (t === "" || t === "-") return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}

/** Whole days forward (0..3650), else null. */
export function daysOf(text: string): number | null {
  const n = signed(text);
  return n != null && Number.isInteger(n) && n >= 0 && n <= 3650 ? n : null;
}

export interface Shifts { spotMove: number | null; ivChange: number | null; days: number | null }
export function shiftsOf(d: ScenarioDraft): Shifts {
  return { spotMove: signed(d.spotMove), ivChange: signed(d.ivChange), days: daysOf(d.days) };
}
export const anyShift = (s: Shifts) => s.spotMove != null || s.ivChange != null || s.days != null;

/** The engine's request for the scenario: the same legs, his IV change and days; the range reaches the moved price. */
export function scenarioRequest(legs: Leg[], spot: number, asOf: string, s: Shifts) {
  const reach = s.spotMove != null ? Math.ceil((Math.abs(s.spotMove) / spot) * 100 + 2) : 0;
  return {
    legs: wireLegs(legs),
    spot, as_of: asOf, days_forward: s.days ?? 0, iv_shift: (s.ivChange ?? 0) / 100,
    range_pct: Math.max(rangePctFor(spot, legs), reach), points: 241,
  };
}

/** The slope of a curve at a price (rupees per point), from the engine's own grid; null outside it. */
export function slopeAt(xs: number[], ys: number[] | null | undefined, price: number): number | null {
  if (!ys || xs.length < 2 || price < xs[0] || price > xs[xs.length - 1]) return null;
  let i = xs.findIndex((x) => x >= price);
  if (i <= 0) i = 1;
  const dx = xs[i] - xs[i - 1];
  return dx > 0 ? (ys[i] - ys[i - 1]) / dx : null;
}

export interface WhatIfRow { key: string; label: string; value: string; sub?: string; tone?: "up" | "down" | "fg" }
const tone = (v: number): "up" | "down" | "fg" => (v > 0 ? "up" : v < 0 ? "down" : "fg");
const signedMoney = (v: number) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${money(Math.abs(v))}`;
const level = (v: number) => formatNumber(v, 2);

/** The scenario in words, one line ("Spot +100 points · IV +2 points · 3 days"); empty when nothing is typed. */
export function scenarioWords(s: Shifts): string {
  const out: string[] = [];
  if (s.spotMove != null) out.push(`Spot ${s.spotMove >= 0 ? "+" : "−"}${formatNumber(Math.abs(s.spotMove), 2, { trim: true })} points`);
  if (s.ivChange != null) out.push(`IV ${s.ivChange >= 0 ? "+" : "−"}${formatNumber(Math.abs(s.ivChange), 2, { trim: true })} points`);
  if (s.days != null) out.push(`${s.days} day${s.days === 1 ? "" : "s"} forward`);
  return out.join(" · ");
}

/** S-1: the scenario beside today, both from the engine. */
export function scenarioRows(base: Evaluation, scen: Evaluation, spot: number, s: Shifts): WhatIfRow[] {
  const at = spot + (s.spotMove ?? 0);
  const curve = (s.days ?? 0) > 0 ? scen.on_date : scen.today;
  const now = seriesAt(base.prices, base.today, spot);
  const inside = (xs: number[]) => xs.length > 0 && at >= xs[0] && at <= xs[xs.length - 1];   // never read past the engine's grid
  const then = inside(scen.prices) ? seriesAt(scen.prices, curve, at) : null;
  const expiry = inside(base.prices) ? seriesAt(base.prices, base.at_expiry, at) : null;
  const slope = slopeAt(scen.prices, curve, at);
  const rows: WhatIfRow[] = [{ key: "price", label: "Price in this scenario", value: level(at) }];
  if (now != null) rows.push({ key: "now", label: "P&L now at spot", value: signedMoney(now), tone: tone(now) });
  rows.push(then == null ? { key: "scenario", label: "P&L in this scenario", value: "Outside the payoff range" }
    : { key: "scenario", label: "P&L in this scenario", value: signedMoney(then), tone: tone(then),
      sub: now != null ? `${signedMoney(then - now)} against now` : undefined });
  if (expiry != null) rows.push({ key: "expiry", label: "P&L at expiry at this price", value: signedMoney(expiry), tone: tone(expiry) });
  if (slope != null) rows.push({ key: "delta", label: "Δ in this scenario (from the curve)", value: `${signedMoney(slope)} per point` });
  return rows;
}

export interface AreaRow { key: string; label: string; range: string; today: string; expiry: string; tone: "up" | "down" | "fg" }
const SIDE_WORDS = { buyer: "buyer", seller: "seller" } as const;

/** S-2: the plan's P&L at both edges of each next area (above / inside / below) of the open chart's reading. */
export function areaRows(base: Evaluation, next: { above: AnnotationArea | null; below: AnnotationArea | null; inside: AnnotationArea | null } | null): AreaRow[] {
  if (!next) return [];
  const out: AreaRow[] = [];
  const pair = (series: number[] | null | undefined, a: AnnotationArea) => {
    const lo = seriesAt(base.prices, series, a.low);
    const hi = seriesAt(base.prices, series, a.high);
    return lo == null || hi == null ? null : [lo, hi] as const;
  };
  for (const [where, a] of [["above", next.above], ["inside", next.inside], ["below", next.below]] as const) {
    if (!a || a.low < base.prices[0] || a.high > base.prices[base.prices.length - 1]) continue;
    const today = pair(base.today, a);
    const exp = pair(base.at_expiry ?? base.on_date, a);
    if (!today || !exp) continue;
    const words = where === "inside" ? "Price inside" : `Next ${SIDE_WORDS[a.side]} area ${where}`;
    out.push({ key: where, label: `${words} (${a.timeframe})`, range: `${a.low_text}–${a.high_text}`,
      today: `${signedMoney(today[0])} to ${signedMoney(today[1])}`, expiry: `${signedMoney(exp[0])} to ${signedMoney(exp[1])}`,
      tone: tone(Math.min(today[0], today[1]) >= 0 ? 1 : Math.max(today[0], today[1]) <= 0 ? -1 : 0) });
  }
  return out;
}

/** A saved scenario from his draft and name, else null (a name of his own and at least one shift). */
export function toSaved(name: string, d: ScenarioDraft): SavedScenario | null {
  const s = shiftsOf(d);
  const n = name.trim();
  if (!SCENARIO_NAME.test(n) || !anyShift(s)) return null;
  return { name: n, spot_move: s.spotMove, iv_change: s.ivChange, days: s.days };
}

export function toDraft(s: SavedScenario): ScenarioDraft {
  return { spotMove: s.spot_move == null ? "" : String(s.spot_move), ivChange: s.iv_change == null ? "" : String(s.iv_change), days: s.days == null ? "" : String(s.days) };
}

/** His saved list from the prefs reply (anything malformed is left out, never guessed). */
export function readScenarios(value: unknown): SavedScenario[] {
  const list = (value as { scenarios?: unknown } | null)?.scenarios;
  if (!Array.isArray(list)) return [];
  const num = (x: unknown) => (typeof x === "number" && Number.isFinite(x) ? x : null);
  return list.filter((x): x is Record<string, unknown> => !!x && typeof x === "object" && typeof (x as { name?: unknown }).name === "string")
    .map((x) => ({ name: String(x.name), spot_move: num(x.spot_move), iv_change: num(x.iv_change), days: num(x.days) }))
    .slice(0, MAX_SCENARIOS);
}
