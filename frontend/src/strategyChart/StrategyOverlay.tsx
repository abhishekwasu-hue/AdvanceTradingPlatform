import { useEffect, useMemo, useRef, useState } from "react";
import type { LightweightEngine } from "../charting/lightweight";
import type { ChartMarker } from "../charting/markers";
import { cx } from "../components/primitives/cx";
import { chartTimeframe } from "../screener/evidence";
import { readChartPalette } from "../chart/chartTheme";
import { timeframeLabel, type ChartBar, type TimeframeId } from "../chart/timeframes";
import { STATE_WORDS, strategyChartApi, strategyLayer, strategyMarkers, strategyStudies, type ChartStrategy, type FormingBar, type PlotReply } from "./model";

/**
 * SFC P1 (flag `strategy_chart`): one of his own strategies on the Charts page - only its indicators (each in its pane),
 * its signals and trades on every closed bar, read again when a newer closed candle shows. Deselecting removes exactly
 * what it added (every study, layer and marker it drew carries its key). Reference only: nothing is ordered.
 * P2: up to MAX_STRATEGIES at once, each in its own colour (the first free `--chart-strat-N`); its PA shapes and open
 * trade as one layer; the forming candle's pending hint, read again at most every PENDING_EVERY_MS (display only).
 */
export const MAX_STRATEGIES = 6;
export const PENDING_EVERY_MS = 60_000;
export interface StrategyOverlayState {
  reply: PlotReply | null;
  loading: boolean;
  failed: boolean;
  sameTf: boolean;
  strategyTf: TimeframeId | null;
  markers: ChartMarker[];
  colour: string;
}

const NONE: ChartMarker[] = [];
const KEY_OF = (id: string) => id.replace(/[^A-Za-z0-9]/g, "_");

export function useStrategyList(on: boolean, api: Pick<typeof strategyChartApi, "list"> = strategyChartApi): ChartStrategy[] {
  const [list, setList] = useState<ChartStrategy[]>([]);
  useEffect(() => {
    if (!on) { setList([]); return undefined; }
    let live = true;
    api.list().then((r) => { if (live) setList(r.strategies); }, () => { if (live) setList([]); });
    return () => { live = false; };
  }, [api, on]);
  return list;
}

