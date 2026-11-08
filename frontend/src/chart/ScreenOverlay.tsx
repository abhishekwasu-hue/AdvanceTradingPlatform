import { useEffect, useMemo, useState } from "react";
import type { LightweightEngine } from "../charting/lightweight";
import type { ChartMarker } from "../charting/markers";
import { cx } from "../components/primitives/cx";
import { chartTimeframe, evidenceChartLayers, type EvidencePack } from "../screener/evidence";
import type { LevelLine } from "./chartCore";
import { timeframeLabel, type ChartBar, type TimeframeId } from "./timeframes";
import { markOf, onCandles, watchScreenApi, type OverlayReply, type WatchScreenApi } from "./watchScreen";

/**
 * M91 C1: the watchlist's chosen screen on the stock's chart - the indicator lines its conditions use (his own
 * parameters), the levels / areas / swing points a condition refers to (the evidence pack's drawables), a marker under
 * each bar where the whole screen held, and a compact legend: every condition with ✓ / ✗ and its value on the last
 * closed bar. From the same server evaluation as the list's rows (C2). Lines and markers sit on the bars of the screen's
 * own timeframe; on another timeframe only the price levels and areas are drawn and the legend offers the screen's one.
 */
export const SCREEN_LAYER = "watchlist-screen";

export interface ScreenOverlayState {
  reply: OverlayReply | null;
  loading: boolean;
  failed: boolean;
  /** The chart shows the screen's own timeframe (lines and markers are drawn). */
  sameTf: boolean;
  screenTf: TimeframeId | null;
  levels: LevelLine[];
  markers: ChartMarker[];
}

const NONE: LevelLine[] = [];
const NO_MARKS: ChartMarker[] = [];

export function overlayLayers(reply: OverlayReply, candles: readonly ChartBar[], sameTf: boolean) {
  if (!reply.available || !reply.bars) return { levels: NONE, markers: NO_MARKS, studies: [], layer: { bands: [], segments: [] } };
  const t = onCandles(reply.bars.t, candles, reply.base_tf);
  const pack = { bars: { ...reply.bars, t }, matched_index: -1 } as unknown as EvidencePack;
  const layers = evidenceChartLayers(pack, reply.drawables ?? []);
  const hits = onCandles(reply.hits ?? [], candles, reply.base_tf);
  const markers: ChartMarker[] = sameTf
    ? [...layers.markers, ...hits.map((ts) => ({ timestamp: ts, position: "belowBar" as const, color: "up", shape: "arrowUp" as const, text: "" }))]
      .sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp))
    : NO_MARKS;
  return {
    levels: layers.levels, markers,
    studies: sameTf ? layers.studies : [],
    layer: { bands: layers.layer.bands ?? [], segments: sameTf ? layers.layer.segments ?? [] : [] },
  };
}

export function useScreenOverlay(engine: LightweightEngine | null, args: { screenId: number | null; symbol: string; exchange: string; timeframe: TimeframeId;
  candles: readonly ChartBar[]; on: boolean }, api: Pick<WatchScreenApi, "overlay"> = watchScreenApi): ScreenOverlayState {
  const { screenId, symbol, exchange, timeframe, candles, on } = args;
  const [reply, setReply] = useState<OverlayReply | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  // read again when a newer closed candle shows (the screen is read on closed bars only)
  const closed = candles.filter((c) => !c.forming);
  const newest = closed.length ? closed[closed.length - 1].timestamp : "";
  useEffect(() => {
    if (screenId == null || !symbol) { setReply(null); return undefined; }
    let live = true;
    setLoading(true);
    api.overlay({ screen_id: screenId, symbol, exchange: exchange || "NSE" }).then(
      (r) => { if (live) { setReply(r); setFailed(false); } },
      () => { if (live) { setReply(null); setFailed(true); } },
    ).finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [api, screenId, symbol, exchange, newest]);
  const screenTf = reply ? chartTimeframe(reply.base_tf) : null;
  const sameTf = screenTf === timeframe;
  const drawn = useMemo(() => (on && reply ? overlayLayers(reply, candles, sameTf) : null), [on, reply, candles, sameTf]);
  useEffect(() => {
    if (!engine || !drawn) return undefined;
    for (const s of drawn.studies) engine.setStudy(s.id, s.line);
    engine.setLayer(SCREEN_LAYER, drawn.layer);
    return () => {
      for (const s of drawn.studies) engine.setStudy(s.id, null);
      engine.setLayer(SCREEN_LAYER, null);
    };
  }, [engine, drawn]);
  return { reply, loading, failed, sameTf, screenTf, levels: drawn?.levels ?? NONE, markers: drawn?.markers ?? NO_MARKS };
}

const TONE = { up: "text-up", down: "text-down", muted: "text-fg-muted" } as const;

export function ScreenLegend({ state, onShowTf }: { state: ScreenOverlayState; onShowTf?: (tf: TimeframeId) => void }) {
  const { reply, loading, failed, sameTf, screenTf } = state;
  if (!reply) {
    if (loading) return <p className="border-t border-subtle px-space-3 py-space-2 text-t12 text-fg-muted" role="status">Reading the screen…</p>;
    if (failed) return <p className="border-t border-subtle px-space-3 py-space-2 text-t12 text-fg-muted" role="status">The screen could not be read just now.</p>;
    return null;
  }
  const met = (reply.legend ?? []).filter((r) => r.state === "met").length;
  return (
    <section aria-label={`Screen: ${reply.screen.name}`} data-testid="screen-legend" className="border-t border-subtle px-space-3 py-space-2 text-t13">
      <div className="flex flex-wrap items-center gap-x-space-2 gap-y-space-1">
        <h3 className="min-w-0 text-t13 font-semibold text-fg">{reply.screen.name}</h3>
        {reply.available && (
          <span data-testid="screen-legend-result" className={cx("rounded-pill border px-space-2 text-t12 font-semibold",
            reply.matched ? "border-up text-up" : "border-border text-fg-muted")}>
            {reply.matched ? "Meets the screen" : "Does not meet it"}
          </span>
        )}
        <span className="text-t12 text-fg-muted">
          {screenTf ? timeframeLabel(screenTf) : reply.base_tf} · last closed bar{reply.available && reply.legend ? ` · ${met} of ${reply.legend.length} met` : ""}
        </span>
        {reply.available && !sameTf && screenTf && onShowTf && (
          <button type="button" onClick={() => onShowTf(screenTf)} data-testid="screen-legend-tf"
            className="min-h-11 rounded-control px-space-1 text-t12 font-semibold text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9">
            Show lines on {timeframeLabel(screenTf)}
          </button>
        )}
      </div>
      {!reply.available && <p className="text-t12 text-fg-muted">{reply.reason || "No closed bars for this stock yet."}</p>}
      {reply.available && (
        <ul className="mt-space-1 grid gap-x-space-4 gap-y-px md:grid-cols-2" aria-label="Conditions">
          {(reply.legend ?? []).map((r) => {
            const m = markOf(r.state);
            return (
              <li key={r.id} data-condition={r.id} data-state={r.state} className="flex min-w-0 items-baseline gap-space-2">
                <span aria-label={m.label} title={m.label} className={cx("w-4 shrink-0 text-center font-semibold", TONE[m.tone])}>{m.sign}</span>
                <span className="min-w-0 break-words text-t12 text-fg tabular-nums">{r.value || r.text}</span>
              </li>
            );
          })}
        </ul>
      )}
      <p className="mt-space-1 text-t12 text-fg-muted">{sameTf ? "▲ under a bar: the whole screen was met on that bar. " : ""}{reply.disclaimer}</p>
    </section>
  );
}
