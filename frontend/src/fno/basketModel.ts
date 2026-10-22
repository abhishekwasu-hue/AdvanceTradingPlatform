/**
 * FNO-2 (owner order section 5 and 8b-c): the basket panel's pure parts (tested in basket.test.tsx) - the contract as
 * traders write it, the one-tap ready strategies from the chain on screen and the user's own settings, the request
 * body, the payoff in plain words, the words of a basket's result, saved baskets (the user's own preferences namespace
 * `fno_baskets`) and the hand-over to the Options Builder. Every figure shown comes from the server (the builder's engine
 * and the ticket's checks); nothing here makes up a price, a margin or a probability. Information only: no wording
 * here tells the user what to trade.
 */
import { request } from "../api/client";
import { ApiError } from "../api/errors";
import { formatNumber, RUPEE } from "../ui/format";
import type { ChainRow } from "../optionChain/types";
import type { Evaluation } from "../optionsBuilder/model";
import type { BasketAsk, BasketLegAsk, BasketResult, BasketSettings, LegStatus, Product, Right, Side } from "./api";
import type { BasketLeg, NewLeg } from "./basket";

const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

/** "NIFTY 28 OCT 22800 CE", as the server writes it (fno_ticket.contract_label). */
export function contractLabel(underlying: string, expiry: string, strike: number, right: Right): string {
  const [, m, d] = expiry.split("-").map(Number);
  const k = Number.isInteger(strike) ? String(strike) : String(Math.round(strike * 100) / 100);
  return `${underlying} ${String(d).padStart(2, "0")} ${MONTHS[(m || 1) - 1]} ${k} ${right}`;
}

export function legLabel(leg: Pick<BasketLeg, "underlying" | "expiry" | "strike" | "right" | "label">): string {
  return leg.label ?? contractLabel(leg.underlying, leg.expiry, leg.strike, leg.right);
}

// --- one-tap ready strategies --------------------------------------------------------------------------------------

export const STRATEGIES = ["Bull put spread", "Bear call spread", "Iron condor", "Straddle", "Strangle", "Calendar"] as const;
export type StrategyName = (typeof STRATEGIES)[number];

export const STRATEGY_TEXT: Record<StrategyName, string> = {
  "Bull put spread": "Sell a put at the strike, buy a put lower",
  "Bear call spread": "Sell a call at the strike, buy a call higher",
  "Iron condor": "A put spread below and a call spread above",
  Straddle: "Buy a call and a put at the strike",
  Strangle: "Buy a call above and a put below",
  Calendar: "Sell this expiry's call, buy the next expiry's",
};

/** The strike step of a chain: the smallest gap between neighbouring strikes (null with fewer than two). */
export function strikeStep(rows: readonly ChainRow[]): number | null {
  const ks = rows.map((r) => r.strike).sort((a, b) => a - b);
  let step: number | null = null;
  for (let i = 1; i < ks.length; i += 1) {
    const gap = Math.round((ks[i] - ks[i - 1]) * 100) / 100;
    if (gap > 0 && (step === null || gap < step)) step = gap;
  }
  return step;
}

/** A sold leg's stop-loss: `pct` % above its price, on the 0.05 tick (null without a price). */
export function stopFor(price: number | null | undefined, pct: number): number | null {
  if (typeof price !== "number" || !(price > 0) || !(pct > 0)) return null;
  const ticks = Math.ceil(Math.round((price * (1 + pct / 100)) / 0.05 * 1e6) / 1e6);
  const value = Math.round(ticks * 5) / 100;
  return value > price ? value : null;
}

export interface StrategyInputs {
  underlying: string;
  expiry: string;
  rows: readonly ChainRow[];
  /** The strike to build around: the user's chosen one, else the chain's ATM. */
  around: number;
  settings: Pick<BasketSettings, "width_steps" | "lots" | "stop_pct">;
  /** The next expiry's chain, for a Calendar. */
  next?: { expiry: string; rows: readonly ChainRow[] } | null;
}

type Shape = { side: Side; right: Right; offset: number; next?: boolean };

const SHAPES: Record<StrategyName, Shape[]> = {
  "Bull put spread": [{ side: "BUY", right: "PE", offset: -1 }, { side: "SELL", right: "PE", offset: 0 }],
  "Bear call spread": [{ side: "BUY", right: "CE", offset: 1 }, { side: "SELL", right: "CE", offset: 0 }],
  "Iron condor": [{ side: "BUY", right: "PE", offset: -2 }, { side: "BUY", right: "CE", offset: 2 },
    { side: "SELL", right: "PE", offset: -1 }, { side: "SELL", right: "CE", offset: 1 }],
  Straddle: [{ side: "BUY", right: "CE", offset: 0 }, { side: "BUY", right: "PE", offset: 0 }],
  Strangle: [{ side: "BUY", right: "CE", offset: 1 }, { side: "BUY", right: "PE", offset: -1 }],
  Calendar: [{ side: "BUY", right: "CE", offset: 0, next: true }, { side: "SELL", right: "CE", offset: 0 }],
};

