/**
 * TERM-2 step 4 R1 (owner order ATP_ORDER_TERM2.md; flag `term_roll`, with `ui_v2` + `terminal`): "Roll" a position or a
 * strategy to the next weekly expiry - the pure parts (tested in roll.test.tsx) and the one read (backend
 * app/paper/fno_term.py `roll_preview`, POST /api/paper/fno/roll/preview).
 *
 * The roll is NOT atomic, and the screen says so: step 1 closes the current legs through the EXISTING exit (Exit now, or
 * the group exit for the legs of one basket - exits are never blocked, ADR-0004); step 2 puts the new legs (same strikes,
 * same side and lots, next expiry) into the ONE basket, where the user reviews and places them himself. Between the steps
 * a notice (`RollPendingV2`) says the roll is half done until the basket is placed or he dismisses it. Nothing here places,
 * closes or changes an order by itself, and the AI never calls it (ADR-0006). A sold leg's stop stays blank (DEF-0).
 */
import { api as appApi, request } from "../api/client";
import { closedText, failedText } from "../trading/model";
import type { ContractRight, Side } from "./api";
import type { BasketStore, NewLeg } from "./basket";
import { groupExitApi, resultText } from "./groupExits";
import { formatINR, formatNumber } from "../ui/format";

export interface RollExit { trade_id: number; label: string; side: Side; lots: number; units: number; price: number | null; price_source: "live" | "last" | null }
export interface RollLeg { underlying: string; expiry: string; strike: number | null; right: ContractRight; side: Side; lots: number; price: number | null; stop_loss: null; label: string }
export interface RollPreview {
  mode: "PAPER"; ok: boolean; errors: string[]; exit: RollExit[]; legs: RollLeg[]; expiry: string | null; underlying: string | null;
  spot: number | null;
  numbers: { net_premium: number; extremes: { max_profit: number | null; max_loss: number | null; unbounded_profit: boolean; unbounded_loss: boolean } | null;
    breakevens: number[] | null; lot_sizes: (number | null)[] } | null;
  numbers_errors: string[]; exit_value: number | null; net: number | null; atomic: false; steps: string; as_of: string;
}

export const rollApi = {
  preview: (trade_ids: number[], spot?: number | null) =>
    request<RollPreview>("/paper/fno/roll/preview", { method: "POST", body: JSON.stringify({ trade_ids, ...(spot ? { spot } : {}) }) }),
};
export type RollApi = typeof rollApi;

/** What is being rolled: one position, or the legs of one group (a basket's legs close with the group exit; a strategy
 * structure closes whole with its first leg's Exit now). */
export type RollAsk =
  | { kind: "single"; tradeId: number; title: string }
  | { kind: "group"; tradeIds: number[]; title: string; basket: boolean };

export const rollIds = (ask: RollAsk): number[] => (ask.kind === "single" ? [ask.tradeId] : ask.tradeIds);

/** The new legs as basket legs (the basket's own shape). Pure. */
export function rollLegs(p: RollPreview): NewLeg[] {
  return p.legs.map((l) => ({ underlying: l.underlying, expiry: l.expiry, strike: l.strike ?? 0, right: l.right, side: l.side, lots: l.lots,
    price: l.price, label: l.label, stopLoss: null }));
}

/** "Net credit ₹3,375" / "Net debit ₹1,200" / null. Structure words only. Pure. */
export function netWords(value: number | null): string | null {
  if (value === null) return null;
  return `${value >= 0 ? "Net credit" : "Net debit"} ${formatINR(Math.abs(value))}`;
}

/** One exit row: the leg that closes, in words ("Close sold ..." / "Close bought ..."). Pure. */
export function exitWords(e: RollExit): string {
  return `${e.side === "SELL" ? "Close sold" : "Close bought"} ${e.label} · ${formatNumber(e.lots, 0)} lot${e.lots === 1 ? "" : "s"}`;
}

export function legWords(l: RollLeg): string {
  return `${l.side === "SELL" ? "Sell" : "Buy"} ${l.label} · ${formatNumber(l.lots, 0)} lot${l.lots === 1 ? "" : "s"}`;
}

export const STEP_ONE = "Step 1 of 2: exit";
export const STEP_TWO = "Step 2 of 2: new legs in your basket";
export const NOT_ATOMIC = "Two steps, not one: you hold neither set of legs between them.";

