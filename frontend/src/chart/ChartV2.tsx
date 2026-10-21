import { useEffect, useMemo, useRef, useState } from "react";
import { useDrawingTools } from "../charting/DrawingToolbar";
import { venueZone } from "../charting/chartTypes";
import type { LightweightEngine } from "../charting/lightweight";
import { THEME_EVENT, useAppearance } from "../theme";
import type { LtpResponse } from "../types";
import { ChartCore, type ChartTypeV2, type OverlayId } from "./chartCore";
import { readChartPalette, resolveChartTheme } from "./chartTheme";
import { foldLive, type ChartBar, type TimeframeId } from "./timeframes";
import { DEFAULT_CHART_PREFS, type ChartPrefs } from "./useChartPrefs";

/**
 * UI2-4b1 (Charts page plan §3): the one v2 chart component - the Chart tab, the Multi-chart cells and the charts on
 * other pages use it under `ui_v2` (UI2-4b2 onward); ProChart stays as it is for the pages without the flag.
 *
 * It draws what it is given: bars (no sample data), the trader's chart settings (`useChartPrefs`) and an optional live
 * price. The chart theme sits on the root as `data-chart-theme`, so the chart tokens resolve for it even when it
 * differs from the app theme. The chart is built once; theme, grid, panes, type and overlays change in place, so the
 * zoom and the drawings stay. The toolbar, the Indicators menu and the drawing rail are the page's (UI2-4b2, UI2-4c):
 * they reach the drawing tools through `onTools`.
 */
export interface ChartV2Props {
  candles: ChartBar[];
  timeframe: TimeframeId;
  symbol?: string;
  exchange?: string;
  chartType?: ChartTypeV2;
  /** The trader's chart settings (theme, grid, candle colours, the volume and RSI panes). */
  prefs?: ChartPrefs;
  overlays?: readonly OverlayId[];
  /** The last price: the forming bar moves (day / week / month: only a bar the server marked forming). */
  live?: Pick<LtpResponse, "ltp" | "timestamp" | "fetched_at"> | null;
  /** Scrolled back to the oldest bars: load the page before them. */
  onLoadOlder?: () => void;
  /** The drawing tools (saved per user and symbol); on for a chart of a symbol. */
  drawingTools?: boolean;
  onEngine?: (engine: LightweightEngine | null) => void;
  onTools?: (tools: ReturnType<typeof useDrawingTools>) => void;
  className?: string;
}

const NO_OVERLAYS: readonly OverlayId[] = [];

export default function ChartV2({
  candles, timeframe, symbol, exchange = "NSE", chartType = "candles", prefs = DEFAULT_CHART_PREFS, overlays = NO_OVERLAYS, live,
  onLoadOlder, drawingTools = true, onEngine, onTools, className = "h-[480px]",
}: ChartV2Props) {
  const rootRef = useRef<HTMLDivElement>(null);
  const plotRef = useRef<HTMLDivElement>(null);
  const [core, setCore] = useState<ChartCore | null>(null);
  const [, , appTheme] = useAppearance();
  const theme = resolveChartTheme(prefs.theme, appTheme);
  const callbacks = useRef({ onLoadOlder, onEngine, onTools });
  callbacks.current = { onLoadOlder, onEngine, onTools };
  const display = useMemo(() => foldLive(candles, live?.ltp, live?.timestamp ?? live?.fetched_at, timeframe), [candles, live, timeframe]);

  // Built once per mount (and per venue: the time axis zone); everything else changes in place below.
  useEffect(() => {
    if (!plotRef.current) return undefined;
    const c = new ChartCore(plotRef.current, {
      palette: readChartPalette(rootRef.current, prefs.candle_colors), grid: prefs.grid, panes: prefs.indicators, type: chartType,
      overlays, zone: venueZone(exchange), onLoadOlder: () => callbacks.current.onLoadOlder?.(),
    });
    setCore(c);
    callbacks.current.onEngine?.(c.engine);
    return () => { callbacks.current.onEngine?.(null); setCore(null); c.remove(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [exchange]);

  // The chart theme, the grid or the candle colours changed, or the app theme / colour-blind option did (THEME_EVENT).
  const { up, down } = prefs.candle_colors ?? {};
  useEffect(() => {
    if (!core) return undefined;
    const apply = () => core.applyTheme(readChartPalette(rootRef.current, up && down ? { up, down } : null), prefs.grid);
    apply();
    window.addEventListener(THEME_EVENT, apply);
    return () => window.removeEventListener(THEME_EVENT, apply);
  }, [core, theme, prefs.grid, up, down]);

  const { volume, rsi } = prefs.indicators;
  useEffect(() => { core?.setPanes({ volume, rsi }); }, [core, volume, rsi]);
  useEffect(() => { core?.setType(chartType); }, [core, chartType]);
  const overlayKey = overlays.join(",");
  useEffect(() => { core?.setOverlays(overlays); }, [core, overlayKey]);  // eslint-disable-line react-hooks/exhaustive-deps
  // The chart type is not in the key: setType re-renders the same bars in place, so the trader's zoom stays.
  useEffect(() => { core?.setData(display, `${symbol ?? ""}|${exchange}|${timeframe}`); }, [core, display, symbol, exchange, timeframe]);

  const tools = useDrawingTools(core?.engine ?? null, core ? plotRef.current : null, symbol, exchange, drawingTools);
  useEffect(() => { callbacks.current.onTools?.(tools); }, [tools.view, tools.controller]);  // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div ref={rootRef} data-chart-theme={theme} data-chart-grid={prefs.grid ? "on" : "off"} className={`relative w-full ${className}`}
         style={{ backgroundColor: "rgb(var(--chart-bg))" }}>
      <div ref={plotRef} className="h-full w-full" />
      {candles.length === 0 && (
        <p className="pointer-events-none absolute inset-0 flex items-center justify-center text-t13" style={{ color: "rgb(var(--chart-text))" }}>
          No bars for this timeframe yet.
        </p>
      )}
    </div>
  );
}
