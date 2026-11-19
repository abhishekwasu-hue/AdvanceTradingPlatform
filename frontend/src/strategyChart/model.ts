import { request } from "../api/client";
import type { ChartMarker } from "../charting/markers";
import type { StudyLine } from "../charting/engine";

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
export interface PlotReply {
  strategy_id: string; name: string; timeframe: string | null; available: boolean; reason: string | null;
  series?: PlotSeries[]; markers?: PlotMarker[]; why?: Record<string, WhyRow[]>; trades?: PlotTrade[];
  state?: "warming" | "waiting" | "in_trade" | "exited"; last_signal?: { t: string; side: "long" | "short" } | null; gaps?: string[];
}

export const strategyChartApi = {
  list: () => request<{ strategies: ChartStrategy[] }>("/strategies/v2/chart-list"),
  plot: (body: { strategy_id: string; symbol: string; exchange: string }) =>
    request<PlotReply>("/strategies/v2/plot", { method: "POST", body: JSON.stringify(body) }),
};

const unix = (iso: string) => Math.floor(Date.parse(iso) / 1000);

/** The strategy's own indicators as chart studies: price-unit lines on the price pane, every other family in its own
 * pane (RSI and MACD never stack); colours from the chart's own overlay tokens. */
export function strategyStudies(reply: PlotReply, colours: readonly string[], key: string): { id: string; line: StudyLine }[] {
  return (reply.series ?? []).map((s, i) => ({
    id: `strategy:${key}:${s.id}`,
    line: {
      data: s.points.filter((p) => p.v !== null).map((p) => ({ time: unix(p.t), value: p.v as number })),
      color: colours[i % Math.max(1, colours.length)], title: s.label,
      pane: s.pane === "price" ? "price" : "own", group: s.pane === "price" ? undefined : `strategy:${s.pane}`,
    },
  }));
}

/** SPEC section 2: long = arrowUp below the bar, short = arrowDown above it, exits distinct (a circle coloured by the
 * trade's result, with the rule that closed it); a signal while already in a trade is a small muted square (OQ-1). */
export function strategyMarkers(reply: PlotReply): ChartMarker[] {
  return (reply.markers ?? []).map((m) => {
    const long = m.side === "long";
    if (m.kind === "exit") {
      const won = (m.pnl ?? 0) >= 0;
      return { timestamp: m.t, position: long ? "aboveBar" : "belowBar", shape: "circle", color: won ? "up" : "down", text: exitWord(m.rule) };
    }
    if (m.kind === "skipped") return { timestamp: m.t, position: long ? "belowBar" : "aboveBar", shape: "square", color: "fg", text: "" };
    return { timestamp: m.t, position: long ? "belowBar" : "aboveBar", shape: long ? "arrowUp" : "arrowDown", color: long ? "up" : "down",
      text: long ? "Long" : "Short" };
  });
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