// --- the half-done notice --------------------------------------------------------------------------------------------

export interface RollPending { title: string; expiry: string | null; legs: number; at: number }
export const ROLL_PENDING_KEY = "atp_roll_pending";

export function readPending(): RollPending | null {
  try {
    const raw = JSON.parse(sessionStorage.getItem(ROLL_PENDING_KEY) ?? "null") as RollPending | null;
    return raw && typeof raw.title === "string" ? raw : null;
  } catch {
    return null;
  }
}

const listeners = new Set<() => void>();
export function writePending(p: RollPending | null): void {
  try {
    if (p) sessionStorage.setItem(ROLL_PENDING_KEY, JSON.stringify(p));
    else sessionStorage.removeItem(ROLL_PENDING_KEY);
  } catch { /* storage unavailable: the notice lives for this page only */ }
  memory = p;
  listeners.forEach((fn) => fn());
}
let memory: RollPending | null = readPending();
export const pendingStore = {
  subscribe: (fn: () => void) => { listeners.add(fn); return () => { listeners.delete(fn); }; },
  snapshot: () => memory,
};

/** The half-done line. Pure. */
export function pendingText(p: RollPending): string {
  const day = p.expiry ? ` (${p.expiry})` : "";
  return `Roll half done: ${p.title} is closed. The ${p.legs} new leg${p.legs === 1 ? " is" : "s are"} in your basket${day}, not placed yet.`;
}

// --- the confirm: step 1 (the existing exit), then step 2 (the basket) ------------------------------------------------

/** The existing exits the roll's step 1 uses - Exit now and the group exit - injectable for tests. Never a "Buy in a
 * basket" standing in for an exit. */
export interface RollExits {
  exitNow: (tradeId: number) => Promise<{ closed: { exit_price?: number | null; pnl?: number | null; exit_reason?: string | null }[]; warnings: string[] }>;
  exitGroup: (tradeId: number) => ReturnType<typeof groupExitApi.exitGroup>;
}
export const existingExits: RollExits = { exitNow: (id) => appApi.exitNow(id), exitGroup: (id) => groupExitApi.exitGroup(id) };

export interface RollOutcome { text: string; ok: boolean; toBasket: boolean }

/** The roll's one confirm: step 1 closes the legs through the EXISTING exit (a basket's legs: the group exit, sold legs
 * first; a structure: its first leg's Exit now, which closes it whole; one position: its Exit now). Only when every leg
 * closed does step 2 put the new legs into the basket (an empty basket takes them as they are; otherwise they are added)
 * and mark the roll half done. A failure is a plain line, never a crash, and nothing goes into the basket. */
export async function runRoll(ask: RollAsk, preview: RollPreview | null, exits: RollExits, store: BasketStore): Promise<RollOutcome> {
  let ok: boolean;
  let text: string;
  try {
    if (ask.kind === "group" && ask.basket) {
      const r = await exits.exitGroup(ask.tradeIds[0]);
      ok = r.status === "CLOSED";
      text = resultText(r);
    } else {
      const r = await exits.exitNow(ask.kind === "single" ? ask.tradeId : ask.tradeIds[0]);
      const first = r.closed[0];
      ok = r.closed.length > 0;
      text = ok ? `${ask.title}: ${closedText(first?.exit_price, first?.pnl, first?.exit_reason)}` : `${ask.title} was not closed. ${r.warnings[0] ?? ""}`.trim();
    }
  } catch (e) {
    return { text: failedText(`${ask.title} was not closed.`, e), ok: false, toBasket: false };
  }
  if (!ok) return { text: `${text} The roll stopped after step 1: nothing went into your basket.`, ok: false, toBasket: false };
  if (!preview || !preview.ok || preview.legs.length === 0) return { text: `${text} The new legs could not be prepared: add them from the chain.`, ok: true, toBasket: false };
  const legs = rollLegs(preview);
  if (store.snapshot().length === 0) store.replace(legs);
  else legs.forEach((l) => store.add(l));
  writePending({ title: ask.title, expiry: preview.expiry, legs: legs.length, at: Date.now() });
  return { text: `${text} Step 2: review and place the new legs in your basket.`, ok: true, toBasket: true };
}
