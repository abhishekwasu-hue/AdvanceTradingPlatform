/**
 * Z7a-3 (CH3 / U8 minimal style): the market-zones chart layer in the browser.
 *
 * The backend (`GET /api/charts/layers/market-zones`) reads the zones stored as of a time for the organisation and picks
 * at most six bands. Every number and text drawn comes from that response (bounds, label prices, touches, strength);
 * the browser only re-picks bands when the price on screen moves, with `selectBands` - a line-for-line port of
 * backend/app/price_action/zones/chart.py `select_bands`, checked against the same cases
 * (backend/tests/fixtures/zones/chart_bands_cases.json, copied byte for byte to ./__fixtures__/) so the two cannot drift. The U8 evidence charts draw the same
 * bands server-side (Telegram images) from the Python rule.
 *
 * Reference levels from past prices - nothing here places, changes or proposes an order.
 */
import { request } from "../api/client";
import type { LayerBand, LayerData } from "./engine";

export type ZoneRole = "demand" | "supply";

/** One live zone as the backend sends it (chart.py `candidate`). */
export interface ZoneCandidate {
  zone_key: string;
  zone_type: string;
  family: string;
  timeframe: string;
  kind: "band" | "line";
  low: number;
  high: number;
  low_text: string;
  high_text: string;
  state: string;
  touches: number;
  strength: number;
}

/** A picked band: the candidate plus its role and distance at the reference price and its texts. */
export interface ZoneBand extends ZoneCandidate {
  role: ZoneRole;
  distance_pct: number;
  label: string;
  touch_text: string;
}

export interface MarketZonesLayerResponse {
  instrument: string;
  at: string;
  as_of: string | null;
  scope: "platform" | "organisation" | null;
  /** The platform's zones (default settings) served because none are stored with the organisation's own settings. */
  fallback?: boolean;
  settings_note?: string | null;
  price: number | null;
  price_source: "request" | "snapshot" | null;
  price_text?: string | null;
  bands: ZoneBand[];
  zones: ZoneCandidate[];
  live_count: number;
  max_bands: number;
  note: string;
}

export const MAX_BANDS = 6;
export const PER_ROLE = 3;
export const MAX_DISTANCE_PCT = 10;
export const LAYER_ID = "market_zones";

export function roleAt(c: Pick<ZoneCandidate, "low" | "high">, price: number): ZoneRole {
  return (c.low + c.high) / 2 > price ? "supply" : "demand";
}

export function distancePct(c: Pick<ZoneCandidate, "low" | "high">, price: number): number {
  if (c.low <= price && price <= c.high) return 0;
  const gap = price < c.low ? c.low - price : price - c.high;
  return gap / price * 100;
}

export function touchText(touches: number): string {
  return touches <= 0 ? "no touches" : touches === 1 ? "1 touch" : `${touches} touches`;
}

/** "demand 512.30 (fresh)" / "supply 530.00 (tested)": the edge price meets first (a line has one price). */
export function zoneLabel(c: Pick<ZoneCandidate, "low_text" | "high_text" | "state">, role: ZoneRole): string {
  return `${role} ${role === "demand" ? c.high_text : c.low_text} (${c.state})`;
}

type Item = ZoneCandidate & { role: ZoneRole; distance_pct: number };

const cmpStr = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);
const byProximity = (a: Item, b: Item) => a.distance_pct - b.distance_pct || b.strength - a.strength || cmpStr(a.zone_key, b.zone_key);
const byStrength = (a: Item, b: Item) => b.strength - a.strength || a.distance_pct - b.distance_pct || cmpStr(a.zone_key, b.zone_key);
const overlaps = (a: Item, b: Item) => a.low <= b.high && b.low <= a.high;

/**
 * At most `maxBands` bands at `price` (chart.py `select_bands`): role by the price and distance to it (beyond
 * `maxDistancePct` left out); per role the strongest of each overlapping cluster; per role the nearest `perRole`, a
 * short role's slots going to the other role's next nearest; top to bottom by high, then low.
 */
export function selectBands(cands: readonly ZoneCandidate[], price: number, maxBands = MAX_BANDS, perRole = PER_ROLE,
                            maxDistancePct: number | null = MAX_DISTANCE_PCT): ZoneBand[] {
  if (!(price > 0) || maxBands <= 0) return [];
  const items: Item[] = [];
  for (const c of cands) {
    const d = distancePct(c, price);
    if (maxDistancePct != null && d > maxDistancePct) continue;
    items.push({ ...c, role: roleAt(c, price), distance_pct: d });
  }
  const kept: Record<ZoneRole, Item[]> = { demand: [], supply: [] };
  for (const item of [...items].sort(byStrength)) {
    const same = kept[item.role];
    if (!same.some((k) => overlaps(item, k))) same.push(item);
  }
  const near = { demand: [...kept.demand].sort(byProximity), supply: [...kept.supply].sort(byProximity) };
  const spare = [...near.demand.slice(perRole), ...near.supply.slice(perRole)].sort(byProximity);
  const chosen = [...near.demand.slice(0, perRole), ...near.supply.slice(0, perRole)].sort(byProximity).slice(0, maxBands);
  for (const item of spare) {
    if (chosen.length >= maxBands) break;
    chosen.push(item);
  }
  chosen.sort((a, b) => b.high - a.high || b.low - a.low || cmpStr(a.zone_key, b.zone_key));
  return chosen.map((item) => ({ ...item, label: zoneLabel(item, item.role), touch_text: touchText(item.touches) }));
}

export const ZONE_COLORS: Record<ZoneRole, string> = { demand: "#16a34a", supply: "#dc2626" };
const STATE_STYLE: Record<string, { opacity: number; style: "solid" | "dashed" | "dotted" }> = {
  fresh: { opacity: 0.18, style: "solid" },
  tested: { opacity: 0.11, style: "dashed" },
  flipped: { opacity: 0.11, style: "dotted" },
};

export const FALLBACK_TAG = "default settings";

/** The bands as an engine layer (minimal style: a tinted band or a line per zone, its label and touch count). Zones
 * served from the platform's default settings (`fallback`) say so on every band. */
export function bandsLayer(bands: readonly ZoneBand[], asOf?: string | null, fallback = false): LayerData {
  const out: LayerBand[] = bands.map((b) => {
    const st = STATE_STYLE[b.state] ?? STATE_STYLE.tested;
    const label = `${b.label} · ${b.touch_text}${fallback ? ` · ${FALLBACK_TAG}` : ""}`;
    return { low: b.low, high: b.high, color: ZONE_COLORS[b.role], opacity: st.opacity, style: st.style, label };
  });
  return { asOf: asOf ?? undefined, bands: out };
}

/** The bands to draw at `price`: the backend's own pick when it was made at that price, else re-picked here. */
export function bandsAt(layer: MarketZonesLayerResponse, price: number | null | undefined): ZoneBand[] {
  if (price == null || !(price > 0) || price === layer.price) return layer.bands;
  return selectBands(layer.zones, price, layer.max_bands);
}

export const marketZonesApi = {
  layer: (instrument: string, opts: { at?: string; price?: number; maxBands?: number } = {}) => {
    const q = new URLSearchParams({ instrument });
    if (opts.at) q.set("at", opts.at);
    if (opts.price != null && opts.price > 0) q.set("price", String(opts.price));
    if (opts.maxBands) q.set("max_bands", String(opts.maxBands));
    return request<MarketZonesLayerResponse>(`/charts/layers/market-zones?${q.toString()}`);
  },
};
