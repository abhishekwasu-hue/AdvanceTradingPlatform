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
import { signedParts } from "../ui/Signed";
import type { ContractFacts } from "./contracts";

export type Measure = "MTM_INR" | "SPOT" | "NET_PREMIUM";
export type CheckMode = "TOUCH" | "CLOSE";
/** CX2 optional trailing: LOCK - once the gain reaches A, the stop moves to keep B; TRAIL - every X of favourable move
 * shifts the stop by Y; SX2 LOCK_TRAIL - lock B at A, then every further X shifts it by Y; BREAK_EVEN - once the gain
 * reaches A, the stop moves to break-even. The numbers are the user's own (no default). The stop only ever moves his way. */
export type TrailKind = "NONE" | "LOCK" | "TRAIL" | "LOCK_TRAIL" | "BREAK_EVEN";
export interface TrailBody { kind: Exclude<TrailKind, "NONE">; after?: number; lock?: number; step?: number; by?: number }
/** A stop that appears once a gain is reached (no stop-loss needed to start). */
export const isLocking = (k: TrailKind | undefined) => k === "LOCK" || k === "LOCK_TRAIL" || k === "BREAK_EVEN";

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
  /** CX2: the trailing as set (null / absent: none) and the stop in force now (₹ MTM signed: -X is a loss of ₹X). */
  trail_kind?: TrailKind;
  trailing?: { after?: number; lock?: number; step?: number; by?: number } | null;
  stop_now?: number | null;
  stop_moved?: boolean;
  locked?: boolean;
}

export interface CombinedBody {
  scope: "SELECTED";
  trade_ids: number[];
  measure: Measure;
  sl_value: number | null;
  target_value: number | null;
  spot_symbol?: string | null;
  spot_exchange?: "NSE" | "BSE" | null;
  /** M68 PR-3 (TS1-031): left out = the server reads his own setting (₹ / premium: every price, their only check). */
  check_mode?: CheckMode;
  timeframe_minutes?: number | null;
  client_ref: string;
  trailing?: TrailBody | null;
}

export interface CombinedPreview { ok: boolean; reasons: string[]; words: string; value_now: number | null; distance: { sl: number | null; target: number | null } }

export const combinedApi = {
  list: () => request<CombinedExit[]>("/paper/fno/group-exits"),
  preview: (body: CombinedBody) => request<CombinedPreview>("/paper/fno/group-exits/preview", { method: "POST", body: JSON.stringify(body) }),
  create: (body: CombinedBody) => request<CombinedExit>("/paper/fno/group-exits", { method: "POST", body: JSON.stringify(body) }),
  edit: (id: number, body: { version: number; sl_value: number | null; target_value: number | null; check_mode?: CheckMode; timeframe_minutes?: number | null; trailing?: TrailBody | null }) =>
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
  /** M68 PR-3 (TS1-031): "" = not chosen yet (his own Trade settings > Exits value starts it, else the form asks). */
  measure: Measure | "";
  sl: string;
  target: string;
  check: CheckMode | "";
  timeframe: number;
  /** CX2 (optional): the trailing kind and its two numbers - LOCK: A = the gain, B = what is kept; TRAIL: A = the move,
   * B = how far the stop shifts. */
  trailKind?: TrailKind;
  trailA?: string;
  trailB?: string;
  /** SX2 LOCK_TRAIL only: C = the further move, D = how far the stop shifts. */
  trailC?: string;
  trailD?: string;
}

/** A typed amount: a positive finite number, or null (empty / not a number). */
export function amount(text: string): number | null {
  const v = Number(text.replace(/[₹,\s]/g, ""));
  return text.trim() !== "" && Number.isFinite(v) && v > 0 ? v : null;
}

/** A typed amount that may be zero (what a lock keeps): a finite number >= 0, or null. */
export function amountOrZero(text: string): number | null {
  const v = Number(text.replace(/[₹,\s]/g, ""));
  return text.trim() !== "" && Number.isFinite(v) && v >= 0 ? v : null;
}

