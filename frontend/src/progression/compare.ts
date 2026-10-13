// N2-8: pure helpers for "compare up to three strategies" in the builder. Each strategy is evaluated by the existing
// engine (POST /api/options-builder/evaluate) at the same spot, date, range and points; this lays the numbers side by
// side and the expiry curves on one scale. The page computes no figure itself.
import type { TFunction } from "i18next";
import type { Evaluation, Leg } from "../optionsBuilder/model";

export const MAX_SLOTS = 3;
export const COMPARE_POINTS = 121;
export const COMPARE_RANGE_PCT = 10;

/** `underlying` is set for a saved strategy: the one it was saved on, checked against the page's before it is added. */
export interface CompareSlot { key: string; name: string; legs: Leg[]; underlying?: string }

const sym = (u: string | null | undefined) => (u ?? "").trim().toUpperCase();

/** True when a saved strategy is on another underlying than the page: evaluated at the page's spot it would be priced
 *  thousands of points away from its strikes, so it is refused rather than shown with absurd numbers. */
export function otherUnderlying(slot: CompareSlot, pageUnderlying: string): boolean {
  return slot.underlying !== undefined && sym(slot.underlying) !== sym(pageUnderlying);
}

/** A stable signature of a strategy's legs (ids and premium source left out), to refuse adding the same one twice. */
export function signature(legs: Leg[]): string {
  return legs.map((l) => `${l.direction}|${l.option_type}|${l.strike}|${l.expiry}|${l.lots}|${l.lot_size}|${l.premium}`).sort().join(";");
}

export type Refusal = "full" | "same" | "underlying";

/** The slots with `slot` added, or a reason it was not ("full" | "same" | "underlying"). */
export function addSlot(slots: CompareSlot[], slot: CompareSlot, pageUnderlying = ""): { slots: CompareSlot[]; refused: Refusal | null } {
  if (otherUnderlying(slot, pageUnderlying)) return { slots, refused: "underlying" };
  if (slots.some((s) => signature(s.legs) === signature(slot.legs))) return { slots, refused: "same" };
  if (slots.length >= MAX_SLOTS) return { slots, refused: "full" };
  return { slots: [...slots, slot], refused: null };
}

const money = (n: number | null | undefined) =>
  n == null ? "-" : `${n < 0 ? "-" : n > 0 ? "+" : ""}₹${Math.abs(Math.round(n)).toLocaleString("en-IN")}`;

/** [label, ...one value per strategy] rows. */
export function compareTable(evals: Evaluation[], t: TFunction<"progression">): string[][] {
  const ext = (e: Evaluation, k: "max_profit" | "max_loss") => {
    if (!e.extremes) return t("compare.calendar");
    if (k === "max_profit" ? e.extremes.unbounded_profit : e.extremes.unbounded_loss) return t("compare.unbounded");
    return money(e.extremes[k]);
  };
  const row = (label: string, f: (e: Evaluation) => string) => [label, ...evals.map(f)];
  return [
    row(t("compare.netPremium"), (e) => money(e.net_premium)),
    row(t("compare.maxProfit"), (e) => ext(e, "max_profit")),
    row(t("compare.maxLoss"), (e) => ext(e, "max_loss")),
    row(t("compare.rr"), (e) => (e.reward_to_risk == null ? "-" : e.reward_to_risk.toFixed(2))),
    row(t("compare.pop"), (e) => `${(e.summary.pop * 100).toFixed(1)}%`),
    row(t("compare.margin"), (e) => (e.margin.value == null ? t("compare.brokerOnly") : money(e.margin.value).replace("+", ""))),
    row(t("compare.theta"), (e) => money(e.summary.greeks.theta)),
    row(t("compare.breakevens"), (e) => (e.breakevens?.length ? e.breakevens.map((x) => Math.round(x).toLocaleString("en-IN")).join(", ") : "-")),
    row(t("compare.expectedMove"), (e) => `±${Math.round(e.summary.expected_move).toLocaleString("en-IN")}`),
  ];
}

/** One path per strategy on a shared y-scale (the expiry payoff, or the near-expiry curve for a calendar). */
export function overlay(evals: Evaluation[], w: number, h: number): { paths: string[]; zeroY: number | null } {
  const series = evals.map((e) => e.at_expiry ?? e.on_date);
  const all = series.flat();
  if (!all.length) return { paths: [], zeroY: null };
  let lo = Math.min(0, ...all), hi = Math.max(0, ...all);
  if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
  const prices = evals[0].prices;
  const x0 = prices[0], x1 = prices[prices.length - 1];
  const sx = (x: number) => (x1 === x0 ? 0 : ((x - x0) / (x1 - x0)) * w);
  const sy = (y: number) => h - ((y - lo) / (hi - lo)) * h;
  const paths = series.map((ys, k) => ys.map((y, i) => `${i ? "L" : "M"}${sx(evals[k].prices[i]).toFixed(1)},${sy(y).toFixed(1)}`).join(""));
  return { paths, zeroY: sy(0) };
}
