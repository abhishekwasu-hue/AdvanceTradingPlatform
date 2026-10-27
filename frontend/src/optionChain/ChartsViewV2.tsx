import { ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect, useId, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { cx } from "../components/primitives/cx";
import { Combobox } from "../ui/Combobox";
import {
  DEFAULT_RANGE, INTERVALS, MAX_PICKED, RANGES, axisScale, axisTick, chartsProvenance, chartsStateMessage, fetchCharts, initialPicks, labelEvery,
  layout, markerAt, pickedSeries, pointNote, togglePick, toneClasses, xAt, xText, xValuesOf, yAt,
  type ChainChart, type ChartSeriesV2, type ChartsResponse, type Layout, type Scale,
} from "./charts";

/**
 * OC5 (owner's order, handover 2c): the Option chain "Charts" view - shown in place of the chain when the trader picks
 * "Charts" (OptionChainPageV2). Ten charts from `GET /api/option-chain/charts`: OI by strike (calls vs puts, today's
 * change in each bar's note), change in OI by strike, OI per strike through the session, total call vs put OI with spot,
 * PCR, max pain vs spot, the ATM straddle premium, ATM IV, the IV smile now and the future's price vs its OI - each with
 * one plain caption. The strike range around ATM (default ± 10) and the 5 / 10 / 15-minute view are the only choices;
 * the expiry is the page's own picker. The view reads again whenever the chain refreshes (`refreshKey`).
 * A phone shows one chart at a time and swipes sideways between them; from the md breakpoint they sit in a grid.
 * No broker, or no reading yet, shows the server's plain sentence - never made-up numbers. Read-only.
 */
export interface ChainChartsProps {
  symbol: string;
  expiry: string | null;
  refreshKey?: string | number | null;
  load?: (symbol: string, expiry: string | null, strikes: number, interval: number) => Promise<ChartsResponse>;
}

const CONTROL = "min-h-11 rounded-control px-space-2 text-t13 md:px-space-3 transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9";

export default function ChainCharts({ symbol, expiry, refreshKey, load = fetchCharts }: ChainChartsProps) {
  const [range, setRange] = useState<number>(DEFAULT_RANGE);
  const [interval, setInterval] = useState<number>(5);
  const [data, setData] = useState<ChartsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(!!symbol.trim());
  const loader = useRef(load);
  loader.current = load;
  const asked = `${symbol.trim().toUpperCase()}|${expiry ?? ""}`;
  const shown = useRef(asked);

  useEffect(() => {
    if (shown.current !== asked) { shown.current = asked; setData(null); setError(null); }   // another symbol: never the old one's numbers
    if (!symbol.trim()) { setLoading(false); return; }
    let live = true;
    setLoading(true);
    loader.current(symbol, expiry, range, interval)
      .then((d) => { if (live) { setData(d); setError(null); } })
      .catch(() => { if (live) setError("The charts could not be read. We will try again with the next refresh."); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [asked, range, interval, refreshKey]);   // `asked` stands for the symbol and the expiry

  return <ChartsPanel data={data} error={error} loading={loading} range={range} onRange={setRange} interval={interval} onInterval={setInterval} />;
}

/** The view for a response already in hand (pure: no reads, no timers). */
export function ChartsPanel({ data, error, loading, range, onRange, interval, onInterval }: {
  data: ChartsResponse | null; error: string | null; loading: boolean;
  range: number; onRange: (n: number) => void; interval: number; onInterval: (n: number) => void;
}) {
  const rangeId = useId();
  const controls = (
    <div className="flex items-end gap-space-2 md:gap-space-3" data-charts-controls>
      <div>
        <label htmlFor={rangeId} className="mb-space-1 block text-t12 font-medium text-fg-muted">Strikes around ATM</label>
        <Combobox id={rangeId} label="Strikes around ATM" value={String(range)} onChange={(v) => onRange(Number(v))}
          options={RANGES.map((n) => ({ value: String(n), label: `ATM ± ${n}` }))} />
      </div>
      <div role="group" aria-label="Reading every" className="flex items-center gap-space-1 rounded-panel bg-surface-2 p-0 md:p-space-1">
        {INTERVALS.map((n) => (
          <button key={n} type="button" aria-pressed={n === interval} onClick={() => onInterval(n)}
            className={cx(CONTROL, n === interval ? "bg-surface-1 font-semibold text-fg shadow-card" : "text-fg-muted hover:text-fg")}>
            {n} min
          </button>
        ))}
      </div>
    </div>
  );

  if (!data) {
    return (
      <section className="space-y-space-3 rounded-panel border border-subtle bg-surface-1 p-space-4" aria-label="Option chain charts" data-oc-charts="empty">
        {controls}
        {loading && !error ? (
          <div aria-busy="true" className="space-y-space-2">
            <span className="sr-only">Reading the option chain</span>
            {Array.from({ length: 3 }, (_, i) => <div key={i} className="h-10 animate-pulse rounded-control bg-surface-2" />)}
          </div>
        ) : <p className="text-t14 text-fg-muted">{error ?? "Pick a symbol to see the charts."}</p>}
      </section>
    );
  }
  const plain = chartsStateMessage(data);
  if (plain) {
    return (
      <section className="rounded-panel border border-subtle bg-surface-1 p-space-4" aria-label="Option chain charts" data-oc-charts={data.state}>
        <h2 className="text-t15 font-semibold text-fg">Charts</h2>
        <p className="mt-space-1 text-t14 text-fg-muted">{plain}</p>
      </section>
    );
  }
  return (
    <section className="space-y-space-3" aria-label="Option chain charts" data-oc-charts="ok">
      <div className="space-y-space-3 rounded-panel border border-subtle bg-surface-1 p-space-3 md:p-space-4">
        <div className="flex flex-col gap-space-3 md:flex-row md:items-end md:justify-between">
          {controls}
          <p className="text-t12 text-fg-muted">{chartsProvenance(data)}</p>
        </div>
        {data.facts && data.facts.sentences.length > 0 && (
          <ul className="space-y-space-1" data-charts-facts>
            {data.facts.sentences.map((s, i) => <li key={i} className="text-t14 text-fg">{s}</li>)}
          </ul>
        )}
        {data.session?.note && <p className="text-t12 text-fg-muted" data-charts-note>{data.session.note}</p>}
        {error && <p className="text-t12 text-fg-muted">{error}</p>}
      </div>
      <ChartDeck charts={data.charts} />
    </section>
  );
}

/** Phone: one chart at a time, swiped sideways (scroll snap) or moved with the arrows; md and up: a two-column grid. */
export function ChartDeck({ charts }: { charts: ChainChart[] }) {
  const deck = useRef<HTMLDivElement>(null);
  const [at, setAt] = useState(0);
  const go = (i: number) => {
    const el = deck.current;
    const to = Math.max(0, Math.min(charts.length - 1, i));
    setAt(to);
    const card = el?.children[to] as HTMLElement | undefined;
    if (el && card && typeof el.scrollTo === "function") el.scrollTo({ left: card.offsetLeft - el.offsetLeft, behavior: "smooth" });
  };
  return (
    <div className="space-y-space-2">
      <div className="flex items-center justify-between gap-space-2 md:hidden" data-charts-pager>
        <button type="button" aria-label="Previous chart" disabled={at === 0} onClick={() => go(at - 1)}
          className="inline-flex h-11 w-11 items-center justify-center rounded-control border border-subtle bg-surface-1 text-fg disabled:opacity-40">
          <ChevronLeft size={18} aria-hidden />
        </button>
        <p className="min-w-0 truncate text-center text-t13 text-fg" aria-live="polite">
          <span className="font-semibold">{charts[at]?.title}</span> <span className="text-fg-muted">· {at + 1} of {charts.length}</span>
        </p>
        <button type="button" aria-label="Next chart" disabled={at >= charts.length - 1} onClick={() => go(at + 1)}
          className="inline-flex h-11 w-11 items-center justify-center rounded-control border border-subtle bg-surface-1 text-fg disabled:opacity-40">
          <ChevronRight size={18} aria-hidden />
        </button>
      </div>
      <div ref={deck} data-charts-deck
        onScroll={(e) => setAt(nearestCard(e.currentTarget))}
        className="flex snap-x snap-mandatory items-start gap-space-3 overflow-x-auto md:grid md:items-stretch md:grid-cols-2 md:gap-space-4 md:overflow-visible">
        {charts.map((c) => <ChartCard key={c.key} chart={c} />)}
      </div>
    </div>
  );
}

/** The card whose left edge is nearest the deck's scroll position (the one a swipe settled on). */
export function nearestCard(el: HTMLElement): number {
  const cards = [...el.children] as HTMLElement[];
  if (!cards.length) return 0;
  const base = cards[0].offsetLeft;
  let best = 0;
  cards.forEach((c, i) => { if (Math.abs(c.offsetLeft - base - el.scrollLeft) < Math.abs(cards[best].offsetLeft - base - el.scrollLeft)) best = i; });
  return best;
}

export function ChartCard({ chart }: { chart: ChainChart }) {
  const [picked, setPicked] = useState<number[]>(() => initialPicks(chart));
  useEffect(() => {   // a new strike window (range, symbol): keep the picks that are still listed
    setPicked((p) => { const still = p.filter((s) => chart.strikes?.includes(s)); return still.length ? still : initialPicks(chart); });
  }, [chart.strikes?.join(",")]);
  const multi = chart.key === "multi_strike_oi" && chart.state === "ok";
  const shown = multi ? { ...chart, series: pickedSeries(chart, picked) } : chart;
  return (
    <article className="w-full shrink-0 snap-start space-y-space-2 rounded-panel border border-subtle bg-surface-1 p-space-3 md:w-auto md:p-space-4"
      data-chart-card={chart.key} data-state={chart.state} aria-label={chart.title}>
      <h3 className="text-t14 font-semibold text-fg">{chart.title}</h3>
      <p className="text-t12 text-fg-muted">{chart.caption}</p>
      {multi && chart.strikes && (
        <div role="group" aria-label={`Strikes shown (up to ${MAX_PICKED})`} className="flex gap-space-1 overflow-x-auto pb-space-1">
          {chart.strikes.map((s) => {
            const on = picked.includes(s);
            return (
              <button key={s} type="button" aria-pressed={on} onClick={() => setPicked((p) => togglePick(p, s))}
                className={cx("min-h-11 shrink-0 rounded-control border px-space-2 text-t12 font-tabular md:min-h-8",
                  on ? "border-brand bg-surface-2 font-semibold text-fg" : "border-subtle text-fg-muted hover:text-fg")}>
                {xText(s, "strike")}
              </button>
            );
          })}
        </div>
      )}
      {chart.state === "ok" ? <ChartSvg chart={shown} /> : (
        <p className="flex min-h-40 items-center rounded-control bg-surface-2 px-space-3 text-t13 text-fg-muted" data-chart-building>{chart.message}</p>
      )}
    </article>
  );
}

/** The drawn width of the box in px (so a 12 px label is 12 px on screen); 640 until it is measured. */
function useWidth(): [RefObject<HTMLDivElement>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (el.clientWidth) setWidth(el.clientWidth);
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([entry]) => { const w = Math.round(entry.contentRect.width); if (w) setWidth(w); });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width];
}

/** One chart in plain SVG and v2 tokens: bars by strike, or lines by strike / through the session, one or two y axes. */
export function ChartSvg({ chart }: { chart: ChainChart }) {
  const [ref, width] = useWidth();
  const leftSeries = chart.series.filter((s) => s.axis !== "right");
  const rightSeries = chart.series.filter((s) => s.axis === "right");
  const l = layout(width, rightSeries.length > 0);
  const left = axisScale(leftSeries, chart.type);
  const right = rightSeries.length ? axisScale(rightSeries, "line") : null;
  const xs = xValuesOf(chart.series);
  return (
    <div ref={ref} className="w-full">
      {left && xs.length ? <Drawn chart={chart} l={l} left={left} right={right} xs={xs} leftSeries={leftSeries} rightSeries={rightSeries} />
        : <p className="text-t13 text-fg-muted">Nothing to draw for these strikes yet.</p>}
    </div>
  );
}

function Drawn({ chart, l, left, right, xs, leftSeries, rightSeries }: {
  chart: ChainChart; l: Layout; left: Scale; right: Scale | null; xs: (number | string)[]; leftSeries: ChartSeriesV2[]; rightSeries: ChartSeriesV2[];
}) {
  const n = xs.length;
  const slot = (l.width - l.left - l.right) / n;
  const index = new Map(xs.map((x, i) => [String(x), i]));
  const every = labelEvery(n, l);
  const marker = chart.marker ? markerAt(chart.marker.x, xs, l) : null;
  const zero = yAt(Math.max(left.min, Math.min(0, left.max)), left, l);
  const bars = chart.type === "bar" ? leftSeries : [];
  const barWidth = Math.max(1, (slot * 0.8) / Math.max(bars.length, 1));
  const ticks = (s: Scale) => [s.max, (s.max + s.min) / 2, s.min];
  return (
    <figure className="space-y-space-1" data-chart={chart.type}>
      <svg viewBox={`0 0 ${l.width} ${l.height}`} width="100%" height={l.height} role="img" className="block"
          aria-label={`${chart.title}: ${chart.y_label}${chart.y2_label ? ` and ${chart.y2_label}` : ""} by ${chart.x_label.toLowerCase()}`}>
          {ticks(left).map((t, i) => (
            <g key={`l${i}`}>
              <line x1={l.left} x2={l.width - l.right} y1={yAt(t, left, l)} y2={yAt(t, left, l)} className="stroke-border-subtle" strokeWidth={1} />
              <text x={l.left - 4} y={yAt(t, left, l) + 4} textAnchor="end" className="fill-fg-muted text-t12">{axisTick(t, left)}</text>
            </g>
          ))}
          {right && ticks(right).map((t, i) => (
            <text key={`r${i}`} x={l.width - l.right + 4} y={yAt(t, right, l) + 4} textAnchor="start" className="fill-fg-muted text-t12">{axisTick(t, right)}</text>
          ))}
          {chart.type === "bar" && <line x1={l.left} x2={l.width - l.right} y1={zero} y2={zero} className="stroke-fg-muted" strokeWidth={1} />}
          {bars.map((s, si) => s.points.map((p) => {
            if (p.y === null) return null;
            const i = index.get(String(p.x)) ?? 0;
            const x = xAt(i, n, l) - (slot * 0.8) / 2 + si * barWidth;
            const y = yAt(p.y, left, l);
            return (
              <rect key={`${s.key}-${p.x}`} x={x} y={Math.min(y, zero)} width={Math.max(1, barWidth - 1)} height={Math.max(0.5, Math.abs(zero - y))}
                className={toneClasses(s.tone).fill}>
                <title>{pointNote(s, p, chart.x_kind)}</title>
              </rect>
            );
          }))}
          {chart.type === "line" && leftSeries.map((s) => <Line key={s.key} s={s} chart={chart} index={index} n={n} scale={left} l={l} />)}
          {right && rightSeries.map((s) => <Line key={s.key} s={s} chart={chart} index={index} n={n} scale={right} l={l} />)}
          {marker !== null && chart.marker && (
            <g>
              <line x1={marker} x2={marker} y1={l.top} y2={l.height - l.bottom} className="stroke-fg" strokeDasharray="3 3" strokeWidth={1} />
              <text x={marker + 4} y={l.top + 12} className="fill-fg text-t12">{chart.marker.label}</text>
            </g>
          )}
          {xs.map((x, i) => (i % every === 0 || (i === n - 1 && (n - 1) % every > every / 2)) && (
            <text key={String(x)} x={xAt(i, n, l)} y={l.height - 8} textAnchor="middle" className="fill-fg-muted text-t12">{xText(x, chart.x_kind)}</text>
          ))}
      </svg>
      <figcaption className="flex flex-wrap gap-x-space-3 gap-y-space-1 text-t12 text-fg-muted">
        {chart.series.map((s) => (
          <span key={s.key} className="inline-flex items-center gap-space-1">
            <span aria-hidden className={cx("inline-block h-2 w-3 rounded-sm", toneClasses(s.tone).swatch, s.dashed && "opacity-50")} />
            {s.label}{s.axis === "right" ? " (right scale)" : ""}
          </span>
        ))}
        {chart.marker && marker !== null && <span>Dashed line: {chart.marker.label.toLowerCase()}</span>}
      </figcaption>
    </figure>
  );
}

function Line({ s, chart, index, n, scale, l }: {
  s: ChartSeriesV2; chart: ChainChart; index: Map<string, number>; n: number; scale: Scale; l: Layout;
}) {
  // A missing value breaks the line (never drawn through as 0).
  const runs: string[][] = [[]];
  const dots: { x: number; y: number; note: string }[] = [];
  for (const p of s.points) {
    if (p.y === null) { runs.push([]); continue; }
    const x = xAt(index.get(String(p.x)) ?? 0, n, l), y = yAt(p.y, scale, l);
    runs[runs.length - 1].push(`${x.toFixed(1)},${y.toFixed(1)}`);
    dots.push({ x, y, note: pointNote(s, p, chart.x_kind) });
  }
  const tone = toneClasses(s.tone);
  return (
    <g data-series={s.key}>
      {runs.filter((r) => r.length > 1).map((r, i) => (
        <polyline key={i} points={r.join(" ")} fill="none" className={tone.stroke} strokeWidth={1.75} strokeDasharray={s.dashed ? "5 4" : undefined} />
      ))}
      {dots.map((d, i) => (
        <circle key={i} cx={d.x} cy={d.y} r={n > 40 ? 1.5 : 2.5} className={cx(tone.stroke, tone.fill)}><title>{d.note}</title></circle>
      ))}
    </g>
  );
}
