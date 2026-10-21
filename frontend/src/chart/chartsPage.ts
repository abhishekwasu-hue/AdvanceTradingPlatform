/**
 * UI2-4b2 (Charts page plan §3): the Charts page's rules without React - the symbol it opens on, the timeframes it
 * offers, the market state chip and the trader's view (timeframe, chart type, price lines, layers) kept per browser.
 */
import type { CandleVolumeSource, SystemStatus } from "../types";
import { formatDateIST, formatTimeIST } from "../ui/format";
import { CHART_TYPES_V2, OVERLAYS_V2, type ChartTypeV2, type OverlayId } from "./chartCore";
import { DEFAULT_TIMEFRAME, TIMEFRAMES, isTimeframe, type TimeframeId } from "./timeframes";

/** The last symbol viewed on Charts (per browser): Charts opens on it, NIFTY 50 on first use (§6 default state). */
export const LAST_SYMBOL_KEY = "atp_charts_last_symbol";
export const DEFAULT_SYMBOL = "NIFTY 50";

/** The symbol Charts shows: the one in the address, else the last one viewed, else NIFTY 50. */
export function chartsSymbol(fromPath: string | null | undefined, last: string | null): string {
  return (fromPath ?? last ?? DEFAULT_SYMBOL).trim().toUpperCase() || DEFAULT_SYMBOL;
}

/** Week and month need the server's support (UI2-4a2): they are offered only when `/market-data/sources` lists them.
 * Not known yet (or the read failed): the intraday timeframes and D. */
export function offeredTimeframes(server: readonly string[] | null | undefined): TimeframeId[] {
  return TIMEFRAMES.filter((tf) => (tf !== "week" && tf !== "month") || !!server?.includes(tf));
}

/** The volume pane's name: an index's volume comes from its near-month future, and says so ("Volume · NIFTY OCT FUT"). */
export function volumeLabelFor(source: CandleVolumeSource | null | undefined): string {
  if (!source?.underlying || !/^\d{4}-\d{2}-\d{2}$/.test(source.expiry ?? "")) return "Volume";
  const month = new Date(`${source.expiry}T00:00:00Z`).toLocaleDateString("en-IN", { month: "short", timeZone: "UTC" });
  return `Volume · ${source.underlying} ${month.toUpperCase()} FUT`;
}

// --- The market state chip -------------------------------------------------------------------------------------------

/** The session family of a venue, as the server names it (`calendar.session_family`); unknown venues follow NSE. */
export function sessionFamily(exchange: string): string {
  const x = exchange.trim().toUpperCase();
  if (x.startsWith("MCX")) return "MCX";
  if (x === "CDS" || x === "BCD" || x.endsWith("_CD")) return "CDS";
  if (["CRYPTO", "BINANCE", "COINDCX", "WAZIRX"].includes(x)) return "CRYPTO";
  return "NSE";
}

/** "Market open", "Closed · opens 09:15" (later today), "Closed · opens Mon 09:15"; null while the status is unknown. */
export function marketChip(status: SystemStatus | null | undefined, exchange: string, nowMs: number): { text: string; open: boolean } | null {
  const s = status?.sessions?.[sessionFamily(exchange)];
  if (!s) return null;
  if (s.is_open) return { text: "Market open", open: true };
  const next = s.next_open ? Date.parse(s.next_open) : NaN;
  if (!Number.isFinite(next)) return { text: "Market closed", open: false };
  const time = formatTimeIST(next, { suffix: false });
  const sameDay = formatDateIST(next) === formatDateIST(nowMs);
  return { text: sameDay ? `Closed · opens ${time}` : `Closed · opens ${formatDateIST(next, { weekday: true }).split(" ")[0]} ${time}`, open: false };
}

// --- The trader's view, per browser ------------------------------------------------------------------------------------

/** What the trader last chose on this browser. The panes (volume, RSI) and the theme are in the server prefs
 * (`useChartPrefs`); the prefs schema has no place for the rest yet, so it stays on this device. */
export interface ChartsView {
  timeframe: TimeframeId;
  type: ChartTypeV2;
  overlays: OverlayId[];
  /** Layers turned on (each still needs its flag). */
  layers: string[];
}

export const VIEW_KEY = "atp_charts_view";
export const DEFAULT_VIEW: ChartsView = { timeframe: DEFAULT_TIMEFRAME, type: "candles", overlays: [], layers: [] };

/** Any stored value as a valid view: a field that is missing or wrong takes its default. */
export function cleanView(raw: unknown): ChartsView {
  const v = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const list = (x: unknown) => (Array.isArray(x) ? x.filter((i): i is string => typeof i === "string") : []);
  return {
    timeframe: isTimeframe(v.timeframe) ? v.timeframe : DEFAULT_VIEW.timeframe,
    type: (CHART_TYPES_V2 as readonly unknown[]).includes(v.type) ? (v.type as ChartTypeV2) : DEFAULT_VIEW.type,
    overlays: OVERLAYS_V2.filter((o) => list(v.overlays).includes(o)),
    layers: [...new Set(list(v.layers))].slice(0, 10),
  };
}

export function readView(): ChartsView {
  try { return cleanView(JSON.parse(localStorage.getItem(VIEW_KEY) ?? "null")); } catch { return DEFAULT_VIEW; }
}

export function saveView(view: ChartsView): void {
  try { localStorage.setItem(VIEW_KEY, JSON.stringify(view)); } catch { /* storage unavailable: the choice lasts this visit */ }
}

/** The drawing tools' message in plain words: the plain ones as they are, anything else (a raw error) as what happened
 * and what to do next. */
export function drawingNote(message: string | null | undefined): string | null {
  if (!message) return null;
  if (message.startsWith("Drawings could not be loaded")) return "Your drawings could not be loaded. Reload the page to try again.";
  if (/^This drawing (is locked|changed in another window)/.test(message)) return message;
  if (/needs (a )?(text|time|price)|anchor|< or >/.test(message)) return "That drawing was not finished. Pick the tool and place it again.";
  return "A drawing change could not be saved. Check your connection, then try again.";
}
