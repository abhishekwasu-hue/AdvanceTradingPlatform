/**
 * Terminal T2 (P1-6): "Exit part" and "Reverse" on an open PAPER position (TT-9), and Modify / Cancel of a waiting order
 * (TT-10) - the pure parts (tested in positionActions.test.tsx) and the calls (backend app/paper/fno_position_actions.py).
 *
 * Exit part is an exit (ADR-0004): nothing here disables it, and no kill switch, limit or approval rule stops it on the
 * server. Reverse closes the whole position (the same exit) and then opens the other side with the same lots through the
 * one order ticket, where every entry check runs. Every action carries its own reference, made when the confirm step
 * opens and reused on a retry, so a double tap never acts twice.
 */
import { friendlyError, request } from "../api/client";
import type { TradeRecord } from "../types";
import { formatPrice, formatSignedINR } from "../ui/format";
import { isFnoPosition } from "./contracts";

export interface ClosedPart { trade_id: number; symbol: string; lots: number; closed: boolean; exit_price: number | null; pnl: number | null }
export interface ExitPartResult { status: "CLOSED" | "CONFIRM"; repeat?: boolean; warning?: string | null; closed: ClosedPart | null; remaining_lots: number }
export interface ReverseEntry { status: string; order_id: number | null; trade_id: number | null; reasons: string[] }
export interface ReverseResult {
  status: "REVERSED" | "EXITED" | "CONFIRM" | "PREVIEW"; repeat?: boolean; warning?: string | null; closed?: ClosedPart | null;
  entry?: ReverseEntry | null; text?: string;
}
export interface WaitingOrder {
  id: number; purpose: "ENTRY" | "EXIT"; kind: "LIMIT" | "STOP" | "TARGET"; trade_id: number | null; symbol: string; side: "BUY" | "SELL";
  quantity: number; order_type: string; price: number | null; trigger_price: number | null;
  trigger: { source: "PREMIUM" | "SPOT"; symbol: string; level: number; side: "ABOVE" | "BELOW"; mode: string; timeframe_minutes: number | null };
  status: string; status_words: string; note: string | null;
}
export interface ModifyAsk { price?: number; trigger_price?: number; lots?: number; level?: number; spot_level?: number }

