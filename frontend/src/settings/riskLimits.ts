/**
 * M64 (owner order 30 Oct + amendment): Settings › Risk limits - the one place the trader's order-time risk checks live.
 * Every check is a switch, OFF until he turns it on, and its value. With all of them off an order is the lots he typed and
 * only the broker / exchange checks and the kill switch apply. Exits are never checked. The server sends every check
 * with its words, units and bounds (GET /api/risk-settings/limits), so this page and the engine never disagree.
 */
import { request } from "../api/client";
import { formatNumber } from "../ui/format";

export const RISK_LIMITS_PATH = "/settings/risk-limits";

export interface RiskLimitField {
  key: string; label: string; unit: string; min: number; max: number; whole: boolean; optional: boolean; value: number | null;
  /** The platform ceiling (an admin setting) when it is below the field's own top; null: none. */
  ceiling?: number | null;
}

export interface RiskLimitCheck {
  id: string; label: string; help: string; group: "main" | "more"; enabled: boolean; saved: boolean; fields: RiskLimitField[];
}

export type PausePick = "next_session" | "until_resume";

export interface RiskLimitsView {
  checks: RiskLimitCheck[];
  capital: number | null;
  loss_streak_pause: PausePick;
  on: number;
  exits: string;
  /** The PAPER ticket's own values of the same checks (saved there before), {check: {field: value}}. */
  paper?: Record<string, Record<string, number>>;
  note?: string | null;
}

/** One block message from the server (app/risk_engine/risk_limits.block). */
export interface RiskBlock {
  check: string; text: string; numbers?: Record<string, number>; admin: boolean; link: string | null; link_text: string;
  smaller_lots: number | null;
}

/** The form: each check's switch and its fields as typed text ("" = empty). */
export interface RiskLimitsDraft {
  checks: Record<string, { enabled: boolean; values: Record<string, string> }>;
  capital: string;
  pause: PausePick;
}

const text = (v: number | null | undefined): string => (typeof v === "number" && Number.isFinite(v) ? String(v) : "");

export function draftOf(view: RiskLimitsView): RiskLimitsDraft {
  const checks: RiskLimitsDraft["checks"] = {};
  for (const c of view.checks) checks[c.id] = { enabled: c.enabled, values: Object.fromEntries(c.fields.map((f) => [f.key, text(f.value)])) };
  return { checks, capital: text(view.capital), pause: view.loss_streak_pause === "until_resume" ? "until_resume" : "next_session" };
}

const number = (raw: string): number | null => {
  const clean = raw.replace(/[,\s₹]/g, "");
  if (clean === "") return null;
  const v = Number(clean);
  return Number.isFinite(v) ? v : Number.NaN;
};

/** The PUT body for the checks that changed, or the first problem in plain words. */
export function bodyOf(view: RiskLimitsView, draft: RiskLimitsDraft): { body: Record<string, unknown> | null; problem: string | null } {
  const checks: Record<string, { enabled: boolean; values: Record<string, number | null> }> = {};
  for (const c of view.checks) {
    const d = draft.checks[c.id];
    if (!d) continue;
    const values: Record<string, number | null> = {};
    let changed = d.enabled !== c.enabled;
    for (const f of c.fields) {
      const v = number(d.values[f.key] ?? "");
      if (v !== null && Number.isNaN(v)) return { body: null, problem: `${c.label}: enter a number.` };
      if (v !== null && typeof f.ceiling === "number" && v > f.ceiling) {
        return { body: null, problem: `${c.label}: the most allowed is ${f.ceiling} (your admin's ceiling). Ask your admin to change it.` };
      }
      if (v !== null && (v < f.min || v > f.max)) return { body: null, problem: `${c.label}: ${f.label.toLowerCase()} must be between ${f.min} and ${f.max}.` };
      if (v !== null && f.whole && !Number.isInteger(v)) return { body: null, problem: `${c.label}: enter a whole number.` };
      values[f.key] = v;
      if (v !== f.value) changed = true;
    }
    if (d.enabled && c.fields.length && c.id !== "event_rules" && c.fields.every((f) => values[f.key] === null)) {
      return { body: null, problem: `${c.label}: enter a value to switch it on.` };
    }
    if (changed) checks[c.id] = { enabled: d.enabled, values };
  }
  // the halve-size step cannot be further below the peak than the pause step (the server checks it too)
  const dd = draft.checks.drawdown;
  if (dd?.enabled) {
    const half = number(dd.values.dd_level_1_pct ?? ""), pause = number(dd.values.dd_level_2_pct ?? "");
    if (half !== null && pause !== null && !Number.isNaN(half) && !Number.isNaN(pause) && pause > 0 && half > pause) {
      return { body: null, problem: `Drawdown steps: halve the size at a drawdown no larger than the one that pauses new entries (${pause}%).` };
    }
  }
  const capital = number(draft.capital);
  if (capital !== null && (Number.isNaN(capital) || capital <= 0)) {
    return { body: null, problem: "Account basis: enter an amount above 0, or leave it empty to use your paper balance." };
  }
  return { body: { checks, capital, loss_streak_pause: draft.pause }, problem: null };
}

/** "3 of 12 on" - the summary line. */
export function onCount(view: RiskLimitsView): string {
  return view.on === 0 ? "All checks are off" : `${view.on} of ${view.checks.length} on`;
}

/** A switched-on check's value in words ("₹2,000 or 2%"), for the closed row. */
export function valueWords(check: RiskLimitCheck): string {
  const parts = check.fields.filter((f) => typeof f.value === "number").map((f) =>
    f.unit === "₹" ? `₹${formatNumber(f.value as number, 3, { trim: true })}` : f.unit === "%" ? `${f.value}%` : f.unit === "x" ? `${f.value}` : `${f.value} ${f.unit}`);
  return parts.join(" · ");
}

export async function loadRiskLimits(fetcher: typeof request = request): Promise<RiskLimitsView> {
  return fetcher<RiskLimitsView>("/risk-settings/limits");
}

export async function saveRiskLimits(body: Record<string, unknown>, fetcher: typeof request = request): Promise<RiskLimitsView> {
  return fetcher<RiskLimitsView>("/risk-settings/limits", { method: "PUT", body: JSON.stringify(body) });
}
