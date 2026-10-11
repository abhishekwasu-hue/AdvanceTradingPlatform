/**
 * P1-c2: the Options Strategy Builder page as data - legs, the server's evaluation, and the pure geometry the payoff
 * canvas draws (scales, curve paths, profit / loss shading, the probability cone, strike snapping) plus the template
 * thumbnails' shapes. No React, no network: every rule here is unit-tested.
 *
 * Numbers that belong to an instrument (strike step, lot size, the template width) always come from the trader's
 * inputs (the instrument master), never from defaults here.
 */

export type Direction = "BUY" | "SELL";
export type OptionKind = "CE" | "PE" | "FUT";

export interface Leg {
  id: string;
  direction: Direction;
  option_type: OptionKind;
  strike: number;
  premium: number;
  lots: number;
  lot_size: number;
  expiry: string;                 // YYYY-MM-DD
  iv: number | null;              // fraction; null = solved from the premium by the server
  premium_source?: "model" | "manual";
  /** for a model premium: the contract it was priced for (`contractKey`); re-priced only when the contract changes */
  priced_for?: string;
}

export interface Greeks { delta: number; gamma: number; theta: number; vega: number; rho: number }

export interface Evaluation {
  prices: number[];
  today: number[];
  on_date: number[];
  at_expiry: number[] | null;
  days_forward: number;
  iv_shift: number;
  single_expiry: boolean;
  extremes: { max_profit: number | null; max_loss: number | null; unbounded_profit: boolean; unbounded_loss: boolean } | null;
  profitable: [number | null, number | null][] | null;
  breakevens: number[] | null;
  summary: { expiry: string; t_years: number; sigma: number; pop: number; expected_move: number; expected_pnl: number; greeks: Greeks; method: string; model: string };
  legs: (Omit<Leg, "id"> & { iv: number; iv_source?: string; greeks: Greeks; theoretical: number })[];
  disclaimer: string;
}

export interface TemplateInfo { family: string; what: string; two_expiries: boolean; legs: { direction: Direction; option_type: OptionKind; offset: number; lots: number; expiry_slot: 0 | 1 }[] }

let seq = 0;
export function legId(): string {
  seq += 1;
  return `l${Date.now().toString(36)}${seq}`;
}

/** Hedge first: every BUY before every SELL (the margin rule carried over from the Trade repo), order kept within. */
export function hedgeFirst<T extends { direction: Direction }>(legs: T[]): T[] {
  return [...legs.filter((l) => l.direction === "BUY"), ...legs.filter((l) => l.direction !== "BUY")];
}

/** A strike moved by hand (or dragged) lands on the instrument's strike grid. */
export function snapStrike(value: number, step: number): number {
  if (!(step > 0) || !Number.isFinite(value)) return value;
  return Math.max(step, Math.round(value / step) * step);
}

/** Net option premium in money: positive = credit received, negative = debit paid. A futures leg's "premium" is
 * its entry price, not money changing hands, so it is left out. */
export function netPremium(legs: Leg[]): number {
  return legs.reduce((sum, l) => (l.option_type === "FUT" ? sum : sum + (l.direction === "SELL" ? 1 : -1) * l.premium * l.lots * l.lot_size), 0);
}

/** Reward to risk, when both are finite and there is a risk; null otherwise (an undefined risk has no ratio). */
export function rewardToRisk(e: Evaluation["extremes"]): number | null {
  if (!e || e.max_profit == null || e.max_loss == null || e.max_loss >= 0 || e.max_profit <= 0) return null;
  return e.max_profit / Math.abs(e.max_loss);
}

export interface Scale { (v: number): number; invert: (p: number) => number; domain: [number, number] }

export function linear(domain: [number, number], range: [number, number]): Scale {
  const [d0, d1] = domain;
  const [r0, r1] = range;
  const k = d1 === d0 ? 0 : (r1 - r0) / (d1 - d0);
  const f = ((v: number) => r0 + (v - d0) * k) as Scale;
  f.invert = (p: number) => (k === 0 ? d0 : d0 + (p - r0) / k);
  f.domain = domain;
  return f;
}

/** The y-domain for the curves on show, padded and always containing zero (the zero line is the reference). */
export function yDomain(series: (number[] | null | undefined)[], pad = 0.08): [number, number] {
  const all = series.flatMap((s) => s ?? []).filter(Number.isFinite);
  let lo = Math.min(0, ...all);
  let hi = Math.max(0, ...all);
  if (lo === hi) { lo -= 1; hi += 1; }
  const span = hi - lo;
  return [lo - span * pad, hi + span * pad];
}

