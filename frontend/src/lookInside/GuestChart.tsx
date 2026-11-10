import type { GuestArea, GuestChainRow } from "./lookInside";
import { lakhText, stepLabel } from "./lookInside";

/**
 * M95 PR-5: the guest view's drawings - our own SVG, no chart library. The candles are in % from the day's close and
 * the chart has NO price axis (public surface rule, M95-Q1): the dashed line is the close, the tinted bands are the areas.
 */
const STEP = 6;             // px per candle in the drawing's own units
const BODY = 4;
const HEIGHT = 200;
const PAD = 8;

export function chartRange(bars: [number, number, number, number][], areas: GuestArea[]): [number, number] {
  const lows = bars.map((b) => b[2]);
  const highs = bars.map((b) => b[1]);
  const lo = Math.min(0, ...lows);
  const hi = Math.max(0, ...highs);
  // an area just beyond the candles stays in view; a far one is clipped at the edge
  const span = hi - lo || 1;
  const near = areas.filter((a) => a.high_pct >= lo - span * 0.25 && a.low_pct <= hi + span * 0.25);
  return [Math.min(lo, ...near.map((a) => a.low_pct)), Math.max(hi, ...near.map((a) => a.high_pct))];
}

export function GuestChartSvg({ bars, areas, name }: { bars: [number, number, number, number][]; areas: GuestArea[]; name: string }) {
  const width = Math.max(1, bars.length) * STEP + STEP;
  const [lo, hi] = chartRange(bars, areas);
  const span = hi - lo || 1;
  const y = (v: number) => PAD + (1 - (v - lo) / span) * (HEIGHT - 2 * PAD);
  const x = (i: number) => STEP / 2 + i * STEP + STEP / 2;
  const clip = (v: number) => Math.min(Math.max(v, lo), hi);
  return (
    <figure className="m-0" data-guest-chart>
      <svg role="img" aria-label={`${name} daily chart with its areas; no price axis`} viewBox={`0 0 ${width} ${HEIGHT}`}
        preserveAspectRatio="none" className="block h-48 w-full md:h-64">
        {areas.map((a, i) => {
          const demand = a.role === "demand";
          if (a.kind === "line") {
            const ly = y(clip(a.low_pct));
            return <line key={i} x1={0} x2={width} y1={ly} y2={ly} strokeWidth={1} vectorEffect="non-scaling-stroke"
              className={demand ? "stroke-up" : "stroke-down"} data-area={a.role} />;
          }
          const top = y(clip(a.high_pct));
          const bottom = y(clip(a.low_pct));
          return <rect key={i} x={0} width={width} y={top} height={Math.max(1, bottom - top)}
            className={demand ? "fill-up/15" : "fill-down/15"} data-area={a.role} />;
        })}
        <line x1={0} x2={width} y1={y(0)} y2={y(0)} strokeDasharray="4 3" strokeWidth={1} vectorEffect="non-scaling-stroke"
          className="stroke-fg-muted" data-close-line />
        {bars.map(([o, h, l, c], i) => {
          const up = c >= o;
          const top = y(Math.max(o, c));
          const bottom = y(Math.min(o, c));
          return (
            <g key={i} className={up ? "fill-up stroke-up" : "fill-down stroke-down"} data-candle={i}>
              <line x1={x(i)} x2={x(i)} y1={y(h)} y2={y(l)} strokeWidth={1} vectorEffect="non-scaling-stroke" />
              <rect x={x(i) - BODY / 2} y={top} width={BODY} height={Math.max(0.5, bottom - top)} strokeWidth={0} />
            </g>
          );
        })}
      </svg>
      <figcaption className="mt-space-2 flex flex-wrap gap-x-space-4 gap-y-space-1 text-t12 text-fg-muted">
        <span className="flex items-center gap-space-1"><span aria-hidden className="inline-block h-3 w-4 rounded-sm bg-up/25" />Buyer area</span>
        <span className="flex items-center gap-space-1"><span aria-hidden className="inline-block h-3 w-4 rounded-sm bg-down/25" />Seller area</span>
        <span className="flex items-center gap-space-1"><span aria-hidden className="inline-block h-0 w-4 border-t-2 border-dashed border-fg-muted" />The day&apos;s close</span>
      </figcaption>
    </figure>
  );
}

/** The chain as open interest bars by strike distance: calls and puts per row, in lakhs. */
export function ChainBars({ rows }: { rows: GuestChainRow[] }) {
  const max = Math.max(1, ...rows.flatMap((r) => [r.call_oi_lakh ?? 0, r.put_oi_lakh ?? 0]));
  const width = (v: number | null) => `${Math.max(0, ((v ?? 0) / max) * 100).toFixed(1)}%`;
  return (
    <div data-guest-chain>
      <div className="mb-space-2 flex gap-space-4 text-t12 text-fg-muted">
        <span className="flex items-center gap-space-1"><span aria-hidden className="inline-block h-3 w-4 rounded-sm bg-oi-call" />Call open interest</span>
        <span className="flex items-center gap-space-1"><span aria-hidden className="inline-block h-3 w-4 rounded-sm bg-oi-put" />Put open interest</span>
      </div>
      <ul className="space-y-space-1" aria-label="Open interest by strikes from the money">
        {rows.map((r) => (
          <li key={r.steps} className={`grid grid-cols-[3rem_1fr] items-center gap-space-2 ${r.steps === 0 ? "rounded-control bg-surface-2" : ""}`}
            data-steps={r.steps}>
            <span className={`text-right text-t12 tabular-nums ${r.steps === 0 ? "font-semibold text-fg" : "text-fg-muted"}`}>{stepLabel(r.steps)}</span>
            <span className="flex flex-col gap-px py-px">
              <span className="flex items-center gap-space-1">
                <span className="min-w-0 flex-1"><span className="block h-2 rounded-sm bg-oi-call" style={{ width: width(r.call_oi_lakh) }} /></span>
                <span className="w-16 shrink-0 text-right text-t12 tabular-nums text-fg-muted">{lakhText(r.call_oi_lakh)}</span>
              </span>
              <span className="flex items-center gap-space-1">
                <span className="min-w-0 flex-1"><span className="block h-2 rounded-sm bg-oi-put" style={{ width: width(r.put_oi_lakh) }} /></span>
                <span className="w-16 shrink-0 text-right text-t12 tabular-nums text-fg-muted">{lakhText(r.put_oi_lakh)}</span>
              </span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
