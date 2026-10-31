/**
 * U8 EV1: the evidence chart - the matched symbol's closed candles up to the matched bar with ONLY the screen's own
 * conditions drawn (the pack's drawables), the matched bar marked, and at most one lower pane (volume or an oscillator
 * with the threshold it was tested against). An SVG, so it is crisp, themable (light / dark through the tokens) and
 * readable by screen readers (role img, a title and a one-line description). Candles are neutral - rising hollow,
 * falling filled - so direction never depends on colour and only the screen's drawables carry colour (Okabe-Ito).
 */
import { ExternalLink } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useInRouterContext } from "react-router";
import { useOptionalAuth } from "../auth/AuthContext";
import ChartV2 from "../chart/ChartV2";
import { timeframeLabel, type TimeframeId } from "../chart/timeframes";
import { useChartPrefs } from "../chart/useChartPrefs";
import type { LightweightEngine } from "../charting/lightweight";
import { buttonClass, cx } from "../components/primitives";
import { chartTabPath } from "../shell/shellRoutes";
import { formatCount, formatNumber } from "../ui/format";
import {
  barTimeText, chartSummary, chartTimeframe, drawableColor, evidenceChartLayers, evidenceGeometry, matchedBar, packCandles, priceText,
  type EvidenceDrawable, type EvidencePack,
} from "./evidence";

export interface EvidenceChartProps {
  pack: EvidencePack;
  drawables: readonly EvidenceDrawable[];
  width?: number;
  height?: number;
  highlight?: string | null;          // a drawable id: the others fade (hovering a block in "Why it matched")
}

export function EvidenceChart({ pack, drawables, width = 720, height = 360, highlight = null }: EvidenceChartProps) {
  const g = useMemo(() => evidenceGeometry(pack, drawables, width, height), [pack, drawables, width, height]);
  const titleId = `ev-title-${pack.evidence_id ?? pack.symbol}`;
  const descId = `ev-desc-${pack.evidence_id ?? pack.symbol}`;
  const fade = (id?: string) => (highlight && id && id !== highlight ? 0.25 : 1);
  return (
    <svg viewBox={`0 0 ${width} ${height}`} width="100%" role="img" aria-labelledby={`${titleId} ${descId}`}
         className="block h-auto text-fg-muted" data-testid="evidence-chart" preserveAspectRatio="xMidYMid meet">
      <title id={titleId}>{`Evidence chart: ${pack.symbol}`}</title>
      <desc id={descId}>{chartSummary(pack, drawables)}</desc>
      {[1, 2, 3, 4].map((k) => {
        const y = g.priceTop + ((g.priceBottom - g.priceTop) * k) / 5;
        return <line key={`grid-${k}`} x1={g.left} x2={g.right} y1={y} y2={y} stroke="rgb(var(--chart-grid))" strokeWidth={1} />;
      })}
      <line x1={g.right} x2={g.right} y1={g.priceTop} y2={g.priceBottom} stroke="rgb(var(--border))" />
      {g.shapes.filter((s) => s.kind === "rect" && s.fill !== "none").map((s) => (
        <rect key={s.key} data-drawable={s.drawable} x={s.x} y={s.y} width={s.w} height={s.h} fill={s.fill} fillOpacity={(s.opacity ?? 1) * fade(s.drawable)}
              stroke={s.color} strokeOpacity={0.8 * fade(s.drawable)} strokeWidth={1} />
      ))}
      <g data-testid="evidence-candles">
        {g.candles.map((c) => (
          <g key={c.index}>
            <line x1={c.x} x2={c.x} y1={c.high} y2={c.low} stroke="rgb(var(--chart-text))" strokeWidth={1} />
            <rect x={c.x - c.w / 2} y={Math.min(c.open, c.close)} width={c.w} height={Math.max(1, Math.abs(c.close - c.open))}
                  fill={c.rising ? "rgb(var(--surface-1))" : "rgb(var(--chart-text))"} stroke="rgb(var(--chart-text))" strokeWidth={1} />
          </g>
        ))}
      </g>
      {g.shapes.filter((s) => s.kind !== "rect" || s.fill === "none").map((s) => {
        if (s.kind === "path") return <path key={s.key} data-drawable={s.drawable} d={s.d} fill="none" stroke={s.color} strokeWidth={2} strokeDasharray={s.dash} opacity={fade(s.drawable)} />;
        if (s.kind === "line") return <line key={s.key} data-drawable={s.drawable} x1={s.x1} x2={s.x2} y1={s.y1} y2={s.y2} stroke={s.color} strokeWidth={1.5} strokeDasharray={s.dash} opacity={fade(s.drawable)} />;
        if (s.kind === "text") return <text key={s.key} data-drawable={s.drawable} x={s.x} y={s.y} fontSize={11} fill="rgb(var(--chart-text))" opacity={fade(s.drawable)}>{s.text}</text>;
        return <rect key={s.key} data-drawable={s.drawable} x={s.x} y={s.y} width={s.w} height={s.h} fill="none" stroke={s.color} strokeWidth={2} opacity={fade(s.drawable)} />;
      })}
      {g.matchedX != null && (
        <g data-testid="evidence-matched-bar">
          <line x1={g.matchedX} x2={g.matchedX} y1={g.priceTop} y2={g.sub ? g.sub.bottom : g.priceBottom} stroke="rgb(var(--fg))" strokeDasharray="2 3" strokeWidth={1} />
          <path d={`M${g.matchedX - 5} ${g.priceBottom + 1}L${g.matchedX + 5} ${g.priceBottom + 1}L${g.matchedX} ${g.priceBottom - 6}Z`} fill="rgb(var(--fg))" />
        </g>
      )}
      <text x={g.right + 4} y={g.priceTop + 10} fontSize={11} fill="rgb(var(--chart-text))">{formatNumber(g.hi, 2)}</text>
      <text x={g.right + 4} y={g.priceBottom} fontSize={11} fill="rgb(var(--chart-text))">{formatNumber(g.lo, 2)}</text>
      {g.sub && (
        <g data-testid="evidence-sub-pane">
          <line x1={g.left} x2={g.right} y1={g.sub.bottom} y2={g.sub.bottom} stroke="rgb(var(--border))" />
          <text x={g.left + 2} y={g.sub.top + 10} fontSize={11} fill={g.sub.color}>{g.sub.label}</text>
          {g.subShapes.map((s) => {
            if (s.kind === "rect") return <rect key={s.key} data-drawable={s.drawable} x={s.x} y={s.y} width={s.w} height={s.h} fill={s.fill} fillOpacity={s.opacity} />;
            if (s.kind === "path") return <path key={s.key} data-drawable={s.drawable} d={s.d} fill="none" stroke={s.color} strokeWidth={2} strokeDasharray={s.dash} />;
            if (s.kind === "line") return <line key={s.key} x1={s.x1} x2={s.x2} y1={s.y1} y2={s.y2} stroke="currentColor" strokeDasharray={s.dash} />;
            return <text key={s.key} x={s.x} y={s.y} fontSize={11} fill="currentColor">{s.text}</text>;
          })}
        </g>
      )}
    </svg>
  );
}