export function linePath(xs: number[], ys: number[], sx: Scale, sy: Scale): string {
  return xs.map((x, i) => `${i ? "L" : "M"}${sx(x).toFixed(2)},${sy(ys[i]).toFixed(2)}`).join("");
}

/**
 * The areas between a curve and zero, split by sign at the exact crossings (linear between grid points) - profit
 * shaded above, loss below. Returns closed SVG paths.
 */
export function signAreas(xs: number[], ys: number[], sx: Scale, sy: Scale): { profit: string[]; loss: string[] } {
  const profit: string[] = [];
  const loss: string[] = [];
  let run: [number, number][] = [];
  let sign = 0;
  const close = () => {
    if (run.length > 1 && sign !== 0) {
      const path = `M${sx(run[0][0]).toFixed(2)},${sy(0).toFixed(2)}` + run.map(([x, y]) => `L${sx(x).toFixed(2)},${sy(y).toFixed(2)}`).join("")
        + `L${sx(run[run.length - 1][0]).toFixed(2)},${sy(0).toFixed(2)}Z`;
      (sign > 0 ? profit : loss).push(path);
    }
    run = [];
  };
  for (let i = 0; i < xs.length; i++) {
    const s = Math.sign(ys[i]);
    if (i > 0 && s !== 0 && sign !== 0 && s !== sign) {
      const t = ys[i - 1] / (ys[i - 1] - ys[i]);
      const x0 = xs[i - 1] + (xs[i] - xs[i - 1]) * t;
      run.push([x0, 0]);
      close();
      run.push([x0, 0]);
    }
    if (s !== 0) sign = s;
    run.push([xs[i], ys[i]]);
  }
  close();
  return { profit, loss };
}

/** The probability cone behind the curves: one and two expected moves either side of spot (lognormal 1 sd ~ 68 %). */
export function cone(spot: number, expectedMove: number): { inner: [number, number]; outer: [number, number] } {
  return { inner: [spot - expectedMove, spot + expectedMove], outer: [spot - 2 * expectedMove, spot + 2 * expectedMove] };
}

/** A template's expiry payoff shape for its gallery thumbnail - premiums left out (the shape, not the money). A
 * calendar or diagonal has no expiry shape (its far leg is still alive at the near expiry): null, and the gallery
 * says "two expiries" instead of drawing something misleading. */
export function thumbnailShape(t: TemplateInfo, points = 41): number[] | null {
  if (t.two_expiries) return null;
  const near = t.legs;
  const xs = Array.from({ length: points }, (_, i) => -3 + (6 * i) / (points - 1));
  return xs.map((x) => near.reduce((sum, l) => {
    const sign = l.direction === "BUY" ? 1 : -1;
    const v = l.option_type === "FUT" ? x : l.option_type === "CE" ? Math.max(x - l.offset, 0) : Math.max(l.offset - x, 0);
    return sum + sign * v * l.lots;
  }, 0));
}

export function money(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "–";
  const s = Math.abs(v) >= 100 ? Math.round(Math.abs(v)).toLocaleString("en-IN") : Math.abs(v).toFixed(2);
  return `${v < 0 ? "−" : ""}₹${s}`;
}

/** Days from `from` to `to` (YYYY-MM-DD), never negative. */
export function daysBetween(from: string, to: string): number {
  const a = Date.parse(`${from}T00:00:00Z`);
  const b = Date.parse(`${to}T00:00:00Z`);
  return Number.isFinite(a) && Number.isFinite(b) ? Math.max(0, Math.round((b - a) / 86400e3)) : 0;
}

/** What an evaluation was asked with, besides the legs (the sliders and the spot). */
export interface EvalParams { spot: number; daysForward: number; ivShift: number }

/** The legs an evaluation was asked for, by id - the server answers in request order, so its i-th leg is sent[i]. */
export interface Evaluated { evaluation: Evaluation; sent: Leg[]; params?: EvalParams }

/** The contract a model price belongs to. Time passing does not change it, so a model premium is not re-priced (and
 * the page does not re-evaluate) just because the clock moved between two replies. */