export function useStrategyOverlay(engine: LightweightEngine | null, args: { strategyId: string | null; slot?: number; symbol: string; exchange: string;
  timeframe: TimeframeId; candles: readonly ChartBar[] }, api: Pick<typeof strategyChartApi, "plot"> = strategyChartApi): StrategyOverlayState {
  const { strategyId, slot = 0, symbol, exchange, timeframe, candles } = args;
  const [reply, setReply] = useState<PlotReply | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const closed = candles.filter((c) => !c.forming);
  const newest = closed.length ? closed[closed.length - 1].timestamp : "";
  const last = candles.length ? candles[candles.length - 1] : null;
  const formingBar = last && last.forming ? last : null;
  const formingRef = useRef<FormingBar | null>(null);
  formingRef.current = formingBar ? { t: formingBar.timestamp, o: formingBar.open, h: formingBar.high, l: formingBar.low, c: formingBar.close, v: formingBar.volume } : null;
  const hasForming = formingBar !== null;
  // the pending hint: read again while a candle forms, at most every PENDING_EVERY_MS (never per tick)
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!strategyId || !hasForming) return undefined;
    const id = window.setInterval(() => setTick((k) => k + 1), PENDING_EVERY_MS);
    return () => window.clearInterval(id);
  }, [strategyId, hasForming]);
  useEffect(() => {
    if (!strategyId || !symbol) { setReply(null); return undefined; }
    let live = true;
    setLoading(true);
    const forming = formingRef.current;
    api.plot({ strategy_id: strategyId, symbol, exchange: exchange || "NSE", ...(forming ? { forming } : {}) }).then(
      (r) => { if (live) { setReply(r); setFailed(false); } },
      () => { if (live) { setReply(null); setFailed(true); } },
    ).finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [api, strategyId, symbol, exchange, newest, tick]);
  const strategyTf = reply?.timeframe ? chartTimeframe(reply.timeframe) : null;
  const sameTf = strategyTf === timeframe;
  const shown = reply && reply.available && sameTf && reply.strategy_id === strategyId ? reply : null;
  const palette = useMemo(() => (engine ? readChartPalette(document.documentElement) : null), [engine]);
  const colour = palette ? palette.strat[slot % palette.strat.length] : "";
  const times = useMemo(() => closed.map((c) => Math.floor(Date.parse(c.timestamp) / 1000)), [newest]);  // eslint-disable-line react-hooks/exhaustive-deps
  const key = shown ? KEY_OF(shown.strategy_id) : "";
  const studies = useMemo(() => (shown && palette ? strategyStudies(shown, colour, key, times) : []), [shown, palette, colour, key, times]);
  useEffect(() => {
    if (!engine || !studies.length) return undefined;
    for (const s of studies) engine.setStudy(s.id, s.line);
    return () => { for (const s of studies) engine.setStudy(s.id, null); };    // deselect: exactly what it drew goes
  }, [engine, studies]);
  const layer = useMemo(() => (shown && palette ? strategyLayer(shown, colour, { up: palette.up, down: palette.down }) : null), [shown, palette, colour]);
  useEffect(() => {
    if (!engine || !layer || !key) return undefined;
    const id = `strategy:${key}`;
    engine.setLayer(id, layer);
    return () => engine.setLayer(id, null);
  }, [engine, layer, key]);
  const formingAt = formingBar ? formingBar.timestamp : null;
  const markers = useMemo(() => (shown ? strategyMarkers(shown, formingAt) : NONE), [shown, formingAt]);
  return { reply, loading, failed, sameTf, strategyTf, markers, colour };
}

/** One selected strategy: draws itself and hands its markers to the page (the chart has ONE marker list); removing it
 * takes back exactly what it drew. */
export function StrategyLayerV2({ engine, strategyId, slot, symbol, exchange, timeframe, candles, onMarkers, onShowTf, onClear, legend = true }: {
  engine: LightweightEngine | null; strategyId: string; slot: number; symbol: string; exchange: string; timeframe: TimeframeId;
  candles: readonly ChartBar[]; onMarkers: (id: string, markers: ChartMarker[]) => void; onShowTf?: (tf: TimeframeId) => void; onClear: () => void;
  legend?: boolean }) {
  const state = useStrategyOverlay(engine, { strategyId, slot, symbol, exchange, timeframe, candles });
  useEffect(() => { onMarkers(strategyId, state.markers); }, [onMarkers, strategyId, state.markers]);
  useEffect(() => () => onMarkers(strategyId, NONE), [onMarkers, strategyId]);
  return legend ? <StrategyLegendV2 state={state} onShowTf={onShowTf} onClear={onClear} /> : null;
}

/** The toolbar's Strategy choice: his own strategies, up to MAX_STRATEGIES at once (a chosen one is removed by choosing
 * it again), or none. */