/** The legend under the chart: a swatch and the condition's words per drawable (a pattern too, so colour is not alone). */
export function EvidenceLegend({ drawables }: { drawables: readonly EvidenceDrawable[] }) {
  if (!drawables.length) return null;
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-t12 text-fg-muted" aria-label="What is drawn" data-testid="evidence-legend">
      {drawables.map((d) => (
        <li key={d.id} className="inline-flex items-center gap-1.5">
          <span aria-hidden className="inline-block h-2.5 w-2.5 rounded-sm border" style={{ backgroundColor: drawableColor(d, drawables), borderColor: drawableColor(d, drawables) }} />
          <span>{d.label}</span>
          {d.failed && <span className="text-warn">(did not pass)</span>}
        </li>
      ))}
    </ul>
  );
}

/**
 * SCR-FIX C1: the evidence on the Charts page's own chart (ChartV2) - candles in the trader's up / down colours, the
 * price and time axes, the volume pane, the crosshair with its readout, zoom and pan - with the pack's bars (the run's
 * bars, never a second read), the screen's own conditions drawn (`evidenceChartLayers`), the matched bar marked and
 * the crosshair put on it (the matched bar is the window's newest, so the chart opens scrolled to it). "Open in Charts"
 * opens the Charts page on the same symbol and timeframe.
 */
export interface EvidenceChartLiveProps {
  pack: EvidencePack;
  drawables: readonly EvidenceDrawable[];
  highlight?: string | null;
  /** The chart's height (a Tailwind class). */
  className?: string;
  /** A small chart (the result grid): no Auto / Reset / Latest controls. */
  compact?: boolean;
}

const NO_RSI = { volume: true, rsi: false };   // owner rule 0: the volume and the screen's own panes, no other indicator

