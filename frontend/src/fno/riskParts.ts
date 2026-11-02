/**
 * F&O workspace 8b, slice 4 (owner order ATP_SESSION_FNO_WORKSPACE.md, section 8b-d): the risk line's other parts - the
 * events before the expiry, the strike's liquidity and the margin beside the PAPER balance (tested in riskParts.test.tsx).
 * Only data ATP already has: the events calendar (backend app/paper/fno_insights.py event_risk), the chain on screen
 * (bid, ask, OI, volume) and the margin estimate the ticket / basket already show. Where a figure is missing the line
 * says so; nothing is made up. Every line is information only: no button reads it, it never holds back an order or exit.
 */
import { request } from "../api/client";
import type { ChainLeg, ChainRow } from "../optionChain/types";
import { formatCount, formatDateIST, formatINR, formatNumber, formatPrice } from "../ui/format";
import type { LiquidityLimits } from "../settings/liquidityLimits";
import type { ContractRight } from "./api";
import type { RiskBudget, RiskLine } from "./insights";

// --- events ------------------------------------------------------------------------------------------------------------

export interface RiskEvent { date: string; kind: string; name: string; scope: string; rule: string | null; source: "market" | "company" }
export interface EventRisk {
  underlying: string; from: string; until: string; clipped: boolean; events: RiskEvent[]; more: number;
  calendar_empty: boolean; message: string | null; source: string; blocks: false;
}

export const EVENTS_NOTE = "From your events calendar.";
/** Said once under the parts (each part names only its source). */
export const CHECKS_NOTE = "None of this holds back the order or any exit.";
export const EVENTS_UNREAD = "The events calendar could not be read just now, so event risk is not checked here.";

/** The events before the expiry in plain words; null while it is being read. */
export function eventLine(ev: EventRisk | "error" | null, expiry: string | null | undefined): RiskLine | null {
  if (ev === null) return null;
  if (ev === "error") return { tone: "info", note: EVENTS_NOTE, text: EVENTS_UNREAD };
  if (ev.calendar_empty) return { tone: "info", note: EVENTS_NOTE, text: ev.message ?? EVENTS_UNREAD };
  const until = expiry ? `the ${formatDateIST(expiry)} expiry` : "the expiry";
  const read = ev.clipped ? ` (read up to ${formatDateIST(ev.until)})` : "";
  if (!ev.events.length) return { tone: "info", note: EVENTS_NOTE, text: `No event in your events calendar between today and ${until}${read}.` };
  const items = ev.events.map((e) => `${e.name} on ${formatDateIST(e.date)} (${e.scope})${e.rule ? `. ${e.rule.replace(/\.$/, "")}` : ""}`);
  const more = ev.more > 0 ? ` And ${ev.more} more.` : "";
  return { tone: "warn", note: EVENTS_NOTE, text: `Before ${until}${read}: ${items.join("; ")}.${more}` };
}

// --- liquidity -----------------------------------------------------------------------------------------------------------

export interface LegLiquidity {
  label: string;
  /** false: the strike is not on the chain on screen (another expiry, or out of the strikes shown). */
  onScreen: boolean;
  bid: number | null; ask: number | null; ltp: number | null; oi: number | null; volume: number | null;
}

// M76-2 (owner-approved, M76Q-3): the line states the leg's own facts (bid, ask, gap, open interest, volume) and is never
// compared with ATP's idea of "thin" (the chain-quartile comparison is gone); it warns only against HIS OWN limits.
export const LIQUIDITY_NOTE = "From the chain on screen.";
export const NO_CHAIN = "Bid-ask and open interest are not known here. Open the chain to see them.";

const num = (v: number | null | undefined): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** The bid-ask gap as a percent of its middle; null without both quotes. */
export function gapPct(leg: Pick<ChainLeg, "bid" | "ask">): number | null {
  const bid = num(leg.bid);
  const ask = num(leg.ask);
  if (bid === null || ask === null || !(bid > 0) || !(ask >= bid)) return null;
  return ((ask - bid) / ((ask + bid) / 2)) * 100;
}