const post = (path: string, body?: unknown) => ({ method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const positionActionsApi = {
  exitPart: (tradeId: number, lots: number, clientRef: string, confirm: boolean) =>
    request<ExitPartResult>(`/paper/fno/positions/${tradeId}/exit-part`, post("", { lots, client_ref: clientRef, confirm })),
  reverse: (tradeId: number, body: { client_ref: string; stop_loss?: number; preview?: boolean; confirm?: boolean }) =>
    request<ReverseResult>(`/paper/fno/positions/${tradeId}/reverse`, post("", body)),
  waiting: () => request<WaitingOrder[]>("/paper/fno/resting"),
  modify: (id: number, ask: ModifyAsk) => request<WaitingOrder>(`/paper/fno/resting/${id}/modify`, post("", ask)),
  cancel: (id: number) => request<WaitingOrder>(`/paper/fno/resting/${id}/cancel`, post("")),
};
export type PositionActionsApi = typeof positionActionsApi;

/** A fresh reference for one action (the server's idempotency key). */
export function newRef(): string {
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === "function") return c.randomUUID().replace(/-/g, "").slice(0, 24);
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
}

/** Whole lots held (a position without a lot size counts one unit per lot). Pure. */
export function openLots(p: Pick<TradeRecord, "quantity" | "lot_size">): number {
  const unit = p.lot_size && p.lot_size > 0 ? p.lot_size : 1;
  return Math.max(0, Math.round(p.quantity / unit));
}

/** The lots stepper's value kept between 1 and what is held. Pure. */
export function clampLots(value: number, held: number): number {
  if (!Number.isFinite(value)) return 1;
  return Math.min(Math.max(1, Math.round(value)), Math.max(1, held));
}

/** Exit part is offered on a PAPER position with more than one lot that is not a leg of a strategy structure. Pure. */
export function canExitPart(p: TradeRecord): boolean {
  return p.mode === "PAPER" && !p.leg_group_id && openLots(p) > 1;
}

/** Reverse is offered on an F&O PAPER position that is not a leg of a strategy structure. Pure. */
export function canReverse(p: TradeRecord): boolean {
  return p.mode === "PAPER" && !p.leg_group_id && isFnoPosition(p) && openLots(p) >= 1;
}

/** Does the reversed side need the user's own stop-loss (a sold option, or any future)? Pure. */
export function reverseNeedsStop(p: Pick<TradeRecord, "direction" | "instrument_kind">): boolean {
  return p.instrument_kind === "FUTURE" || p.direction === "LONG";
}

const lotWord = (n: number) => `${n} lot${n === 1 ? "" : "s"}`;

/** The confirm step's words. Plain language, no internal terms. Pure. */
export function exitPartCopy(symbol: string, lots: number, held: number): { title: string; body: string; confirm: string } {
  const rest = held - lots;
  return {
    title: "Exit part of this position?",
    body: rest > 0
      ? `${lotWord(lots)} of ${symbol} ${lots === 1 ? "closes" : "close"} at the current price. ${lotWord(rest)} ${rest === 1 ? "stays" : "stay"} open with the same stop and target.`
      : `All ${lotWord(lots)} of ${symbol} close at the current price.`,
    confirm: `Exit ${lotWord(lots)}`,
  };
}

export function reverseCopy(symbol: string, held: number, long: boolean): { title: string; body: string; confirm: string } {
  return {
    title: "Reverse this position?",
    body: `All ${lotWord(held)} of ${symbol} close at the current price, then a new ${long ? "short" : "long"} position of ${lotWord(held)} opens. `
      + "The new position goes through the usual order checks; if it is not allowed, your position stays closed and you see why.",
    confirm: "Reverse",
  };
}

/** The line after "Exit part". Pure. */
export function exitPartText(result: ExitPartResult): string {
  const c = result.closed;
  if (!c) return "Nothing was closed.";
  const money = c.pnl === null ? "" : `: ${formatSignedINR(Math.round(c.pnl * 100) / 100)}`;
  const rest = result.remaining_lots > 0 ? ` ${lotWord(result.remaining_lots)} still open.` : "";
  return `${lotWord(c.lots)} of ${c.symbol} closed${c.exit_price !== null ? ` at ${formatPrice(c.exit_price)}` : ""}${money}.${rest}`;
}

/** The line after "Reverse". Pure. */
export function reverseText(symbol: string, result: ReverseResult): string {
  if (result.text) return result.text;
  return result.status === "REVERSED" ? `${symbol} closed and the opposite position is open.` : `${symbol} closed.`;
}

export type ActionStep = { kind: "warn"; warning: string } | { kind: "done"; text: string; ok: boolean };

/** One tap on the confirm of "Exit part" / "Reverse": a server warning (a hedge before its sold leg) keeps the sheet open
 *  and the next tap confirms; a failure is a plain line, never a crash. */
export async function runAction(
  ask: { kind: "part"; tradeId: number; symbol: string; lots: number; ref: string } | { kind: "reverse"; tradeId: number; symbol: string; ref: string; stopLoss?: number },
  warned: boolean, api: PositionActionsApi = positionActionsApi,
): Promise<ActionStep> {
  try {
    if (ask.kind === "part") {
      const r = await api.exitPart(ask.tradeId, ask.lots, ask.ref, warned);
      if (r.status === "CONFIRM") return { kind: "warn", warning: r.warning || "This leg protects a sold leg that is still open." };
      return { kind: "done", text: exitPartText(r), ok: true };
    }
    const r = await api.reverse(ask.tradeId, { client_ref: ask.ref, stop_loss: ask.stopLoss, confirm: warned });
    if (r.status === "CONFIRM") return { kind: "warn", warning: r.warning || "This leg protects a sold leg that is still open." };
    return { kind: "done", text: reverseText(ask.symbol, r), ok: r.status === "REVERSED" };
  } catch (e) {
    const why = friendlyError(e).trim();
    return { kind: "done", text: `${ask.symbol}: ${ask.kind === "part" ? "nothing was closed" : "not reversed"}. ${why || "Try again in a moment."}`, ok: false };
  }
}

/** A waiting order in one line. Pure. */
export function waitingLine(o: WaitingOrder): string {
  const qty = o.quantity % 1 ? o.quantity.toFixed(2) : String(o.quantity);
  if (o.purpose === "EXIT") {
    const what = o.trigger.source === "SPOT" ? `underlying ${o.trigger.side === "ABOVE" ? "at or above" : "at or below"}` : "at";
    return `${o.kind === "STOP" ? "Stop" : "Target"} on ${o.symbol} ${what} ${formatPrice(o.trigger.level)}`;
  }
  return `${o.side === "BUY" ? "Buy" : "Sell"} ${qty} ${o.symbol} · waits for ${formatPrice(o.trigger.level)}`;
}

/** What Modify asks for: an entry its price (and trigger for a stop order) and lots; an exit its level only. Pure. */
export function modifyFields(o: WaitingOrder): Array<"price" | "trigger_price" | "lots" | "level" | "spot_level"> {
  if (o.purpose === "EXIT") return [o.trigger.source === "SPOT" ? "spot_level" : "level"];
  if (o.order_type === "SL") return ["price", "trigger_price", "lots"];
  if (o.order_type === "SL-M") return ["trigger_price", "lots"];
  return ["price", "lots"];
}

export const FIELD_LABEL: Record<ReturnType<typeof modifyFields>[number], string> = {
  price: "Limit price", trigger_price: "Trigger price", lots: "Lots", level: "Price", spot_level: "Underlying level",
};

/** Only the fields he typed, as numbers; null when one is not a number. Pure. */
export function modifyAsk(values: Partial<Record<keyof ModifyAsk, string>>): ModifyAsk | null {
  const out: ModifyAsk = {};
  for (const [k, raw] of Object.entries(values) as Array<[keyof ModifyAsk, string | undefined]>) {
    if (raw === undefined || raw.trim() === "") continue;
    const n = Number(raw);
    if (!Number.isFinite(n) || n <= 0) return null;
    out[k] = k === "lots" ? Math.round(n) : n;
  }
  return Object.keys(out).length ? out : null;
}
