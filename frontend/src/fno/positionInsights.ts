/**
 * F&O workspace 8b, slice 2 (owner order ATP_SESSION_FNO_WORKSPACE.md, section 8b e, f, g, j): the pure parts of the
 * information features on the user's OWN PAPER positions and baskets (tested in positionInsights.test.tsx).
 *
 * - 8b-e premium capture: for a short-premium position (one sold option, or legs placed together that took in a net
 *   premium), the premium received beside what closing costs now, and the share kept so far, in plain words.
 * - 8b-f structure alerts: the user's OWN levels of his OWN position (its breakevens, his short strikes, his own exit
 *   and profit levels, a share of the premium he types) as a choice for the existing price alert (an instrument rule of
 *   the alert rules, `POST /api/alerts/rules`). An alert only tells him: no rule here carries an action, and nothing
 *   here is read by an order or an exit.
 * - 8b-g / 8b-j comparisons: the outcome at expiry of two sides (the position now vs the same position with a change he
 *   built himself; or two baskets), computed exactly from the legs (the payoff at expiry is piecewise linear), so both
 *   sides are drawn on one scale. The margin and the probability come from the Options Builder's engine (the basket
 *   panel's numbers); this module invents none of them.
 * Information only: no wording here tells the user what to trade, and no level is ever suggested - every level is one
 * of his own position's or one he typed.
 */
import { formatNumber } from "../ui/format";
import type { ContractFacts } from "./contracts";
import type { BasketLegAsk, ContractRight, Side } from "./api";
import type { NewLeg } from "./basket";
import type { Evaluation } from "../optionsBuilder/model";
import type { TradeRecord } from "../types";

// --- the payoff at expiry ------------------------------------------------------------------------------------------

/** One leg as the payoff reads it: `price` per unit (the premium, or a future's entry), `units` = lots x lot size. */
export interface PayoffLeg { right: ContractRight; side: Side; strike: number; price: number; units: number; expiry: string }

const sign = (s: Side) => (s === "BUY" ? 1 : -1);
const round2 = (v: number) => Math.round(v * 100) / 100;

/** A leg's P&L if the underlying closes at `s` on expiry. */
export function legPnlAt(l: PayoffLeg, s: number): number {
  const value = l.right === "FUT" ? s : l.right === "CE" ? Math.max(0, s - l.strike) : Math.max(0, l.strike - s);
  return sign(l.side) * (value - l.price) * l.units;
}

/** The whole side's P&L at expiry: its legs plus `shift` (what closing legs books now). */
export const pnlAt = (legs: readonly PayoffLeg[], s: number, shift = 0): number => legs.reduce((sum, l) => sum + legPnlAt(l, s), shift);

/** Every leg expires on the same day (else there is no single outcome at expiry). No legs: true (a flat line). */
export const oneExpiry = (legs: readonly PayoffLeg[]): boolean => new Set(legs.map((l) => l.expiry)).size <= 1;

/** + premium received, - premium paid (options only; a future has no premium), as the engine's `net_premium`. */
export const netPremium = (legs: readonly PayoffLeg[]): number =>
  round2(legs.filter((l) => l.right !== "FUT").reduce((s, l) => s - sign(l.side) * l.price * l.units, 0));

export interface Outcome {
  /** The most it can make (null: no limit, it grows the higher the price closes). */
  maxProfit: number | null;
  /** The most it can lose, as a negative number (null: no limit). */
  maxLoss: number | null;
  unboundedProfit: boolean;
  unboundedLoss: boolean;
  breakevens: number[];
}

/** The exact outcome at expiry: the values at zero and at every strike, the slope beyond the last strike, and the
 * prices where it crosses zero. Null when the legs expire on different days. */
