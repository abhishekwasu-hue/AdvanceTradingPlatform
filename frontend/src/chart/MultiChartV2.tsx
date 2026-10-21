import { ExternalLink, Save, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { CrosshairLink } from "../charting/crosshairLink";
import type { LightweightEngine } from "../charting/lightweight";
import { ACTIVE_LTP_MS, INACTIVE_LTP_MS, type LtpHub, type LtpUpdate } from "../charting/ltpHub";
import {
  GRIDS, GROUPS, LayoutConflict, gridShape, layoutProblem, layoutsApi, linkedPeers, nextGroup, setActive, setChartType, setGrid, setGroup, setGroupSync, setSymbol,
  setTimeframe, syncOf, type CellV1, type Group, type GroupSync, type LayoutV1, type StoredLayout,
} from "../charting/workspace";
import { cx } from "../components/primitives/cx";
import { SymbolPicker, recentKeyFor } from "../ui/SymbolPicker";
import { PHONE_QUERY, useMediaQuery } from "../utils/useMediaQuery";
import ChartV2 from "./ChartV2";
import { CHART_TYPES_V2, CHART_TYPE_LABELS_V2 } from "./chartCore";
import { volumeLabelFor } from "./chartsPage";
import { ConfirmBox } from "./ConfirmBox";
import {
  type CandlePool, cellChartType, cellTimeframe, layoutErrorText, readLastLayout, readMultiDraft, saveLastLayout, saveMultiDraft, shownCount, startLayoutV2,
  type StartChart,
} from "./multiChart";
import { DAILY_OR_LONGER, DEFAULT_TIMEFRAME, timeframeLabel, type TimeframeId } from "./timeframes";
import { useChartCandles } from "./useChartCandles";
import type { ChartPrefs } from "./useChartPrefs";

/**
 * UI2-4d (Charts page plan §3, CH5): the Multi-chart tab of the v2 Charts page, shown while `chart_workspace` is on.
 * The CH5 workspace model and saved layouts (`charting/workspace.ts`, `/api/charts/layouts`), with a v2 chart (ChartV2)
 * in every cell: 1, 2, 4, 6 or 8 charts (a phone shows one or two, stacked); link groups A-D share the symbol, the
 * timeframe and the crosshair as each group says; one chart theme for every cell (the trader's chart settings).
 *
 * The cells share their reads: one last-price poll per symbol (LtpHub: 5 s for the active chart, 20 s for the others),
 * one candle read per symbol and timeframe (CandlePool, two on the wire at once) - both the page's, so they outlive a
 * tab switch - and a cell that is off screen, on the hidden tab, or in a hidden browser tab reads nothing until it
 * shows again. Drawings are drawn and edited on the active chart only (as CH5). Charts only: nothing here places an
 * order.
 */
export interface MultiChartV2Props {
  /** The page's symbol: a new trader's two charts show it. */
  symbol: string;
  exchange: string;
  userId: number | string | null;
  /** The timeframes the server offers (week and month only when it lists them), and whether its list is in. */
  offered: TimeframeId[];
  timeframesKnown: boolean;
  prefs: ChartPrefs;
  drawingsHidden: boolean;
  /** A venue's session: false while closed (no live price for intraday bars); null while not known. */
  marketOpen: (exchange: string) => boolean | null;
  /** A symbol the address asked for (an old workspace link); `onStarted` then clears it from the address. */
  start: StartChart | null;
  onStarted: () => void;
  onOpenChart: (symbol: string, exchange: string, timeframe: TimeframeId) => void;
  /** The page's shared reads (kept across tab switches). */
  pool: CandlePool;
  ltpHub: LtpHub;
  /** False while the Chart tab shows: the tab stays mounted, hidden, and reads nothing. */
  shown: boolean;
}

const focus = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";
const btn = cx("inline-flex min-h-11 items-center justify-center gap-space-1 rounded-control px-space-3 text-t13 md:min-h-9", focus);
const iconBtn = cx("inline-flex min-h-11 min-w-11 items-center justify-center rounded-control text-fg-muted hover:bg-surface-2 hover:text-fg md:min-h-9 md:min-w-9", focus);
const field = cx("h-11 rounded-control border border-border bg-surface-2 px-space-2 text-t13 text-fg md:h-9", focus);
const SYNC_LABELS: Record<keyof GroupSync, string> = { symbol: "Link symbol", interval: "Link timeframe", crosshair: "Link crosshair" };
const groupColour = (g: Group) => `rgb(var(--chart-ov-${GROUPS.indexOf(g) + 1}))`;

/** False while the browser tab is hidden. */
function usePageVisible(): boolean {
  const [visible, setVisible] = useState(() => typeof document === "undefined" || document.visibilityState !== "hidden");
  useEffect(() => {
    const on = () => setVisible(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", on);
    return () => document.removeEventListener("visibilitychange", on);
  }, []);
  return visible;
}

/** True while the element is on screen (or near it); false until the browser first says so. */
function useOnScreen(ref: RefObject<HTMLElement | null>): boolean {
  const [on, setOn] = useState(() => typeof IntersectionObserver === "undefined");
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === "undefined") return undefined;
    const io = new IntersectionObserver((entries) => setOn(entries.some((e) => e.isIntersecting)), { rootMargin: "64px" });
    io.observe(el);
    return () => io.disconnect();
  }, [ref]);
  return on;
}

