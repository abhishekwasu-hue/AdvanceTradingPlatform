/**
 * N4 CH3: the price-action layer on a chart - toggles per concept (swings, legs, breaks, levels, trend), a debug mode
 * (every degree, tentative pivots, broken levels, thresholds) and the trend per degree. The layer is the backend's,
 * as of `at` (replay: the replayed bar's end; otherwise now), so it never shows what was not known then.
 * Flag `chart_price_action`: while it is off the backend answers 503 and nothing is shown (`onAvailable(false)`).
 */
import { useEffect, useMemo, useState } from "react";
import type { ChartEngine } from "./engine";
import {
  CONCEPTS, CONCEPT_LABELS, LayerLoader, PA_COLORS, PA_LAYER_ID, layerTf, loadToggles, paApi, saveToggles, toLayerData, trendRows,
  type Concept, type LayerState, type PaLayerResponse, type Toggles,
} from "./priceAction";
import { windowStart } from "./replay";

const IST = "Asia/Kolkata";
const btn = "rounded px-1.5 py-0.5 text-[10px] font-semibold";

function asOfText(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString("en-IN", { timeZone: IST, day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
}

/** The panel: toggles, debug, the trend per degree (top degree first) and what the layer was computed from. */
export function PriceActionPanel({ layer, toggles, debug, onToggle, onDebug }: {
  layer: PaLayerResponse; toggles: Toggles; debug: boolean; onToggle: (c: Concept) => void; onDebug: (on: boolean) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-fg-muted" data-testid="price-action-panel" title={layer.note}>
      <span className="font-semibold text-fg">Price action</span>
      <span role="group" aria-label="Price-action layers" className="inline-flex gap-1">
        {CONCEPTS.map((c) => (
          <button key={c} type="button" aria-pressed={toggles[c]} onClick={() => onToggle(c)}
                  className={`${btn} ${toggles[c] ? "bg-brand/15 text-brand ring-1 ring-brand/40" : "text-fg-muted hover:text-fg"}`}>
            {CONCEPT_LABELS[c]}
          </button>
        ))}
        <button type="button" aria-pressed={debug} onClick={() => onDebug(!debug)} title="Every degree, unconfirmed swings, broken levels and thresholds"
                className={`${btn} ${debug ? "bg-warn/15 text-warn ring-1 ring-warn/40" : "text-fg-muted hover:text-fg"}`}>
          Debug
        </button>
      </span>
      {toggles.trend && trendRows(layer).map((t) => (
        <span key={t.degree} data-trend={t.trend} title={t.basis} className={t.degree === layer.degree ? "font-semibold" : undefined}
              style={{ color: t.trend === "up" ? PA_COLORS.up : t.trend === "down" ? PA_COLORS.down : undefined }}>
          D{t.degree} {t.trend}
        </span>
      ))}
      {layer.as_of && <span className="opacity-70">as of {asOfText(layer.as_of)} IST · {layer.bars} bars{layer.source ? ` · ${layer.source}` : ""}</span>}
      {layer.reason && <span className="opacity-70">{layer.reason}</span>}
      {layer.debug && <span className="opacity-70" data-testid="price-action-debug">debug · {layer.swings.length} swings · {layer.breaks.length} breaks · {layer.levels.length} levels</span>}
    </div>
  );
}

/**
 * Fetches the layer for `instrument` ("NSE:BANKNIFTY") on the chart's timeframe as of `at`, draws it on `engine` and
 * shows the panel. `start` fixes the window's start (replay); `onWindow` reports the start the backend used.
 */
export function PriceActionLayer({ engine, instrument, timeframe, at = null, start = null, refreshKey = null, onAvailable, onWindow }: {
  engine: ChartEngine | null; instrument: string; timeframe: string; at?: string | null; start?: string | null;
  /** Live view: changes when a new candle starts (the last one closed), so the layer is asked for again. */
  refreshKey?: string | number | null;
  onAvailable?: (available: boolean) => void; onWindow?: (start: string) => void;
}) {
  const tf = layerTf(timeframe);
  const [state, setState] = useState<LayerState>({ layer: null, failed: null, available: null });
  const [toggles, setToggles] = useState<Toggles>(() => loadToggles());
  const [debug, setDebug] = useState(false);
  const loader = useMemo(() => new LayerLoader((q) => paApi.layer(q.instrument, q), setState), []);

  // every new question clears the layer first: never a layer as of another bar on screen, nor after a failure
  useEffect(() => loader.load(instrument && tf ? { instrument, tf, at, start: windowStart(at, start), debug } : null),
            [loader, instrument, tf, at, start, debug, refreshKey]);

  const { layer, failed, available } = state;
  useEffect(() => { if (available != null) onAvailable?.(available); }, [available, onAvailable]);
  useEffect(() => { if (layer) onWindow?.(layer.start); }, [layer, onWindow]);

  const data = useMemo(() => (layer ? toLayerData(layer, toggles) : null), [layer, toggles]);

  useEffect(() => {
    if (!engine) return;
    engine.setLayer(PA_LAYER_ID, data);
    return () => engine.setLayer(PA_LAYER_ID, null);
  }, [engine, data]);

  const toggle = (c: Concept) => setToggles((t) => { const next = { ...t, [c]: !t[c] }; saveToggles(next); return next; });

  if (failed) return <div className="text-xs text-fg-muted">Price action could not be loaded: {failed}</div>;
  if (!layer) return null;
  return <PriceActionPanel layer={layer} toggles={toggles} debug={debug} onToggle={toggle} onDebug={setDebug} />;
}

export default PriceActionLayer;