export function outcome(legs: readonly PayoffLeg[], shift = 0): Outcome | null {
  if (!oneExpiry(legs)) return null;
  const kinks = [...new Set(legs.filter((l) => l.right !== "FUT" && l.strike > 0).map((l) => l.strike))].sort((a, b) => a - b);
  const pts = [0, ...kinks];
  const vals = pts.map((p) => pnlAt(legs, p, shift));
  const slope = legs.reduce((s, l) => s + (l.right === "PE" ? 0 : sign(l.side) * l.units), 0);
  const eps = 1e-6;
  const unboundedProfit = slope > eps;
  const unboundedLoss = slope < -eps;
  const hi = Math.max(...vals);
  const lo = Math.min(...vals);
  const roots: number[] = [];
  const add = (x: number) => { if (x > 0 && !roots.some((r) => Math.abs(r - x) < 0.005)) roots.push(round2(x)); };
  for (let i = 0; i < pts.length; i += 1) {
    if (Math.abs(vals[i]) < eps) add(pts[i]);
    if (i + 1 < pts.length && vals[i] * vals[i + 1] < 0) add(pts[i] + (-vals[i] * (pts[i + 1] - pts[i])) / (vals[i + 1] - vals[i]));
  }
  const last = pts[pts.length - 1];
  const vLast = vals[vals.length - 1];
  if (Math.abs(slope) > eps && Math.abs(vLast) > eps && Math.sign(vLast) !== Math.sign(slope)) add(last - vLast / slope);
  return {
    maxProfit: unboundedProfit ? null : round2(hi), maxLoss: unboundedLoss ? null : round2(lo), unboundedProfit, unboundedLoss,
    breakevens: roots.sort((a, b) => a - b),
  };
}

/** One price scale for every side: from a little below the lowest strike (or price) to a little above the highest. */
export function priceGrid(sides: readonly (readonly PayoffLeg[])[], spot: number | null, points = 121): number[] {
  const marks = sides.flat().map((l) => (l.right === "FUT" ? l.price : l.strike)).filter((v) => v > 0);
  if (spot && spot > 0) marks.push(spot);
  if (!marks.length) return [];
  const lo = Math.min(...marks) * 0.94;
  const hi = Math.max(...marks) * 1.06;
  const xs = Array.from({ length: points }, (_, i) => lo + ((hi - lo) * i) / (points - 1));
  // The strikes themselves, so the drawn line bends exactly where the payoff does.
  const kinks = sides.flat().filter((l) => l.right !== "FUT" && l.strike > lo && l.strike < hi).map((l) => l.strike);
  return [...new Set([...xs, ...kinks].map(round2))].sort((a, b) => a - b);
}

/** A side as the basket's plain-words sentence reads it (`basketModel.payoffSentence`). */
export function sentenceInput(legs: readonly PayoffLeg[], shift: number, xs: readonly number[]):
  Pick<Evaluation, "prices" | "at_expiry" | "breakevens" | "extremes" | "net_premium"> {
  const out = outcome(legs, shift);
  const ys = out ? xs.map((x) => pnlAt(legs, x, shift)) : null;
  return {
    prices: [...xs], at_expiry: ys, breakevens: out ? out.breakevens : null, net_premium: netPremium(legs) + shift,
    extremes: out ? { max_profit: out.maxProfit, max_loss: out.maxLoss, unbounded_profit: out.unboundedProfit, unbounded_loss: out.unboundedLoss } : null,
  };
}

// --- the user's own positions ----------------------------------------------------------------------------------------

/** An open F&O PAPER position as these features read it (the user's own record and what the contract is). */
export interface PosLeg {
  id: number;
  /** "NIFTY 27 OCT 24900 CE" */
  label: string;
  symbol: string;
  exchange: string;
  underlying: string;
  spotSymbol: string;
  spotExchange: string;
  expiry: string;
  /** 0 for a future */
  strike: number;
  right: ContractRight;
  side: Side;
  units: number;
  lotSize: number | null;
  entry: number;
  ltp: number | null;
  /** His own exit level on the contract's price (the position's stop), when it sits on the losing side of the entry. */
  exitLevel: number | null;
  /** His own profit level on the contract's price (the position's first target), when it sits on the winning side. */
  profitLevel: number | null;
}