/** The legs of a ready strategy around `around`, `width_steps` strikes apart, with the chain's prices and IVs and the
 * user's own lots and stop for a sold leg - or a plain reason when the chain cannot give them. */
export function readyStrategy(name: StrategyName, inp: StrategyInputs): { legs: NewLeg[]; problem: string | null } {
  const step = strikeStep(inp.rows);
  if (step === null) return { legs: [], problem: "The chain has too few strikes for a ready strategy." };
  const width = Math.max(1, Math.round(inp.settings.width_steps)) * step;
  const lots = Math.max(1, Math.round(inp.settings.lots));
  const legs: NewLeg[] = [];
  for (const s of SHAPES[name]) {
    const source = s.next ? inp.next : { expiry: inp.expiry, rows: inp.rows };
    if (!source) return { legs: [], problem: "A calendar needs the next expiry's chain, which is not loaded yet." };
    const strike = Math.round((inp.around + s.offset * width) * 100) / 100;
    const row = source.rows.find((r) => Math.abs(r.strike - strike) < 1e-6);
    if (!row) return { legs: [], problem: `The chain has no ${formatNumber(strike, 0)} strike for the ${source.expiry} expiry. Pick another strike or a smaller width.` };
    const quote = s.right === "CE" ? row.call : row.put;
    const price = typeof quote.ltp === "number" && quote.ltp > 0 ? quote.ltp : null;
    legs.push({ underlying: inp.underlying, expiry: source.expiry, strike, right: s.right, side: s.side, lots, price, label: null,
      iv: quote.iv ?? null, stopLoss: s.side === "SELL" ? stopFor(price, inp.settings.stop_pct) : null });
  }
  return { legs, problem: null };
}

// --- the request ---------------------------------------------------------------------------------------------------

export function legAsk(l: NewLeg): BasketLegAsk {
  return { underlying: l.underlying, expiry: l.expiry, strike: l.strike, right: l.right, side: l.side, lots: l.lots,
    price: typeof l.price === "number" && l.price > 0 ? l.price : null,
    stop_loss: l.side === "SELL" && typeof l.stopLoss === "number" && l.stopLoss > 0 ? l.stopLoss : null,
    iv: typeof l.iv === "number" && l.iv > 0 && l.iv < 500 ? l.iv / 100 : null };
}

export function basketAsk(legs: readonly NewLeg[], spot: number | null, product: Product): BasketAsk {
  return { legs: legs.map(legAsk), spot: spot && spot > 0 ? spot : null, product };
}

/** What is missing before the basket can go to the confirm step (the server checks everything else). */
export function basketProblems(legs: readonly NewLeg[]): string[] {
  const out: string[] = [];
  if (!legs.length) out.push("Add a leg first.");
  if (new Set(legs.map((l) => l.underlying)).size > 1) out.push("A basket holds one underlying. Remove the legs of the other.");
  for (const l of legs) {
    if (l.side === "SELL" && !(typeof l.stopLoss === "number" && l.stopLoss > 0)) out.push(`${legLabel(l)}: a sold leg needs a stop-loss.`);
  }
  return out;
}

// --- the payoff in plain words (8b-c) -------------------------------------------------------------------------------

/** "about ₹4,200": rounded to a readable step (₹100 from ₹1,000, ₹10 from ₹100). */
export function aboutMoney(v: number): string {
  const a = Math.abs(v);
  const step = a >= 1000 ? 100 : a >= 100 ? 10 : 1;
  return `${RUPEE}${formatNumber(Math.round(a / step) * step, 0)}`;
}

const priceText = (v: number) => formatNumber(Math.round(v), 0);

/** One sentence for the outcomes at expiry, from the engine's expiry curve and breakevens, e.g. "If NIFTY closes below
 * 24,400 at expiry you lose up to about ₹7,500; above 24,400 you keep up to about ₹7,500." Information only. */
