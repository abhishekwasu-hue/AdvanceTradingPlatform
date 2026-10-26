/**
 * CX6 (owner, 24 Oct) + CX-F1 / CX-F2 (25 Oct): the user-facing part of the combined stop-loss / target - the pure parts
 * (tested in combinedExit.test.tsx) and the calls to the routes merged in #527 (backend app/paper/fno_routes.py
 * `/api/paper/fno/group-exits`, app/execution/group_exits.py).
 *
 * Setting a combined exit sends nothing now: the trigger engine closes the ticked legs as one group when the level is
 * reached, the sold legs first (never blocked, ADR-0004; AI never sets or changes it, ADR-0006). Every number is the
 * user's own; nothing here suggests a level.
 */
import { friendlyError, request } from "../api/client";
import type { TradeRecord } from "../types";
import { formatINR, formatNumber } from "../ui/format";
import type { ContractFacts } from "./contracts";

export type Measure = "MTM_INR" | "SPOT" | "NET_PREMIUM";
export type CheckMode = "TOUCH" | "CLOSE";

export interface CombinedLeg { trade_id: number; symbol: string; role: "SHORT" | "LONG"; direction?: string; quantity?: number }
export interface StaleLeg { trade_id: number; symbol: string; price: number; seen_at: string | null }

/** One combined exit as `GET /api/paper/fno/group-exits` returns it (app/execution/group_exits.py view()). */
export interface CombinedExit {
  id: number;
  mode?: string;
  origin?: string;
  scope?: string;
  measure: Measure;
  sl_value: number | null;
  target_value: number | null;
  sl_side?: string | null;
  target_side?: string | null;
  spot_symbol: string | null;
  check_mode: CheckMode;
  timeframe_minutes: number | null;
  status: "ARMED" | "FIRING" | "DONE" | "CANCELLED";
  last_value: number | null;
  distance: { sl: number | null; target: number | null };
  version: number;
  legs: CombinedLeg[];
  words: string;
  note?: string | null;
  /** CX-F1: legs read at their last traded price on the last pass (no fresh quote). */
  stale: StaleLeg[];
}

export interface CombinedBody {
  scope: "SELECTED";
  trade_ids: number[];
  measure: Measure;
  sl_value: number | null;
  target_value: number | null;
  spot_symbol?: string | null;
  spot_exchange?: "NSE" | "BSE" | null;
  check_mode: CheckMode;
  timeframe_minutes?: number | null;
  client_ref: string;
}

export interface CombinedPreview { ok: boolean; reasons: string[]; words: string; value_now: number | null; distance: { sl: number | null; target: number | null } }

export const combinedApi = {
  list: () => request<CombinedExit[]>("/paper/fno/group-exits"),
  preview: (body: CombinedBody) => request<CombinedPreview>("/paper/fno/group-exits/preview", { method: "POST", body: JSON.stringify(body) }),
  create: (body: CombinedBody) => request<CombinedExit>("/paper/fno/group-exits", { method: "POST", body: JSON.stringify(body) }),
  edit: (id: number, body: { version: number; sl_value: number | null; target_value: number | null; check_mode?: CheckMode; timeframe_minutes?: number | null }) =>
    request<CombinedExit>(`/paper/fno/group-exits/${id}/edit`, { method: "POST", body: JSON.stringify(body) }),
  cancel: (id: number) => request<CombinedExit>(`/paper/fno/group-exits/${id}/cancel`, { method: "POST" }),
};
export type CombinedApi = typeof combinedApi;

/** The exits the chip shows: armed or closing its legs (a finished or removed one is gone). */
export const active = (list: readonly CombinedExit[] | null | undefined): CombinedExit[] =>
  (list ?? []).filter((x) => x.status === "ARMED" || x.status === "FIRING");

export const MEASURES: { key: Measure; label: string; hint: string }[] = [
  { key: "MTM_INR", label: "Combined ₹ profit / loss", hint: "All ticked legs together, in rupees" },
  { key: "SPOT", label: "Spot level", hint: "The underlying's price" },
  { key: "NET_PREMIUM", label: "Net premium", hint: "The ticked legs' premium together" },
];

/** The candle sizes the closed-candle check offers (a choice of the user, not a rule). */
export const TIMEFRAMES: { minutes: number; label: string }[] = [
  { minutes: 5, label: "5 minutes" }, { minutes: 15, label: "15 minutes" }, { minutes: 60, label: "1 hour" }, { minutes: 1440, label: "1 day" },
];

/** A leg the sheet can tick: one of the user's own open positions (`mode`: PAPER or LIVE - one exit never mixes them). */
export interface SheetLeg {
  id: number; symbol: string; short: boolean; underlying: string | null; spotSymbol: string | null; spotExchange: "NSE" | "BSE" | null; mode?: string;
}

