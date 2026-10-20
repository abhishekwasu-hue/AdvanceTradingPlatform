/**
 * UI2-4b1 (Charts page plan §2 "Chart theme switch", §3): the v2 chart's colours and how a change is applied.
 *
 * - The chart theme is the trader's choice for charts alone: App (follow the app theme, the default), Light or Dark.
 *   The chart's root carries `data-chart-theme`, and tokens.css repeats every chart token under it, so the colours are
 *   read from that element (`getComputedStyle(root)`), never from `:root`.
 * - A change is applied with `applyOptions` on the chart and its series (the chart repaints its primitives with it): the
 *   chart is never rebuilt, so the data, the zoom, the drawings and the indicators stay as they are.
 * - Solid background, no grid unless the trader turns it on, a themed crosshair.
 */
import { ColorType, CrosshairMode, type IChartApi, type IPriceLine, type ISeriesApi, type SeriesType } from "lightweight-charts";

export type ChartThemeChoice = "app" | "light" | "dark";
export interface CandleColors { up: string; down: string }

/** Every chart colour, as a CSS colour string the canvas takes. */
export interface ChartPalette {
  bg: string; text: string; axis: string; grid: string; crosshair: string; crosshairLabel: string;
  up: string; down: string; volUp: string; volDown: string; drawing: string; rsi: string; rsiBand: string;
  ov: [string, string, string, string];
}

/** Volume bars take the candle colours at this alpha (UI v2 proposal §8). */
export const VOLUME_ALPHA = 0.28;

/** The candle colour presets (CHP-12): one pair for both themes, so each is AA (3 : 1) on the light and the dark
 * background (tokens.test.ts). `null` = the theme's own up / down colours (green / red, blue / orange colour-blind). */
export const CANDLE_PRESETS: { id: string; label: string; colors: CandleColors | null }[] = [
  { id: "theme", label: "Green / red", colors: null },
  { id: "teal", label: "Teal / orange", colors: { up: "#0d9488", down: "#ea580c" } },
  { id: "blue", label: "Blue / orange", colors: { up: "#2563eb", down: "#d55e00" } },
];

export function resolveChartTheme(choice: ChartThemeChoice, app: "light" | "dark"): "light" | "dark" {
  return choice === "app" ? app : choice;
}

const TOKENS = {
  bg: "chart-bg", text: "chart-text", axis: "chart-axis", grid: "chart-grid", crosshair: "chart-crosshair",
  crosshairLabel: "chart-crosshair-label", up: "chart-up", down: "chart-down", drawing: "chart-drawing", rsi: "chart-rsi",
  rsiBand: "chart-rsi-band", ov1: "chart-ov-1", ov2: "chart-ov-2", ov3: "chart-ov-3", ov4: "chart-ov-4",
} as const;
type Key = keyof typeof TOKENS;

// The dark values, used when the stylesheet has not loaded (unit tests, a detached window); tokens.css is the source.
const FALLBACK: Record<Key, string> = {
  bg: "9 12 18", text: "194 202 216", axis: "36 44 63", grid: "26 35 51", crosshair: "148 163 184", crosshairLabel: "100 116 139",
  up: "34 197 94", down: "239 68 68", drawing: "96 165 250", rsi: "56 189 248", rsiBand: "148 163 184",
  ov1: "56 189 248", ov2: "245 158 11", ov3: "232 121 249", ov4: "167 139 250",
};

const rgb = (triple: string, alpha = 1) => {
  const parts = triple.trim().split(/\s+/).join(", ");
  return alpha < 1 ? `rgba(${parts}, ${alpha})` : `rgb(${parts})`;
};

/** `#rrggbb` or `rgb(r, g, b)` with an alpha (volume bars under custom candle colours). */
export function withAlpha(color: string, alpha: number): string {
  const hex = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(color);
  if (hex) return rgb(hex.slice(1).map((h) => parseInt(h, 16)).join(" "), alpha);
  const m = /^rgba?\(([^)]*)\)$/.exec(color);
  return m ? rgb(m[1].split(",").slice(0, 3).join(" "), alpha) : color;
}

