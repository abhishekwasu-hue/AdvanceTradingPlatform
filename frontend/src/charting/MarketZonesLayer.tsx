/**
 * Z7a-3 (CH3 / U8 minimal style): market zones on a chart - at most six demand / supply bands near the price, each
 * labelled "demand 512.30 (fresh)" with its touch count, and a one-line legend. The zones are the organisation's stored
 * snapshot as of now (flag `market_zones`; while it is off the backend answers 503 and nothing is shown). When the price
 * on screen moves the bands are re-picked from the same response (`selectBands`), never re-computed from pixels.
 */
import { useEffect, useMemo, useState } from "react";
import { formatDateTimeIST } from "../ui/format";
import { ApiError } from "../api/client";
import type { ChartEngine } from "./engine";
import { LAYER_ID, ZONE_COLORS, bandsAt, bandsLayer, marketZonesApi, type MarketZonesLayerResponse, type ZoneBand } from "./marketZones";


function asOfText(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return formatDateTimeIST(d, { year: false, suffix: false });
}

/** The legend: one row per band, top to bottom (the same order as on the chart). Renders nothing without bands. */
export function MarketZonesLegend({ bands, asOf, note, settingsNote }: { bands: readonly ZoneBand[]; asOf: string | null; note?: string;
                                                                      settingsNote?: string | null }) {
  if (bands.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-fg-muted" data-testid="market-zones-legend" title={note}>
      <span className="font-semibold text-fg">Zones</span>
      {bands.map((b) => (
        <span key={b.zone_key} className="inline-flex items-center gap-1" data-role={b.role}>
          <span aria-hidden className="inline-block h-2 w-2 rounded-sm" style={{ backgroundColor: ZONE_COLORS[b.role] }} />
          <span>{b.label}</span>
          <span className="opacity-70">· {b.touch_text}</span>
        </span>
      ))}
      {asOf && <span className="opacity-70">as of {asOfText(asOf)} IST</span>}
      {settingsNote && <span className="text-warn" data-testid="market-zones-fallback">from {settingsNote}</span>}
    </div>
  );
}

/**
 * Fetches the layer for `instrument` (lake key, "NSE:BANKNIFTY"), draws the bands on `engine` and shows the legend.
 * `price` is the last close on screen (from the candles the backend served); `at` (CH3 replay) reads the snapshot as of
 * the replayed bar instead of now.
 */
export function MarketZonesLayer({ engine, instrument, price, at = null }: { engine: ChartEngine | null; instrument: string; price?: number | null;
                                                                            at?: string | null }) {
  const [layer, setLayer] = useState<MarketZonesLayerResponse | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    setLayer(null);
    setFailed(false);
    if (!instrument) return () => { live = false; };
    marketZonesApi.layer(instrument, at ? { at } : {}).then(
      (r) => { if (live) setLayer(r); },
      // 503: flag off; 404 / 422: no zones for this instrument - nothing to show, never an error on the chart
      (e: unknown) => { if (live) setFailed(!(e instanceof ApiError && [404, 422, 503].includes(e.status))); },
    );
    return () => { live = false; };
  }, [instrument, at]);

  const bands = useMemo(() => (layer ? bandsAt(layer, price) : []), [layer, price]);

  useEffect(() => {
    if (!engine) return;
    engine.setLayer(LAYER_ID, bands.length ? bandsLayer(bands, layer?.as_of, !!layer?.fallback) : null);
    return () => engine.setLayer(LAYER_ID, null);
  }, [engine, bands, layer?.as_of, layer?.fallback]);

  if (failed) return <div className="text-xs text-fg-muted">Market zones could not be loaded.</div>;
  if (!layer) return null;
  return <MarketZonesLegend bands={bands} asOf={layer.as_of} note={layer.note} settingsNote={layer.fallback ? layer.settings_note : null} />;
}

export default MarketZonesLayer;
