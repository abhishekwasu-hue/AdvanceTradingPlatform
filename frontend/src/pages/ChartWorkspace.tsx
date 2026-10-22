import { HelpCircle, Maximize2, Minimize2, Save, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import { useFeatures } from "../ui/useFeatures";
import { useAuth } from "../auth/AuthContext";
import { useBrokerChart } from "../components/chartHistory";
import ProChart from "../components/ProChart";
import { ACTIVE_LTP_MS, INACTIVE_LTP_MS, LtpHub, type LtpUpdate } from "../charting/ltpHub";
import type { ChartEngine } from "../charting/engine";
import { CHART_TYPE_LABELS } from "../charting/chartTypes";
import type { IndicatorId } from "../utils/indicators";
import { CrosshairLink } from "../charting/crosshairLink";
import { SHORTCUTS, nextChartType, shortcutFor, type WorkspaceAction } from "../charting/shortcuts";
import {
  CH5_TIMEFRAMES, CHART_TYPES, GRIDS, GROUPS, GROUP_COLORS, LayoutConflict, ch5Cell, defaultLayout, gridShape, layoutProblem, layoutsApi, linkedPeers, moveActive,
  nextGroup, setActive, setChartType, setGrid, setGroup, setGroupSync, setSymbol, setTimeframe, stepTimeframe, syncOf,
  type CellV1, type ChartType, type Group, type LayoutV1, type StoredLayout, type Timeframe,
} from "../charting/workspace";
import { workspaceRedirect } from "../chart/multiChart";

const DRAFT_KEY = "atp_workspace_draft";
const LAST_KEY = "atp_workspace_last";
const HEADER_PX = 56;
const FULL_INDICATORS: IndicatorId[] = ["ema_fast", "ema_slow"];   // stable arrays: ProChart resets its toggles when these change
const NO_INDICATORS: IndicatorId[] = [];
const CELL_CHROME_PX = 84;

function readDraft(): LayoutV1 | null {
  try {
    const raw = localStorage.getItem(DRAFT_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    return parsed && layoutProblem(parsed) === null ? (parsed as LayoutV1) : null;
  } catch {
    return null;
  }
}

function store(key: string, value: string | null) {
  try { if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value); } catch { /* storage unavailable */ }
}

/**
 * CH5: the multi-chart workspace, in its own browser tab: `/?workspace=1` (optionally `&chart=SYMBOL&tf=5min&exchange=NSE`
 * to start from one chart). 1, 2, 4, 6 or 8 charts; link groups A-D share the symbol / interval / crosshair as each
 * group says; layouts are saved per user (flag `chart_workspace`; while it is off the page says so and saves nothing).
 * Each chart is the same ProChart as the single-chart window, drawing tools on the active chart only. Charts only:
 * nothing here places an order.
 */
export default function ChartWorkspace({ params }: { params: URLSearchParams }) {
  const { user, loading } = useAuth();
  // UI2-0: from the app's single /system/features read; null until it is in.
  const features = useFeatures();
  const enabled: boolean | null = features.loaded ? features.on("chart_workspace") : null;
  // UI2-4d: with `ui_v2` on, an old workspace link opens the Charts page's Multi-chart tab instead (or, while
  // `chart_workspace` is off, its symbol on the Chart tab). With `ui_v2` off: as before.
  const v2 = features.loaded && features.on("ui_v2");
  useEffect(() => { if (v2) window.location.replace(workspaceRedirect(params, enabled === true)); }, [v2, params, enabled]);

  if (loading || v2) return <div className="p-6 text-sm text-fg-muted">Loading…</div>;
  if (!user) return <div className="p-6 text-sm text-fg">Log in in the main tab first, then open the workspace again.</div>;
  if (enabled === null) return <div className="p-6 text-sm text-fg-muted">Loading…</div>;
  if (!enabled) return <div className="p-6 text-sm text-fg">The multi-chart workspace is not enabled for your organisation yet. Single charts open from any chart&apos;s &quot;New tab&quot; button.</div>;
  return <Workspace params={params} />;
}

function startLayout(params: URLSearchParams): LayoutV1 {
  const symbol = (params.get("chart") ?? "").trim();
  if (symbol) {
    const tf = (CH5_TIMEFRAMES as readonly string[]).includes(params.get("tf") ?? "") ? (params.get("tf") as Timeframe) : "5min";
    return defaultLayout(symbol, params.get("exchange") ?? "NSE", tf);
  }
  return readDraft() ?? defaultLayout();
}

function Workspace({ params }: { params: URLSearchParams }) {
  const [layout, setLayout] = useState<LayoutV1>(() => startLayout(params));
  const [saved, setSaved] = useState<StoredLayout[]>([]);
  const [current, setCurrent] = useState<StoredLayout | null>(null);
  const [maximized, setMaximized] = useState(false);
  const [help, setHelp] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [winH, setWinH] = useState(() => window.innerHeight);
  const symbolBoxes = useRef<(HTMLInputElement | null)[]>([]);
  const layoutRef = useRef(layout);
  layoutRef.current = layout;

  useEffect(() => { store(DRAFT_KEY, JSON.stringify(layout)); }, [layout]);
  useEffect(() => {
    const onResize = () => setWinH(window.innerHeight);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  useEffect(() => {
    const a = layout.cells[layout.active];
    document.title = `Workspace · ${a?.symbol ?? ""} · AiAlgoDesk`;
  }, [layout]);

  // Saved layouts: the list, and the one used last (unless the URL asked for a symbol).
  useEffect(() => {
    let live = true;
    layoutsApi.list().then((rows) => {
      if (!live) return;
      setSaved(rows);
      let last: string | null = null;
      try { last = localStorage.getItem(LAST_KEY); } catch { last = null; }
      const found = rows.find((r) => String(r.id) === last);
      if (found && !params.get("chart") && layoutProblem(found.layout) === null) { setCurrent(found); setLayout(found.layout); }
    }).catch((e) => setMessage(`Saved layouts unavailable: ${String(e).replace(/^Error:\s*/, "")}`));
    return () => { live = false; };
  }, [params]);

  // CH5-a review (ADR-0004): one last-price poll per symbol for the whole workspace, shared by the charts showing it.
  const ltpHub = useMemo(() => new LtpHub((symbol, exchange) => api.marketDataLtp(symbol, exchange)), []);
  useEffect(() => () => ltpHub.dispose(), [ltpHub]);
  // Crosshair link: one per workspace; the peers follow the layout's groups.
  const link = useMemo(() => new CrosshairLink(() => []), []);
  useEffect(() => { link.setPeers((i) => linkedPeers(layoutRef.current, i, "crosshair")); }, [link, layout]);
  useEffect(() => () => link.dispose(), [link]);

  const apply = useCallback((change: (l: LayoutV1) => LayoutV1) => setLayout((l) => change(l)), []);

  const save = useCallback(async (asNew: boolean) => {
    const l = layoutRef.current;
    try {
      let row: StoredLayout;
      if (current && !asNew) {
        row = await layoutsApi.update(current, l);
      } else {
        const name = window.prompt("Name of this layout", current && asNew ? `${current.name} copy` : "My layout");
        if (!name) return;
        row = await layoutsApi.create(name, l);
      }
      setCurrent(row);
      setSaved((rows) => [row, ...rows.filter((r) => r.id !== row.id)]);
      store(LAST_KEY, String(row.id));
      setMessage(`Saved “${row.name}”.`);
    } catch (e) {
      setMessage(e instanceof LayoutConflict ? e.message : `Not saved: ${String(e).replace(/^Error:\s*/, "")}`);
    }
  }, [current]);

  const remove = useCallback(async () => {
    if (!current || !window.confirm(`Delete the saved layout “${current.name}”? The charts on screen stay.`)) return;
    try {
      await layoutsApi.remove(current);
      setSaved((rows) => rows.filter((r) => r.id !== current.id));
      setCurrent(null);
      store(LAST_KEY, null);
      setMessage(`Deleted “${current.name}”.`);
    } catch (e) {
      setMessage(e instanceof LayoutConflict ? e.message : `Not deleted: ${String(e).replace(/^Error:\s*/, "")}`);
    }
  }, [current]);

  const load = (id: string) => {
    const row = saved.find((r) => String(r.id) === id);
    if (!row) { setCurrent(null); store(LAST_KEY, null); return; }
    const problem = layoutProblem(row.layout);
    if (problem) { setMessage(`That layout cannot be shown: ${problem}`); return; }
    setCurrent(row); setLayout(row.layout); setMaximized(false); store(LAST_KEY, String(row.id));
  };

  // Keyboard shortcuts (Alt+...; "?" for the list).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const action: WorkspaceAction | null = shortcutFor(e);
      if (!action) { if (e.key === "Escape") setHelp(false); return; }
      e.preventDefault();
      const l = layoutRef.current;
      const a = l.active;
      switch (action.type) {
        case "grid": apply((x) => setGrid(x, action.grid)); setMaximized(false); break;
        case "focus": apply((x) => moveActive(x, action.delta)); break;
        case "timeframe": apply((x) => setTimeframe(x, a, stepTimeframe(ch5Cell(x.cells[a]).timeframe, action.delta, CH5_TIMEFRAMES))); break;
        case "cycleGroup": apply((x) => setGroup(x, a, nextGroup(x.cells[a].group))); break;
        case "cycleChartType": apply((x) => setChartType(x, a, nextChartType(ch5Cell(x.cells[a]).chart_type))); break;
        case "maximize": setMaximized((m) => !m); break;
        case "symbol": symbolBoxes.current[a]?.focus(); symbolBoxes.current[a]?.select(); break;
        case "save": void save(false); break;
        case "help": setHelp((h) => !h); break;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [apply, save]);

  const { cols, rows } = gridShape(layout.grid);
  const shownRows = maximized ? 1 : rows;
  const cellHeight = Math.max(160, Math.floor((winH - HEADER_PX - 16) / shownRows) - CELL_CHROME_PX);
  const usedGroups = GROUPS.filter((g) => layout.cells.some((c) => c.group === g));

  return (
    <div className="flex min-h-screen flex-col bg-surface">
      <h1 className="sr-only">Chart workspace</h1>
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2 text-xs" style={{ minHeight: HEADER_PX }}>
        <span className="font-semibold text-fg">Workspace</span>
        <div className="flex overflow-hidden rounded-md border border-border" role="group" aria-label="Charts on screen">
          {GRIDS.map((g) => (
            <button key={g} onClick={() => { apply((x) => setGrid(x, g)); setMaximized(false); }} title={`${g} chart${g > 1 ? "s" : ""} (Alt+${g})`}
                    className={`px-2 py-0.5 ${g === layout.grid ? "bg-brand/15 font-semibold text-brand" : "text-fg-muted hover:text-fg"}`}>{g}</button>
          ))}
        </div>
        {usedGroups.map((g) => {
          const sync = syncOf(layout, g);
          return (
            <span key={g} className="flex items-center gap-1 rounded border border-border px-1.5 py-0.5" aria-label={`Link group ${g}`}>
              <b style={{ color: GROUP_COLORS[g] }}>{g}</b>
              {(["symbol", "interval", "crosshair"] as const).map((what) => (
                <label key={what} className="flex items-center gap-0.5 text-fg-muted">
                  <input type="checkbox" checked={!!sync?.[what]} onChange={(e) => apply((x) => setGroupSync(x, g, { [what]: e.target.checked }))} />
                  {what}
                </label>
              ))}
            </span>
          );
        })}
        <div className="ml-auto flex items-center gap-1">
          <select aria-label="Saved layouts" value={current ? String(current.id) : ""} onChange={(e) => load(e.target.value)}
                  className="rounded border border-border bg-surface px-1.5 py-0.5 text-fg">
            <option value="">Unsaved layout</option>
            {saved.map((r) => <option key={r.id} value={String(r.id)}>{r.name}</option>)}
          </select>
          <button onClick={() => void save(false)} title="Save the layout (Alt+Shift+S)" className="flex items-center gap-1 rounded border border-border px-1.5 py-0.5 font-semibold text-fg hover:bg-surface-2"><Save size={11} /> Save</button>
          <button onClick={() => void save(true)} title="Save under a new name" className="rounded border border-border px-1.5 py-0.5 text-fg hover:bg-surface-2">Save as…</button>
          {current && <button onClick={() => void remove()} title="Delete this saved layout" aria-label="Delete this saved layout" className="rounded p-1 text-fg-muted hover:text-down"><Trash2 size={12} /></button>}
          <button onClick={() => setHelp((h) => !h)} title="Keyboard shortcuts (?)" aria-label="Keyboard shortcuts" className="rounded p-1 text-fg-muted hover:text-fg"><HelpCircle size={14} /></button>
        </div>
        {message && <span role="status" className="w-full text-xs text-fg-muted">{message}</span>}
      </div>
      {help && (
        <div role="dialog" aria-label="Keyboard shortcuts" className="absolute right-3 top-14 z-50 w-80 rounded-md border border-border bg-panel p-3 text-xs shadow-lg">
          <div className="mb-2 font-semibold text-fg">Keyboard shortcuts</div>
          <table className="w-full"><tbody>
            {SHORTCUTS.map((s) => <tr key={s.keys}><td className="pr-2 font-mono text-fg">{s.keys}</td><td className="text-fg-muted">{s.does}</td></tr>)}
          </tbody></table>
          <div className="mt-2 text-fg-muted">Drawing tools work on the active chart (Esc, Delete, Ctrl/Cmd+Z / Y).</div>
        </div>
      )}
      <div className="grid flex-1 gap-1 p-1" style={{ gridTemplateColumns: `repeat(${maximized ? 1 : cols}, minmax(0, 1fr))` }}>
        {layout.cells.map((c, i) => (maximized && i !== layout.active ? null : (
          <WorkspaceCell key={i} index={i} cell={c} active={i === layout.active} compact={!maximized && layout.grid >= 4 && i !== layout.active}
                         height={cellHeight} maximized={maximized} link={link} ltpHub={ltpHub}
                         symbolBox={(el) => { symbolBoxes.current[i] = el; }}
                         onFocus={() => apply((x) => setActive(x, i))}
                         onSymbol={(s) => apply((x) => setSymbol(x, i, s))}
                         onTimeframe={(tf) => apply((x) => setTimeframe(x, i, tf))}
                         onChartType={(t) => apply((x) => setChartType(x, i, t))}
                         onGroup={(g) => apply((x) => setGroup(x, i, g))}
                         onMaximize={() => { apply((x) => setActive(x, i)); setMaximized((m) => !m); }} />
        )))}
      </div>
    </div>
  );
}

interface CellProps {
  index: number; cell: CellV1; active: boolean; compact: boolean; height: number; maximized: boolean; link: CrosshairLink; ltpHub: LtpHub;
  symbolBox: (el: HTMLInputElement | null) => void;
  onFocus: () => void; onSymbol: (symbol: string) => void; onTimeframe: (tf: Timeframe) => void; onGroup: (g: Group | null) => void; onMaximize: () => void;
  onChartType: (t: ChartType) => void;
}

function WorkspaceCell({ index, cell, active, compact, height, maximized, link, ltpHub, symbolBox, onFocus, onSymbol, onTimeframe, onGroup, onMaximize, onChartType }: CellProps) {
  const c = ch5Cell(cell);           // UI2-4d: a v2 cell's week / month / bars shows as day / candles here
  const chart = useBrokerChart({ enabled: true, symbol: c.symbol, timeframe: c.timeframe, exchange: c.exchange });
  const live = useSharedLtp(ltpHub, c.timeframe !== "day", c.symbol, c.exchange, active ? ACTIVE_LTP_MS : INACTIVE_LTP_MS);
  const [draft, setDraft] = useState(c.symbol);
  useEffect(() => { setDraft(c.symbol); }, [c.symbol]);
  const onEngine = useCallback((engine: ChartEngine | null) => link.register(index, engine), [link, index]);
  useEffect(() => () => link.register(index, null), [link, index]);

  return (
    <section aria-label={`Chart ${index + 1}: ${c.symbol} ${c.timeframe}`} onPointerDownCapture={onFocus}
             className={`min-w-0 rounded-md border p-1 ${active ? "border-brand/60" : "border-border"}`}>
      <div className="mb-1 flex items-center gap-1 text-xs">
        <button onClick={() => onGroup(nextGroup(c.group))} title="Link group: none → A → B → C → D (Alt+G)"
                aria-label={`Link group ${c.group ?? "none"}`}
                className="h-5 w-5 rounded border border-border text-xs font-bold"
                style={c.group ? { color: GROUP_COLORS[c.group], borderColor: GROUP_COLORS[c.group] } : undefined}>{c.group ?? "–"}</button>
        <form onSubmit={(e) => { e.preventDefault(); onSymbol(draft); }} className="min-w-0">
          <input ref={symbolBox} value={draft} onChange={(e) => setDraft(e.target.value.toUpperCase())} onBlur={() => setDraft(c.symbol)}
                 aria-label={`Symbol of chart ${index + 1}`} className="w-28 rounded border border-border bg-surface px-1 py-0.5 font-semibold text-fg" />
        </form>
        <span className="text-fg-muted">{c.exchange}</span>
        <select value={c.chart_type} onChange={(e) => onChartType(e.target.value as ChartType)} aria-label={`Chart type of chart ${index + 1}`}
                title="Chart type (Alt+T)" className="rounded border border-border bg-surface px-1 py-0.5 text-fg">
          {CHART_TYPES.map((t) => <option key={t} value={t}>{CHART_TYPE_LABELS[t]}</option>)}
        </select>
        {chart.error && <span className="truncate text-down" title={chart.error}>{chart.error}</span>}
        <button onClick={onMaximize} title={maximized ? "Back to the grid (Alt+Enter)" : "Full size (Alt+Enter)"} aria-label={maximized ? "Back to the grid" : "Full size"}
                className="ml-auto rounded p-0.5 text-fg-muted hover:text-fg">{maximized ? <Minimize2 size={11} /> : <Maximize2 size={11} />}</button>
      </div>
      <ProChart candles={chart.candles} symbol={c.symbol} timeframe={c.timeframe} timeframes={[...CH5_TIMEFRAMES]} onTimeframeChange={(tf) => onTimeframe(tf as Timeframe)}
                live={c.timeframe === "day" ? null : live.ltp} liveError={live.error} exchange={c.exchange} height={height} compact={compact}
                defaultIndicators={compact ? NO_INDICATORS : FULL_INDICATORS} drawingTools={active} chartType={c.chart_type} venueAxis
                onLoadOlder={chart.loadOlder} loadingOlder={chart.loadingOlder} olderExhausted={chart.exhausted} onEngine={onEngine} />
    </section>
  );
}

/** The cell's share of the workspace's last-price poll (nothing while disabled, e.g. daily charts). */
function useSharedLtp(hub: LtpHub, enabled: boolean, symbol: string, exchange: string, intervalMs: number): LtpUpdate {
  const [state, setState] = useState<LtpUpdate>({ ltp: null, error: null });
  useEffect(() => {
    setState({ ltp: null, error: null });
    if (!enabled || !symbol) return;
    return hub.subscribe(symbol, exchange, intervalMs, setState);
  }, [hub, enabled, symbol, exchange, intervalMs]);
  return state;
}
