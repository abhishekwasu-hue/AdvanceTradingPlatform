import { request } from "../api/client";
import type { ChartMarker } from "../charting/markers";
import type { LayerBand, LayerData, LayerLine, LayerSegment, StudyLine } from "../charting/engine";

/**
 * SFC P1 (owner APPROVED 19 Nov, docs/plans/strategy-first-chart-PLAN.md): his own strategy on the chart. The server
 * reads the bars the way his bot does and runs the SAME evaluator as the backtest and the bot; this module only turns
 * its answer into chart parts. Nothing here orders anything.
 */
export interface ChartStrategy { id: string; name: string; timeframe: string | null; drawable: boolean; reason: string | null }

export interface PlotSeries { id: string; label: string; pane: string; points: { t: string; v: number | null }[] }
export interface PlotMarker { id: string; t: string; kind: "entry" | "skipped" | "exit"; side: "long" | "short"; rule?: string; pnl?: number | null }
export interface WhyRow { id: string; held: boolean; text: string; values: Record<string, number | null>; timeframe: string }
export interface PlotTrade { entry_time: string; entry: number | null; side: "long" | "short"; stop: number | null; target: number | null;
  exit_time: string | null; exit: number | null; exit_rule: string | null; pnl: number | null }
/** P2: a fixed number his condition compares a series with (a flat line in that series' pane). */
export interface PlotLevel { id: string; value: number; pane: string; label: string }
/** P2: a price-action shape his conditions read on the last closed bar (a breakout's box, a zone band). */
export interface PlotPa { id: string; kind: "band" | "box"; low: number; high: number; label: string; from: string; to: string; role?: string | null;
  level?: number | null }
/** P2: the trade open on the last bar, with his own stop / target (none set: none drawn). */
export interface PlotPosition { side: "long" | "short"; entry_time: string; entry: number | null; stop: number | null; target: number | null }
export interface PlotReply {
  strategy_id: string; name: string; timeframe: string | null; available: boolean; reason: string | null;
  series?: PlotSeries[]; markers?: PlotMarker[]; why?: Record<string, WhyRow[]>; trades?: PlotTrade[];
  state?: "warming" | "waiting" | "in_trade" | "exited"; last_signal?: { t: string; side: "long" | "short" } | null; gaps?: string[];
  levels?: PlotLevel[]; pa?: PlotPa[]; position?: PlotPosition | null; pending?: { t: string; side: "long" | "short" } | null;
}

/** P2: the chart's forming candle, sent for the pending hint only (display only on the server too). */
export interface FormingBar { t: string; o: number; h: number; l: number; c: number; v?: number | null }

export const strategyChartApi = {
  list: () => request<{ strategies: ChartStrategy[] }>("/strategies/v2/chart-list"),
  plot: (body: { strategy_id: string; symbol: string; exchange: string; forming?: FormingBar }) =>
    request<PlotReply>("/strategies/v2/plot", { method: "POST", body: JSON.stringify(body) }),
};

const unix = (iso: string) => Math.floor(Date.parse(iso) / 1000);

/** The strategy's own indicators as chart studies: price-unit lines on the price pane, every other family in its own
 * pane (RSI and MACD never stack); all in the strategy's own colour (P2: two strategies never share one), and each fixed
 * number it compares a series with as a flat line in that series' pane. */
export function strategyStudies(reply: PlotReply, colour: string, key: string, times: readonly number[] = []): { id: string; line: StudyLine }[] {
  const lineOf = (pane: string) => ({ pane: pane === "price" ? "price" as const : "own" as const, group: pane === "price" ? undefined : `strategy:${pane}` });   // one pane per family, shared by his strategies (one scale)
  const series = (reply.series ?? []).map((s) => ({
    id: `strategy:${key}:${s.id}`,
    line: { data: s.points.filter((p) => p.v !== null).map((p) => ({ time: unix(p.t), value: p.v as number })), color: colour, title: s.label, ...lineOf(s.pane) },
  }));
  const span = times.length ? times : (series[0]?.line.data.map((d) => d.time) ?? []);
  const levels = (reply.levels ?? []).filter((lv) => Number.isFinite(lv.value) && span.length).map((lv) => ({
    id: `strategy:${key}:${lv.id}`,
    line: { data: span.map((time) => ({ time, value: lv.value })), color: colour, title: lv.label, ...lineOf(lv.pane) },
  }));
  return [...series, ...levels];
}

const roleOf = (role: string | null | undefined): LayerBand["role"] => {
  const r = (role ?? "").toLowerCase();
  return r.includes("demand") || r.includes("support") ? "demand" : r.includes("supply") || r.includes("resist") ? "supply" : "neutral";
};