/** The position as a `PosLeg`, or null while what the contract is is not known (or it has expired). Pure. */
export function posLeg(p: Pick<TradeRecord, "id" | "symbol" | "exchange" | "direction" | "quantity" | "entry_price" | "stop_loss" | "target1" | "lot_size">,
  facts: ContractFacts | undefined, ltp: number | null): PosLeg | null {
  if (!facts || !facts.known || facts.expired || !facts.expiry || !(p.quantity > 0) || !(p.entry_price > 0)) return null;
  const side: Side = p.direction.toUpperCase() === "SHORT" ? "SELL" : "BUY";
  const stop = typeof p.stop_loss === "number" && p.stop_loss > 0 ? p.stop_loss : null;
  const profit = typeof p.target1 === "number" && p.target1 > 0 ? p.target1 : null;
  const losing = (v: number | null) => v !== null && (side === "SELL" ? v > p.entry_price : v < p.entry_price);
  const winning = (v: number | null) => v !== null && (side === "SELL" ? v < p.entry_price : v > p.entry_price);
  return {
    id: p.id, label: facts.label, symbol: p.symbol, exchange: (p.exchange ?? facts.exchange).toUpperCase(), underlying: facts.underlying,
    spotSymbol: facts.spot_symbol, spotExchange: facts.spot_exchange, expiry: facts.expiry, strike: facts.right === "FUT" ? 0 : facts.strike ?? 0,
    right: facts.right, side, units: p.quantity, lotSize: facts.lot_size ?? p.lot_size ?? null, entry: p.entry_price,
    ltp: typeof ltp === "number" && ltp > 0 ? ltp : null, exitLevel: losing(stop) ? stop : null, profitLevel: winning(profit) ? profit : null,
  };
}

/** The legs at their entry prices: the position's own outcome at expiry. */
export const heldPayoff = (legs: readonly PosLeg[]): PayoffLeg[] =>
  legs.map((l) => ({ right: l.right, side: l.side, strike: l.strike, price: l.entry, units: l.units, expiry: l.expiry }));

// --- 8b-e: premium capture ------------------------------------------------------------------------------------------

export interface Capture {
  /** The net premium the legs took in when they were opened. */
  received: number;
  /** What closing every leg costs at the live prices (null while any leg has no live price). */
  closeNow: number | null;
  /** received - closeNow (null with closeNow). */
  kept: number | null;
  /** kept as a % of received (null with kept). */
  pct: number | null;
}

/** For short premium only: options, and a net premium received. Null otherwise (a long option, a future, a debit). */
export function premiumCapture(legs: readonly PosLeg[]): Capture | null {
  if (!legs.length || legs.some((l) => l.right === "FUT")) return null;
  const received = round2(legs.reduce((s, l) => s - sign(l.side) * l.entry * l.units, 0));
  if (!(received > 0)) return null;
  if (legs.some((l) => l.ltp === null)) return { received, closeNow: null, kept: null, pct: null };
  const closeNow = round2(legs.reduce((s, l) => s - sign(l.side) * (l.ltp as number) * l.units, 0));
  const kept = round2(received - closeNow);
  return { received, closeNow, kept, pct: Math.round((kept / received) * 1000) / 10 };
}

/** The share kept, for the bar: 0-100 (a premium that grew past what came in reads 0). */
export const captureBar = (c: Capture): number => (c.pct === null ? 0 : Math.max(0, Math.min(100, c.pct)));

// --- 8b-f: structure alerts on the user's own levels ----------------------------------------------------------------

export type AlertOp = ">=" | "<=";

export interface AlertChoice {
  key: string;
  /** What is watched and why, in plain words: "NIFTY 50 reaches 24,935 (your breakeven)". */
  label: string;
  /** "your breakeven", "your short strike", "your exit level", "your profit level", "40% of the premium kept" */
  why: string;
  /** "NIFTY 50" (the underlying) or the contract's own trading symbol. */
  symbol: string;
  exchange: string;
  /** The name shown for that symbol: the underlying's, or the contract's label. */
  shown: string;
  level: number;
  op: AlertOp;
}

/** A level as traders write it: "24,935", "24,935.5". */
export function levelText(v: number): string {
  const r = round2(v);
  return formatNumber(r, Number.isInteger(r) ? 0 : Number.isInteger(Math.round(r * 100) / 10) ? 1 : 2);
}