/** One strike's liquidity facts from the chain rows on screen (no comparison with the chain's other strikes). */
export function legLiquidity(rows: readonly ChainRow[] | null | undefined, strike: number, right: ContractRight, label: string): LegLiquidity {
  const row = right === "FUT" ? undefined : (rows ?? []).find((r) => Math.abs(r.strike - strike) < 1e-6);
  if (!row || right === "FUT") return { label, onScreen: false, bid: null, ask: null, ltp: null, oi: null, volume: null };
  const leg = right === "CE" ? row.call : row.put;
  return { label, onScreen: true, bid: num(leg.bid), ask: num(leg.ask), ltp: num(leg.ltp), oi: num(leg.oi), volume: num(leg.volume) };
}

/** One leg's liquidity facts in plain words. */
function facts(l: LegLiquidity): string {
  if (!l.onScreen) return "not on the chain on screen, so its bid-ask and open interest are not known here";
  const g = gapPct(l);
  const parts = [
    l.bid !== null && l.bid > 0 ? `bid ${formatPrice(l.bid)}` : "no bid quoted right now",
    l.ask !== null && l.ask > 0 ? `ask ${formatPrice(l.ask)}` : "no ask quoted right now",
    g !== null ? `bid-ask gap ${formatPrice((l.ask ?? 0) - (l.bid ?? 0))} (${formatNumber(g, 1)}% of the price)` : null,
    l.oi === 0 ? "no open interest yet" : l.oi !== null ? `open interest ${formatCount(l.oi)}` : null,
    l.volume === 0 ? "no trades in it today" : l.volume !== null ? `volume ${formatCount(l.volume)} today` : null,
  ].filter(Boolean);
  return parts.join(", ");
}

// FNO-8B-28: his own limits (an optional override, OFF by default; src/settings/liquidityLimits.ts). Only words: the line
// says whether the strike is inside them; nothing reads it and it never holds back an order or an exit.
export const LIMITS_NOTE = "From the chain on screen, compared with your own limits.";

const pctWord = (v: number) => {
  const t = formatNumber(v, 2);
  return `${t.includes(".") ? t.replace(/\.?0+$/, "") : t}%`;
};

/** His limits in words: "bid-ask gap up to 2%, open interest at least 5,000". */
export function limitsText(limits: LiquidityLimits): string {
  return [
    limits.max_spread_pct !== null ? `bid-ask gap up to ${pctWord(limits.max_spread_pct)}` : null,
    limits.min_oi !== null ? `open interest at least ${formatCount(limits.min_oi)}` : null,
  ].filter(Boolean).join(", ");
}

export interface LimitCheck { outside: string[]; unknown: string[] }

/** One leg against his own limits: what is outside them and what cannot be checked (each in plain words). */
export function checkLimits(l: LegLiquidity, limits: LiquidityLimits): LimitCheck {
  const outside: string[] = [];
  const unknown: string[] = [];
  if (limits.max_spread_pct !== null) {
    const g = l.onScreen ? gapPct(l) : null;
    if (g === null) unknown.push("the bid-ask gap");
    else if (g > limits.max_spread_pct) outside.push(`bid-ask gap ${formatNumber(g, 1)}% is above your ${pctWord(limits.max_spread_pct)}`);
  }
  if (limits.min_oi !== null) {
    if (!l.onScreen || l.oi === null) unknown.push("the open interest");
    else if (l.oi < limits.min_oi) outside.push(`open interest ${formatCount(l.oi)} is below your ${formatCount(limits.min_oi)}`);
  }
  return { outside, unknown };
}

