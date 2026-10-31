/**
 * N4 CH4a: the trades layer - the user's own PAPER trades on the chart (`GET /api/charts/layers/trades`, flag
 * `chart_trades_layer`; off -> 503 and nothing is shown). Every number comes from the trade rows: entry and exit at
 * their recorded prices on the trade's own chart; a derived contract (an option on this underlying) at its times only,
 * marked at the bar's low / high, because its prices are the contract's. As of `at` (replay): no trade or exit before it
 * happened. Read-only: nothing here can place or change an order.
 */
import { ApiError, request } from "../api/client";
import type { LayerData, LayerLabel, LayerLine, LayerSegment } from "./engine";
import { parseCandleTime } from "./replay";
import { formatNumber } from "../ui/format";

export const TRADES_LAYER_ID = "trades";
export const TRADE_COLORS = { long: "#0072B2", short: "#D55E00", exit: "#6B7280" } as const;   // Okabe-Ito, colour-blind safe

export interface TradeMark {
  id: number; symbol: string; side: "long" | "short"; mode: string; quantity: number; on_price_axis: boolean;
  contract: { symbol: string; kind: string; expiry: string | null; position: string | null; lot_size: number | null } | null;
  strategy: string;
  entry: { time: number; price: number };
  exit: { time: number; price: number; reason: string | null } | null;
  open: boolean; stop_loss: number | null; target: number | null; pnl: number | null; label: string;
  /** "initial stop" when replaying the past: the row's stop trails, so the stop in force then is not recorded - the
   * trade's initial stop is drawn; "stop" (the current one) only on the live chart. */
  stop_label?: "stop" | "initial stop" | null;
}
export interface TradesResponse {
  schema: "trades/1"; instrument: string; at: string; start: string; mode: string; trades: TradeMark[]; open: number; capped: boolean; note: string;
}

export const tradesApi = {
  layer: (instrument: string, at: string | null, start: string | null) => {
    const q = new URLSearchParams({ instrument });
    if (at) q.set("at", at);
    if (start) q.set("start", start);
    return request<TradesResponse>(`/charts/layers/trades?${q.toString()}`);
  },
};

export interface LayerBar { time: number; high: number; low: number }

/** The chart's bars (start times in epoch seconds, as the engine keys them), oldest first. */
export function layerBars(candles: readonly { timestamp: string; high: number; low: number }[]): LayerBar[] {
  const out: LayerBar[] = [];
  for (const c of candles) {
    const ms = parseCandleTime(c.timestamp);
    if (Number.isFinite(ms)) out.push({ time: Math.floor(ms / 1000), high: c.high, low: c.low });
  }
  return out;
}

/** The bar a time falls in: the last bar starting at or before it (null before the first bar - not on this chart). */
export function barAt(t: number, bars: readonly LayerBar[]): LayerBar | null {
  let lo = 0, hi = bars.length - 1, found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (bars[mid].time <= t) { found = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return found < 0 ? null : bars[found];
}

const money = (v: number) => formatNumber(v, 2);

/** The trades as chart shapes: a label at each entry and exit, a dashed segment from entry to exit (own chart), and the
 * open trade's stop and target as price lines (own chart only - a premium level never goes on the underlying's axis). */
export function toTradesLayer(resp: TradesResponse, bars: readonly LayerBar[]): LayerData {
  const labels: LayerLabel[] = [], segments: LayerSegment[] = [], lines: LayerLine[] = [];
  for (const t of resp.trades) {
    const long = t.side === "long";
    const color = long ? TRADE_COLORS.long : TRADE_COLORS.short;
    const inBar = barAt(t.entry.time, bars);
    if (!inBar) continue;                                         // entered before the first bar on screen
    const entryPrice = t.on_price_axis ? t.entry.price : long ? inBar.low : inBar.high;
    labels.push({ time: inBar.time, price: entryPrice, text: `${long ? "▲" : "▼"} ${t.label}`, color, position: long ? "below" : "above" });
    if (t.exit) {
      const outBar = barAt(t.exit.time, bars);
      if (outBar) {
        const exitPrice = t.on_price_axis ? t.exit.price : long ? outBar.high : outBar.low;
        const pnl = t.pnl == null ? "" : ` · P&L ${t.pnl >= 0 ? "+" : ""}${money(t.pnl)}`;
        const where = t.on_price_axis ? ` @ ${money(t.exit.price)}` : ` ${t.symbol}`;
        labels.push({ time: outBar.time, price: exitPrice, text: `✕ Exit${where}${t.exit.reason ? ` (${t.exit.reason})` : ""}${pnl}`,
                      color: TRADE_COLORS.exit, position: long ? "above" : "below" });
        if (t.on_price_axis) segments.push({ t1: inBar.time, p1: t.entry.price, t2: outBar.time, p2: t.exit.price, color, width: 1, style: "dashed" });
      }
    }
    if (t.open && t.on_price_axis) {
      if (t.stop_loss != null) lines.push({ price: t.stop_loss, color, style: "dashed", title: `${t.stop_label === "initial stop" ? "Initial stop" : "Stop"} ${money(t.stop_loss)}` });
      if (t.target != null) lines.push({ price: t.target, color, style: "dotted", title: `Target ${money(t.target)}` });
    }
  }
  return { asOf: resp.at, labels, segments, lines };
}

export interface TradesState { resp: TradesResponse | null; failed: string | null; available: boolean | null }

/** One request at a time: a new question clears the old answer at once (never trades as of another bar on screen), a
 * late answer to an older question is dropped, a failure draws nothing and says why, 503 (flag off) shows nothing. */
export class TradesLoader {
  private seq = 0;
  private available: boolean | null = null;

  constructor(private readonly fetcher: (instrument: string, at: string | null, start: string | null) => Promise<TradesResponse>,
              private readonly emit: (s: TradesState) => void) {}

  load(instrument: string | null, at: string | null, start: string | null): () => void {
    const my = ++this.seq;
    this.emit({ resp: null, failed: null, available: this.available });
    if (!instrument) return () => {};
    this.fetcher(instrument, at, start).then(
      (r) => { if (my === this.seq) { this.available = true; this.emit({ resp: r, failed: null, available: true }); } },
      (e: unknown) => {
        if (my !== this.seq) return;
        if (e instanceof ApiError && e.status === 503) { this.available = false; this.emit({ resp: null, failed: null, available: false }); return; }
        this.emit({ resp: null, failed: e instanceof Error ? e.message : "Your trades could not be loaded.", available: this.available });
      },
    );
    return () => { if (my === this.seq) this.seq++; };
  }
}