const isShortTrade = (p: Pick<TradeRecord, "direction" | "leg_role">) =>
  (p.leg_role ? p.leg_role.toUpperCase() === "SHORT" : p.direction.toUpperCase() === "SHORT");

/** The sheet's legs from the open positions (PAPER and LIVE the same way, owner principle; the LIVE switches are the
 * owner's), with the spot the contract's facts name (else the position's underlying on NSE, or BSE for a BSE contract). */
export function sheetLegs(list: readonly TradeRecord[], facts: ReadonlyMap<string, ContractFacts> = new Map()): SheetLeg[] {
  return list.filter((p) => p.exit_time === null).map((p) => {
    const f = facts.get(`${(p.exchange ?? "NFO").toUpperCase()}:${p.symbol.toUpperCase()}`);   // itemKey(positionItem(p))
    const known = f && f.known ? f : null;
    const ex = (known?.spot_exchange ?? ((p.exchange ?? "").toUpperCase().startsWith("B") ? "BSE" : "NSE")).toUpperCase();
    return {
      id: p.id, symbol: p.symbol, short: isShortTrade(p),
      underlying: known?.underlying ?? p.underlying_symbol ?? null,
      spotSymbol: known?.spot_symbol ?? p.underlying_symbol ?? null,
      spotExchange: ex === "BSE" ? "BSE" : "NSE", mode: p.mode,
    };
  });
}

/** CX2 / T-D2: the spot level is the default for a spread (two or more legs on one underlying with a sold leg); the
 * combined ₹ profit / loss otherwise. Pure. */
export function defaultMeasure(legs: readonly SheetLeg[]): Measure {
  const under = new Set(legs.map((l) => l.spotSymbol ?? ""));
  return legs.length > 1 && under.size === 1 && !under.has("") && legs.some((l) => l.short) ? "SPOT" : "MTM_INR";
}

export interface CombinedForm {
  ticked: number[];
  measure: Measure;
  sl: string;
  target: string;
  check: CheckMode;
  timeframe: number;
}

/** A typed amount: a positive finite number, or null (empty / not a number). */
export function amount(text: string): number | null {
  const v = Number(text.replace(/[₹,\s]/g, ""));
  return text.trim() !== "" && Number.isFinite(v) && v > 0 ? v : null;
}

/** Plain-word reasons the form cannot be set yet (the server checks again). Pure. */
export function formErrors(form: CombinedForm, legs: readonly SheetLeg[]): string[] {
  const out: string[] = [];
  const picked = legs.filter((l) => form.ticked.includes(l.id));
  if (!picked.length) out.push("Tick at least one open position.");
  if (new Set(picked.map((l) => l.mode ?? "")).size > 1) out.push("PAPER and LIVE positions cannot share one combined exit.");
  if (form.sl.trim() && amount(form.sl) === null) out.push("The stop-loss must be a number above zero.");
  if (form.target.trim() && amount(form.target) === null) out.push("The target must be a number above zero.");
  if (amount(form.sl) === null && amount(form.target) === null) out.push("Enter a stop-loss, a target, or both.");
  if (form.measure !== "MTM_INR" && new Set(picked.map((l) => l.spotSymbol ?? "")).size > 1) {
    out.push("A spot or net-premium exit needs positions on one underlying. Use the combined ₹ profit / loss for a mix.");
  }
  if (form.measure === "SPOT" && picked.length && !picked[0].spotSymbol) out.push("The underlying of these positions is not known.");
  return out;
}

/** The request body (CX1-CX3). `ref`: one per sheet opening, so a double tap sets it once. Pure. */
export function bodyOf(form: CombinedForm, legs: readonly SheetLeg[], ref: string): CombinedBody {
  const first = legs.find((l) => form.ticked.includes(l.id));
  const spot = form.measure === "SPOT";
  const close = spot && form.check === "CLOSE";
  return {
    scope: "SELECTED", trade_ids: legs.filter((l) => form.ticked.includes(l.id)).map((l) => l.id), measure: form.measure,
    sl_value: amount(form.sl), target_value: amount(form.target),
    spot_symbol: spot ? first?.spotSymbol ?? null : null, spot_exchange: spot ? first?.spotExchange ?? null : null,
    check_mode: close ? "CLOSE" : "TOUCH", timeframe_minutes: close ? form.timeframe : null, client_ref: ref,
  };
}

