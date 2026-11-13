/**
 * Owner bug report 13 Nov ("I clicked Chart and the NIFTY chart opened; the option's chart should have opened"): a
 * position's Chart button opens the chart of the contract HELD - an option's premium candles, a future's own price, a
 * stock as it is - with this position's own lines on it (its average, and the stop / targets he set on that price), and
 * a one-tap switch to the underlying (its own stop / targets and the short strike's area). The candles come through the
 * same candle read as any chart (his broker session reads an NFO / BFO contract by its trading symbol; the stored chain
 * history holds chain snapshots, not candles). A contract without candles says so and shows the underlying, labelled -
 * never the underlying silently as if it were the option. Pure: no fetch, no order path (ADR-0006).
 */
import type { LayerBand } from "../charting/engine";
import type { MyDayArea } from "../dashboard/myDay";
import { chartTabPath } from "../shell/shellRoutes";
import { ownLevel, ownStop } from "../trading/positionStops";
import type { TradeRecord } from "../types";
import type { LevelLine } from "./chartCore";

export type PositionChartView = "contract" | "underlying";

/** The address keys (also the chart panel's keys): the position, which chart of it, and "the contract had no candles". */
export const POSITION_PARAM = "position";
export const VIEW_PARAM = "on";
export const NO_CANDLES_PARAM = "nocandles";

type Held = Pick<TradeRecord, "id" | "symbol" | "exchange" | "instrument_kind" | "underlying_symbol">;

/** An option or a future with a known underlying: it has a contract chart AND an underlying chart. Pure. */
export function hasUnderlying(p: Pick<TradeRecord, "instrument_kind" | "underlying_symbol">): boolean {
  return (p.instrument_kind === "OPTION" || p.instrument_kind === "FUTURE") && !!p.underlying_symbol?.trim();
}

/** The underlying's own venue: a BSE contract's index (SENSEX) on BSE, every other on NSE (as chain_view.spot_instrument). */
export function underlyingExchange(contractExchange: string | null | undefined): string {
  const x = (contractExchange ?? "").toUpperCase();
  return x === "BFO" || x === "BSE_FO" ? "BSE" : "NSE";
}

/** What the chart of this position shows: the contract (or the stock) itself, or - asked - its underlying. Pure. */
export function positionChartTarget(p: Held, view: PositionChartView): { symbol: string; exchange: string; view: PositionChartView } {
  const contract = p.instrument_kind === "OPTION" || p.instrument_kind === "FUTURE";
  if (view === "underlying" && hasUnderlying(p)) {
    return { symbol: (p.underlying_symbol as string).trim().toUpperCase(), exchange: underlyingExchange(p.exchange), view: "underlying" };
  }
  return { symbol: p.symbol.trim().toUpperCase(), exchange: (p.exchange ?? (contract ? "NFO" : "NSE")).toUpperCase(), view: "contract" };
}

/** The Chart button's address: `/charts/NIFTY%2025150%20CE%2017%20NOV%2026?exchange=NFO&position=12` (the chart panel
 * opens it in place). `noCandles`: the contract had none, so this is its underlying, labelled so. Pure. */
export function positionChartPath(p: Held, view: PositionChartView = "contract", noCandles = false): string {
  const t = positionChartTarget(p, view);
  const base = chartTabPath(t.symbol, t.exchange);
  const q = new URLSearchParams();
  q.set(POSITION_PARAM, String(p.id));
  if (t.view === "underlying") q.set(VIEW_PARAM, "underlying");
  if (noCandles && t.view === "underlying") q.set(NO_CANDLES_PARAM, "1");
  return `${base}${base.includes("?") ? "&" : "?"}${q.toString()}`;
}

/** The view an address asks for (anything but "underlying" is the contract). Pure. */
export function viewOf(raw: string | null | undefined): PositionChartView {
  return raw === "underlying" ? "underlying" : "contract";
}

const shortOf = (p: Pick<TradeRecord, "direction" | "option_position">) => p.direction === "SHORT" || p.option_position === "WRITE";

/** The name of the held contract's stop on its own price: a sold option's premium ceiling, a bought one's floor. */
export function contractStopLabel(p: Pick<TradeRecord, "instrument_kind" | "direction" | "option_position">): string {
  if (p.instrument_kind === "OPTION") return shortOf(p) ? "Premium ceiling" : "Premium floor";
  return "Stop";
}

function targets(t1: number | null | undefined, t2: number | null | undefined): LevelLine[] {
  const a = ownLevel(t1);
  const b = ownLevel(t2);
  if (a !== null && b !== null) return [{ price: a, title: "Target 1", tone: "up" }, { price: b, title: "Target 2", tone: "up" }];
  const one = a ?? b;
  return one !== null ? [{ price: one, title: "Target", tone: "up" }] : [];
}

/**
 * The lines this position draws on the chart shown. The contract (or a stock): its average and the stop / targets he
 * set on that same price (an option's targets are on its underlying - it has none on the premium; a stop he did not set
 * draws nothing). The underlying: the underlying stop / targets only (the premium is not on this price scale). The
 * live price is the chart's own last-price line. Pure.
 */
export function positionChartLevels(p: TradeRecord, view: PositionChartView): LevelLine[] {
  if (view === "underlying" && hasUnderlying(p)) {
    const stop = ownLevel(p.underlying_stop_loss);
    return [...(stop !== null ? [{ price: stop, title: "Stop", tone: "down" as const }] : []), ...targets(p.underlying_target1, p.underlying_target2)];
  }
  const stop = ownStop(p);
  return [
    { price: p.entry_price, title: "Avg", tone: "neutral" },
    ...(stop !== null ? [{ price: stop, title: contractStopLabel(p), tone: "down" as const }] : []),
    ...(p.instrument_kind === "OPTION" ? [] : targets(p.target1, p.target2)),
  ];
}

/** A short strike's nearest area (TERM-2 R3) as a band on the underlying's chart; none on the contract's. Pure. */
export function positionChartAreas(area: MyDayArea | null | undefined, view: PositionChartView): LayerBand[] {
  if (view !== "underlying" || !area || !(area.high > area.low)) return [];
  return [{ low: area.low, high: area.high, role: "neutral", label: "Area" }];
}

/** "NIFTY 25150 CE 17 NOV 26" -> the switch's short words: "Option", "Future", or the stock. Pure. */
export function contractWord(p: Pick<TradeRecord, "instrument_kind" | "symbol">): string {
  return p.instrument_kind === "OPTION" ? "Option" : p.instrument_kind === "FUTURE" ? "Future" : p.symbol;
}

/** The plain line over an underlying shown because the contract had no candles (never silent). Pure. */
export function noCandlesText(p: Pick<TradeRecord, "symbol" | "underlying_symbol">): string {
  return `No candles for ${p.symbol} from your broker. This chart is ${p.underlying_symbol ?? "the underlying"}, the underlying.`;
}

/** One chart's candle read as the embedded chart reports it, tagged with the chart it belongs to (`exchange:symbol`). */
export interface CandleRead { key: string; loading: boolean; count: number; empty: string | null; error: string | null }

/** The contract chart of an option or future falls back to its underlying when the read of THIS chart finished with no
 * candles at all - the broker sent none (`empty`) or refused (`error`). A read still loading, of another chart, or with
 * bars never falls back. Pure. */
export function fallsBack(p: Pick<TradeRecord, "instrument_kind" | "underlying_symbol">, view: PositionChartView, read: CandleRead | null, key: string): boolean {
  if (view !== "contract" || !hasUnderlying(p) || !read || read.key !== key) return false;
  return !read.loading && read.count === 0 && !!(read.empty || read.error);
}