/** The sentence about his own limits for the leg(s), and whether any leg is outside them. */
export function limitWords(items: readonly LegLiquidity[], limits: LiquidityLimits): { text: string; outside: boolean } {
  const checks = items.map((l) => ({ l, c: checkLimits(l, limits) }));
  const many = items.length > 1;
  const out = checks.filter((x) => x.c.outside.length);
  if (out.length) {
    const parts = out.map((x) => (many ? `${x.l.label}: ` : "") + x.c.outside.join("; "));
    return { outside: true, text: `Outside your own limits: ${parts.join(". ")}.` };
  }
  const unknown = checks.filter((x) => x.c.unknown.length);
  if (unknown.length) {
    const head = `Your own limits (${limitsText(limits)}) cannot be fully checked here`;
    if (!many) {
      const u = unknown[0].c.unknown;
      return { outside: false, text: `${head}: ${u.join(" and ")} ${u.length > 1 ? "are" : "is"} not known.` };
    }
    return { outside: false, text: `${head}: not known for ${unknown.map((x) => `${x.l.label} (${x.c.unknown.join(" and ")})`).join(", ")}.` };
  }
  return { outside: false, text: `${many ? "Every leg is inside" : "Inside"} your own limits (${limitsText(limits)}).` };
}

/** The liquidity of the order's strike(s). `chainKnown` false (the ticket opened away from the chain): said plainly.
 * `limits` (FNO-8B-28, only while he turned them on): the line also says whether the strike is inside his own limits. */
export function liquidityLine(items: readonly LegLiquidity[], chainKnown = true, limits: LiquidityLimits | null = null): RiskLine | null {
  const own = limits && limits.on && (limits.max_spread_pct !== null || limits.min_oi !== null) ? limits : null;
  const note = own ? LIMITS_NOTE : LIQUIDITY_NOTE;
  if (!chainKnown) {
    return { tone: "info", note, text: own ? `${NO_CHAIN} Your own limits (${limitsText(own)}) cannot be checked here.` : NO_CHAIN };
  }
  if (!items.length) return null;
  const base = factsLine(items);
  if (!own) return base;
  const w = limitWords(items, own);
  return { tone: w.outside ? "warn" : "info", note, text: `${base.text} ${w.text}` };
}

/** The facts of each leg, never a judgement: always an information line (M76-2). */
function factsLine(items: readonly LegLiquidity[]): RiskLine {
  if (items.length === 1) return { tone: "info", note: LIQUIDITY_NOTE, text: `Liquidity: ${facts(items[0])}.` };
  return { tone: "info", note: LIQUIDITY_NOTE, text: `Liquidity: ${items.map((l) => `${l.label}: ${facts(l)}`).join(". ")}.` };
}

// --- margin --------------------------------------------------------------------------------------------------------------

export const MARGIN_NOTE = "Your PAPER balance is your starting capital plus your closed PAPER trades; margin your open positions use is not taken off.";

const pctText = (v: number) => (v > 0 && v < 0.1 ? "under 0.1%" : `${formatNumber(Math.round(v * 10) / 10, v >= 10 ? 0 : 1)}%`);

/** The margin estimate beside his PAPER balance; null while the balance is not known. */
export function marginLine(margin: number | null | undefined, b: RiskBudget | null | undefined): RiskLine | null {
  const balance = b?.paper_balance?.amount;
  if (typeof balance !== "number" || !Number.isFinite(balance)) return null;
  if (typeof margin !== "number" || !Number.isFinite(margin) || !(margin > 0)) {
    return { tone: "info", note: MARGIN_NOTE, text: `The margin is not estimated here, so it is not set beside your PAPER balance of ${formatINR(balance)}.` };
  }
  if (!(balance > 0) || margin > balance) {
    return { tone: "warn", note: MARGIN_NOTE, text: `Margin estimate ${formatINR(margin)} is more than your PAPER balance of ${formatINR(balance)}.` };
  }
  return { tone: "info", note: MARGIN_NOTE, text: `Margin estimate ${formatINR(margin)} is ${pctText((margin / balance) * 100)} of your PAPER balance of ${formatINR(balance)}.` };
}

// --- the read --------------------------------------------------------------------------------------------------------

export const riskPartsApi = {
  eventRisk: (underlying: string, until: string) =>
    request<EventRisk>(`/paper/fno/event-risk?${new URLSearchParams({ underlying, until }).toString()}`),
};
export type RiskPartsApi = typeof riskPartsApi;