/** The cell's share of the tab's last-price poll (nothing while off). */
function useSharedLtp(hub: LtpHub, enabled: boolean, symbol: string, exchange: string, intervalMs: number): LtpUpdate {
  const [state, setState] = useState<LtpUpdate>({ ltp: null, error: null });
  useEffect(() => {
    setState({ ltp: null, error: null });
    if (!enabled || !symbol) return undefined;
    return hub.subscribe(symbol, exchange, intervalMs, setState);
  }, [hub, enabled, symbol, exchange, intervalMs]);
  return state;
}

export default function MultiChartV2({
  symbol, exchange, userId, offered, timeframesKnown, prefs, drawingsHidden, marketOpen, start, onStarted, onOpenChart, pool, ltpHub, shown,
}: MultiChartV2Props) {
  const phone = useMediaQuery(PHONE_QUERY);
  const pageVisible = usePageVisible();
  const [draftAtStart] = useState(() => readMultiDraft(userId));
  const [layout, setLayout] = useState<LayoutV1>(() => startLayoutV2(start, draftAtStart, symbol, exchange));
  const [saved, setSaved] = useState<StoredLayout[]>([]);
  const [current, setCurrent] = useState<StoredLayout | null>(null);
  const [naming, setNaming] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const layoutRef = useRef(layout);
  layoutRef.current = layout;
  const fromAddress = useRef(start !== null);
  const saveButton = useRef<HTMLButtonElement>(null);

  // An old link's charts are a new, unsaved layout: no saved layout is current, so Save never writes over one.
  useEffect(() => { if (fromAddress.current) { saveLastLayout(null); onStarted(); } }, []);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { saveMultiDraft(userId, layout); }, [userId, layout]);

  // Saved layouts: the list, and the one used last (current unless the address named a symbol; shown unless this
  // browser has a draft, which is that layout as last left).
  useEffect(() => {
    let live = true;
    layoutsApi.list().then((rows) => {
      if (!live) return;
      setSaved(rows);
      const found = rows.find((r) => String(r.id) === readLastLayout());
      if (!found || fromAddress.current || layoutProblem(found.layout) !== null) return;
      setCurrent(found);
      if (!draftAtStart) setLayout(found.layout);
    }).catch(() => { if (live) setMessage("Your saved layouts could not be loaded. Reload the page to try again."); });
    return () => { live = false; };
  }, [draftAtStart]);

  // One crosshair link for the whole tab.
  const link = useMemo(() => new CrosshairLink(() => []), []);
  useEffect(() => { link.setPeers((i) => linkedPeers(layoutRef.current, i, "crosshair")); }, [link, layout]);
  useEffect(() => () => link.dispose(), [link]);

  const apply = useCallback((change: (l: LayoutV1) => LayoutV1) => setLayout((l) => change(l)), []);

  const save = async (name: string | null) => {
    try {
      const row = current && name === null ? await layoutsApi.update(current, layoutRef.current) : await layoutsApi.create(name ?? "My layout", layoutRef.current);
      setCurrent(row);
      setSaved((rows) => [row, ...rows.filter((r) => r.id !== row.id)]);
      saveLastLayout(row.id);
      setNaming(null);
      setMessage(`Saved "${row.name}".`);
    } catch (e) {
      conflicted(e);
      setMessage(layoutErrorText(e, "saved"));
    }
  };

  // A 409 / 404: the stored layout changed or went elsewhere. Its newest version becomes current (the next Save
  // writes over it on purpose) or, gone, none is; the charts on screen stay as they are.
  const conflicted = (e: unknown) => {
    if (!(e instanceof LayoutConflict) || !current) return;
    const fresh = e.current;
    if (fresh) {
      setCurrent(fresh);
      setSaved((rows) => rows.map((r) => (r.id === fresh.id ? fresh : r)));
    } else {
      setCurrent(null);
      setSaved((rows) => rows.filter((r) => r.id !== current.id));
      saveLastLayout(null);
    }
  };
  const cancelNaming = () => { setNaming(null); saveButton.current?.focus(); };

  const remove = async () => {
    setAsking(false);
    if (!current) return;
    try {
      await layoutsApi.remove(current);
      setSaved((rows) => rows.filter((r) => r.id !== current.id));
      setCurrent(null);
      saveLastLayout(null);
      setMessage(`Deleted "${current.name}". The charts on screen stay.`);
    } catch (e) {
      conflicted(e);
      setMessage(layoutErrorText(e, "deleted"));
    }
  };

  const load = (id: string) => {
    const row = saved.find((r) => String(r.id) === id);
    if (!row) { setCurrent(null); saveLastLayout(null); return; }
    if (layoutProblem(row.layout) !== null) { setMessage("That layout cannot be shown here. Pick another one."); return; }
    setCurrent(row); setLayout(row.layout); saveLastLayout(row.id); setMessage(null);
  };

  // Compact cells (four charts or more) leave the RSI pane out; the theme and the rest are the trader's, in every cell.
  const compact = layout.grid >= 4;
  const cellPrefs = useMemo(() => (compact ? { ...prefs, indicators: { ...prefs.indicators, rsi: false } } : prefs), [compact, prefs]);
  const count = shownCount(layout, phone);
  const { cols, rows } = gridShape(layout.grid);
  const grids = phone ? GRIDS.filter((g) => g <= 2) : GRIDS;
  const usedGroups = GROUPS.filter((g) => layout.cells.some((c) => c.group === g));
  // A phone shows the first one or two: the grid button for what is shown is pressed, and the active chart is one of them.
  const pressed = Math.min(layout.grid, count);
  const active = Math.min(layout.active, count - 1);

  return (
    <div data-multi-chart hidden={!shown} className={cx("flex-col gap-space-2", shown ? "flex" : "hidden")}>
      <div role="group" aria-label="Multi-chart controls"
        className="flex flex-wrap items-center gap-space-2 rounded-panel border border-subtle bg-surface-1 p-space-2 text-t13 text-fg">
        <div role="group" aria-label="Charts on screen" className="flex gap-space-1 rounded-control bg-surface-2 p-space-1">
          {grids.map((g) => (
            <button key={g} type="button" aria-pressed={g === pressed} aria-label={`${g} chart${g > 1 ? "s" : ""}`} onClick={() => apply((x) => setGrid(x, g))}
              className={cx("min-h-11 min-w-11 rounded-control px-space-2 md:min-h-8 md:min-w-8", focus,
                g === pressed ? "bg-surface-1 font-semibold text-fg shadow-card" : "text-fg-muted hover:text-fg")}>{g}</button>
          ))}
        </div>
        {usedGroups.map((g) => {
          const sync = syncOf(layout, g);
          return (
            <div key={g} role="group" aria-label={`Link group ${g}`} className="flex flex-wrap items-center gap-x-space-2 rounded-control border border-subtle px-space-2">
              <b style={{ color: groupColour(g) }}>{g}</b>
              {(["symbol", "interval", "crosshair"] as const).map((what) => (
                <label key={what} className="flex min-h-11 items-center gap-space-1 text-fg-muted md:min-h-9">
                  <input type="checkbox" checked={!!sync?.[what]} onChange={(e) => apply((x) => setGroupSync(x, g, { [what]: e.target.checked }))} />
                  {SYNC_LABELS[what]}
                </label>
              ))}
            </div>
          );
        })}
        <div className="flex w-full flex-wrap items-center gap-space-1 md:ml-auto md:w-auto">
          <label className="sr-only" htmlFor="multi-layouts">Saved layouts</label>
          <select id="multi-layouts" value={current ? String(current.id) : ""} onChange={(e) => load(e.target.value)} className={cx(field, "min-w-0 flex-1 md:w-44 md:flex-none")}>
            <option value="">Unsaved layout</option>
            {saved.map((r) => <option key={r.id} value={String(r.id)}>{r.name}</option>)}
          </select>
          <button ref={saveButton} type="button" onClick={() => (current ? void save(null) : setNaming("My layout"))} className={cx(btn, "border border-border bg-surface-2 font-semibold text-fg hover:bg-surface-3")}>
            <Save size={14} aria-hidden /> Save
          </button>
          <button type="button" onClick={() => setNaming(current ? `${current.name} copy` : "My layout")} className={cx(btn, "text-fg-muted hover:bg-surface-2 hover:text-fg")}>Save as…</button>
          {current && (
            <button type="button" aria-label="Delete this saved layout" title="Delete this saved layout" onClick={() => setAsking(true)} className={iconBtn}>
              <Trash2 size={16} aria-hidden />
            </button>
          )}
        </div>
      </div>

      {naming !== null && (
        <form className="flex items-center gap-space-1 rounded-panel border border-subtle bg-surface-1 p-space-2" onSubmit={(e) => { e.preventDefault(); if (naming.trim()) void save(naming.trim()); }}>
          <label className="sr-only" htmlFor="multi-layout-name">Name of the layout</label>
          <input id="multi-layout-name" autoFocus value={naming} maxLength={60} onChange={(e) => setNaming(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); cancelNaming(); } }}
            className={cx(field, "min-w-0 flex-1 text-t14 md:max-w-xs")} />
          <button type="submit" disabled={!naming.trim()} className={cx(btn, "bg-brand font-semibold text-on-brand hover:bg-brand-strong disabled:opacity-50")}>Save layout</button>
          <button type="button" aria-label="Cancel" className={iconBtn} onClick={cancelNaming}><X size={16} aria-hidden /></button>
        </form>
      )}
      {asking && current && (
        <ConfirmBox title={`Delete the saved layout "${current.name}"?`} body="The charts on screen stay; only the saved copy goes." confirm="Delete layout"
          onConfirm={() => void remove()} onCancel={() => setAsking(false)} />
      )}
      {(message || count < layout.grid) && (
        <p role="status" className="px-space-1 text-t13 text-fg-muted">
          {message}{message && count < layout.grid ? " " : ""}
          {count < layout.grid && `This screen shows the first ${count} of the layout's ${layout.grid} charts.`}
        </p>
      )}

      <div className={cx("grid gap-space-2", !phone && "h-[calc(100vh-17rem)] min-h-[480px]")}
        style={phone ? undefined : { gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`, gridTemplateRows: `repeat(${rows}, minmax(0, 1fr))` }}>
        {layout.cells.slice(0, count).map((c, i) => (
          <Cell key={i} index={i} cell={c} active={i === active} phone={phone} pageVisible={pageVisible && shown} userId={userId}
            offered={offered} timeframesKnown={timeframesKnown} prefs={cellPrefs} drawingsHidden={drawingsHidden} marketOpen={marketOpen}
            pool={pool} hub={ltpHub} link={link}
            onFocus={() => apply((x) => setActive(x, i))}
            onSymbol={(s, ex) => apply((x) => setSymbol(x, i, s, ex))}
            onTimeframe={(tf) => apply((x) => setTimeframe(x, i, tf))}
            onType={(t) => apply((x) => setChartType(x, i, t))}
            onGroup={() => apply((x) => setGroup(x, i, nextGroup(x.cells[i].group)))}
            onOpen={(tf) => onOpenChart(c.symbol, c.exchange, tf)} />
        ))}
      </div>
    </div>
  );
}

interface CellProps {
  index: number; cell: CellV1; active: boolean; phone: boolean; pageVisible: boolean; userId: number | string | null;
  offered: TimeframeId[]; timeframesKnown: boolean; prefs: ChartPrefs; drawingsHidden: boolean; marketOpen: (exchange: string) => boolean | null;
  pool: CandlePool; hub: LtpHub; link: CrosshairLink;
  onFocus: () => void; onSymbol: (symbol: string, exchange: string) => void; onTimeframe: (tf: TimeframeId) => void;
  onType: (t: CellV1["chart_type"]) => void; onGroup: () => void; onOpen: (tf: TimeframeId) => void;
}

function Cell({
  index, cell: c, active, phone, pageVisible, userId, offered, timeframesKnown, prefs, drawingsHidden, marketOpen, pool, hub, link,
  onFocus, onSymbol, onTimeframe, onType, onGroup, onOpen,
}: CellProps) {
  const ref = useRef<HTMLElement>(null);
  const onScreen = useOnScreen(ref);
  const running = onScreen && pageVisible;
  const tf = cellTimeframe(c.timeframe, offered, timeframesKnown);
  const shownTf = tf ?? DEFAULT_TIMEFRAME;
  const data = useChartCandles({ enabled: tf !== null, symbol: c.symbol, exchange: c.exchange, timeframe: shownTf, paused: !running, fetchCandles: pool.fetchCandles });
  const last = data.candles[data.candles.length - 1];
  const liveOn = running && data.candles.length > 0 && (DAILY_OR_LONGER.has(shownTf) ? !!last?.forming : marketOpen(c.exchange) !== false);
  const live = useSharedLtp(hub, liveOn, c.symbol, c.exchange, active ? ACTIVE_LTP_MS : INACTIVE_LTP_MS);
  const onEngine = useCallback((engine: LightweightEngine | null) => link.register(index, engine), [link, index]);
  useEffect(() => () => link.register(index, null), [link, index]);
  const n = index + 1;
  const waiting = tf === null || data.loading || (!running && data.candles.length === 0);
  const emptyText = waiting ? "Loading prices…" : data.error ? "" : `No prices for ${c.symbol} on this timeframe yet.`;

  return (
    <section ref={ref} aria-label={`Chart ${n}: ${c.symbol} ${timeframeLabel(shownTf)}`} data-cell={index} data-active={active ? "true" : "false"}
      onPointerDownCapture={onFocus} onFocusCapture={onFocus}
      className={cx("flex min-h-0 min-w-0 flex-col overflow-hidden rounded-panel border bg-surface-1", active ? "border-brand" : "border-subtle")}>
      <div className="flex flex-wrap items-center gap-space-1 border-b border-subtle p-space-1">
        <button type="button" onClick={onGroup} aria-label={`Link group of chart ${n}: ${c.group ?? "none"}`} title="Link group: none, A, B, C, D"
          className={cx("min-h-11 min-w-11 rounded-control border text-t12 font-bold md:min-h-9 md:min-w-9", focus, c.group ? "" : "border-border text-fg-muted")}
          style={c.group ? { color: groupColour(c.group), borderColor: groupColour(c.group) } : undefined}>
          {c.group ?? "–"}
        </button>
        <SymbolPicker value={c.symbol} exchange={c.exchange} onPick={(p) => onSymbol(p.symbol, p.exchange)} storageKey={recentKeyFor(userId)}
          label={`Symbol of chart ${n}`} className="min-w-[7.5rem] flex-1" />
        <label className="sr-only" htmlFor={`multi-tf-${index}`}>Timeframe of chart {n}</label>
        <select id={`multi-tf-${index}`} value={shownTf} onChange={(e) => onTimeframe(e.target.value as TimeframeId)} className={field}>
          {offered.map((t) => <option key={t} value={t}>{timeframeLabel(t)}</option>)}
        </select>
        <label className="sr-only" htmlFor={`multi-type-${index}`}>Chart type of chart {n}</label>
        <select id={`multi-type-${index}`} value={cellChartType(c.chart_type)} onChange={(e) => onType(e.target.value as CellV1["chart_type"])} className={field}>
          {CHART_TYPES_V2.map((t) => <option key={t} value={t}>{CHART_TYPE_LABELS_V2[t]}</option>)}
        </select>
        <button type="button" aria-label={`Open chart ${n} in the Chart tab`} title="Open in the Chart tab" onClick={() => onOpen(shownTf)} className={iconBtn}>
          <ExternalLink size={16} aria-hidden />
        </button>
      </div>
      <ChartV2 candles={data.candles} timeframe={shownTf} symbol={c.symbol} exchange={c.exchange} chartType={cellChartType(c.chart_type)} prefs={prefs}
        live={live.ltp} onLoadOlder={data.loadOlder} drawingTools={active && !drawingsHidden} volumeLabel={volumeLabelFor(data.volumeSource)} emptyText={emptyText}
        onEngine={onEngine} className={phone ? "h-[46vh] min-h-[240px]" : "min-h-0 flex-1"} />
      {data.error && (
        <p role="status" className="flex flex-wrap items-center gap-x-space-2 border-t border-subtle px-space-2 py-space-1 text-t12 text-fg">
          {data.error}
          <button type="button" onClick={data.retry} className={cx("min-h-11 font-semibold text-brand hover:underline md:min-h-8", focus)}>Try again</button>
        </p>
      )}
    </section>
  );
}
