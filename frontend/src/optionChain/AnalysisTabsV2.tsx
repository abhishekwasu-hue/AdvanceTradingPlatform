import { useEffect, useRef, useState } from "react";
import { cx } from "../components/primitives/cx";
import { SubTabs } from "../ui/SubTabs";
import { HelpPopover } from "../ui/HelpPopover";
import { TermHint } from "../ui/TermHint";
import { formatTimeIST } from "../ui/format";
import {
  CHART, TABS, analysisFor, fetchAnalyses, formatCell, hasOutside, isFigure, markerPixel, pcrHeadline, pointNote, provenance, stateMessage,
  timePixel, timeTicks, toneClasses, visibleChart, xLabel, xPixel, xValues, yPixel, yScale, yTick, type Analysis, type AnalysesResponse,
  type AnalysisChart, type AnalysisKey, type AnalysisTable,
} from "./analyses";

/**
 * OC2 (owner's order, PART C1): the built-in analyses below the v2 option chain - six tabs (support / resistance from OI,
 * OI build-up, PCR, max pain, expected move, IV smile / skew), each with a table, a small chart and two or three plain
 * sentences. The numbers and sentences come from `GET /api/option-chain/analyses` (the user's own broker chain and the
 * session's earlier snapshots); the tabs only show them. No broker, or no data yet, shows the server's plain sentence -
 * never sample numbers.
 *
 * Standalone: the option chain page mounts it with one line, `<AnalysisTabs symbol="NIFTY" expiry={expiry} />`, and
 * passes `refreshKey` to read again when its own chain refreshes. `load` replaces the server read (tests, stories).
 */
export interface AnalysisTabsProps {
  symbol: string;
  expiry?: string | null;
  refreshKey?: number | string;
  initialTab?: AnalysisKey;
  load?: (symbol: string, expiry?: string | null) => Promise<AnalysesResponse>;
}

