// N2-3: pure helpers for the decision tools card - inputs to request bodies, the what-if chart geometry, and the
// prefill from the user's own figures (N2-1). The server computes every number; this only shapes them.
import type { LadderEvidence, WhatIfResult } from "../types";

export interface RuinInputs { winRatePct: string; payoff: string; riskPct: string; ruinPct: string }

export const RUIN_DEFAULTS: RuinInputs = { winRatePct: "50", payoff: "1.5", riskPct: "1", ruinPct: "50" };

/** The calculator's starting values: the user's own win rate and payoff ratio when the ladder has them. */
export function ruinPrefill(ev: LadderEvidence | null | undefined): { inputs: RuinInputs; prefilled: boolean } {
  if (!ev || ev.status !== "ok" || ev.win_rate == null || ev.payoff_ratio == null || ev.win_rate <= 0 || ev.win_rate >= 1) {
    return { inputs: RUIN_DEFAULTS, prefilled: false };
  }
  return {
    inputs: { ...RUIN_DEFAULTS, winRatePct: (ev.win_rate * 100).toFixed(1), payoff: ev.payoff_ratio.toFixed(2),
              ruinPct: ev.ruin?.ruin_pct != null ? String(ev.ruin.ruin_pct) : RUIN_DEFAULTS.ruinPct },
    prefilled: true,
  };
}

/** The request body, or null while a field is not a usable number. */
export function ruinBody(i: RuinInputs): { win_rate: number; payoff_ratio: number; risk_pct: number; ruin_pct: number } | null {
  const w = Number(i.winRatePct), b = Number(i.payoff), r = Number(i.riskPct), d = Number(i.ruinPct);
  if (![w, b, r, d].every((x) => Number.isFinite(x)) || i.winRatePct.trim() === "" || i.payoff.trim() === "" || i.riskPct.trim() === "" || i.ruinPct.trim() === "") return null;
  if (!(w > 0 && w < 100 && b > 0 && r > 0 && r < 100 && d > 0 && d <= 100)) return null;
  return { win_rate: w / 100, payoff_ratio: b, risk_pct: r, ruin_pct: d };
}

export interface ChartGeometry {
  paths: { key: "now" | "scenario" | "expiry"; d: string }[];
  zeroY: number | null; spotX: number; yMin: number; yMax: number;
}

/** SVG paths for the what-if curves in a w x h box (no margins: the caller translates). */
export function chartGeometry(r: WhatIfResult, w: number, h: number): ChartGeometry {
  const keys = (["expiry", "now", "scenario"] as const).filter((k) => r.curves[k] && r.curves[k]!.length === r.prices.length);
  const all = keys.flatMap((k) => r.curves[k]!);
  let yMin = Math.min(0, ...all), yMax = Math.max(0, ...all);
  if (yMax - yMin < 1e-9) { yMin -= 1; yMax += 1; }
  const pad = (yMax - yMin) * 0.06;
  yMin -= pad; yMax += pad;
  const x0 = r.prices[0], x1 = r.prices[r.prices.length - 1];
  const sx = (x: number) => (x1 === x0 ? 0 : ((x - x0) / (x1 - x0)) * w);
  const sy = (y: number) => h - ((y - yMin) / (yMax - yMin)) * h;
  const fmt = (n: number) => n.toFixed(1);
  const paths = keys.map((k) => ({ key: k, d: r.curves[k]!.map((y, i) => `${i === 0 ? "M" : "L"}${fmt(sx(r.prices[i]))},${fmt(sy(y))}`).join("") }));
  return { paths, zeroY: yMin <= 0 && yMax >= 0 ? sy(0) : null, spotX: sx(r.spot), yMin, yMax };
}

/** Signed rupees without decimals for the chart and the at-spot line: "+₹1,250", "-₹480", "₹0". */
export function pnl(n: number): string {
  const s = `₹${Math.abs(Math.round(n)).toLocaleString("en-IN")}`;
  return Math.round(n) > 0 ? `+${s}` : Math.round(n) < 0 ? `-${s}` : s;
}
