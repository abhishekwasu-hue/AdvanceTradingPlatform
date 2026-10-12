// N2-1: pure helpers for the ladder evidence block. The server computes every figure from the user's own closed PAPER
// trades (app/decision_tools/own_stats.py); this only formats them. Facts only - no advice wording.
import type { TFunction } from "i18next";
import type { LadderCriterion, LadderEvidence } from "../types";

/** 0.6 -> "60.0%"; null -> "-". */
export function pct(x: number | null | undefined, digits = 1): string {
  return x == null ? "-" : `${(x * 100).toFixed(digits)}%`;
}

/** Rupees with two decimals, signed when asked: "₹1,234.50", "+₹80.00", "-₹20.00". */
export function rupees(x: number | null | undefined, signed = false): string {
  if (x == null) return "-";
  const body = `₹${Math.abs(x).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  if (x < 0) return `-${body}`;
  return signed && x > 0 ? `+${body}` : body;
}

/** An upper bound without a misleading "0.00%": below 0.01 % (or underflowed to 0) it reads "< 0.01%". */
export function probability(p: number | null | undefined): string {
  if (p == null) return "-";
  if (p < 0.0001) return "< 0.01%";
  return `${(p * 100).toFixed(2)}%`;
}

/** A ratio with two decimals, or "-". */
export function ratio(x: number | null | undefined): string {
  return x == null ? "-" : x.toFixed(2);
}

export function kellyLine(ev: LadderEvidence, t: TFunction<"progression">): string {
  const k = ev.kelly;
  if (!k) return "";
  const p = pct(k.fraction);
  if (k.status === "ok") return t("evidence.kelly.ok", { pct: p, formula: k.formula });
  if (k.status === "negative") return t("evidence.kelly.negative", { pct: p });
  return t(`evidence.kelly.${k.status}`);
}

export function ruinLine(ev: LadderEvidence, t: TFunction<"progression">): string {
  const r = ev.ruin;
  if (!r) return "";
  const args = { pct: probability(r.probability), ruinPct: r.ruin_pct, distance: rupees(r.distance) };
  return t(`evidence.ruin.${r.status}`, args);
}

/** The discipline criterion's week strip: "3 of 4 weeks scored", or why it is not measured; "" when complete. */
export function disciplineWeeksNote(c: LadderCriterion, t: TFunction<"progression">): string {
  if (c.status === "engine_off") return t("discipline.engineOff");
  if (c.weeks_needed == null) return "";
  const have = c.weeks?.length ?? 0;
  return have < c.weeks_needed ? t("discipline.needed", { have, needed: c.weeks_needed }) : "";
}

/** "2026-W41" -> "W41" for a compact chip (the year stays in the title attribute). */
export function shortWeek(label: string): string {
  const m = /^\d{4}-(W\d{2})$/.exec(label);
  return m ? m[1] : label;
}