export function EvidenceChartLive({ pack, drawables, highlight = null, className = "h-[420px] max-h-[60vh]", compact = false }: EvidenceChartLiveProps) {
  const auth = useOptionalAuth();
  const { prefs } = useChartPrefs(auth?.user?.id ?? null);
  const shown = useMemo(() => ({ ...prefs, indicators: NO_RSI }), [prefs]);
  const [engine, setEngine] = useState<LightweightEngine | null>(null);
  const candles = useMemo(() => packCandles(pack), [pack]);
  const layers = useMemo(() => evidenceChartLayers(pack, drawables, highlight), [pack, drawables, highlight]);
  const tf = chartTimeframe(pack.base_tf);
  const timeframe = tf ?? (INTRADAY_SCREEN.has(pack.base_tf) ? "60min" : "day");
  const matched = matchedBar(pack);

  // The screen's series and shapes on the chart's engine; replaced when the pack or the shown blocks change.
  useEffect(() => {
    if (!engine) return undefined;
    for (const s of layers.studies) engine.setStudy(s.id, s.line);
    engine.setLayer(EVIDENCE_LAYER, layers.layer);
    return () => {
      for (const s of layers.studies) engine.setStudy(s.id, null);
      engine.setLayer(EVIDENCE_LAYER, null);
    };
  }, [engine, layers]);
  // The crosshair on the matched bar, so its readout shows at once (the trader's own pointer takes over).
  useEffect(() => {
    if (!engine || !matched) return;
    const t = Math.floor(Date.parse(matched.time) / 1000);
    if (Number.isFinite(t)) engine.setCrosshair(t);
  }, [engine, matched?.time]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <figure className="m-0 flex min-w-0 flex-col gap-1" data-testid="evidence-chart-live" data-timeframe={timeframe}>
      <figcaption className="flex flex-wrap items-center justify-between gap-2 text-t12 text-fg-muted">
        <span>
          <span className="font-mono font-semibold text-fg">{pack.symbol}</span> · <span data-testid="evidence-timeframe">{timeframeLabel(timeframe)}</span>
          {!compact && <> · {barTimeText(pack)}</>}
        </span>
        <OpenInCharts symbol={pack.symbol} exchange={pack.exchange} timeframe={tf} compact={compact} />
      </figcaption>
      <span className="sr-only">{chartSummary(pack, drawables)}</span>
      <ChartV2 candles={candles} timeframe={timeframe} symbol={pack.symbol} exchange={pack.exchange || "NSE"} prefs={shown}
               levels={layers.levels} markers={layers.markers} drawingTools={false} viewControls={!compact} onEngine={setEngine}
               className={cx("rounded-control", className)} />
    </figure>
  );
}

const EVIDENCE_LAYER = "screener-evidence";
const INTRADAY_SCREEN = new Set(["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h"]);

/** "Open in Charts": the Charts page on this symbol and timeframe (a link inside the app; a plain one outside a router). */
export function OpenInCharts({ symbol, exchange, timeframe, compact = false }: { symbol: string; exchange?: string; timeframe: TimeframeId | null; compact?: boolean }) {
  const to = chartTabPath(symbol, exchange, timeframe);
  const inRouter = useInRouterContext();
  const cls = buttonClass({ variant: "secondary", size: "sm", className: "min-h-11 md:min-h-9" });
  const body = <><ExternalLink size={14} aria-hidden /> {compact ? "Charts" : "Open in Charts"}</>;
  return inRouter
    ? <Link to={to} className={cls} data-testid="open-in-charts" aria-label={`Open ${symbol} in Charts`}>{body}</Link>
    : <a href={to} className={cls} data-testid="open-in-charts" aria-label={`Open ${symbol} in Charts`}>{body}</a>;
}

/** SCR-FIX C2: the matched bar's own time and O / H / L / C (and volume), beside the chart - the pack's numbers. */
export function MatchedBarCard({ pack }: { pack: EvidencePack }) {
  const bar = matchedBar(pack);
  if (!bar) return null;
  const rows: [string, string][] = [["Open", priceText(bar.open)], ["High", priceText(bar.high)], ["Low", priceText(bar.low)], ["Close", priceText(bar.close)]];
  if (bar.volume != null) rows.push(["Volume", formatCount(bar.volume)]);
  return (
    <section aria-label="The matched bar" className="rounded-control border border-border bg-surface-2 px-3 py-2 text-t13" data-testid="matched-bar">
      <h3 className="text-t13 font-semibold text-fg">The matched bar</h3>
      <p className="text-t12 text-fg-muted">{barTimeText(pack)}</p>
      <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 font-tabular">
        {rows.map(([k, v]) => (
          <div key={k} className="contents"><dt className="text-fg-muted">{k}</dt><dd className="text-right text-fg" data-field={k.toLowerCase()}>{v}</dd></div>
        ))}
      </dl>
    </section>
  );
}

export default EvidenceChart;
