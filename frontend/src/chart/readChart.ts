import { request } from "../api/client";
import type { LayerData, LayerMarker } from "../charting/engine";
import { ZONE_COLORS } from "../charting/marketZones";
import type { TimeframeId } from "./timeframes";
import { areaLabelText } from "../charting/geometry";

/**
 * AUD-T5 (owner audit 28 Oct; flags `ui_v2` + `read_chart`): "Read this chart" - the server reads the open chart with
 * the readers ATP already has, as of its last closed bar (`GET /api/charts/read`, 404 while the flag is off). Every
 * word and number comes from the server; nothing here reads the candles.
 */

export type ReadRole = "demand" | "supply";

export interface ReadArea {
  key: string | null;
  role: ReadRole;
  low: number;
  high: number;
  low_text: string;
  high_text: string;
  timeframe: string;
  state: string;
  state_words: string;
  touches: number;
  words: string;
}

export interface ReadChart {
  instrument: string;
  tf: string;
  bars: number;
  as_of: string | null;
  last_bar: number | null;
  note: string;
  answer_key: null | { cases: number; words: string };
  structure: null | {
    direction: string;
    words: string;
    last_event: null | { type: string; direction: string; time: number; words: string };
    protected: null | { price: number; side: "low" | "high"; words: string };
  };
  areas: ReadArea[];
  pullback: { available: boolean; trend: string | null; phase: string | null; counter: string | null; as_of: string | null; words: string };
  commitment: null | { time: number; bar_end: string | null; role: ReadRole; form: string; area: { low: number; high: number; key: string | null }; words: string };
  lines: string[];
}

export const READ_LAYER_ID = "read-chart";

/** The server's timeframe for a chart timeframe; null where the reading is not offered (week, month). */
const SERVER_TF: Partial<Record<TimeframeId, string>> = { "1min": "1m", "3min": "3m", "5min": "5m", "15min": "15m", "30min": "30m", "60min": "1h", day: "1d" };
export const readTimeframe = (tf: TimeframeId): string | null => SERVER_TF[tf] ?? null;

export const readChartApi = {
  read: (instrument: string, tf: string) => request<ReadChart>(`/charts/read?${new URLSearchParams({ instrument, tf }).toString()}`),
};

/** The reading as chart layers: each area as a band (its own timeframe in the label), the protected swing as a dashed
 * line and the commitment candle as a marker. */
export function readLayer(r: ReadChart): LayerData {
  const bands = r.areas.map((a) => ({
    low: a.low, high: a.high, role: a.role, color: ZONE_COLORS[a.role], opacity: 0.14, style: "dashed" as const, label: areaLabelText(a),
  }));
  const p = r.structure?.protected;
  const lines = p ? [{ price: p.price, style: "dashed" as const, title: `protected swing ${p.side}` }] : [];
  const c = r.commitment;
  const markers: LayerMarker[] = c
    ? [{ time: c.time, position: c.role === "demand" ? "belowBar" : "aboveBar", shape: c.role === "demand" ? "arrowUp" : "arrowDown",
         color: ZONE_COLORS[c.role], text: "commitment" }]
    : [];
  return { asOf: r.as_of ?? undefined, bands, lines, markers };
}

/** The one line that stays open: the structure (or why nothing could be read). */
export function readSummary(r: ReadChart): string {
  return r.structure?.words ?? r.lines[0] ?? "Nothing to read on this chart yet.";
}
