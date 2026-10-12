// N2-7: pure helpers for the builder's adjustment drawer. The server prices and evaluates everything; this shapes the
// request and the comparison rows.
import type { TFunction } from "i18next";
import type { AdjustRequest, AdjustResult, AdjustSide } from "../optionsBuilder/api";

export interface AdjustForm { kind: AdjustRequest["kind"]; index: string; steps: string; expiry: string; width: string; side: "CE" | "PE"; newIv: string }

export const ADJUST_DEFAULTS: AdjustForm = { kind: "roll", index: "0", steps: "1", expiry: "", width: "2", side: "CE", newIv: "" };

/** The adjustment's own fields, or null while one is not a usable number. */
export function adjustFields(f: AdjustForm): Partial<AdjustRequest> | null {
  const int = (s: string) => (/^-?\d+$/.test(s.trim()) ? Number(s) : null);
  const extra: Partial<AdjustRequest> = {};
  if (f.newIv.trim()) {
    const iv = Number(f.newIv);
    if (!(iv > 0 && iv < 500)) return null;
    extra.new_iv = iv / 100;
  }
  if (f.kind === "roll") {
    const index = int(f.index), steps = int(f.steps);
    if (index == null || steps == null || (steps === 0 && !f.expiry)) return null;
    return { ...extra, index, steps, ...(f.expiry ? { expiry: f.expiry } : {}) };
  }
  if (f.kind === "add_hedge") {
    const index = int(f.index), width = int(f.width);
    return index == null || width == null || width < 1 ? null : { ...extra, index, width_steps: width };
  }
  if (f.kind === "iron_condor") {
    const width = int(f.width);
    return width == null || width < 1 ? null : { ...extra, width_steps: width };
  }
  return { side: f.side };
}

const money = (n: number | null | undefined) =>
  n == null ? "-" : `${n < 0 ? "-" : ""}₹${Math.abs(Math.round(n)).toLocaleString("en-IN")}`;

/** [label, before, after] rows for the comparison table. */
export function compareRows(r: AdjustResult, t: TFunction<"progression">): [string, string, string][] {
  const ext = (s: AdjustSide, which: "max_profit" | "max_loss") => {
    if (!s.extremes) return "-";
    const unb = which === "max_profit" ? s.extremes.unbounded_profit : s.extremes.unbounded_loss;
    return unb ? t("adjust.unbounded") : money(s.extremes[which]);
  };
  const be = (s: AdjustSide) => (s.breakevens.length ? s.breakevens.map((x) => x.toLocaleString("en-IN")).join(", ") : "-");
  const pop = (s: AdjustSide) => (s.pop == null ? "-" : `${(s.pop * 100).toFixed(1)}%`);
  const g = (s: AdjustSide, k: keyof AdjustSide["greeks"], d = 2) => s.greeks[k].toFixed(d);
  return [
    [t("adjust.maxProfit"), ext(r.before, "max_profit"), ext(r.after, "max_profit")],
    [t("adjust.maxLoss"), ext(r.before, "max_loss"), ext(r.after, "max_loss")],
    [t("adjust.breakevens"), be(r.before), be(r.after)],
    [t("adjust.pop"), pop(r.before), pop(r.after)],
    [t("adjust.netPremium"), money(r.before.net_premium), money(r.after.net_premium)],
    [t("adjust.delta"), g(r.before, "delta"), g(r.after, "delta")],
    [t("adjust.theta"), g(r.before, "theta"), g(r.after, "theta")],
    [t("adjust.vega"), g(r.before, "vega"), g(r.after, "vega")],
  ];
}

export function changeLine(c: AdjustResult["changes"][number], t: TFunction<"progression">): string {
  return t("adjust.change", { action: t(c.action === "open" ? "adjust.open" : "adjust.close"), direction: c.leg.direction, type: c.leg.option_type,
                              strike: c.leg.strike.toLocaleString("en-IN"), expiry: c.leg.expiry, lots: c.leg.lots, price: c.price.toFixed(2) });
}

export function moneyLine(r: AdjustResult, t: TFunction<"progression">): string {
  return t("adjust.money", { realised: money(r.realised), cash: money(r.cash) });
}

/** Two polylines (before dashed, after solid) in a w x h box. */
export function curvePaths(r: AdjustResult, w: number, h: number): { before: string; after: string; zeroY: number | null } {
  const all = [...r.before.at_expiry, ...r.after.at_expiry];
  let lo = Math.min(0, ...all), hi = Math.max(0, ...all);
  if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
  const x0 = r.prices[0], x1 = r.prices[r.prices.length - 1];
  const sx = (x: number) => (x1 === x0 ? 0 : ((x - x0) / (x1 - x0)) * w);
  const sy = (y: number) => h - ((y - lo) / (hi - lo)) * h;
  const path = (ys: number[]) => ys.map((y, i) => `${i ? "L" : "M"}${sx(r.prices[i]).toFixed(1)},${sy(y).toFixed(1)}`).join("");
  return { before: path(r.before.at_expiry), after: path(r.after.at_expiry), zeroY: sy(0) };
}