export function payoffSentence(e: Pick<Evaluation, "prices" | "at_expiry" | "breakevens" | "extremes" | "net_premium">, underlying: string): string {
  const ys = e.at_expiry;
  const xs = e.prices;
  if (!ys || !e.extremes || xs.length < 2) {
    return "These legs expire on different dates, so there is no single outcome at expiry: the chart shows the value on each day.";
  }
  const win = e.net_premium > 0 ? "keep" : "make";
  const eps = Math.max(1, Math.max(...ys.map(Math.abs)) * 1e-6);
  const bounds = [-Infinity, ...(e.breakevens ?? []).slice().sort((a, b) => a - b), Infinity];
  const parts: string[] = [];
  for (let i = 0; i < bounds.length - 1; i += 1) {
    const lo = bounds[i];
    const hi = bounds[i + 1];
    const idx = xs.map((x, j) => j).filter((j) => xs[j] > lo && xs[j] < hi && Math.abs(ys[j]) > eps);
    if (!idx.length) continue;
    const sign = Math.sign(ys[idx[Math.floor(idx.length / 2)]]);
    const vals = idx.map((j) => ys[j]).filter((y) => Math.sign(y) === sign).map(Math.abs);
    const most = Math.max(...vals);
    const least = Math.min(...vals);
    const flat = most - least <= Math.max(1, most * 0.01);
    const where = lo === -Infinity ? (hi === Infinity ? "anywhere" : `below ${priceText(hi)}`)
      : hi === Infinity ? `above ${priceText(lo)}` : `between ${priceText(lo)} and ${priceText(hi)}`;
    const growsDown = lo === -Infinity && Math.abs(ys[0]) > Math.abs(ys[1]) + eps;
    const growsUp = hi === Infinity && (sign < 0 ? e.extremes.unbounded_loss : e.extremes.unbounded_profit);
    const amount = flat && !growsDown && !growsUp ? `about ${aboutMoney(most)}` : growsDown || growsUp ? `about ${aboutMoney(most)} or more` : `up to about ${aboutMoney(most)}`;
    const more = growsDown ? ", more the lower it closes" : growsUp ? ", more the higher it closes" : "";
    parts.push(`${where} you ${sign > 0 ? win : "lose"} ${amount}${more}`);
  }
  if (!parts.length) return `At expiry this basket ends about even whatever ${underlying} closes at.`;
  const [first, ...rest] = parts;
  const lead = first.startsWith("anywhere") ? `Wherever ${underlying} closes at expiry${first.slice("anywhere".length)}`
    : `If ${underlying} closes ${first.replace(/ you /, " at expiry you ")}`;
  return `${[lead, ...rest].join("; ")}.`;
}

// --- the result of Execute basket ----------------------------------------------------------------------------------

export const STATUS_WORD: Record<LegStatus, string> = { PLACED: "Filled", REFUSED: "Refused", FAILED: "Failed", NOT_PLACED: "Not placed" };

export function resultHeadline(r: BasketResult): string {
  const n = r.legs.length;
  if (r.status === "PLACED") return `All ${n} legs placed in practice mode, hedge legs first.`;
  if (r.status === "PARTIAL") return `${r.placed} of ${n} legs filled; ${n - r.placed} did not. Each leg says what happened.`;
  return r.placed === 0 && r.reasons.length ? "Nothing was placed." : "No leg filled. Each leg says why.";
}

/** The legs that did not fill, in the user's own order, for "Place the remaining legs" (only on the user's tap). */
export function remainingLegs<T>(sent: readonly T[], r: BasketResult): T[] {
  const left = new Set(r.legs.filter((l) => l.status !== "PLACED").map((l) => l.leg));
  return sent.filter((_, i) => left.has(i));
}

// --- saved baskets: the user's own preferences namespace `fno_baskets` ------------------------------------------------

export interface SavedLeg { expiry: string; strike: number; right: Right; side: Side; lots: number; price?: number | null; stop_loss?: number | null }
export interface SavedBasket { name: string; underlying: string; product: Product; legs: SavedLeg[]; saved_at?: string | null }
export type SavedBaskets = Record<string, SavedBasket>;
export interface SavedState { items: SavedBaskets; version: number }
export const MAX_SAVED = 8;
const NAME = /^[A-Za-z0-9][A-Za-z0-9 &()+\-/.,:]{0,39}$/;

export function nameProblem(name: string): string | null {
  return NAME.test(name.trim()) ? null : "Use up to 40 letters, digits, spaces or & ( ) + - / . , : for the name.";
}

export function savedOf(legs: readonly NewLeg[], name: string, product: Product, now = new Date()): SavedBasket {
  return {
    name: name.trim(), underlying: legs[0]?.underlying ?? "", product, saved_at: now.toISOString(),
    legs: legs.map((l) => ({ expiry: l.expiry, strike: l.strike, right: l.right, side: l.side, lots: l.lots,
      ...(typeof l.price === "number" && l.price > 0 ? { price: l.price } : {}),
      ...(l.side === "SELL" && typeof l.stopLoss === "number" && l.stopLoss > 0 ? { stop_loss: l.stopLoss } : {}) })),
  };
}