/** CX2: the trailing's two labels in the measure's own unit (₹ for the combined profit / loss, points for a level). */
export function trailLabels(kind: TrailKind, measure: Measure): { a: string; b?: string; c?: string; d?: string } {
  const unit = measure === "MTM_INR" ? "₹" : "points";
  if (kind === "LOCK") return { a: `Once the gain reaches (${unit})`, b: `Move the stop to keep (${unit})` };
  if (kind === "BREAK_EVEN") return { a: `Once the gain reaches (${unit})` };
  if (kind === "LOCK_TRAIL") {
    return { a: `Once the gain reaches (${unit})`, b: `Move the stop to keep (${unit})`, c: `Then for every further move of (${unit})`,
      d: `Shift the stop by (${unit})` };
  }
  return { a: `For every move your way of (${unit})`, b: `Shift the stop by (${unit})` };
}

/** CX2: plain-word reasons the trailing cannot be set (the same checks as the server's). Pure. */
export function trailErrors(form: CombinedForm): string[] {
  const kind = form.trailKind ?? "NONE";
  if (kind === "NONE") return [];
  const a = form.trailA ?? "";
  const b = form.trailB ?? "";
  const out: string[] = [];
  if (kind === "BREAK_EVEN") {
    if (amount(a) === null) out.push("Enter the gain after which the stop moves to break-even (more than zero).");
    return out;
  }
  if (kind === "LOCK" || kind === "LOCK_TRAIL") {
    const after = amount(a);
    const keep = amountOrZero(b);
    if (after === null) out.push("Enter the gain after which the stop moves (more than zero).");
    if (keep === null) out.push("Enter how much of the gain to keep (zero or more).");
    if (after !== null && keep !== null && keep >= after) out.push("The gain kept must be less than the gain that moves the stop.");
    if (kind === "LOCK") return out;
    const step = amount(form.trailC ?? "");
    const by = amount(form.trailD ?? "");
    if (step === null) out.push("Enter the further move that shifts the stop (more than zero).");
    if (by === null) out.push("Enter how far the stop shifts each time (more than zero).");
    if (step !== null && by !== null && by > step) out.push("The stop cannot shift further than the move itself.");
    return out;
  }
  const step = amount(a);
  const by = amount(b);
  if (amount(form.sl) === null) out.push("A trailing stop needs a stop-loss to start from.");
  if (step === null) out.push("Enter the move that shifts the stop (more than zero).");
  if (by === null) out.push("Enter how far the stop shifts each time (more than zero).");
  if (step !== null && by !== null && by > step) out.push("The stop cannot shift further than the move itself.");
  return out;
}

/** CX2: the request's trailing (null when none). Pure. */
export function trailOf(form: CombinedForm): TrailBody | null {
  const kind = form.trailKind ?? "NONE";
  if (kind === "LOCK") return { kind, after: amount(form.trailA ?? "") ?? undefined, lock: amountOrZero(form.trailB ?? "") ?? undefined };
  if (kind === "TRAIL") return { kind, step: amount(form.trailA ?? "") ?? undefined, by: amount(form.trailB ?? "") ?? undefined };
  if (kind === "BREAK_EVEN") return { kind, after: amount(form.trailA ?? "") ?? undefined };
  if (kind === "LOCK_TRAIL") {
    return { kind, after: amount(form.trailA ?? "") ?? undefined, lock: amountOrZero(form.trailB ?? "") ?? undefined,
      step: amount(form.trailC ?? "") ?? undefined, by: amount(form.trailD ?? "") ?? undefined };
  }
  return null;
}

/** CX2: the trailing in plain words, the same words as group_exits.trail_words (empty when none or not complete). Pure. */
export function trailWords(form: CombinedForm, spotSymbol: string | null = null): string {
  const t = trailOf(form);
  if (!t || !form.measure || trailErrors({ ...form, sl: form.sl || "1" }).length) return "";
  if (t.kind === "LOCK_TRAIL" || t.kind === "BREAK_EVEN") return sxTrailWords(t, form.measure, spotSymbol);
  if (form.measure === "MTM_INR") {
    return t.kind === "LOCK"
      ? `Once the combined profit reaches ${formatINR(t.after ?? 0)}, the stop moves to keep ${formatINR(t.lock ?? 0)}.`
      : `For every ${formatINR(t.step ?? 0)} the combined profit rises, the stop moves up ${formatINR(t.by ?? 0)}.`;
  }
  const what = form.measure === "SPOT" ? `${spotSymbol ?? "the"} spot` : "net premium";
  return t.kind === "LOCK"
    ? `Once the ${what} moves ${formatNumber(t.after ?? 0, 2)} points your way, the stop moves to keep ${formatNumber(t.lock ?? 0, 2)} points.`
    : `For every ${formatNumber(t.step ?? 0, 2)} points the ${what} moves your way, the stop moves ${formatNumber(t.by ?? 0, 2)} points with it.`;
}

