/**
 * CH2b (ADR-0023): the pure geometry of the drawings core. A `drawing/1` (time/price anchors) plus the chart's
 * converters becomes a list of plain shapes in pane pixels; `primitives.ts` paints them. Nothing here touches a chart
 * library, a canvas or an order, so every kind is unit-tested without a browser.
 *
 * Every number in a label comes from the anchors (prices, ratios), never from pixels.
 */
import type { DrawingV1 } from "./drawings";

/** Pane coordinates for one paint: `x` maps an anchor time (epoch seconds), `y` a price; null = cannot place it. */
export interface Converters {
  x(timeSec: number): number | null;
  y(price: number): number | null;
  width: number;
  height: number;
}

export type Dash = "solid" | "dashed" | "dotted";
export type Shape =
  | { type: "segment"; x1: number; y1: number; x2: number; y2: number; color: string; width: number; dash: Dash }
  | { type: "rect"; x: number; y: number; w: number; h: number; fill: string; stroke?: string }
  | { type: "polygon"; points: [number, number][]; fill: string }
  | { type: "text"; x: number; y: number; text: string; color: string; align: "left" | "right" | "center"; baseline: "top" | "middle" | "bottom" };

export const DEFAULT_COLOR = "#2962ff";
export const FIB_RETRACEMENT_LEVELS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
export const FIB_EXTENSION_LEVELS = [0, 0.618, 1, 1.618, 2.618];
const PROFIT = "#16a34a";
const LOSS = "#dc2626";

const toSec = (iso: string | undefined) => (iso ? Math.floor(Date.parse(iso) / 1000) : NaN);

/** A colour with a fill alpha (`#rrggbb` -> `#rrggbbaa`); anything else is returned unchanged. */
export function withAlpha(color: string, alpha: number): string {
  if (!/^#[0-9a-fA-F]{6}$/.test(color)) return color;
  return color + Math.round(Math.min(1, Math.max(0, alpha)) * 255).toString(16).padStart(2, "0");
}

/**
 * The fractional bar index (lightweight-charts "logical") of a time, from the ascending bar times on the chart.
 * Between two bars it interpolates, so an anchor drawn on another timeframe lands where it belongs; beyond either
 * end it extrapolates with the nearest bar spacing, so a future anchor (a target) still has a place.
 */
export function timeToLogical(times: readonly number[], t: number): number | null {
  const n = times.length;
  if (!Number.isFinite(t) || n === 0) return null;
  if (n === 1) return t === times[0] ? 0 : null;
  if (t <= times[0]) return (t - times[0]) / (times[1] - times[0] || 1);
  if (t >= times[n - 1]) return n - 1 + (t - times[n - 1]) / (times[n - 1] - times[n - 2] || 1);
  let lo = 0;
  let hi = n - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (times[mid] <= t) lo = mid; else hi = mid;
  }
  return lo + (t - times[lo]) / (times[hi] - times[lo] || 1);
}

/** Fibonacci levels with their prices. Retracement: level 0 at the second anchor, 1 at the first (a pullback from
 * the move's end). Extension: the first move (anchor 1 -> 2) projected from the third anchor. */
export function fibLevels(d: DrawingV1): { level: number; price: number }[] {
  const p = d.anchors.map((a) => a.p ?? NaN);
  if (d.kind === "fib_retracement" && p.length === 2) {
    return (d.levels ?? FIB_RETRACEMENT_LEVELS).map((level) => ({ level, price: p[1] - (p[1] - p[0]) * level }));
  }
  if (d.kind === "fib_extension" && p.length === 3) {
    return (d.levels ?? FIB_EXTENSION_LEVELS).map((level) => ({ level, price: p[2] + (p[1] - p[0]) * level }));
  }
  return [];
}

export interface PositionStats { entry: number; stop: number; target: number; risk: number; reward: number; rr: number | null; problem: string | null }

/** Entry/stop/target arithmetic for the position tools (anchors: entry, stop, target). A stop or target on the wrong
 * side of the entry is reported, not silently flipped. */
