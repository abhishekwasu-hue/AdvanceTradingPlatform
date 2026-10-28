import { useEffect, useMemo, useRef, useState } from "react";
import type { OHLCVBar } from "../types";
import { formatPrice } from "../ui/format";
import { snapshotGeometry } from "./snapshot";

/**
 * F&O workspace 8b, slice 4: a small candle chart as an SVG (pure geometry in snapshot.ts) - the journal draft's
 * snapshot (the moments the trade opened and closed marked) and the cockpit's chart panel (his own strikes as lines).
 * Neutral candles (rising hollow, falling filled) so nothing depends on colour; themed through the tokens.
 */
export function CandleSnapshotV2({ bars, marks = [], levels = [], title, summary, width: initial = 640, height = 220, testId }: {
  bars: readonly OHLCVBar[]; marks?: readonly { at: number; label: string }[]; levels?: readonly { price: number; label: string }[];
  title: string; summary: string; width?: number; height?: number; testId: string;
}) {
  // Drawn at the width it is shown at (so the labels keep their size on a phone and on a desktop).
  const box = useRef<HTMLElement | null>(null);
  const [width, setWidth] = useState(initial);
  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const fit = () => { const w = Math.round(el.getBoundingClientRect().width); if (w > 0) setWidth(Math.max(280, Math.min(1200, w))); };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const g = useMemo(() => snapshotGeometry(bars, marks, width, height, levels), [bars, marks, width, height, levels]);
  if (!g) return null;
  const id = `snap-${testId}`;
  return (
    <figure ref={box} className="space-y-space-1" data-snapshot={testId}>
      <svg viewBox={`0 0 ${width} ${height}`} width="100%" role="img" aria-labelledby={`${id}-t ${id}-d`} className="block h-auto"
        preserveAspectRatio="xMidYMid meet">
        <title id={`${id}-t`}>{title}</title>
        <desc id={`${id}-d`}>{summary}</desc>
        {[1, 2, 3].map((k) => {
          const y = g.top + ((g.bottom - g.top) * k) / 4;
          return <line key={k} x1={g.left} x2={g.right} y1={y} y2={y} stroke="rgb(var(--chart-grid))" strokeWidth={1} />;
        })}
        <line x1={g.right} x2={g.right} y1={g.top} y2={g.bottom} stroke="rgb(var(--border))" />
        {g.levels.map((l) => (
          <g key={l.label} data-snapshot-level={l.label}>
            <line x1={g.left} x2={g.right} y1={l.y} y2={l.y} stroke="rgb(var(--brand))" strokeWidth={1} strokeDasharray="4 3" />
            <text x={g.left + 2} y={l.y - 3} fontSize={11} fill="rgb(var(--brand))">{l.label}</text>
          </g>
        ))}
        <g>
          {g.candles.map((c, i) => (
            <g key={i}>
              <line x1={c.x} x2={c.x} y1={c.high} y2={c.low} stroke="rgb(var(--chart-text))" strokeWidth={1} />
              <rect x={c.x - c.w / 2} y={Math.min(c.open, c.close)} width={c.w} height={Math.max(1, Math.abs(c.close - c.open))}
                fill={c.rising ? "rgb(var(--surface-1))" : "rgb(var(--chart-text))"} stroke="rgb(var(--chart-text))" strokeWidth={1} />
            </g>
          ))}
        </g>
        {g.markers.map((m, i) => (
          <g key={m.label} data-snapshot-mark={m.label}>
            <line x1={m.x} x2={m.x} y1={g.top} y2={g.bottom} stroke="rgb(var(--fg))" strokeDasharray="2 3" strokeWidth={1} />
            <text x={Math.min(m.x + 3, g.right - 40)} y={g.top + 10 + i * 12} fontSize={11} fill="rgb(var(--chart-text))">{m.label}</text>
          </g>
        ))}
        <text x={g.right + 4} y={g.top + 8} fontSize={11} fill="rgb(var(--chart-text))">{formatPrice(g.hi)}</text>
        <text x={g.right + 4} y={g.bottom} fontSize={11} fill="rgb(var(--chart-text))">{formatPrice(g.lo)}</text>
      </svg>
      {g.outside.length > 0 && <figcaption className="text-t12 text-fg-muted">Beyond this chart's range: {g.outside.join(", ")}.</figcaption>}
    </figure>
  );
}