/** SX2: lock-and-trail / break-even, the same words as group_exits._sx_trail_words. Pure. */
function sxTrailWords(t: TrailBody, measure: Measure, spotSymbol: string | null): string {
  if (measure === "MTM_INR") {
    return t.kind === "BREAK_EVEN"
      ? `Once the combined profit reaches ${formatINR(t.after ?? 0)}, the stop moves to break-even (no loss).`
      : `Once the combined profit reaches ${formatINR(t.after ?? 0)}, the stop moves to keep ${formatINR(t.lock ?? 0)}; then for every further `
        + `${formatINR(t.step ?? 0)}, it moves up ${formatINR(t.by ?? 0)}.`;
  }
  const what = measure === "SPOT" ? `${spotSymbol ?? "the"} spot` : "net premium";
  return t.kind === "BREAK_EVEN"
    ? `Once the ${what} moves ${formatNumber(t.after ?? 0, 2)} points your way, the stop moves to break-even.`
    : `Once the ${what} moves ${formatNumber(t.after ?? 0, 2)} points your way, the stop moves to keep ${formatNumber(t.lock ?? 0, 2)} points; `
      + `then for every further ${formatNumber(t.step ?? 0, 2)} points, it moves ${formatNumber(t.by ?? 0, 2)} points with it.`;
}

/** Plain-word reasons the form cannot be set yet (the server checks again). Pure. */
export function formErrors(form: CombinedForm, legs: readonly SheetLeg[]): string[] {
  const out: string[] = [];
  const picked = legs.filter((l) => form.ticked.includes(l.id));
  if (!picked.length) out.push("Tick at least one open position.");
  if (!form.measure) out.push("Choose what the exit watches.");                          // M68 PR-3: never pre-chosen
  if (form.measure === "SPOT" && !form.check) out.push("Choose when the spot level counts as reached.");
  if (new Set(picked.map((l) => l.mode ?? "")).size > 1) out.push("PAPER and LIVE positions cannot share one combined exit.");
  if (form.sl.trim() && amount(form.sl) === null) out.push("The stop-loss must be a number above zero.");
  if (form.target.trim() && amount(form.target) === null) out.push("The target must be a number above zero.");
  if (amount(form.sl) === null && amount(form.target) === null && !isLocking(form.trailKind)) out.push("Enter a stop-loss, a target, or both.");
  if (form.measure !== "MTM_INR" && amount(form.sl) === null && amount(form.target) === null && isLocking(form.trailKind)) {
    out.push("A spot or premium lock needs a stop-loss or a target too, so the exit knows which way is yours.");
  }
  out.push(...trailErrors(form));
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
  const measure: Measure = form.measure || "MTM_INR";        // only sent once formErrors is empty (a measure is chosen)
  return {
    scope: "SELECTED", trade_ids: legs.filter((l) => form.ticked.includes(l.id)).map((l) => l.id), measure,
    sl_value: amount(form.sl), target_value: amount(form.target),
    spot_symbol: spot ? first?.spotSymbol ?? null : null, spot_exchange: spot ? first?.spotExchange ?? null : null,
    ...(spot && form.check ? { check_mode: form.check } : {}), timeframe_minutes: close ? form.timeframe : null, client_ref: ref,
    ...(trailOf(form) ? { trailing: trailOf(form) } : {}),
  };
}

/** The preview sentence on the page while the server's own is on its way (the same words as group_exits.describe). Pure. */
export function previewWords(form: CombinedForm, legs: readonly SheetLeg[]): string {
  const picked = legs.filter((l) => form.ticked.includes(l.id));
  const sl = amount(form.sl);
  const target = amount(form.target);
  const trailing = trailWords(form, picked[0]?.spotSymbol ?? null);
  if (!picked.length || !form.measure || (sl === null && target === null && !trailing)) return "";
  const parts: string[] = [];
  if (form.measure === "MTM_INR") {
    if (sl !== null) parts.push(`combined loss reaches ${formatINR(sl)}`);
    if (target !== null) parts.push(`combined profit reaches ${formatINR(target)}`);
  } else {
    const what = form.measure === "SPOT" ? `${picked[0].spotSymbol ?? "the"} spot` : "net premium";
    if (sl !== null) parts.push(`${what} reaches ${formatNumber(sl, 2)} (stop-loss)`);
    if (target !== null) parts.push(`${what} reaches ${formatNumber(target, 2)} (target)`);
  }
  if (!parts.length) parts.push("the trailing stop is reached");
  const when = form.measure === "SPOT" && form.check === "CLOSE" ? " on a closed candle" : "";
  const n = picked.length;
  const order = picked.some((l) => l.short) ? " - sold legs first" : "";
  const tail = trailing ? ` ${trailing} The stop only moves your way.` : "";
  return `If ${parts.join(" or ")}${when}, ${n === 1 ? "this leg exits" : `these ${n} legs exit`}${order}.${tail}`;
}