export function StrategyPickerV2({ list, value, onPick, onClose }: { list: ChartStrategy[]; value: readonly string[];
  onPick: (id: string | null) => void; onClose: () => void }) {
  const full = value.length >= MAX_STRATEGIES;
  return (
    <div role="dialog" aria-label="Your strategies" data-strategy-picker
      className="absolute right-space-3 top-full z-30 mt-space-1 w-72 max-w-[calc(100vw-2rem)] rounded-panel border border-subtle bg-surface-1 p-space-2 shadow-raised">
      {list.length === 0 ? (
        <p className="px-space-2 py-space-2 text-t13 text-fg-muted">No saved strategies yet. Build one in Strategies.</p>
      ) : (
        <ul className="max-h-80 overflow-y-auto" role="listbox" aria-label="Strategy">
          {[{ id: "", name: "None", timeframe: null, drawable: true, reason: null } as ChartStrategy, ...list].map((s) => {
            const chosen = s.id ? value.includes(s.id) : value.length === 0;
            const blocked = !s.drawable || (!!s.id && full && !chosen);
            return (
            <li key={s.id || "none"}>
              <button type="button" role="option" aria-selected={chosen} disabled={blocked}
                onClick={() => { onPick(s.id || null); onClose(); }} data-strategy-option={s.id || "none"}
                title={s.reason ?? (blocked && s.drawable ? `At most ${MAX_STRATEGIES} strategies at once` : undefined)}
                className={cx("flex min-h-11 w-full items-center justify-between gap-space-2 rounded-control px-space-2 text-left text-t14 md:min-h-9",
                  chosen ? "bg-brand-tint text-brand" : "text-fg hover:bg-surface-2", blocked && "cursor-not-allowed opacity-50")}>
                <span className="truncate">{s.name}</span>
                {s.timeframe && <span className="shrink-0 text-t12 text-fg-muted">{s.timeframe}</span>}
              </button>
            </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/** One line under the chart: the strategy, its state, its last signal, and × to remove it. */
export function StrategyLegendV2({ state, onShowTf, onClear }: { state: StrategyOverlayState; onShowTf?: (tf: TimeframeId) => void; onClear: () => void }) {
  const { reply, loading, failed, sameTf, strategyTf, colour } = state;
  if (!reply) {
    if (loading) return <p className="border-t border-subtle px-space-3 py-space-2 text-t12 text-fg-muted" role="status">Reading your strategy…</p>;
    if (failed) return <p className="border-t border-subtle px-space-3 py-space-2 text-t12 text-fg-muted" role="status">Your strategy could not be read just now.</p>;
    return null;
  }
  return (
    <section aria-label={`Strategy: ${reply.name}`} data-strategy-legend={reply.strategy_id} className="flex flex-wrap items-center gap-x-space-3 gap-y-space-1 border-t border-subtle px-space-3 py-space-2 text-t13">
      <span className="flex min-w-0 items-center gap-space-2 font-semibold text-fg">
        {colour && <span aria-hidden className="inline-block h-space-3 w-space-3 shrink-0 rounded-full" style={{ backgroundColor: colour }} data-strategy-colour={colour} />}
        <span className="truncate">{reply.name}</span>
      </span>
      {!reply.available ? <span className="text-fg-muted">{reply.reason}</span>
        : !sameTf && strategyTf ? (
          <button type="button" className="text-brand hover:underline" onClick={() => onShowTf?.(strategyTf)} data-strategy-switch-tf>
            Built for {timeframeLabel(strategyTf)} - switch
          </button>
        ) : (
          <>
            {reply.state && <span className="text-fg-muted" data-strategy-state={reply.state}>{STATE_WORDS[reply.state]}</span>}
            {reply.position && <span className={reply.position.side === "long" ? "text-up" : "text-down"} data-strategy-position>
              {reply.position.side === "long" ? "Long" : "Short"} open{reply.position.target == null ? " · no target set" : ""}</span>}
            {reply.pending && <span className="text-fg-muted" data-strategy-pending>Forming: {reply.pending.side === "long" ? "Long" : "Short"} if this candle closes so</span>}
            {reply.last_signal && <span className={reply.last_signal.side === "long" ? "text-up" : "text-down"}>
              Last: {reply.last_signal.side === "long" ? "Long" : "Short"}</span>}
            {(reply.series ?? []).length > 0 && <span className="text-fg-muted">{(reply.series ?? []).map((s) => s.label).join(" · ")}</span>}
          </>
        )}
      <button type="button" onClick={onClear} aria-label={`Remove ${reply.name} from the chart`} data-strategy-clear
        className="ml-auto inline-flex min-h-9 min-w-9 items-center justify-center rounded-control text-fg-muted hover:bg-surface-2 hover:text-fg">×</button>
    </section>
  );
}