export function AnalysisTabs({ symbol, expiry = null, refreshKey, initialTab = "support_resistance", load = fetchAnalyses }: AnalysisTabsProps) {
  const [data, setData] = useState<AnalysesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(!!symbol.trim());
  const [tab, setTab] = useState<AnalysisKey>(initialTab);
  // The latest `load` without making it a reason to read again (a host may pass a new function on every render).
  const loader = useRef(load);
  loader.current = load;
  const asked = `${symbol.trim().toUpperCase()}|${expiry ?? ""}`;
  const shown = useRef(asked);

  useEffect(() => {
    if (shown.current !== asked) { shown.current = asked; setData(null); setError(null); }   // another symbol: never show the old one's numbers
    if (!symbol.trim()) { setLoading(false); return; }
    let live = true;
    setLoading(true);
    loader.current(symbol, expiry)
      .then((d) => { if (live) { setData(d); setError(null); } })
      .catch(() => { if (live) setError("The analyses could not be read. Try again in a moment."); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [asked, refreshKey]);   // `asked` stands for the symbol and the expiry

  return <AnalysisTabsView data={data} error={error} loading={loading} tab={tab} onTab={setTab} />;
}

export default AnalysisTabs;

/** The tabs for a response already in hand (pure: no reads, no timers). */
export function AnalysisTabsView({ data, error, loading, tab, onTab }: {
  data: AnalysesResponse | null; error: string | null; loading: boolean; tab: AnalysisKey; onTab: (t: AnalysisKey) => void;
}) {
  if (!data) {
    return (
      <section className="rounded-panel border border-subtle bg-surface-1 p-space-4" aria-label="Option chain analyses" data-oc-analyses="empty">
        <p className="text-t14 text-fg-muted">{error ?? (loading ? "Reading the option chain…" : "Pick a symbol to see the analyses.")}</p>
      </section>
    );
  }
  const plain = stateMessage(data);
  if (plain) {
    return (
      <section className="rounded-panel border border-subtle bg-surface-1 p-space-4" aria-label="Option chain analyses" data-oc-analyses={data.state}>
        <h2 className="text-t15 font-semibold text-fg">Analyses</h2>
        <p className="mt-space-1 text-t14 text-fg-muted">{plain}</p>
      </section>
    );
  }
  const current = analysisFor(data, tab);
  return (
    <section className="space-y-space-3 rounded-panel border border-subtle bg-surface-1 p-space-3 md:p-space-4" aria-label="Option chain analyses"
      data-oc-analyses="ok">
      <div className="flex flex-wrap items-baseline justify-between gap-space-2">
        <h2 className="text-t15 font-semibold text-fg">Analyses · {data.symbol}{data.expiry ? ` · expiry ${data.expiry}` : ""}</h2>
        <p className="text-t12 text-fg-muted">{provenance(data)}</p>
      </div>
      {data.session?.note && <p className="text-t12 text-fg-muted">{data.session.note}</p>}
      {error && <p className="text-t12 text-fg-muted">{error}</p>}

      {/* UI-H Wave 0 (H2): the shared SubTabs - roving focus (only the selected tab is in the Tab order; the arrow keys,
          Home and End move between tabs), the active tab filled with the accent, every tab visible on a phone. */}
      <SubTabs label="Analyses" idPrefix="oc" value={tab} onChange={onTab}
        items={TABS.map((t) => ({ key: t.key, label: t.label, short: t.short, ariaLabel: t.label }))} />

      <div role="tabpanel" id={`oc-panel-${tab}`} aria-labelledby={`oc-tab-${tab}`} tabIndex={0}
        className="rounded-control focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
        {current ? <AnalysisPanel analysis={current} /> : <p className="text-t14 text-fg-muted">This analysis is not available.</p>}
      </div>
    </section>
  );
}

/** One tab: the sentences first (what the numbers say), then the chart and the tables, then how it is worked out. */
export function AnalysisPanel({ analysis }: { analysis: Analysis }) {
  const term = TABS.find((t) => t.key === analysis.key)?.term;
  // M61 PCR-1: readings outside the session (pre-open, after close) are left off the chart unless the trader asks.
  const [showOutside, setShowOutside] = useState(false);
  // M61 PCR-3: the PCR headline in numbers ("Overall PCR 1.60 · Near-ATM PCR 1.03 (11 strikes 22,800–23,300)").
  const headline = pcrHeadline(analysis);
  const sentences = headline ? [headline, ...analysis.sentences.slice(1)] : analysis.sentences;
  const outside = analysis.chart ? hasOutside(analysis.chart) : false;
  return (
    <div className="space-y-space-3" data-analysis={analysis.key} data-state={analysis.state}>
      <h3 className="flex items-center gap-space-1 text-t14 font-semibold text-fg">{analysis.title}{term && <TermHint term={term} />}</h3>
      <div className="space-y-space-1">
        {sentences.map((s, i) => <p key={i} className={cx("text-t14", headline && i === 0 ? "font-semibold font-tabular text-fg" : "text-fg")}>{s}</p>)}
      </div>
      {outside && (
        <button type="button" aria-pressed={showOutside} onClick={() => setShowOutside((v) => !v)} data-show-outside
          className={cx("min-h-8 rounded-control border px-space-2 text-t12", showOutside ? "border-brand text-fg" : "border-subtle text-fg-muted")}>
          Show pre-open and after-close readings
        </button>
      )}
      {analysis.chart && <MiniChart chart={visibleChart(analysis.chart, showOutside)} title={analysis.title} />}
      {analysis.tables.map((t, i) => <AnalysisTableView key={i} table={t} />)}
      <p className="text-t12 text-fg-muted">How it is worked out: {analysis.method}</p>
    </div>
  );
}

export function AnalysisTableView({ table }: { table: AnalysisTable }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-max border-collapse text-t13">
        <caption className="pb-space-1 text-left text-t12 font-semibold text-fg-muted">
          <span className="inline-flex items-center gap-space-1">{table.title}
            {table.help && <HelpPopover title={`${table.title}: the strikes`}><p>{table.help}</p></HelpPopover>}</span>
        </caption>
        <thead>
          <tr className="border-b border-subtle">
            {table.columns.map((c) => (
              <th key={c.key} scope="col" className={cx("px-space-2 py-space-1 font-normal text-fg-muted", isFigure(c.kind) ? "text-right" : "text-left")}>{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((r, i) => (
            <tr key={i} className={cx("border-b border-subtle last:border-0", r.is_max_pain === true && "bg-surface-2 font-semibold")}>
              {table.columns.map((c) => (
                <td key={c.key} className={cx("px-space-2 py-space-1 text-fg", isFigure(c.kind) ? "text-right font-tabular" : "text-left")}>
                  {formatCell(r[c.key], c.kind)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** A small plain-SVG chart (bars by strike, or lines through the session / across strikes) in v2 tokens. */
export function MiniChart({ chart, title }: { chart: AnalysisChart; title: string }) {
  const scale = yScale(chart);
  const xs = xValues(chart);
  if (!scale || !xs.length) return null;
  const { width, height, padLeft, padRight, padTop, padBottom } = CHART;
  const n = xs.length;
  const slot = (width - padLeft - padRight) / n;
  const zero = yPixel(Math.max(scale.min, Math.min(0, scale.max)), scale);
  const ticks = [scale.max, (scale.max + scale.min) / 2, scale.min];
  const labelEvery = Math.max(1, Math.ceil(n / 6));
  const marker = chart.marker ? markerPixel(chart.marker.x, xs) : null;
  const barWidth = Math.max(1, (slot * 0.8) / Math.max(chart.series.length, 1));
  const index = new Map(xs.map((x, i) => [String(x), i]));
  // M61 PCR-2: a session line is drawn on a time axis (one point per change, so the gaps are real time), its ticks
  // every N minutes so they never overlap.
  const times = chart.type === "line" && chart.x_kind === "time" ? xs.map((x) => Date.parse(String(x))) : [];
  const timeAxis = times.length > 0 && times.every((t) => Number.isFinite(t));
  const t0 = timeAxis ? Math.min(...times) : 0, t1 = timeAxis ? Math.max(...times) : 0;
  const px = (x: number | string) => (timeAxis ? timePixel(Date.parse(String(x)), t0, t1) : xPixel(index.get(String(x)) ?? 0, n));
  const marks = timeAxis ? (chart.x_marks ?? []).filter((m) => { const t = Date.parse(m.x); return t >= t0 && t <= t1; }) : [];
  return (
    <figure className="space-y-space-1" data-chart={chart.type}>
      <svg viewBox={`0 0 ${width} ${height}`} className="h-40 w-full" role="img" aria-label={`${title}: ${chart.y_label} by ${chart.x_label.toLowerCase()}`}>
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={padLeft} x2={width - padRight} y1={yPixel(t, scale)} y2={yPixel(t, scale)} className="stroke-border-subtle" strokeWidth={1} />
            <text x={padLeft - 4} y={yPixel(t, scale) + 3} textAnchor="end" className="fill-fg-muted text-xs">{yTick(t)}</text>
          </g>
        ))}
        {chart.type === "bar" && <line x1={padLeft} x2={width - padRight} y1={zero} y2={zero} className="stroke-fg-muted" strokeWidth={1} />}
        {chart.type === "bar" && chart.series.map((s, si) => s.points.map((p) => {
          if (p.y === null) return null;
          const i = index.get(String(p.x)) ?? 0;
          const x = xPixel(i, n) - (slot * 0.8) / 2 + si * barWidth;
          const y = yPixel(p.y, scale);
          return (
            <rect key={`${s.key}-${p.x}`} x={x} y={Math.min(y, zero)} width={Math.max(1, barWidth - 1)} height={Math.max(0.5, Math.abs(zero - y))}
              className={toneClasses(s.tone).fill}>
              <title>{`${s.label} at ${xLabel(p.x, chart.x_kind)}: ${yTick(p.y)}`}</title>
            </rect>
          );
        }))}
        {marks.map((m) => (
          <g key={m.x} data-x-mark>
            <line x1={px(m.x)} x2={px(m.x)} y1={padTop} y2={height - padBottom} className="stroke-fg-muted" strokeDasharray="1 3" strokeWidth={1} />
            <text x={px(m.x) + 3} y={height - padBottom - 3} className="fill-fg-muted text-xs">{m.label}</text>
          </g>
        ))}
        {chart.type === "line" && chart.series.map((s) => <SeriesLine key={s.key} chart={chart} seriesKey={s.key} px={px} scale={scale} />)}
        {marker !== null && chart.marker && (
          <g>
            <line x1={marker} x2={marker} y1={padTop} y2={height - padBottom} className="stroke-fg" strokeDasharray="3 3" strokeWidth={1} />
            <text x={marker + 3} y={padTop + 8} className="fill-fg text-xs">{chart.marker.label}</text>
          </g>
        )}
        {timeAxis && timeTicks(t0, t1).map((t) => (
          <text key={t} x={timePixel(t, t0, t1)} y={height - 6} textAnchor="middle" className="fill-fg-muted text-xs">{formatTimeIST(t, { suffix: false })}</text>
        ))}
        {!timeAxis && xs.map((x, i) => (i % labelEvery === 0 || i === n - 1) && (
          <text key={String(x)} x={xPixel(i, n)} y={height - 6} textAnchor="middle" className="fill-fg-muted text-xs">{xLabel(x, chart.x_kind)}</text>
        ))}
      </svg>
      <figcaption className="flex flex-wrap gap-x-space-3 gap-y-space-1 text-t12 text-fg-muted">
        {chart.series.map((s) => (
          <span key={s.key} className="inline-flex items-center gap-space-1">
            <span aria-hidden className={cx("inline-block h-2 w-3 rounded-sm", toneClasses(s.tone).swatch)} />{s.label}
          </span>
        ))}
        {chart.marker && marker !== null && <span>Dashed line: {chart.marker.label.toLowerCase()}</span>}
        {marks.length > 0 && <span>Dotted line: the ATM strike changed</span>}
        {chart.series.some((s) => s.points.some((p) => p.hollow)) && <span>Hollow dot: partial reading (some strikes missing)</span>}
      </figcaption>
    </figure>
  );
}

function SeriesLine({ chart, seriesKey, px, scale }: {
  chart: AnalysisChart; seriesKey: string; px: (x: number | string) => number; scale: { min: number; max: number };
}) {
  const s = chart.series.find((x) => x.key === seriesKey);
  if (!s) return null;
  // A missing value breaks the line (never drawn through as 0). M61 PCR-1: a partial reading is a hollow dot and the
  // line does not pass through it (its totals are not comparable with the others).
  const runs: string[][] = [[]];
  const dots: { x: number; y: number; label: string; hollow: boolean }[] = [];
  for (const p of s.points) {
    if (p.y === null) { runs.push([]); continue; }
    const x = px(p.x), y = yPixel(p.y, scale);
    if (!p.hollow) runs[runs.length - 1].push(`${x.toFixed(1)},${y.toFixed(1)}`);
    dots.push({ x, y, label: pointNote(s.label, p, chart.x_kind), hollow: p.hollow === true });
  }
  const tone = toneClasses(s.tone);
  return (
    <g>
      {runs.filter((r) => r.length > 1).map((r, i) => <polyline key={i} points={r.join(" ")} fill="none" className={tone.stroke} strokeWidth={1.5} />)}
      {dots.map((d, i) => (
        <circle key={i} cx={d.x} cy={d.y} r={d.hollow ? 3 : 2} strokeWidth={d.hollow ? 1.5 : 1} data-hollow={d.hollow || undefined}
          className={cx(tone.stroke, d.hollow ? "fill-surface-1" : tone.fill)}><title>{d.label}</title></circle>
      ))}
    </g>
  );
}