/** How far a level still is, in its own unit: rupees for ₹ profit / loss, points for spot or premium. Pure. */
export function distanceText(x: Pick<CombinedExit, "measure">, gap: number | null, masked = false): string {
  if (gap === null || !Number.isFinite(gap)) return "waiting for a price";
  if (gap <= 0) return "reached";
  if (x.measure === "MTM_INR") return masked ? "₹ ••• to go" : `${formatINR(gap)} to go`;
  return `${formatNumber(gap, 2)} points to go`;
}

/** CX2 / F2: the stop in force now - the server's `stop_now` as it is, never worked out here (null: no trailing). Before
 * the trailing starts it is the user's own stop; after, the trailed one. ₹ MTM as the shared signed value (F3: "+₹500" a
 * stop in profit, "−₹2,500" one still in loss); a level: the level. Pure. */
export function stopNowText(x: Pick<CombinedExit, "measure" | "trail_kind" | "stop_now" | "stop_moved">, masked = false): string | null {
  const parts = stopNowParts(x, masked);
  return parts && (parts.value !== undefined ? `${parts.lead}${signedParts({ value: parts.value }).text}` : parts.lead);
}

/** The same, split for rendering: `lead` words and the signed ₹ `value` (absent: the words say it all). Pure. */
export function stopNowParts(x: Pick<CombinedExit, "measure" | "trail_kind" | "stop_now" | "stop_moved">, masked = false):
  { lead: string; value?: number } | null {
  if (!x.trail_kind || x.trail_kind === "NONE") return null;
  const stop = x.stop_now ?? null;
  if (stop === null) return { lead: "trailing waits for the gain" };
  const moved = x.stop_moved ? "stop moved to" : "stop at";
  if (x.measure === "MTM_INR") return masked ? { lead: `${moved} ₹ •••` } : { lead: `${moved} `, value: stop };
  return { lead: `${moved} ${formatNumber(stop, 2)}` };
}

/** The chip's words (CX6): what it is, how many legs, and the distance to the stop-loss (else the target); with a
 * trailing (CX2), the stop in force now. Pure. */
export function chipText(x: CombinedExit, masked = false):
  { title: string; distance: string; closing: boolean; stop?: string; stopLead?: string; stopValue?: number } {
  const n = x.legs.length;
  const hasStop = x.sl_value !== null || (x.stop_now ?? null) !== null;
  const trailing = !!x.trail_kind && x.trail_kind !== "NONE";
  // SX6: an exit set with the strategy at entry is named as such (the same chip, Edit and Remove).
  const kind = x.origin === "SX" ? "Strategy exit"
    : trailing ? "Combined trailing stop" : x.sl_value !== null ? "Combined stop-loss" : "Combined target";
  const title = `${kind} · ${n} ${n === 1 ? "leg" : "legs"}`;
  const stop = stopNowText(x, masked);
  const parts = stopNowParts(x, masked);
  const shown = stop && parts ? { stop, ...(parts.value !== undefined ? { stopLead: parts.lead, stopValue: parts.value } : {}) } : {};
  if (x.status === "FIRING") return { title, distance: "closing the legs now", closing: true, ...shown };
  // F2: the distance is the server's, measured to the same `stop_now` the stop words show.
  const gap = hasStop ? x.distance.sl : x.distance.target;
  const head = distanceText(x, gap, masked);
  const both = hasStop && x.target_value !== null ? ` · target ${distanceText(x, x.distance.target, masked)}` : "";
  return { title, distance: `${hasStop ? "stop " : ""}${head}${both}`, closing: false, ...shown };
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
  const gap = first.sl_value !== null || (first.stop_now ?? null) !== null ? first.distance.sl : first.distance.target;
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
