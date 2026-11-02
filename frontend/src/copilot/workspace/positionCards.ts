/**
 * M76 MP-10 (owner order M76 E-3 / E-4; flag `copilot_workspace`): the live position cards inside the workspace - the
 * pure parts. One card per structure (the legs placed together, from `/paper/fno/positions/groups`) or per single
 * position on the chart's underlying: its P&L (the positions list's own figures), the spot's distance to each sold
 * strike in points and %, the delta (the Greeks route's own per-leg figures), the days to expiry (the contract facts),
 * and what the one-tap exits need.
 *
 * Every exit here is the existing one (ADR-0004): "Close all legs" = Exit the whole group (a basket) or Exit now (a
 * strategy structure closes whole, a single position), "Close half" = Exit part with half the lots in the stepper (his
 * tap, his confirm), "Close leg" = Exit this leg, "Stop to entry" = Modify of the position's own waiting stop (its level
 * set to the entry price, his confirm). Nothing here adds a check to an exit or decides one for him; no figure is made up
 * (a missing price or Greek reads "Not known").
 */
import type { LayerLine } from "../../charting/engine";
import type { ContractFacts, PositionGroup } from "../../fno/contracts";
import { openLots, type WaitingOrder } from "../../fno/positionActions";
import type { HeldRow } from "../../trading/model";
import type { TradeRecord } from "../../types";

/** The F&O underlying of a symbol (the server's UNDERLYING_ALIASES, app/instruments/master.py). Pure. */
const ALIASES: Record<string, string> = {
  "NIFTY 50": "NIFTY", "NIFTY BANK": "BANKNIFTY", "NIFTY FIN SERVICE": "FINNIFTY", "NIFTY MID SELECT": "MIDCPNIFTY", "NIFTY NEXT 50": "NIFTYNXT50",
};
export function rootOf(symbol: string | null | undefined): string {
  const key = (symbol ?? "").trim().toUpperCase().replace(/\s+/g, " ");
  return ALIASES[key] ?? key;
}

/** The facts of one position's contract, by its trading symbol and exchange. */
export type FactsOf = (p: TradeRecord) => ContractFacts | undefined;

/** The underlying a position belongs to: its contract's, else its record's, else its own symbol (equity). Pure. */
export function underlyingOfPosition(p: TradeRecord, facts: ContractFacts | undefined): string {
  if (facts && facts.known) return rootOf(facts.underlying);
  return rootOf(p.underlying_symbol || p.symbol);
}

/** His open positions on the chart's underlying. Pure. */
export function positionsHere(list: readonly TradeRecord[], chartSymbol: string, factsOf: FactsOf): TradeRecord[] {
  const root = rootOf(chartSymbol);
  return list.filter((p) => !p.exit_time && underlyingOfPosition(p, factsOf(p)) === root);
}

/** The Greeks route's per-leg answer (`GET /api/positions/greeks?mine=true&underlying=`). */
export interface GreeksLeg { trade_id: number; position_delta: number }
export interface GreeksRead { spot: Record<string, number>; legs: GreeksLeg[] }

/** The spot the Greeks route priced this underlying at (its own LTP read), or null. Pure. */
export function spotOf(g: GreeksRead | null, chartSymbol: string): number | null {
  if (!g) return null;
  const root = rootOf(chartSymbol);
  const hit = Object.entries(g.spot ?? {}).find(([k, v]) => rootOf(k) === root && typeof v === "number" && v > 0);
  return hit ? hit[1] : null;
}

export interface CardLeg {
  trade: TradeRecord;
  label: string;
  sold: boolean;
  right: "CE" | "PE" | "FUT" | null;
  strike: number | null;
  lots: number;
  pnl: number | null;
  /** A basket's bought leg protecting an open sold leg (its Exit this leg warns first - the existing rule). */
  hedge: boolean;
}

export interface Distance { strike: number; right: "CE" | "PE"; points: number; pct: number }

export interface PositionCard {
  key: string;
  kind: "single" | "basket" | "structure";
  title: string;
  legs: CardLeg[];
  pnl: number | null;
  distances: Distance[];
  delta: number | null;
  daysLeft: number | null;
  /** Every leg is a PAPER position (the existing exits act on PAPER positions; a LIVE one closes in the broker's app). */
  paper: boolean;
}

const round2 = (v: number) => Math.round(v * 100) / 100;

/** The spot's distance to a sold strike: + = the spot is above it. Null without a spot. Pure. */
export function distanceTo(spot: number | null, strike: number, right: "CE" | "PE"): Distance | null {
  if (spot == null || !(spot > 0) || !(strike > 0)) return null;
  const points = round2(spot - strike);
  return { strike, right, points, pct: round2((points / spot) * 100) };
}