export function positionStats(d: DrawingV1): PositionStats | null {
  if (d.kind !== "long_position" && d.kind !== "short_position") return null;
  const [entry, stop, target] = d.anchors.map((a) => a.p ?? NaN);
  if (![entry, stop, target].every(Number.isFinite)) return null;
  const long = d.kind === "long_position";
  const risk = long ? entry - stop : stop - entry;
  const reward = long ? target - entry : entry - target;
  let problem: string | null = null;
  if (risk <= 0) problem = long ? "stop must be below the entry" : "stop must be above the entry";
  else if (reward <= 0) problem = long ? "target must be above the entry" : "target must be below the entry";
  return { entry, stop, target, risk, reward, rr: problem ? null : reward / risk, problem };
}

const fmt = (v: number) => (Math.abs(v) >= 1000 ? v.toFixed(1) : v.toFixed(2));
const pct = (v: number, base: number) => `${v >= 0 ? "+" : ""}${((v / base) * 100).toFixed(2)}%`;

/** A segment through two points, extended to the pane's left and/or right edge. */
export function extendSegment(x1: number, y1: number, x2: number, y2: number, width: number, extend: "none" | "left" | "right" | "both") {
  if (extend === "none" || x1 === x2) return { x1, y1, x2, y2 };
  const slope = (y2 - y1) / (x2 - x1);
  const [lx, ly, rx, ry] = x1 < x2 ? [x1, y1, x2, y2] : [x2, y2, x1, y1];
  const left = extend === "left" || extend === "both";
  const right = extend === "right" || extend === "both";
  return {
    x1: left ? 0 : lx, y1: left ? ly - slope * lx : ly,
    x2: right ? width : rx, y2: right ? ry + slope * (width - rx) : ry,
  };
}