const opFrom = (level: number, now: number | null, fallback: AlertOp): AlertOp => (now === null ? fallback : level >= now ? ">=" : "<=");
const opWords = (op: AlertOp) => (op === ">=" ? "at or above" : "at or below");

function choice(key: string, why: string, symbol: string, exchange: string, shown: string, level: number, op: AlertOp): AlertChoice {
  return { key, why, symbol, exchange, shown, level: round2(level), op, label: `${shown} reaches ${levelText(level)} (${why})` };
}

/** The alerts the user can ask for on his own position (one leg or legs placed together), from his own levels only:
 * the breakevens and his short strikes on the underlying's price (the direction from where `spot` is now; unknown,
 * the side of the position the level sits on), and for one leg his exit / profit level on the contract's price. */
export function alertChoices(legs: readonly PosLeg[], spot: number | null): AlertChoice[] {
  if (!legs.length) return [];
  const first = legs[0];
  const under = (key: string, why: string, level: number, fallback: AlertOp) =>
    choice(key, why, first.spotSymbol, first.spotExchange, first.spotSymbol, level, opFrom(level, spot, fallback));
  const out: AlertChoice[] = [];
  const res = outcome(heldPayoff(legs));
  if (res) {
    const strikes = legs.filter((l) => l.right !== "FUT").map((l) => l.strike);
    const mid = strikes.length ? strikes.reduce((a, b) => a + b, 0) / strikes.length : legs.reduce((a, l) => a + l.entry, 0) / legs.length;
    res.breakevens.forEach((b, i) => out.push(under(`be-${i}`, "your breakeven", b, b >= mid ? ">=" : "<=")));
  }
  const shorts = [...new Map(legs.filter((l) => l.side === "SELL" && l.right !== "FUT").map((l) => [`${l.right}${l.strike}`, l])).values()];
  for (const l of shorts) {
    out.push(under(`short-${l.right}-${l.strike}`, `your short ${l.right === "CE" ? "call" : "put"} strike`, l.strike, l.right === "CE" ? ">=" : "<="));
  }
  if (legs.length === 1) {
    const l = first;
    const losingOp: AlertOp = l.side === "SELL" ? ">=" : "<=";
    const winningOp: AlertOp = l.side === "SELL" ? "<=" : ">=";
    if (l.exitLevel !== null) out.push(choice("exit", "your exit level", l.symbol, l.exchange, l.label, l.exitLevel, losingOp));
    if (l.profitLevel !== null) out.push(choice("profit", "your profit level", l.symbol, l.exchange, l.label, l.profitLevel, winningOp));
  }
  return out;
}

/** "Tell me when I have kept N% of the premium" on one sold option: the contract's price at which N% of what it took
 * in is kept (on the 0.05 tick). Null for anything else, or an N outside 1-99 (the user types N; nothing is preset). */
export function keptChoice(leg: PosLeg, pctText: string): AlertChoice | null {
  const pct = Number(pctText.replace(/%/g, "").trim());
  if (leg.side !== "SELL" || leg.right === "FUT" || pctText.trim() === "" || !Number.isFinite(pct) || pct < 1 || pct > 99) return null;
  const level = Math.max(0.05, Math.round((leg.entry * (1 - pct / 100)) / 0.05) * 0.05);
  return choice("kept", `${formatNumber(pct, Number.isInteger(pct) ? 0 : 1)}% of the premium kept`, leg.symbol, leg.exchange, leg.label, level, "<=");
}

/** The alert as the existing instrument rule (the chain's "Price alert" body): it only notifies. No action, no order. */
export function alertBody(c: AlertChoice): Record<string, unknown> {
  return {
    name: `${c.shown} ${opWords(c.op)} ${levelText(c.level)} (${c.why})`.slice(0, 120), kind: "instrument", symbol: c.symbol, exchange: c.exchange,
    condition: `close ${c.op} ${c.level}`, base_tf: "1m", priority: "normal", cooldown_minutes: 60, mode: "instant", digest_every: "hourly",
  };
}

