/**
 * F&O workspace 8b, slice 3 (owner order ATP_SESSION_FNO_WORKSPACE.md, section 8b-i): the pure parts of the F&O cockpit -
 * one read-only view of the user's OWN open PAPER F&O positions (tested in cockpit.test.tsx). Every figure is his own
 * positions' (their legs, their live prices), his own risk settings' or his own alert rules'; nothing here is a
 * suggestion, nothing is read by an order or an exit, and nothing here has a button that trades.
 *
 * - the positions as he holds them: legs placed together (one basket, one structure) as one line, the rest one by one,
 *   with their P&L;
 * - the premium kept so far over the short-premium lines (8b-e's rule, `premiumCapture`);
 * - the most the lines can lose together at expiry (exactly from the legs, 8b-g's `outcome`), beside his own capital,
 *   risk per trade and daily loss limit (8b-d's budget) - a line of text, never a refusal;
 * - the expiries coming up (soonest first) and the alerts he set on these contracts or their underlyings.
 */
import type { AlertRule } from "../alerts/rules";
import { formatDateIST, formatINR, formatNumber } from "../ui/format";
import { countdown } from "./contracts";
import { RISK_NOTE, type RiskBudget, type RiskLine } from "./insights";
import { heldPayoff, outcome, premiumCapture, type PosLeg } from "./positionInsights";

/** One line of the cockpit: legs placed together, or one position on its own. */
export interface CockpitUnit { key: string; title: string; legs: PosLeg[]; pnl: number | null }

export interface CockpitSummary {
  positions: number;
  lines: number;
  /** The lines' P&L added up; null while any has no live P&L (never a partial sum shown as the whole). */
  pnl: number | null;
  /** Over the short-premium lines: the premium received and the part kept so far (null while a price is missing). */
  premium: { received: number; kept: number | null; pct: number | null; lines: number } | null;
  /** The most the lines can lose together at expiry (a positive amount); null with `unbounded` or when unknown. */
  worst: number | null;
  unbounded: boolean;
  /** Lines whose legs expire on different days (no single outcome at expiry): left out of `worst`, said so. */
  noOutcome: number;
}

const round2 = (v: number) => Math.round(v * 100) / 100;

export function summarise(units: readonly CockpitUnit[]): CockpitSummary {
  const positions = units.reduce((n, u) => n + u.legs.length, 0);
  const pnl = units.length && units.every((u) => typeof u.pnl === "number" && Number.isFinite(u.pnl))
    ? round2(units.reduce((s, u) => s + (u.pnl as number), 0)) : null;
  let received = 0;
  let kept: number | null = 0;
  let premiumLines = 0;
  let worst = 0;
  let unbounded = false;
  let noOutcome = 0;
  for (const u of units) {
    const c = premiumCapture(u.legs);
    if (c) {
      premiumLines += 1;
      received += c.received;
      kept = kept === null || c.kept === null ? null : kept + c.kept;
    }
    const o = outcome(heldPayoff(u.legs));
    if (!o) { noOutcome += 1; continue; }
    if (o.unboundedLoss || o.maxLoss === null) { unbounded = true; continue; }
    if (o.maxLoss < 0) worst += -o.maxLoss;
  }
  const premium = premiumLines
    ? { received: round2(received), kept: kept === null ? null : round2(kept), pct: kept === null || !(received > 0) ? null : Math.round((kept / received) * 1000) / 10, lines: premiumLines }
    : null;
  return { positions, lines: units.length, pnl, premium, worst: unbounded || !units.length ? null : round2(worst), unbounded, noOutcome };
}

const pctOf = (part: number, whole: number) => {
  const v = (part / whole) * 100;
  return v > 0 && v < 0.1 ? "under 0.1%" : `${formatNumber(Math.round(v * 10) / 10, v >= 10 ? 0 : 1)}%`;
};

/** The worst case at expiry beside the user's own budget, in plain words; null while either is not known. Information
 * only: nothing reads it to hold back an order or an exit. */
export function cockpitRiskLine(s: CockpitSummary, b: RiskBudget | null | undefined): RiskLine | null {
  if (!b || b.capital == null || !(b.capital > 0) || !s.lines) return null;
  const daily = b.daily;               // DEF-0: null = no daily loss limit set - the facts alone
  if (s.unbounded) {
    return { tone: "warn", note: RISK_NOTE,
      text: `At least one of your open F&O positions has no fixed limit to its loss.${daily ? ` Your daily loss limit is ${formatINR(daily.amount)}.` : ""}` };
  }
  if (s.worst === null || !(s.worst > 0)) return null;
  const head = `Together, the most your open F&O positions can lose at expiry is ${formatINR(s.worst)}, ${pctOf(s.worst, b.capital)} of your capital.`;
  const left = s.noOutcome ? ` ${s.noOutcome === 1 ? "One position spans" : `${s.noOutcome} positions span`} two expiries and is not counted.` : "";
  if (!daily) return { tone: "info", note: RISK_NOTE, text: `${head}${left}` };
  if (s.worst > daily.amount) {
    return { tone: "warn", note: RISK_NOTE, text: `${head} That is more than your daily loss limit of ${formatINR(daily.amount)}.${left}` };
  }
  return { tone: "info", note: RISK_NOTE, text: `${head} Within your daily loss limit of ${formatINR(daily.amount)}.${left}` };
}

export interface ExpiryLine { expiry: string; when: string; daysLeft: number; legs: number; underlyings: string[] }

/** Whole days from `today` (IST date, yyyy-mm-dd) to `expiry` (yyyy-mm-dd). */
export function daysBetween(today: string, expiry: string): number {
  return Math.round((Date.parse(`${expiry}T00:00:00Z`) - Date.parse(`${today}T00:00:00Z`)) / 86_400_000);
}

export function todayIST(now = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
}

/** The expiries of the open legs, soonest first, with the legs and underlyings on each. */
export function upcomingExpiries(units: readonly CockpitUnit[], today: string): ExpiryLine[] {
  const by = new Map<string, { legs: number; underlyings: Set<string> }>();
  for (const l of units.flatMap((u) => u.legs)) {
    const e = by.get(l.expiry) ?? { legs: 0, underlyings: new Set<string>() };
    e.legs += 1;
    e.underlyings.add(l.underlying);
    by.set(l.expiry, e);
  }
  return [...by.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([expiry, e]) => {
    const daysLeft = Math.max(0, daysBetween(today, expiry));
    return { expiry, when: `${formatDateIST(expiry)} · ${countdown(daysLeft) ?? ""}`.replace(/ · $/, ""), daysLeft, legs: e.legs, underlyings: [...e.underlyings].sort() };
  });
}

/** His active instrument alerts on these contracts or on their underlyings (the levels he chose; "Tell me when"). */
export function alertsSet(rules: readonly AlertRule[] | null | undefined, units: readonly CockpitUnit[]): AlertRule[] {
  if (!rules) return [];
  const symbols = new Set(units.flatMap((u) => u.legs.flatMap((l) => [l.symbol.toUpperCase(), l.spotSymbol.toUpperCase()])));
  return rules.filter((r) => r.kind === "instrument" && r.status === "active" && !!r.symbol && symbols.has(r.symbol.toUpperCase()));
}

export const COCKPIT_NOTE = "Read only. Nothing here places, changes or holds back an order or an exit; each position keeps its own Exit below.";