/** P2: the strategy's price-action shapes and its open trade as one chart layer: a zone band from its own start, a box
 * as its four edges, a box's level as a line; the open trade's entry / stop / target as lines and its profit / loss
 * sides as bands from the entry on (OQ-10: no target - only the loss side, never a made-up target). */
export function strategyLayer(reply: PlotReply, colour: string, tones: { up: string; down: string }): LayerData {
  const bands: LayerBand[] = [];
  const segments: LayerSegment[] = [];
  const lines: LayerLine[] = [];
  for (const p of reply.pa ?? []) {
    if (!Number.isFinite(p.low) || !Number.isFinite(p.high)) continue;
    if (p.kind === "band") {
      bands.push({ low: p.low, high: p.high, color: colour, opacity: 0.16, label: p.label, role: roleOf(p.role), start: unix(p.from) });
    } else {
      const [t1, t2] = [unix(p.from), unix(p.to)];
      for (const [a, pa, b, pb] of [[t1, p.high, t2, p.high], [t1, p.low, t2, p.low], [t1, p.low, t1, p.high], [t2, p.low, t2, p.high]] as const) {
        segments.push({ t1: a, p1: pa, t2: b, p2: pb, color: colour, width: 1, style: "solid" });
      }
      if (p.level != null && Number.isFinite(p.level)) segments.push({ t1, p1: p.level, t2, p2: p.level, color: colour, width: 1, style: "dashed", extend: "right", label: p.label });
    }
  }
  const pos = reply.position;
  if (pos && pos.entry != null) {
    const start = unix(pos.entry_time);
    const long = pos.side === "long";
    lines.push({ price: pos.entry, color: colour, style: "solid", title: "Entry" });
    if (pos.stop != null) {
      lines.push({ price: pos.stop, color: tones.down, style: "dashed", title: "SL" });
      bands.push({ low: Math.min(pos.entry, pos.stop), high: Math.max(pos.entry, pos.stop), opacity: 0.12, role: long ? "supply" : "demand", start,
        color: colour, id: "position-loss" });
    }
    if (pos.target != null) {
      lines.push({ price: pos.target, color: tones.up, style: "dashed", title: "Target" });
      bands.push({ low: Math.min(pos.entry, pos.target), high: Math.max(pos.entry, pos.target), opacity: 0.12, role: long ? "demand" : "supply", start,
        color: colour, id: "position-profit" });
    }
  }
  return { bands, segments, lines };
}

/** SPEC section 2: long = arrowUp below the bar, short = arrowDown above it, exits distinct (a circle coloured by the
 * trade's result, with the rule that closed it); a signal while already in a trade is a small muted square (OQ-1). */
export function strategyMarkers(reply: PlotReply, forming: string | null = null): ChartMarker[] {
  const pending: ChartMarker[] = reply.pending && forming && reply.pending.t && Date.parse(reply.pending.t) === Date.parse(forming)
    ? [{ timestamp: forming, position: reply.pending.side === "long" ? "belowBar" : "aboveBar", shape: "circle", color: "fg", text: "Forming" }] : [];
  return [...(reply.markers ?? []).map((m): ChartMarker => {
    const long = m.side === "long";
    if (m.kind === "exit") {
      const won = (m.pnl ?? 0) >= 0;
      return { timestamp: m.t, position: long ? "aboveBar" : "belowBar", shape: "circle", color: won ? "up" : "down", text: exitWord(m.rule) };
    }
    if (m.kind === "skipped") return { timestamp: m.t, position: long ? "belowBar" : "aboveBar", shape: "square", color: "fg", text: "" };
    return { timestamp: m.t, position: long ? "belowBar" : "aboveBar", shape: long ? "arrowUp" : "arrowDown", color: long ? "up" : "down",
      text: long ? "Long" : "Short" };
  }), ...pending];
}

/** The exit rule in a word or two ("STOP_LOSS" -> "Stop", "TARGET_1" -> "Target" ...). */
export function exitWord(rule: string | undefined): string {
  const r = (rule ?? "").toUpperCase();
  if (r.includes("TRAIL")) return "Trailing";
  if (r.includes("STOP")) return "Stop";
  if (r.includes("TARGET")) return "Target";
  if (r.includes("TIME") || r.includes("SQUARE")) return "Time";
  if (r.includes("SIGNAL") || r.includes("EXIT")) return "Exit rule";
  return "Exit";
}

export const STATE_WORDS: Record<NonNullable<PlotReply["state"]>, string> = {
  warming: "Warming up", waiting: "Waiting", in_trade: "In trade", exited: "Exited",
};