export const ALERT_ONLY = "An alert only tells you. Nothing is traded, and your position stays as it is.";

export function alertSavedText(c: AlertChoice): string {
  return `Alert saved: you will be told when ${c.shown} is ${opWords(c.op)} ${levelText(c.level)} (${c.why}). ${ALERT_ONLY}`;
}

// --- 8b-g: the position now vs the same position with a change he built ---------------------------------------------

export interface ChangeSide {
  /** The legs kept (at their entry prices) and the new legs (at their planning prices). */
  legs: PayoffLeg[];
  /** What closing the left-out legs books at their live prices (0 when none is closed; null while a price is missing). */
  booked: number | null;
  /** The new legs' premium: + received, - paid. */
  newPremium: number;
  /** The new legs that count (the basket's legs on this underlying, priced). */
  added: NewLeg[];
  /** Plain reasons a part was left out. */
  notes: string[];
}

/** The change: the open legs he keeps, those he closes in the comparison (booked at their live price) and the legs of
 * his own basket on the same underlying (each needs its planning price). Nothing here is sent anywhere. Pure. */
export function changeSide(open: readonly PosLeg[], closed: ReadonlySet<number>, basketLegs: readonly NewLeg[]): ChangeSide {
  const notes: string[] = [];
  const underlying = open[0]?.underlying ?? "";
  const lotSize = open.find((l) => l.lotSize)?.lotSize ?? null;
  const kept = open.filter((l) => !closed.has(l.id));
  let booked: number | null = 0;
  for (const l of open.filter((x) => closed.has(x.id))) {
    if (l.ltp === null || booked === null) { booked = null; continue; }
    booked += sign(l.side) * (l.ltp - l.entry) * l.units;
  }
  if (booked === null) notes.push("A leg you close here has no live price yet, so what closing it books is not known.");
  const others = basketLegs.filter((l) => l.underlying !== underlying);
  if (others.length) notes.push(`Your basket also holds legs on ${[...new Set(others.map((l) => l.underlying))].join(", ")}: left out here.`);
  const same = basketLegs.filter((l) => l.underlying === underlying);
  const unpriced = same.filter((l) => !(typeof l.price === "number" && l.price > 0));
  if (unpriced.length) notes.push(`${unpriced.length === 1 ? "A basket leg has" : `${unpriced.length} basket legs have`} no price: enter one in the basket to count ${unpriced.length === 1 ? "it" : "them"}.`);
  // M68 PR-2: a leg with no lots entered yet is left out, said so (never counted as one lot)
  const unsized = same.filter((l) => l.lots === null);
  if (unsized.length) notes.push(`${unsized.length === 1 ? "A basket leg has" : `${unsized.length} basket legs have`} no lots entered: enter them in the basket to count ${unsized.length === 1 ? "it" : "them"}.`);
  const added = lotSize ? same.filter((l) => typeof l.price === "number" && l.price > 0 && l.lots !== null) : [];
  if (!lotSize && same.length) notes.push("The lot size of this contract is not known yet, so the basket legs are left out.");
  const newLegs: PayoffLeg[] = added.map((l) => ({ right: l.right, side: l.side, strike: l.right === "FUT" ? 0 : l.strike, price: l.price as number,
    units: (l.lots as number) * (lotSize as number), expiry: l.expiry }));
  return { legs: [...heldPayoff(kept), ...newLegs], booked: booked === null ? null : round2(booked), newPremium: netPremium(newLegs), added, notes };
}

/** The open legs as basket legs for the engine's margin estimate (at their entry prices), or null when a leg's size is
 * not whole lots. */
export function heldAsks(legs: readonly PosLeg[]): BasketLegAsk[] | null {
  const out: BasketLegAsk[] = [];
  for (const l of legs) {
    const lots = l.lotSize ? l.units / l.lotSize : NaN;
    if (!Number.isInteger(lots) || lots < 1) return null;
    out.push({ underlying: l.underlying, expiry: l.expiry, strike: l.right === "FUT" ? null : l.strike, right: l.right, side: l.side, lots, price: l.entry,
      stop_loss: null, iv: null });
  }
  return out;
}