export function legsOf(s: SavedBasket): NewLeg[] {
  return s.legs.map((l) => ({ underlying: s.underlying, expiry: l.expiry, strike: l.strike, right: l.right, side: l.side, lots: l.lots,
    price: l.price ?? null, stopLoss: l.stop_loss ?? null, label: null, iv: null }));
}

export function newSavedId(): string {
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`.toLowerCase().replace(/[^a-z0-9]/g, "").slice(0, 16).padEnd(8, "0");
}

type Fetcher = <T>(path: string, init?: RequestInit) => Promise<T>;

function readItems(value: unknown): SavedBaskets {
  const items = (value as { items?: unknown } | null)?.items;
  return items && typeof items === "object" ? (items as SavedBaskets) : {};
}

export async function loadBaskets(fetcher: Fetcher = request): Promise<SavedState> {
  const r = await fetcher<{ prefs: { fno_baskets?: unknown }; versions: { fno_baskets?: number } }>("/me/prefs");
  return { items: readItems(r.prefs?.fno_baskets), version: r.versions?.fno_baskets ?? 0 };
}

export type SaveOutcome = { ok: true; state: SavedState } | { ok: false; conflict: SavedState | null; message: string };

/** Saves the whole list on the version it was made on; another device's newer list is a conflict (its list returned). */
export async function saveBaskets(items: SavedBaskets, version: number, fetcher: Fetcher = request): Promise<SaveOutcome> {
  try {
    const r = await fetcher<{ value: unknown; version: number }>(`/me/prefs/fno_baskets?version=${version}`, { method: "PUT", body: JSON.stringify({ items }) });
    return { ok: true, state: { items: readItems(r.value), version: r.version } };
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) {
      try {
        const detail = (JSON.parse(e.body) as { detail?: { version?: unknown; current?: unknown } }).detail;
        if (typeof detail?.version === "number") {
          return { ok: false, conflict: { items: readItems(detail.current), version: detail.version },
            message: "Your saved baskets changed on another device. That list is shown now; save again." };
        }
      } catch { /* fall through */ }
    }
    return { ok: false, conflict: null, message: e instanceof Error ? e.message : String(e) };
  }
}

// --- "Open in Options Builder" --------------------------------------------------------------------------------------

export const BUILDER_HANDOFF_KEY = "atp_builder_handoff";

export interface BuilderHandoff {
  underlying: string;
  spot: number;
  step: number | null;
  lot_size: number;
  near_expiry: string;
  next_expiry: string | null;
  legs: { direction: Side; option_type: Right; strike: number; premium: number; lots: number; lot_size: number; expiry: string; iv: number | null }[];
}

/** The basket as the Options Builder reads it once on opening (every leg needs its price and lot size). */
export function builderHandoff(legs: readonly NewLeg[], lotSizes: readonly (number | null)[], spot: number, step: number | null): BuilderHandoff | null {
  if (!legs.length || legs.some((l, i) => !(typeof l.price === "number" && l.price > 0) || !lotSizes[i])) return null;
  const expiries = [...new Set(legs.map((l) => l.expiry))].sort();
  return {
    underlying: legs[0].underlying, spot, step, lot_size: lotSizes[0] as number, near_expiry: expiries[0], next_expiry: expiries[1] ?? null,
    legs: legs.map((l, i) => ({ direction: l.side, option_type: l.right, strike: l.strike, premium: l.price as number, lots: l.lots,
      lot_size: lotSizes[i] as number, expiry: l.expiry, iv: typeof l.iv === "number" && l.iv > 0 ? l.iv / 100 : null })),
  };
}

export function writeHandoff(h: BuilderHandoff): boolean {
  try { sessionStorage.setItem(BUILDER_HANDOFF_KEY, JSON.stringify(h)); return true; } catch { return false; }
}

/** Read once by the builder page (and removed, so a later visit starts empty). */
export function takeHandoff(): BuilderHandoff | null {
  try {
    const raw = sessionStorage.getItem(BUILDER_HANDOFF_KEY);
    sessionStorage.removeItem(BUILDER_HANDOFF_KEY);
    const h = raw ? (JSON.parse(raw) as BuilderHandoff) : null;
    return h && Array.isArray(h.legs) && h.legs.length && typeof h.spot === "number" ? h : null;
  } catch {
    return null;
  }
}
