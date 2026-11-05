import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

/**
 * CT-1 C10 (owner order CT-1): ONE "Ask AI" entry in the shell. Before, three pages had their own buttons and code -
 * the option chain's header "Ask AI", the Charts toolbar's "Read this chart" (flag `read_chart`) and its "Analyze a
 * chart" link (flag `chart_analyze`). Now the top bar has one Ask AI button; the page on screen hands it what it knows
 * (symbol, timeframe, chain expiry) through this context, and the sheet offers what fits:
 * - Read this chart: Charts, flag `read_chart`, the server's existing reading (src/chart/ReadChartV2.tsx; no new logic).
 * - Ask about this chain: the option chain page, the existing AskAi (src/optionChain/AskAiV2.tsx).
 * - Analyze a chart image: flag `chart_analyze` (the existing page).
 * - Open the Copilot: flag `ai_copilot`, with the symbol on screen.
 * A page outside the shell (no provider: tests, the older shell) keeps its own buttons, as PageIntro does with its help.
 */
export interface AskAiPageContext {
  /** The symbol on screen, e.g. "NIFTY". */
  symbol?: string;
  /** The chart's timeframe in words ("15 min", "Daily"). */
  timeframe?: string;
  /** The chain's expiry date ("2026-11-03"). */
  expiry?: string | null;
  /** Charts: the reading is offered (flag `read_chart` and a timeframe the server reads); `on` while it is drawn. */
  read?: { on: boolean; toggle: () => void };
  /** AN-1: the chart on screen can be analysed (Analyze a chart, ATP's own chart of it): its symbol and, when Analyze
   * reads it, its timeframe ("5m" / "15m" / "1h" / "1d"; else the trader picks one there). */
  analyze?: { symbol: string; timeframe?: string };
  /** The option chain page: what the chain's Ask AI needs. */
  chain?: { symbol: string; expiry: string | null; refreshKey?: number | string };
}

type Setter = (ctx: AskAiPageContext | null) => void;

const SetContext = createContext<Setter | null>(null);
const ValueContext = createContext<AskAiPageContext | null>(null);

/** The shell holds the context of the page on screen (one at a time). */
export function AskAiProvider({ children, initial = null }: { children: ReactNode; initial?: AskAiPageContext | null }) {
  const [value, set] = useState<AskAiPageContext | null>(initial);
  return (
    <SetContext.Provider value={set}>
      <ValueContext.Provider value={value}>{children}</ValueContext.Provider>
    </SetContext.Provider>
  );
}

/** What the page on screen handed to the shell (null: nothing, or no shell). */
export function useAskAiContext(): AskAiPageContext | null {
  return useContext(ValueContext);
}

/** Whether the shell's Ask AI entry is there: a page then leaves out its own button. */
export function useHasAskAiEntry(): boolean {
  return useContext(SetContext) !== null;
}

/** Hands this page's context to the shell's Ask AI while the page is on screen; `null` hands nothing. */
export function useRegisterAskAi(ctx: AskAiPageContext | null): void {
  const set = useContext(SetContext);
  // The toggle is read through a ref, so a new function each render does not hand the context again.
  const toggle = useRef<(() => void) | undefined>(undefined);
  toggle.current = ctx?.read?.toggle;
  const key = ctx ? JSON.stringify({ ...ctx, read: ctx.read ? { on: ctx.read.on } : undefined }) : null;
  useEffect(() => {
    if (!set || key === null) return undefined;
    const parsed = JSON.parse(key) as Omit<AskAiPageContext, "read"> & { read?: { on: boolean } };
    set({ ...parsed, read: parsed.read ? { on: parsed.read.on, toggle: () => toggle.current?.() } : undefined });
    return () => set(null);
  }, [set, key]);
}

export type AskAiOptionId = "read" | "chain" | "analyze_this" | "analyze" | "copilot";

export interface AskAiOption {
  id: AskAiOptionId;
  label: string;
  /** One short line under the label. */
  hint: string;
  /** A link (Analyze, Copilot); the others act inside the sheet or on the page. */
  to?: string;
}

/** AN-1: Analyze a chart with the chart on screen filled in (the trader picks the mode and presses Analyze). */
export function analyzePath(symbol: string, timeframe?: string): string {
  const q = new URLSearchParams({ symbol });
  if (timeframe) q.set("tf", timeframe);
  return `/chart-analyze?${q.toString()}`;
}

/** Analyze a chart's timeframes for a chart timeframe id (5min / 15min / 60min / day); none for the others. */
export function analyzeTimeframe(chartTf: string | null | undefined): string | undefined {
  return ({ "5min": "5m", "15min": "15m", "60min": "1h", day: "1d" } as Record<string, string>)[chartTf ?? ""];
}

/** The Copilot's address with the symbol on screen. */
export function copilotPath(symbol?: string): string {
  return symbol ? `/copilot?symbol=${encodeURIComponent(symbol)}` : "/copilot";
}

/**
 * What the sheet offers for this page, in this order. Each flag gates what it gated before: `read_chart` the reading,
 * `chart_analyze` the image analysis, `ai_copilot` the Copilot. The page the trader is on is not offered again.
 */
export function askAiOptions(ctx: AskAiPageContext | null, on: (flag: string) => boolean, current: string | null): AskAiOption[] {
  const out: AskAiOption[] = [];
  const symbol = ctx?.symbol;
  if (ctx?.read && on("read_chart")) {
    out.push({
      id: "read",
      label: ctx.read.on ? "Hide the reading" : "Read this chart",
      hint: ctx.read.on ? "Take the reading off the chart" : "Trend, areas and the latest candle, drawn on the chart",
    });
  }
  if (ctx?.chain) out.push({ id: "chain", label: "Ask about this chain", hint: "Type a question; the answer is worked out from the live chain" });
  if (ctx?.analyze?.symbol && on("chart_analyze") && current !== "chart-analyze") {
    out.push({ id: "analyze_this", label: "Analyze this chart", hint: `${ctx.analyze.symbol}: AI only or Advanced analysis, on ATP's own chart`,
      to: analyzePath(ctx.analyze.symbol, ctx.analyze.timeframe) });
  }
  if (on("chart_analyze") && current !== "chart-analyze") {
    out.push({ id: "analyze", label: "Analyze a chart image", hint: "Upload a screenshot of any chart", to: "/chart-analyze" });
  }
  if (on("ai_copilot") && current !== "copilot") {
    out.push({ id: "copilot", label: symbol ? `Ask the Copilot about ${symbol}` : "Open the Copilot", hint: "Questions in your own words", to: copilotPath(symbol) });
  }
  return out;
}

/** The options for the page on screen, for the shell. */
export function useAskAiOptions(on: (flag: string) => boolean, current: string | null): { ctx: AskAiPageContext | null; options: AskAiOption[] } {
  const ctx = useAskAiContext();
  const options = useMemo(() => askAiOptions(ctx, on, current), [ctx, on, current]);
  return { ctx, options };
}