export function contractKey(l: Leg): string {
  return [l.option_type, l.strike, l.expiry, l.iv ?? "solve", l.direction].join("|");
}

/** Same contract: the fields a model price depends on. */
function sameContract(a: Leg, b: Leg): boolean {
  return a.option_type === b.option_type && a.strike === b.strike && a.expiry === b.expiry && a.iv === b.iv && a.direction === b.direction;
}

/** Each current leg's evaluated row, matched by id and only while the leg is still the contract that was evaluated
 * (after a removal, a side switch or a strike move, a row never shows another leg's numbers). */
export function evaluatedById(current: Leg[], ev: Evaluated | null): Record<string, Evaluation["legs"][number]> {
  const out: Record<string, Evaluation["legs"][number]> = {};
  if (!ev || ev.evaluation.legs.length !== ev.sent.length) return out;
  const now = new Map(current.map((l) => [l.id, l]));
  ev.sent.forEach((s, i) => {
    const c = now.get(s.id);
    // a premium only moves a leg's greeks when its IV is solved from it
    if (c && sameContract(c, s) && c.lots === s.lots && (c.iv != null || c.premium === s.premium)) out[s.id] = ev.evaluation.legs[i];
  });
  return out;
}

/** Whether an evaluation describes exactly the current legs (same ids, order and every input) and, when given, the
 * current spot and sliders. */
export function isCurrent(current: Leg[], ev: Evaluated | null, params?: EvalParams): boolean {
  if (params && ev?.params && (ev.params.spot !== params.spot || ev.params.daysForward !== params.daysForward || ev.params.ivShift !== params.ivShift)) {
    return false;
  }
  return !!ev && ev.sent.length === current.length && ev.sent.every((s, i) => {
    const c = current[i];
    return c.id === s.id && sameContract(c, s) && c.lots === s.lots && c.lot_size === s.lot_size && c.premium === s.premium;
  });
}

/**
 * Legs whose premium is the model's are re-priced from an evaluation after a strike, expiry, IV or side edit (the
 * old model price belongs to another contract). Matched by id, and only when the leg is still the contract that was
 * evaluated - a reply that lands after a further drag never writes an old strike's price - and only when the contract
 * changed since the premium was set (`priced_for`): time decay alone never re-prices, so the page cannot loop on the
 * clock. Typed premiums are never touched. Returns the same array when nothing changed.
 */
export function repriceModelLegs(legs: Leg[], ev: Evaluated): Leg[] {
  if (ev.evaluation.legs.length !== ev.sent.length) return legs;
  const sent = new Map(ev.sent.map((s, i) => [s.id, { s, t: ev.evaluation.legs[i].theoretical }]));
  let changed = false;
  const out = legs.map((l) => {
    const hit = sent.get(l.id);
    if (!hit || l.premium_source !== "model" || !sameContract(l, hit.s)) return l;
    const key = contractKey(l);
    if (l.priced_for === key || !Number.isFinite(hit.t)) return l;
    changed = true;
    return { ...l, premium: hit.t, priced_for: key };
  });
  return changed ? out : legs;
}

/** The chart's half-width in % of spot: wide enough that every strike sits inside with a margin (a strike off the
 * chart would be drawn at its edge, at the wrong price), never under `min`, and within the server's 60 %. */
export function rangePctFor(spot: number, legs: Leg[], min = 8): number {
  if (!(spot > 0)) return min;
  const far = legs.filter((l) => l.option_type !== "FUT").reduce((m, l) => Math.max(m, Math.abs(l.strike - spot) / spot), 0);
  return Math.min(60, Math.max(min, Math.ceil(far * 100 * 1.25 + 2)));
}

/** Round axis ticks (1, 2 or 5 times a power of ten, chosen as d3 does) spanning the domain - readable money labels. */
export function niceTicks(lo: number, hi: number, count = 5): number[] {
  if (!(hi > lo)) return [lo];
  const raw = (hi - lo) / Math.max(1, count - 1);
  const pow = 10 ** Math.floor(Math.log10(raw));
  const r = raw / pow;
  const step = (r >= Math.sqrt(50) ? 10 : r >= Math.sqrt(10) ? 5 : r >= Math.sqrt(2) ? 2 : 1) * pow;
  const out: number[] = [];
  for (let k = Math.ceil(lo / step); k * step <= hi + step * 1e-9; k++) out.push(Number((k * step).toPrecision(12)) || 0);
  return out;
}