/** The cards for his positions on this underlying, structures first in the groups' order. Pure. */
export function buildCards(here: readonly TradeRecord[], rows: ReadonlyMap<number, HeldRow>, groups: readonly PositionGroup[],
  factsOf: FactsOf, greeks: GreeksRead | null, spot: number | null): PositionCard[] {
  const deltaOf = new Map((greeks?.legs ?? []).map((l) => [l.trade_id, l.position_delta]));
  const byId = new Map(here.map((p) => [p.id, p]));
  const used = new Set<number>();
  const cards: PositionCard[] = [];
  const card = (key: string, kind: PositionCard["kind"], title: string, trades: TradeRecord[], hedges: ReadonlySet<number>) => {
    const legs: CardLeg[] = trades.map((t) => {
      const f = factsOf(t);
      const known = f && f.known ? f : null;
      return {
        trade: t, label: known?.label ?? t.symbol, sold: t.direction === "SHORT", right: known?.right ?? null,
        strike: known && known.right !== "FUT" ? known.strike : null, lots: openLots(t), pnl: rows.get(t.id)?.pnl ?? null, hedge: hedges.has(t.id),
      };
    });
    const pnls = legs.map((l) => l.pnl);
    const deltas = trades.map((t) => deltaOf.get(t.id));
    const days = trades.map((t) => { const f = factsOf(t); return f && f.known ? f.days_left : null; }).filter((d): d is number => typeof d === "number");
    const options = trades.some((t) => t.instrument_kind === "OPTION");
    cards.push({
      key, kind, title, legs,
      pnl: pnls.every((v) => typeof v === "number") ? round2(pnls.reduce((a: number, v) => a + (v as number), 0)) : null,
      distances: legs.flatMap((l) => (l.sold && (l.right === "CE" || l.right === "PE") && l.strike != null ? [distanceTo(spot, l.strike, l.right)] : []))
        .filter((d): d is Distance => d !== null),
      delta: options && deltas.every((d) => typeof d === "number") ? round2(deltas.reduce((a: number, d) => a + (d as number), 0)) : null,
      daysLeft: days.length ? Math.min(...days) : null,
      paper: trades.every((t) => t.mode === "PAPER"),
    });
  };
  for (const g of groups) {
    const trades = g.trade_ids.map((id) => byId.get(id)).filter((t): t is TradeRecord => !!t);
    if (!trades.length) continue;
    trades.forEach((t) => used.add(t.id));
    card(`g-${g.key}`, g.kind, g.name, trades, new Set(g.hedge_ids ?? []));
  }
  for (const p of here) {
    if (used.has(p.id)) continue;
    const f = factsOf(p);
    card(`p-${p.id}`, p.leg_group_id ? "structure" : "single", f && f.known ? f.label : p.symbol, [p], new Set());
  }
  return cards;
}

/** "Close half" is Exit part with half the open lots (rounded down): offered where Exit part is (a PAPER position of two
 *  lots or more that is not a leg of a structure - `canExitPart`). Pure. */
export function halfOf(p: TradeRecord): number | null {
  const held = openLots(p);
  return p.mode === "PAPER" && !p.leg_group_id && held >= 2 ? Math.floor(held / 2) : null;
}

/** The position's own waiting stop on the contract's price and its new level at the entry price, or null (no such stop,
 *  a stop on the underlying's level - an entry price is the contract's - or it is already at the entry). Pure. */
export function stopToEntry(waiting: readonly WaitingOrder[], p: TradeRecord): { order: WaitingOrder; level: number } | null {
  const order = waiting.find((o) => o.purpose === "EXIT" && o.kind === "STOP" && o.trade_id === p.id && o.trigger.source === "PREMIUM"
    && o.status === "RESTING");
  if (!order || !(p.entry_price > 0) || order.trigger.level === p.entry_price) return null;
  return { order, level: p.entry_price };
}

/** His waiting stops and targets on the underlying's own level, for these positions: drawn on the chart as lines. Pure. */
export function exitLines(waiting: readonly WaitingOrder[], tradeIds: ReadonlySet<number>, colors: { stop: string; target: string }): LayerLine[] {
  return waiting.filter((o) => o.purpose === "EXIT" && o.trigger.source === "SPOT" && o.trade_id != null && tradeIds.has(o.trade_id)
    && Number.isFinite(o.trigger.level))
    .map((o) => ({ price: o.trigger.level, color: o.kind === "STOP" ? colors.stop : colors.target, style: "dashed" as const,
      title: `${o.kind === "STOP" ? "Stop" : "Target"} · ${o.symbol}` }));
}