/** The chart colours as the chart's root element sees them (its `data-chart-theme`), with the trader's candle colours. */
export function readChartPalette(root: Element | null, candles: CandleColors | null = null): ChartPalette {
  const style = root && typeof getComputedStyle === "function" ? getComputedStyle(root) : null;
  const read = (k: Key) => style?.getPropertyValue(`--${TOKENS[k]}`).trim() || FALLBACK[k];
  const up = candles?.up ?? rgb(read("up"));
  const down = candles?.down ?? rgb(read("down"));
  return {
    bg: rgb(read("bg")), text: rgb(read("text")), axis: rgb(read("axis")), grid: rgb(read("grid")), crosshair: rgb(read("crosshair")),
    crosshairLabel: rgb(read("crosshairLabel")), up, down, volUp: withAlpha(up, VOLUME_ALPHA), volDown: withAlpha(down, VOLUME_ALPHA),
    drawing: rgb(read("drawing")), rsi: rgb(read("rsi")), rsiBand: rgb(read("rsiBand")),
    ov: [rgb(read("ov1")), rgb(read("ov2")), rgb(read("ov3")), rgb(read("ov4"))],
  };
}

/** The chart-level options for a palette: solid background, the grid only when asked, a themed crosshair. */
export function chartThemeOptions(p: ChartPalette, grid: boolean) {
  const line = { color: p.crosshair, labelBackgroundColor: p.crosshairLabel };
  return {
    layout: { background: { type: ColorType.Solid, color: p.bg }, textColor: p.text, panes: { separatorColor: p.axis } },
    grid: { vertLines: { visible: grid, color: p.grid }, horzLines: { visible: grid, color: p.grid } },
    crosshair: { mode: CrosshairMode.Normal, vertLine: line, horzLine: line },
    rightPriceScale: { borderColor: p.axis },
    timeScale: { borderColor: p.axis },
  };
}

/** The series a theme change recolours (each optional: a pane that is off has none). */
export interface ThemedSeries {
  candles?: ISeriesApi<"Candlestick">;
  bars?: ISeriesApi<"Bar">;
  line?: ISeriesApi<"Line">;
  rsi?: ISeriesApi<"Line">;
  rsiBands?: IPriceLine[];
  overlays?: ISeriesApi<SeriesType>[];
}

/** Candle-type options for a palette; `hidden` when another series draws the price (bars, line). */
export function candleOptions(p: ChartPalette, hidden: boolean) {
  const none = "rgba(0, 0, 0, 0)";
  return hidden
    ? { upColor: none, downColor: none, wickUpColor: none, wickDownColor: none, borderVisible: false, lastValueVisible: false, priceLineVisible: false }
    : { upColor: p.up, downColor: p.down, wickUpColor: p.up, wickDownColor: p.down, borderVisible: false, lastValueVisible: true, priceLineVisible: true };
}

/** Applies a palette in place. Volume bars carry their colour per bar, so the caller re-sends their data, and the
 * default drawing colour goes to the engine. Never removes or creates the chart or a series. */
export function applyChartTheme(chart: Pick<IChartApi, "applyOptions">, series: ThemedSeries, p: ChartPalette, grid: boolean,
                                candlesHidden: boolean): void {
  chart.applyOptions(chartThemeOptions(p, grid));
  series.candles?.applyOptions(candleOptions(p, candlesHidden));
  series.bars?.applyOptions({ upColor: p.up, downColor: p.down });
  series.line?.applyOptions({ color: p.drawing });
  series.rsi?.applyOptions({ color: p.rsi });
  for (const band of series.rsiBands ?? []) band.applyOptions({ color: p.rsiBand });
  series.overlays?.forEach((s, i) => s.applyOptions({ color: p.ov[i % p.ov.length] }));
}
