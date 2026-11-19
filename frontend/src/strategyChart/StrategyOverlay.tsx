import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { LightweightEngine } from "../charting/lightweight";
import type { ChartMarker } from "../charting/markers";
import { cx } from "../components/primitives/cx";
import { chartTimeframe } from "../screener/evidence";
import { readChartPalette } from "../chart/chartTheme";
import { timeframeLabel, type ChartBar, type TimeframeId } from "../chart/timeframes";
import { STATE_WORDS, strategyChartApi, strategyLayer, strategyMarkers, strategyStudies, whyAt, type ChartStrategy, type FormingBar, type PlotReply,
  type WhyCard } from "./model";
import { formatDateTimeIST, formatNumber } from "../ui/format";
import { Sheet } from "../components/primitives/Dialog";
import { PHONE_QUERY, useMediaQuery } from "../utils/useMediaQuery";
import { exitWord, tradePoints, tradeRange, type PlotTrade } from "./model";

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

export function useStrategyList(on: boolean, api: Pick<typeof strategyChartApi, "list"> = strategyChartApi, revision = 0): ChartStrategy[] {
  const [list, setList] = useState<ChartStrategy[]>([]);
  useEffect(() => {
    if (!on) { setList([]); return undefined; }
    let live = true;
    api.list().then((r) => { if (live) setList(r.strategies); }, () => { if (live) setList([]); });
    return () => { live = false; };
  }, [api, on, revision]);
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
export function StrategyLayerV2({ engine, strategyId, slot, symbol, exchange, timeframe, candles, onMarkers, onShowTf, onClear, legend = true, whySlot = null }: {
  engine: LightweightEngine | null; strategyId: string; slot: number; symbol: string; exchange: string; timeframe: TimeframeId;
  candles: readonly ChartBar[]; onMarkers: (id: string, markers: ChartMarker[]) => void; onShowTf?: (tf: TimeframeId) => void; onClear: () => void;
  legend?: boolean; whySlot?: HTMLElement | null }) {
  const state = useStrategyOverlay(engine, { strategyId, slot, symbol, exchange, timeframe, candles });
  useEffect(() => { onMarkers(strategyId, state.markers); }, [onMarkers, strategyId, state.markers]);
  useEffect(() => () => onMarkers(strategyId, NONE), [onMarkers, strategyId]);
  const cards = useMarkerWhy(engine, state.sameTf ? state.reply : null);
  const why = cards.length > 0 ? <StrategyWhyV2 name={state.reply?.name ?? ""} colour={state.colour} cards={cards} floating={!!whySlot} /> : null;
  const [statsOpen, setStatsOpen] = useState(false);
  const phone = useMediaQuery(PHONE_QUERY);
  const barSeconds = candles.length > 1 ? Math.max(60, (Date.parse(candles[candles.length - 1].timestamp) - Date.parse(candles[candles.length - 2].timestamp)) / 1000) : 300;
  const last = candles.length ? Math.floor(Date.parse(candles[candles.length - 1].timestamp) / 1000) : null;
  const showTrade = (t: PlotTrade) => {
    const r = tradeRange(t, barSeconds, last);
    engine?.setVisibleTimeRange(r.from, r.to);
    if (phone) setStatsOpen(false);                       // a phone: the sheet covers the chart - close it to show the trade
  };
  return (
    <>
      {legend && <StrategyLegendV2 state={state} onShowTf={onShowTf} onClear={onClear} onStats={state.sameTf && state.reply?.stats ? () => setStatsOpen(true) : undefined} />}
      {state.reply && <StrategyStatsV2 reply={state.reply} open={statsOpen} onOpenChange={setStatsOpen} side={phone ? "bottom" : "right"} onTrade={showTrade} />}
      {why && (whySlot ? createPortal(why, whySlot) : legend ? why : null)}
    </>
  );
}

/** P3: the marker under the crosshair (desktop hover) or the one tapped (phone; it stays until another tap). */
export function useMarkerWhy(engine: LightweightEngine | null, reply: PlotReply | null): WhyCard[] {
  const [hover, setHover] = useState<number | null>(null);
  const [pinned, setPinned] = useState<number | null>(null);
  const replyRef = useRef(reply);
  replyRef.current = reply;
  useEffect(() => {
    if (!engine) return undefined;
    return engine.on((e) => {
      if (e.type === "crosshair") setHover(e.time);
      else if (e.type === "click") setPinned(whyAt(replyRef.current, e.time).length ? e.time : null);
    });
  }, [engine]);
  return useMemo(() => {
    const live = whyAt(reply, hover);
    return live.length ? live : whyAt(reply, pinned);
  }, [reply, hover, pinned]);
}

const valueText = (v: number | null) => (v === null ? "—" : formatNumber(v, 2, { trim: true }));

/** P3: why a marker is there - the conditions that held (with their values), the ones an OR group did not need, or the
 * rule that closed the trade and its result. Reference only. */
export function StrategyWhyV2({ name, colour, cards, floating = false }: { name: string; colour: string; cards: WhyCard[]; floating?: boolean }) {
  return (
    <section aria-label={`Why: ${name}`} data-strategy-why aria-live="polite"
      className={cx("px-space-3 py-space-2 text-t13", floating ? "mb-space-1 rounded-panel border border-subtle bg-surface-1/95 shadow-raised" : "border-t border-subtle")}>
      {cards.map((c) => (
        <div key={c.id} className="py-space-1" data-why-card={c.id}>
          <p className="flex flex-wrap items-center gap-x-space-2 font-semibold">
            {colour && <span aria-hidden className="inline-block h-space-2 w-space-2 shrink-0 rounded-full" style={{ backgroundColor: colour }} />}
            <span className={c.tone === "up" ? "text-up" : c.tone === "down" ? "text-down" : "text-fg-muted"}>{c.title}</span>
            <span className="font-normal text-fg-muted">{formatDateTimeIST(c.t)}</span>
          </p>
          {c.rule !== undefined ? (
            <p className="text-fg-muted" data-why-exit>
              Rule: <span className="text-fg">{c.rule || "Exit"}</span>
              {c.pnl != null && <> · P&amp;L <span className={c.pnl >= 0 ? "text-up" : "text-down"}>{c.pnl >= 0 ? "+" : ""}{formatNumber(c.pnl, 2)}</span></>}
            </p>
          ) : (
            <ul className="mt-space-1 space-y-space-1">
              {c.rows.map((r) => (
                <li key={r.id} className="flex flex-wrap items-baseline gap-x-space-2" data-why-row={r.id} data-held={r.held}>
                  <span aria-hidden className={r.held ? "text-up" : "text-fg-muted"}>{r.held ? "✓" : "✗"}</span>
                  <span className={r.held ? "text-fg" : "text-fg-muted"}>{r.text}</span>
                  <span className="text-t12 text-fg-muted">{Object.values(r.values).map(valueText).join(" · ")}</span>
                  {r.optional && <span className="text-t12 text-fg-muted">(not needed: another condition of its OR group held)</span>}
                  <span className="sr-only">{r.held ? "held" : "did not hold"}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </section>
  );
}

/** The toolbar's Strategy choice: his own strategies, up to MAX_STRATEGIES at once (a chosen one is removed by choosing
 * it again), or none. */
export function StrategyPickerV2({ list, value, onPick, onClose, onNew, onEdit }: { list: ChartStrategy[]; value: readonly string[];
  onPick: (id: string | null) => void; onClose: () => void; onNew?: () => void; onEdit?: (recordId: number) => void }) {
  const full = value.length >= MAX_STRATEGIES;
  return (
    <div role="dialog" aria-label="Your strategies" data-strategy-picker
      className="absolute right-space-3 top-full z-30 mt-space-1 w-72 max-w-[calc(100vw-2rem)] rounded-panel border border-subtle bg-surface-1 p-space-2 shadow-raised">
      {list.length === 0 ? (
        <p className="px-space-2 py-space-2 text-t13 text-fg-muted">No saved strategies yet.{onNew ? "" : " Build one in Strategies."}</p>
      ) : (
        <ul className="max-h-80 overflow-y-auto" role="listbox" aria-label="Strategy">
          {[{ id: "", name: "None", timeframe: null, drawable: true, reason: null } as ChartStrategy, ...list].map((s) => {
            const chosen = s.id ? value.includes(s.id) : value.length === 0;
            const blocked = !s.drawable || (!!s.id && full && !chosen);
            return (
            <li key={s.id || "none"} className="flex items-center gap-space-1">
              <button type="button" role="option" aria-selected={chosen} disabled={blocked}
                onClick={() => { onPick(s.id || null); onClose(); }} data-strategy-option={s.id || "none"}
                title={s.reason ?? (blocked && s.drawable ? `At most ${MAX_STRATEGIES} strategies at once` : undefined)}
                className={cx("flex min-h-11 w-full items-center justify-between gap-space-2 rounded-control px-space-2 text-left text-t14 md:min-h-9",
                  chosen ? "bg-brand-tint text-brand" : "text-fg hover:bg-surface-2", blocked && "cursor-not-allowed opacity-50")}>
                <span className="truncate">{s.name}</span>
                {s.timeframe && <span className="shrink-0 text-t12 text-fg-muted">{s.timeframe}</span>}
              </button>
              {onEdit && s.editable && (
                <button type="button" aria-label={`Edit ${s.name}`} data-strategy-edit={s.id}
                  onClick={() => { onEdit(Number(s.id.split(":")[1])); onClose(); }}
                  className="inline-flex min-h-11 shrink-0 items-center rounded-control px-space-2 text-t12 text-brand hover:bg-surface-2 md:min-h-9">Edit</button>
              )}
            </li>
            );
          })}
        </ul>
      )}
      {onNew && (
        <button type="button" onClick={() => { onNew(); onClose(); }} data-strategy-new
          className="mt-space-1 flex min-h-11 w-full items-center rounded-control border-t border-subtle px-space-2 text-left text-t14 font-semibold text-brand hover:bg-surface-2 md:min-h-9">
          + New strategy
        </button>
      )}
    </div>
  );
}

/** One line under the chart: the strategy, its state, its last signal, and × to remove it. */
export function StrategyLegendV2({ state, onShowTf, onClear, onStats }: { state: StrategyOverlayState; onShowTf?: (tf: TimeframeId) => void; onClear: () => void;
  onStats?: () => void }) {
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
      {onStats && (
        <button type="button" onClick={onStats} data-strategy-stats aria-haspopup="dialog"
          className="ml-auto inline-flex min-h-9 items-center rounded-control px-space-2 text-t13 text-brand hover:bg-surface-2">Stats</button>
      )}
      <button type="button" onClick={onClear} aria-label={`Remove ${reply.name} from the chart`} data-strategy-clear
        className={cx(!onStats && "ml-auto", " inline-flex min-h-9 min-w-9 items-center justify-center rounded-control text-fg-muted hover:bg-surface-2 hover:text-fg")}>×</button>
    </section>
  );
}

const signed = (v: number | null | undefined, d = 2) => (v == null ? "—" : `${v > 0 ? "+" : ""}${formatNumber(v, d)}`);
const tone = (v: number | null | undefined) => (v == null || v === 0 ? "text-fg" : v > 0 ? "text-up" : "text-down");

/** P4: the strategy's quick stats on the chart's bars and its trades; a trade's row scrolls the chart to it. The same
 * walk as the markers (a backtest of the bars on the chart), so the count is the number of entry arrows (T8). */
export function StrategyStatsV2({ reply, open, onOpenChange, side, onTrade }: { reply: PlotReply; open: boolean; onOpenChange: (o: boolean) => void;
  side: "right" | "bottom"; onTrade: (t: PlotTrade) => void }) {
  const st = reply.stats;
  if (!st) return null;
  const trades = [...(reply.trades ?? [])].reverse();             // newest first
  const tiles: [string, string, string, string][] = [
    ["Trades", `${st.trades}`, st.open ? `${st.open} open` : "", "text-fg"],
    ["Win rate", st.win_rate == null ? "—" : `${formatNumber(st.win_rate, 0)}%`, `${st.wins} of ${st.closed}`, "text-fg"],
    ["P&L (points)", signed(st.points), st.pnl == null ? "" : `₹ ${signed(st.pnl, 0)}`, tone(st.points)],
    ["Max drawdown", signed(st.max_drawdown), "points", tone(st.max_drawdown)],
    ["Average R", signed(st.avg_r), "trades with a stop", tone(st.avg_r)],
  ];
  return (
    <Sheet open={open} onOpenChange={onOpenChange} side={side} title={`${reply.name}: stats`}
      description="Its trades on this chart's bars. Reference only.">
      <div data-strategy-stats-sheet className="space-y-space-4">
        <dl className="grid grid-cols-2 gap-space-2 sm:grid-cols-3">
          {tiles.map(([label, value, note, cls]) => (
            <div key={label} className="rounded-control border border-subtle bg-surface-2 px-space-3 py-space-2" data-stat={label}>
              <dt className="text-t12 text-fg-muted">{label}</dt>
              <dd className={cx("text-t16 font-semibold tabular-nums", cls)}>{value}</dd>
              {note && <dd className="text-t12 text-fg-muted">{note}</dd>}
            </div>
          ))}
        </dl>
        {trades.length === 0 ? <p className="text-t13 text-fg-muted">No trades on these bars yet.</p> : (
          <ul className="divide-y divide-subtle rounded-control border border-subtle" aria-label="Trades, newest first">
            {trades.map((t) => {
              const pts = tradePoints(t);
              return (
                <li key={t.entry_time}>
                  <button type="button" onClick={() => onTrade(t)} data-stats-trade={t.entry_time}
                    className="grid min-h-11 w-full grid-cols-[auto_1fr_auto] items-center gap-x-space-3 px-space-3 py-space-2 text-left text-t13 hover:bg-surface-2">
                    <span className={t.side === "long" ? "font-semibold text-up" : "font-semibold text-down"}>{t.side === "long" ? "Long" : "Short"}</span>
                    <span className="min-w-0">
                      <span className="block truncate text-fg">{formatDateTimeIST(t.entry_time)}</span>
                      <span className="block text-t12 text-fg-muted">
                        {formatNumber(t.entry ?? NaN, 2)} → {t.exit === null ? "open" : `${formatNumber(t.exit, 2)} · ${exitWord(t.exit_rule ?? undefined)}`}
                      </span>
                    </span>
                    <span className={cx("tabular-nums font-semibold", tone(pts))}>{pts === null ? "Open" : signed(pts)}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </Sheet>
  );
}