/** The preview sentence on the page while the server's own is on its way (the same words as group_exits.describe). Pure. */
export function previewWords(form: CombinedForm, legs: readonly SheetLeg[]): string {
  const picked = legs.filter((l) => form.ticked.includes(l.id));
  const sl = amount(form.sl);
  const target = amount(form.target);
  if (!picked.length || (sl === null && target === null)) return "";
  const parts: string[] = [];
  if (form.measure === "MTM_INR") {
    if (sl !== null) parts.push(`combined loss reaches ${formatINR(sl)}`);
    if (target !== null) parts.push(`combined profit reaches ${formatINR(target)}`);
  } else {
    const what = form.measure === "SPOT" ? `${picked[0].spotSymbol ?? "the"} spot` : "net premium";
    if (sl !== null) parts.push(`${what} reaches ${formatNumber(sl, 2)} (stop-loss)`);
    if (target !== null) parts.push(`${what} reaches ${formatNumber(target, 2)} (target)`);
  }
  const when = form.measure === "SPOT" && form.check === "CLOSE" ? " on a closed candle" : "";
  const n = picked.length;
  const order = picked.some((l) => l.short) ? " - sold legs first" : "";
  return `If ${parts.join(" or ")}${when}, ${n === 1 ? "this leg exits" : `these ${n} legs exit`}${order}.`;
}

/** How far a level still is, in its own unit: rupees for ₹ profit / loss, points for spot or premium. Pure. */
export function distanceText(x: Pick<CombinedExit, "measure">, gap: number | null, masked = false): string {
  if (gap === null || !Number.isFinite(gap)) return "waiting for a price";
  if (gap <= 0) return "reached";
  if (x.measure === "MTM_INR") return masked ? "₹ ••• to go" : `${formatINR(gap)} to go`;
  return `${formatNumber(gap, 2)} points to go`;
}

/** The chip's words (CX6): what it is, how many legs, and the distance to the stop-loss (else the target). Pure. */
export function chipText(x: CombinedExit, masked = false): { title: string; distance: string; closing: boolean } {
  const n = x.legs.length;
  const kind = x.sl_value !== null ? "Combined stop-loss" : "Combined target";
  const title = `${kind} · ${n} ${n === 1 ? "leg" : "legs"}`;
  if (x.status === "FIRING") return { title, distance: "closing the legs now", closing: true };
  const gap = x.sl_value !== null ? x.distance.sl : x.distance.target;
  const head = distanceText(x, gap, masked);
  const both = x.sl_value !== null && x.target_value !== null ? ` · target ${distanceText(x, x.distance.target, masked)}` : "";
  return { title, distance: `${x.sl_value !== null ? "stop " : ""}${head}${both}`, closing: false };
}

/** CX-F1: the "stale price" mark - which legs are read at their last traded price. Null when every leg is fresh. Pure. */
export function staleText(x: Pick<CombinedExit, "stale">): string | null {
  const legs = x.stale ?? [];
  if (!legs.length) return null;
  const names = legs.map((l) => `${l.symbol} (last traded ${formatNumber(l.price, 2)})`).join(", ");
  return `No fresh price for ${names}. It keeps watching on the last traded price.`;
}

/** The nearest stop of the active exits, for the status bar (CX6). Null when none is active. Pure. */
export function statusSummary(list: readonly CombinedExit[], masked = false): { text: string; stale: boolean; title: string } | null {
  const live = active(list);
  if (!live.length) return null;
  const firing = live.find((x) => x.status === "FIRING");
  const stale = live.some((x) => (x.stale ?? []).length > 0);
  const title = live.map((x) => x.words).join(" ");
  if (firing) return { text: "Combined exit: closing the legs", stale, title };
  const head = live.length === 1 ? "Combined exit" : `${live.length} combined exits`;
  const first = live[0];
  const gap = first.sl_value !== null ? first.distance.sl : first.distance.target;
  return { text: `${head}: ${distanceText(first, gap, masked)}`, stale, title };
}

/** A sheet opening's reference (letters, digits and dashes, as the route asks). */
export function newRef(): string {
  const c = globalThis.crypto as Crypto | undefined;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  return `cx-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/** A failed call as one plain line (never a crash). */
export function failText(head: string, e: unknown): string {
  const why = friendlyError(e).trim();
  return `${head} ${why || "Try again in a moment."}`;
}

// --- CX-F2: "Exit short now" ------------------------------------------------------------------------------------------

/** The `?exit=<id>` of the CRITICAL notice's link (app/execution/group_exits.py exit_short_action). Pure. */
export function exitParam(search: string): number | null {
  const raw = new URLSearchParams(search).get("exit");
  return raw && /^\d{1,12}$/.test(raw) ? Number(raw) : null;
}

export function exitShortCopy(symbol: string): { title: string; body: string; confirm: string } {
  return {
    title: "Exit short now?",
    body: `${symbol} closes at the current price. A combined exit tried to close it and it is still open.`,
    confirm: "Exit short now",
  };
}
