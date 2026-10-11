/**
 * U8 EV1: the evidence chart - the matched symbol's closed candles up to the matched bar with ONLY the screen's own
 * conditions drawn (the pack's drawables), the matched bar marked, and at most one lower pane (volume or an oscillator
 * with the threshold it was tested against). An SVG, so it is crisp, themable (light / dark through the tokens) and
 * readable by screen readers (role img, a title and a one-line description). Rising candles are hollow and falling ones
 * filled, so direction never depends on colour; drawable colours are the Okabe-Ito set.
 */
import { useMemo } from "react";
import { chartSummary, drawableColor, evidenceGeometry, type EvidenceDrawable, type EvidencePack } from "./evidence";

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
                  fill={c.rising ? "rgb(var(--surface-1))" : "rgb(var(--armed))"} stroke={c.rising ? "rgb(var(--info))" : "rgb(var(--armed))"} strokeWidth={1} />
          </g>
        ))}
      </g>
      {g.shapes.filter((s) => s.kind !== "rect" || s.fill === "none").map((s) => {
        if (s.kind === "path") return <path key={s.key} data-drawable={s.drawable} d={s.d} fill="none" stroke={s.color} strokeWidth={2} strokeDasharray={s.dash} opacity={fade(s.drawable)} />;
        if (s.kind === "line") return <line key={s.key} data-drawable={s.drawable} x1={s.x1} x2={s.x2} y1={s.y1} y2={s.y2} stroke={s.color} strokeWidth={1.5} strokeDasharray={s.dash} opacity={fade(s.drawable)} />;
        return <rect key={s.key} data-drawable={s.drawable} x={s.x} y={s.y} width={s.w} height={s.h} fill="none" stroke={s.color} strokeWidth={2} opacity={fade(s.drawable)} />;
      })}
      {g.matchedX != null && (
        <g data-testid="evidence-matched-bar">
          <line x1={g.matchedX} x2={g.matchedX} y1={g.priceTop} y2={g.sub ? g.sub.bottom : g.priceBottom} stroke="rgb(var(--fg))" strokeDasharray="2 3" strokeWidth={1} />
          <path d={`M${g.matchedX - 5} ${g.priceBottom + 1}L${g.matchedX + 5} ${g.priceBottom + 1}L${g.matchedX} ${g.priceBottom - 6}Z`} fill="rgb(var(--fg))" />
        </g>
      )}
      <text x={g.right + 4} y={g.priceTop + 10} fontSize={10} fill="rgb(var(--chart-text))">{g.hi.toFixed(2)}</text>
      <text x={g.right + 4} y={g.priceBottom} fontSize={10} fill="rgb(var(--chart-text))">{g.lo.toFixed(2)}</text>
      {g.sub && (
        <g data-testid="evidence-sub-pane">
          <line x1={g.left} x2={g.right} y1={g.sub.bottom} y2={g.sub.bottom} stroke="rgb(var(--border))" />
          <text x={g.left + 2} y={g.sub.top + 10} fontSize={10} fill={g.sub.color}>{g.sub.label}</text>
          {g.subShapes.map((s) => {
            if (s.kind === "rect") return <rect key={s.key} data-drawable={s.drawable} x={s.x} y={s.y} width={s.w} height={s.h} fill={s.fill} fillOpacity={s.opacity} />;
            if (s.kind === "path") return <path key={s.key} data-drawable={s.drawable} d={s.d} fill="none" stroke={s.color} strokeWidth={2} strokeDasharray={s.dash} />;
            if (s.kind === "line") return <line key={s.key} x1={s.x1} x2={s.x2} y1={s.y1} y2={s.y2} stroke="currentColor" strokeDasharray={s.dash} />;
            return <text key={s.key} x={s.x} y={s.y} fontSize={10} fill="currentColor">{s.text}</text>;
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
      {drawables.map((d, i) => (
        <li key={d.id} className="inline-flex items-center gap-1.5">
          <span aria-hidden className="inline-block h-2.5 w-2.5 rounded-sm border" style={{ backgroundColor: drawableColor(d, i), borderColor: drawableColor(d, i) }} />
          <span>{d.label}</span>
          {d.failed && <span className="text-warn">(did not pass)</span>}
        </li>
      ))}
    </ul>
  );
}

export default EvidenceChart;
