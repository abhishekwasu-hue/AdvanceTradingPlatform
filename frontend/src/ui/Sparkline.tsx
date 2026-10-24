import { cx } from "../components/primitives/cx";

/**
 * Colour v2 (owner, 23 Oct): a small line of real prices (the index's 5-minute closes of its last session) in the
 * up / down colour of the day's change. It is drawn only from data the page already has a source for - never sample
 * data; with fewer than two prices nothing is drawn. Decoration beside the figures (aria-hidden): the price and the
 * change stay the text.
 */
export type SparkTone = "up" | "down" | "neutral";

/** The SVG path through `values`, scaled to `width` x `height` with `pad` px kept free top and bottom. */
export function sparkPath(values: readonly number[], width: number, height: number, pad = 2): string | null {
  const finite = values.filter((v) => Number.isFinite(v));
  if (finite.length < 2) return null;
  const lo = Math.min(...finite);
  const hi = Math.max(...finite);
  const span = hi - lo || 1;
  const step = width / (finite.length - 1);
  return finite.map((v, i) => {
    const x = i * step;
    const y = hi === lo ? height / 2 : pad + (height - 2 * pad) * (1 - (v - lo) / span);
    return `${i === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`;
  }).join(" ");
}

const STROKE: Record<SparkTone, string> = { up: "stroke-up", down: "stroke-down", neutral: "stroke-fg-muted" };

export function Sparkline({ values, tone, width = 96, height = 28, className }: {
  values: readonly number[]; tone: SparkTone; width?: number; height?: number; className?: string;
}) {
  const d = sparkPath(values, width, height);
  if (!d) return null;
  return (
    <svg aria-hidden data-sparkline={tone} viewBox={`0 0 ${width} ${height}`} width={width} height={height}
      preserveAspectRatio="none" className={cx("block overflow-visible", className)}>
      <path d={d} fill="none" strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" className={STROKE[tone]} />
    </svg>
  );
}