/** The shapes of one drawing in pane pixels; [] when an anchor cannot be placed (no bars yet, a bad anchor). */
export function shapes(d: DrawingV1, c: Converters): Shape[] {
  const style = d.style ?? {};
  const color = style.color ?? DEFAULT_COLOR;
  const width = Math.min(4, Math.max(1, style.width ?? 1));
  const dash: Dash = style.line ?? "solid";
  const xs = d.anchors.map((a) => (a.t ? c.x(toSec(a.t)) : null));
  const ys = d.anchors.map((a) => (a.p != null && Number.isFinite(a.p) ? c.y(a.p) : null));
  const placed = (i: number, need: "tp" | "t" | "p") =>
    (need.includes("t") ? xs[i] != null && Number.isFinite(xs[i]) : true) && (need.includes("p") ? ys[i] != null && Number.isFinite(ys[i]) : true);
  const seg = (x1: number, y1: number, x2: number, y2: number, over: Partial<Extract<Shape, { type: "segment" }>> = {}): Shape =>
    ({ type: "segment", x1, y1, x2, y2, color, width, dash, ...over });
  const label = (x: number, y: number, text: string, align: "left" | "right" | "center" = "left", baseline: "top" | "middle" | "bottom" = "bottom", tint = color): Shape =>
    ({ type: "text", x, y, text, color: tint, align, baseline });

  switch (d.kind) {
    case "hline": {
      if (!placed(0, "p")) return [];
      const y = ys[0] as number;
      return [seg(0, y, c.width, y)];
    }
    case "vline": {
      if (!placed(0, "t")) return [];
      const x = xs[0] as number;
      return [seg(x, 0, x, c.height)];
    }
    case "trendline":
    case "ray":
    case "measure": {
      if (!placed(0, "tp") || !placed(1, "tp")) return [];
      const [x1, x2, y1, y2] = [xs[0], xs[1], ys[0], ys[1]] as number[];
      if (d.kind === "ray" && x1 === x2) {                    // a vertical ray runs from its first anchor past the second
        return [seg(x1, y1, x2, y2 === y1 ? y1 : y2 < y1 ? 0 : c.height)];
      }
      // A ray starts at its first anchor and runs through the second, whichever side that is on.
      const mode = d.kind === "ray" ? (x2 > x1 ? "right" : "left") : d.kind === "measure" ? "none" : style.extend ?? "none";
      const s = extendSegment(x1, y1, x2, y2, c.width, mode);
      const out: Shape[] = [seg(s.x1, s.y1, s.x2, s.y2)];
      if (d.kind === "measure") {
        const [p1, p2] = [d.anchors[0].p as number, d.anchors[1].p as number];
        out.push(label(x2, y2 - 4, `${p2 - p1 >= 0 ? "+" : ""}${fmt(p2 - p1)} (${pct(p2 - p1, p1)})`, x2 >= x1 ? "left" : "right"));
      }
      return out;
    }
    case "rectangle": {
      if (!placed(0, "tp") || !placed(1, "tp")) return [];
      const [x1, x2, y1, y2] = [xs[0], xs[1], ys[0], ys[1]] as number[];
      return [{ type: "rect", x: Math.min(x1, x2), y: Math.min(y1, y2), w: Math.abs(x2 - x1), h: Math.abs(y2 - y1), fill: withAlpha(color, 0.15), stroke: color }];
    }
    case "channel": {
      if (![0, 1, 2].every((i) => placed(i, "tp"))) return [];
      const [x1, x2, x3, y1, y2, y3] = [xs[0], xs[1], xs[2], ys[0], ys[1], ys[2]] as number[];
      const baseAtX3 = x1 === x2 ? y1 : y1 + ((y2 - y1) * (x3 - x1)) / (x2 - x1);
      const dy = y3 - baseAtX3;                                  // the third anchor sets the parallel line's offset
      const a = extendSegment(x1, y1, x2, y2, c.width, style.extend ?? "none");
      const b = extendSegment(x1, y1 + dy, x2, y2 + dy, c.width, style.extend ?? "none");
      return [
        { type: "polygon", points: [[a.x1, a.y1], [a.x2, a.y2], [b.x2, b.y2], [b.x1, b.y1]], fill: withAlpha(color, 0.1) },
        seg(a.x1, a.y1, a.x2, a.y2), seg(b.x1, b.y1, b.x2, b.y2),
        seg((a.x1 + b.x1) / 2, (a.y1 + b.y1) / 2, (a.x2 + b.x2) / 2, (a.y2 + b.y2) / 2, { dash: "dashed", width: 1 }),
      ];
    }
    case "fib_retracement":
    case "fib_extension": {
      const n = d.kind === "fib_retracement" ? 2 : 3;
      if (!Array.from({ length: n }, (_, i) => placed(i, "tp")).every(Boolean)) return [];
      const left = d.kind === "fib_retracement" ? Math.min(xs[0] as number, xs[1] as number) : (xs[2] as number);
      const span = Math.abs((xs[1] as number) - (xs[0] as number));
      const right = style.extend === "right" || style.extend === "both" ? c.width
        : d.kind === "fib_retracement" ? Math.max(xs[0] as number, xs[1] as number) : left + span;
      const out: Shape[] = [];
      for (const { level, price } of fibLevels(d)) {
        const y = c.y(price);
        if (y == null || !Number.isFinite(y)) continue;
        out.push(seg(left, y, right, y, { width: 1 }), label(left + 2, y - 2, `${level} (${fmt(price)})`));
      }
      return out;
    }
    case "text": {
      if (!placed(0, "tp")) return [];
      return [label(xs[0] as number, ys[0] as number, d.text ?? "", "left", "middle")];
    }
    case "long_position":
    case "short_position": {
      const stats = positionStats(d);
      if (!stats || !placed(0, "tp") || !placed(1, "p") || !placed(2, "p")) return [];
      const [x, yEntry, yStop, yTarget] = [xs[0], ys[0], ys[1], ys[2]] as number[];
      const w = Math.max(0, c.width - x);                       // no end time in drawing/1: the box runs to the edge
      const box = (ya: number, yb: number, tint: string): Shape => ({ type: "rect", x, y: Math.min(ya, yb), w, h: Math.abs(yb - ya), fill: withAlpha(tint, 0.18) });
      const headline = stats.problem ?? `R:R ${(stats.rr as number).toFixed(2)}`;
      return [
        box(yEntry, yTarget, PROFIT), box(yEntry, yStop, LOSS), seg(x, yEntry, x + w, yEntry, { width: 1 }),
        label(x + 4, yTarget, `Target ${fmt(stats.target)} (${pct(stats.target - stats.entry, stats.entry)})`, "left", yTarget < yEntry ? "top" : "bottom", PROFIT),
        label(x + 4, yStop, `Stop ${fmt(stats.stop)} (${pct(stats.stop - stats.entry, stats.entry)})`, "left", yStop < yEntry ? "top" : "bottom", LOSS),
        label(x + 4, yEntry - 2, headline, "left", "bottom", stats.problem ? LOSS : color),
      ];
    }
    default:
      return [];
  }
}
