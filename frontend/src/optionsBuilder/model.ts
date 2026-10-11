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
  extremes: {
    max_profit: number | null; max_loss: number | null; unbounded_profit: boolean; unbounded_loss: boolean;
    max_profit_at?: number | null; max_loss_at?: number | null;
  } | null;
  profitable: [number | null, number | null][] | null;
  breakevens: number[] | null;
  summary: { expiry: string; t_years: number; sigma: number; pop: number; expected_move: number; expected_pnl: number; greeks: Greeks; method: string; model: string };
  legs: (Omit<Leg, "id"> & { iv: number; iv_source?: string; greeks: Greeks; theoretical: number })[];
  disclaimer: string;
  /** Z5: the metrics card's money lines, from the engine (the page computes none of them) */
  net_premium: number;                       // + credit received, − debit paid; futures left out
  reward_to_risk: number | null;
  margin: Margin;
  costs: Costs;
}

/** An estimate, always: `value` null when only the broker can say (undefined risk, calendars, futures). */
export interface Margin { value: number | null; estimated: true; basis: string; note: string }
export interface CostParts { stt: number; exch: number; sebi: number; stamp: number; brokerage: number; tax: number; total: number }
export interface Costs { to_open: CostParts; round_trip: number; brokerage_per_order: number; orders: number; as_of: string; note: string }

/** The basket shown before "Place as basket" (hedge first), from the engine. Review only in this release. */
export interface BasketOrder {
  seq: number; leg: number; side: Direction; option_type: OptionKind; strike: number | null; expiry: string; lots: number; lot_size: number;
  quantity: number; price: number; key: string;
}
export interface Basket { underlying: string; orders: BasketOrder[]; net_premium: number; placeable: boolean; why_not: string }

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
export interface EvalParams { spot: number; daysForward: number; ivShift: number; brokerage?: number }

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

/** The value of a curve at any price, linear between grid points (a repeated grid price, possible on a low-priced
 * underlying, takes the later point). Null without a curve; the curve's end values outside the grid. */
export function seriesAt(xs: number[], series: number[] | null | undefined, price: number): number | null {
  if (!series || !xs.length) return null;
  const i = xs.findIndex((x) => x >= price);
  if (i < 0) return series[series.length - 1];
  if (i === 0) return series[0];
  if (xs[i] === xs[i - 1]) return series[i];
  const t = (price - xs[i - 1]) / (xs[i] - xs[i - 1]);
  return series[i - 1] + (series[i] - series[i - 1]) * t;
}

/** The strikes a handle may take on the chart on show: the chart's own range on the instrument's grid. */
export function strikeBounds(xs: number[], step: number): [number, number] {
  const lo = Math.max(step, snapStrike(xs[0], step));
  return [lo, Math.max(lo, snapStrike(xs[xs.length - 1], step))];
}

/**
 * Strike-drag math: the strike under a pointer at `clientX` over a chart drawn `rectWidth` px wide from `rectLeft`
 * (the SVG's viewBox is `viewWidth` wide; `sx` maps price to viewBox x). Snapped to the grid and held inside
 * [lo, hi], so a pointer past the edge keeps the strike at the edge. Deterministic: the same pointer gives the same
 * strike whatever the previous one was.
 */
export function strikeAtPointer(clientX: number, rectLeft: number, rectWidth: number, viewWidth: number, sx: Scale, step: number,
  [lo, hi]: [number, number]): number | null {
  if (!(rectWidth > 0) || !Number.isFinite(clientX)) return null;
  const price = sx.invert(((clientX - rectLeft) / rectWidth) * viewWidth);
  return Math.min(hi, Math.max(lo, snapStrike(price, step)));
}

/** A key pressed on a focused strike handle: one step with the arrows, five with Page Up / Down, the ends with Home /
 * End; null for any other key. Always on the grid and inside [lo, hi]. */
export function strikeForKey(key: string, strike: number, step: number, [lo, hi]: [number, number]): number | null {
  const moves: Record<string, number> = { ArrowLeft: -1, ArrowDown: -1, ArrowRight: 1, ArrowUp: 1, PageDown: -5, PageUp: 5 };
  let next: number;
  if (key in moves) next = snapStrike(strike + moves[key] * step, step);
  else if (key === "Home") next = lo;
  else if (key === "End") next = hi;
  else return null;
  return Math.min(hi, Math.max(lo, next));
}

/** The radius, in viewBox units, of a strike handle's invisible hit circle so that it is at least `minPx` CSS px
 * across however small the chart is drawn (PW2: 44 px touch targets on a phone). */
export function hitRadius(renderedWidth: number, viewWidth: number, minPx = 44, floor = 10): number {
  if (!(renderedWidth > 0)) return floor;
  return Math.max(floor, (minPx / 2) * (viewWidth / renderedWidth));
}

/** Where the max-loss line goes: the y of the worst expiry P&L when it is finite, a loss, and inside the chart's y
 * domain (an unbounded loss has no line; the shading below zero still shows it). */
export function maxLossY(extremes: Evaluation["extremes"], sy: Scale): number | null {
  const v = extremes?.max_loss;
  if (!extremes || extremes.unbounded_loss || v == null || !Number.isFinite(v) || v >= 0) return null;
  const [d0, d1] = sy.domain;
  return v >= Math.min(d0, d1) && v <= Math.max(d0, d1) ? sy(v) : null;
}

/**
 * The page's grid areas. Desktop (wide): instrument and templates on top; payoff then legs on the left; metrics and
 * the actions on the right; adjustments at the bottom. Phone: once there is a strategy the payoff comes first and the
 * legs below it (spec §3.3), then the numbers, and the instrument and templates after; before there is one, the inputs
 * and the gallery lead (there is nothing to draw yet).
 */
export function builderAreas(wide: boolean, hasLegs: boolean): string {
  if (wide) return '"inst inst" "tmpl tmpl" "pay side" "legs side" "drawer drawer"';
  return hasLegs ? '"pay" "legs" "side" "tmpl" "inst" "drawer"' : '"inst" "tmpl" "pay" "legs" "side" "drawer"';
}

/** The name the basket keys use: the trader's label, upper-cased; null when it is not one word of letters, digits
 * and & _ . - (the server's rule). */
export function basketUnderlying(label: string): string | null {
  const u = label.trim().toUpperCase();
  return /^[A-Z0-9&_.-]{1,40}$/.test(u) ? u : null;
}

/** "BUY 22,000 CE 16 Mar" - a basket row in plain words. */
export function contractLabel(o: Pick<BasketOrder, "side" | "option_type" | "strike" | "expiry">): string {
  const d = new Date(`${o.expiry}T00:00:00Z`);
  const day = Number.isFinite(d.getTime()) ? d.toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" }) : o.expiry;
  return o.option_type === "FUT" ? `${o.side} FUT ${day}` : `${o.side} ${Math.round(o.strike ?? 0).toLocaleString("en-IN")} ${o.option_type} ${day}`;
}
