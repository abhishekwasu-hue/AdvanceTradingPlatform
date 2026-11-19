import { useEffect, useMemo, useState } from "react";
import type { LightweightEngine } from "../charting/lightweight";
import type { ChartMarker } from "../charting/markers";
import { cx } from "../components/primitives/cx";
import { chartTimeframe } from "../screener/evidence";
import { readChartPalette } from "../chart/chartTheme";
import { timeframeLabel, type ChartBar, type TimeframeId } from "../chart/timeframes";
import { STATE_WORDS, strategyChartApi, strategyMarkers, strategyStudies, type ChartStrategy, type PlotReply } from "./model";

/**
 * SFC P1 (flag `strategy_chart`): one of his own strategies on the Charts page - only its indicators (each in its pane),
 * its signals and trades on every closed bar, read again when a newer closed candle shows. Deselecting removes exactly
 * what it added (every study and marker it drew carries its key). Reference only: nothing is ordered.
 */
export interface StrategyOverlayState {
  reply: PlotReply | null;
  loading: boolean;
  failed: boolean;
  sameTf: boolean;
  strategyTf: TimeframeId | null;
  markers: ChartMarker[];
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

export function useStrategyOverlay(engine: LightweightEngine | null, args: { strategyId: string | null; symbol: string; exchange: string;
  timeframe: TimeframeId; candles: readonly ChartBar[] }, api: Pick<typeof strategyChartApi, "plot"> = strategyChartApi): StrategyOverlayState {
  const { strategyId, symbol, exchange, timeframe, candles } = args;
  const [reply, setReply] = useState<PlotReply | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const closed = candles.filter((c) => !c.forming);
  const newest = closed.length ? closed[closed.length - 1].timestamp : "";
  useEffect(() => {
    if (!strategyId || !symbol) { setReply(null); return undefined; }
    let live = true;
    setLoading(true);
    api.plot({ strategy_id: strategyId, symbol, exchange: exchange || "NSE" }).then(
      (r) => { if (live) { setReply(r); setFailed(false); } },
      () => { if (live) { setReply(null); setFailed(true); } },
    ).finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [api, strategyId, symbol, exchange, newest]);
  const strategyTf = reply?.timeframe ? chartTimeframe(reply.timeframe) : null;
  const sameTf = strategyTf === timeframe;
  const shown = reply && reply.available && sameTf && reply.strategy_id === strategyId ? reply : null;
  const studies = useMemo(() => {
    if (!shown || !engine) return [];
    const palette = readChartPalette(document.documentElement);
    return strategyStudies(shown, palette.ov, KEY_OF(shown.strategy_id));
  }, [shown, engine]);
  useEffect(() => {
    if (!engine || !studies.length) return undefined;
    for (const s of studies) engine.setStudy(s.id, s.line);
    return () => { for (const s of studies) engine.setStudy(s.id, null); };    // deselect: exactly what it drew goes
  }, [engine, studies]);
  const markers = useMemo(() => (shown ? strategyMarkers(shown) : NONE), [shown]);
  return { reply, loading, failed, sameTf, strategyTf, markers };
}

/** The toolbar's Strategy choice: his own strategies, one at a time (P2 adds several), or none. */
export function StrategyPickerV2({ list, value, onPick, onClose }: { list: ChartStrategy[]; value: string | null;
  onPick: (id: string | null) => void; onClose: () => void }) {
  return (
    <div role="dialog" aria-label="Your strategies" data-strategy-picker
      className="absolute right-space-3 top-full z-30 mt-space-1 w-72 max-w-[calc(100vw-2rem)] rounded-panel border border-subtle bg-surface-1 p-space-2 shadow-raised">
      {list.length === 0 ? (
        <p className="px-space-2 py-space-2 text-t13 text-fg-muted">No saved strategies yet. Build one in Strategies.</p>
      ) : (
        <ul className="max-h-80 overflow-y-auto" role="listbox" aria-label="Strategy">
          {[{ id: "", name: "None", timeframe: null, drawable: true, reason: null } as ChartStrategy, ...list].map((s) => (
            <li key={s.id || "none"}>
              <button type="button" role="option" aria-selected={(value ?? "") === s.id} disabled={!s.drawable}
                onClick={() => { onPick(s.id || null); onClose(); }} data-strategy-option={s.id || "none"} title={s.reason ?? undefined}
                className={cx("flex min-h-11 w-full items-center justify-between gap-space-2 rounded-control px-space-2 text-left text-t14 md:min-h-9",
                  (value ?? "") === s.id ? "bg-brand-tint text-brand" : "text-fg hover:bg-surface-2", !s.drawable && "cursor-not-allowed opacity-50")}>
                <span className="truncate">{s.name}</span>
                {s.timeframe && <span className="shrink-0 text-t12 text-fg-muted">{s.timeframe}</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** One line under the chart: the strategy, its state, its last signal, and × to remove it. */
export function StrategyLegendV2({ state, onShowTf, onClear }: { state: StrategyOverlayState; onShowTf?: (tf: TimeframeId) => void; onClear: () => void }) {
  const { reply, loading, failed, sameTf, strategyTf } = state;
  if (!reply) {
    if (loading) return <p className="border-t border-subtle px-space-3 py-space-2 text-t12 text-fg-muted" role="status">Reading your strategy…</p>;
    if (failed) return <p className="border-t border-subtle px-space-3 py-space-2 text-t12 text-fg-muted" role="status">Your strategy could not be read just now.</p>;
    return null;
  }
  return (
    <section aria-label={`Strategy: ${reply.name}`} data-strategy-legend className="flex flex-wrap items-center gap-x-space-3 gap-y-space-1 border-t border-subtle px-space-3 py-space-2 text-t13">
      <span className="font-semibold text-fg">{reply.name}</span>
      {!reply.available ? <span className="text-fg-muted">{reply.reason}</span>
        : !sameTf && strategyTf ? (
          <button type="button" className="text-brand hover:underline" onClick={() => onShowTf?.(strategyTf)} data-strategy-switch-tf>
            Built for {timeframeLabel(strategyTf)} - switch
          </button>
        ) : (
          <>
            {reply.state && <span className="text-fg-muted" data-strategy-state={reply.state}>{STATE_WORDS[reply.state]}</span>}
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
