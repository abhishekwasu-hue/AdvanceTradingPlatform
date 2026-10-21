/**
 * UI2-4b2 (Charts page plan §3): the Charts page's rules without React - the symbol it opens on, the timeframes it
 * offers, the market state chip and the trader's view (timeframe, chart type, price lines, layers; UI2-4c: drawings
 * hidden, the watchlist column and list) kept per browser.
 */
import type { CandleVolumeSource, SystemStatus } from "../types";
import { formatDateIST, formatTimeIST } from "../ui/format";
import { CHART_TYPES_V2, OVERLAYS_V2, type ChartTypeV2, type OverlayId } from "./chartCore";
import { DEFAULT_TIMEFRAME, TIMEFRAMES, isTimeframe, type TimeframeId } from "./timeframes";

/** The last symbol viewed on Charts (per user, on this browser): Charts opens on it, NIFTY 50 on first use (§6). */
export const LAST_SYMBOL_KEY = "atp_charts_last_symbol";
type UserId = number | string | null | undefined;

/** Reads a per-user value; nothing while signed out or when storage is blocked. */
function readFor(base: string, user: UserId): string | null {
  if (user == null) return null;
  try { return localStorage.getItem(`${base}:${user}`); } catch { return null; }
}

/** Keeps a per-user value; nothing while signed out (storage blocked: it lasts this visit only). */
function writeFor(base: string, user: UserId, value: string): void {
  if (user == null) return;
  try { localStorage.setItem(`${base}:${user}`, value); } catch { /* storage unavailable */ }
}

export const readLastSymbol = (user: UserId): string | null => readFor(LAST_SYMBOL_KEY, user);
export const saveLastSymbol = (user: UserId, symbol: string): void => writeFor(LAST_SYMBOL_KEY, user, symbol);
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

/** What the trader last chose on this browser (per user). The panes (volume, RSI) and the theme are in the server prefs
 * (`useChartPrefs`); the prefs schema has no place for the rest yet, so it stays on this device. */
export interface ChartsView {
  timeframe: TimeframeId;
  type: ChartTypeV2;
  overlays: OverlayId[];
  /** Layers turned on (each still needs its flag). */
  layers: string[];
  /** UI2-4c "Hide all": the drawings are not drawn (and the tools are off) until shown again. */
  drawings_hidden: boolean;
  /** UI2-4c: the watchlist column is open (wide screens; a phone opens it as a sheet). */
  watchlist_open: boolean;
  /** UI2-4c: the watchlist last shown (null: the first one). */
  watchlist_id: number | null;
}

export const VIEW_KEY = "atp_charts_view";
export const DEFAULT_VIEW: ChartsView = {
  timeframe: DEFAULT_TIMEFRAME, type: "candles", overlays: [], layers: [], drawings_hidden: false, watchlist_open: true, watchlist_id: null,
};

/** Any stored value as a valid view: a field that is missing or wrong takes its default. */
export function cleanView(raw: unknown): ChartsView {
  const v = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const list = (x: unknown) => (Array.isArray(x) ? x.filter((i): i is string => typeof i === "string") : []);
  return {
    timeframe: isTimeframe(v.timeframe) ? v.timeframe : DEFAULT_VIEW.timeframe,
    type: (CHART_TYPES_V2 as readonly unknown[]).includes(v.type) ? (v.type as ChartTypeV2) : DEFAULT_VIEW.type,
    overlays: OVERLAYS_V2.filter((o) => list(v.overlays).includes(o)),
    layers: [...new Set(list(v.layers))].slice(0, 10),
    drawings_hidden: v.drawings_hidden === true,
    watchlist_open: v.watchlist_open !== false,
    watchlist_id: typeof v.watchlist_id === "number" && Number.isInteger(v.watchlist_id) ? v.watchlist_id : null,
  };
}

export function readView(user: UserId): ChartsView {
  try { return cleanView(JSON.parse(readFor(VIEW_KEY, user) ?? "null")); } catch { return DEFAULT_VIEW; }
}

export function saveView(user: UserId, view: ChartsView): void {
  writeFor(VIEW_KEY, user, JSON.stringify(view));
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

// --- Addresses of the Chart tab (UI2-4e) -----------------------------------------------------------------------------

/** A chart on the Chart tab with its timeframe: `/charts/NIFTY%2050?tf=15min` (`&exchange=BSE` off NSE). An embedded
 * chart's "Open in Chart tab"; the Charts page takes the timeframe, then drops it from the address. */
export function chartTabPath(symbol: string, exchange?: string | null, timeframe?: string | null): string {
  const s = symbol.trim().toUpperCase();
  const q = new URLSearchParams();
  if (timeframe && isTimeframe(timeframe)) q.set("tf", timeframe);
  const x = (exchange ?? "").trim().toUpperCase();
  if (s && x && x !== "NSE") q.set("exchange", x);
  const path = s ? `/charts/${encodeURIComponent(s)}` : "/charts";
  const query = q.toString();
  return query ? `${path}?${query}` : path;
}

/** UI2-4e: an old single-chart window link (`/?chart=SYM&tf=15min&exchange=NSE&broker_name=..`) under `ui_v2` opens the
 * Chart tab instead, with its timeframe and exchange. The broker name is dropped: v2 charts read the trader's session. */
export function chartWindowRedirect(params: URLSearchParams): string {
  return chartTabPath(params.get("chart") ?? "", params.get("exchange"), params.get("tf"));
}

/** The timeframe a Chart tab address asks for (`?tf=`), or null. Not with `symbol` (an old workspace link's start). */
export function addressTimeframe(params: URLSearchParams): TimeframeId | null {
  const tf = params.get("tf");
  return !params.has("symbol") && isTimeframe(tf) ? tf : null;
}
