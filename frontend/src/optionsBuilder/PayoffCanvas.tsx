/**
 * P1-c2: the payoff canvas - the builder's one memorable element. The expiry payoff and the curve on the chosen date
 * on one chart, profit shaded above zero and loss below, the probability cone (one and two expected moves) behind
 * them, the spot marker, breakevens, and the strikes as handles on the x-axis: drag one (or focus it and press the
 * arrow keys) and the strike moves on its grid and the curves follow. Hover reads the P&L at a price.
 */
import { useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { cx } from "../components/primitives";
import { cone, linear, linePath, money, niceTicks, signAreas, snapStrike, yDomain, type Evaluation, type Leg } from "./model";

const W = 800;
const H = 360;
const M = { top: 16, right: 16, bottom: 44, left: 64 };

export interface PayoffCanvasProps {
  evaluation: Evaluation;
  legs: Leg[];
  spot: number;
  step: number;
  onStrikeChange: (legId: string, strike: number) => void;
}

export function PayoffCanvas({ evaluation: e, legs, spot, step, onStrikeChange }: PayoffCanvasProps) {
  const svg = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const [drag, setDrag] = useState<string | null>(null);
  const xs = e.prices;
  const main = e.at_expiry ?? e.on_date;                       // shading follows the expiry payoff when there is one
  const sx = useMemo(() => linear([xs[0], xs[xs.length - 1]], [M.left, W - M.right]), [xs]);
  const sy = useMemo(() => linear(yDomain([e.at_expiry, e.on_date, e.today]), [H - M.bottom, M.top]), [e.at_expiry, e.on_date, e.today]);
  const areas = useMemo(() => signAreas(xs, main, sx, sy), [xs, main, sx, sy]);
  const c = cone(spot, e.summary.expected_move);
  const clampX = (v: number) => Math.min(W - M.right, Math.max(M.left, v));
  const yTicks = useMemo(() => niceTicks(sy.domain[0], sy.domain[1], 6), [sy]);
  const xTicks = useMemo(() => Array.from({ length: 5 }, (_, i) => xs[0] + ((xs[xs.length - 1] - xs[0]) * i) / 4), [xs]);

  const toPrice = (clientX: number): number => {
    const r = svg.current?.getBoundingClientRect();
    if (!r || r.width === 0) return spot;
    return sx.invert(((clientX - r.left) / r.width) * W);
  };
  const at = (series: number[] | null, price: number): number | null => {
    if (!series) return null;
    const i = xs.findIndex((x) => x >= price);
    if (i <= 0) return series[0];
    if (xs[i] === xs[i - 1]) return series[i];                 // a low-priced underlying can repeat a rounded price
    const t = (price - xs[i - 1]) / (xs[i] - xs[i - 1]);
    return series[i - 1] + (series[i] - series[i - 1]) * t;
  };
  const onMove = (ev: PointerEvent<SVGSVGElement>) => {
    const p = toPrice(ev.clientX);
    setHover(p);
    if (drag) {
      const leg = legs.find((l) => l.id === drag);
      const next = snapStrike(p, step);
      if (leg && next !== leg.strike) onStrikeChange(drag, next);
    }
  };
  const keyMove = (leg: Leg) => (ev: KeyboardEvent) => {
    if (ev.key === "ArrowLeft" || ev.key === "ArrowRight") {
      ev.preventDefault();
      onStrikeChange(leg.id, snapStrike(leg.strike + (ev.key === "ArrowRight" ? step : -step), step));
    }
  };
  // numbered as in the leg table (futures have no strike handle but keep their number)
  const optionLegs = legs.map((l, i) => ({ l, n: i + 1 })).filter(({ l }) => l.option_type !== "FUT");
  const lo = Math.max(step, snapStrike(xs[0], step));
  const hi = snapStrike(xs[xs.length - 1], step);

  return (
    <figure className="relative">
      <svg ref={svg} viewBox={`0 0 ${W} ${H}`} className="block h-auto w-full touch-none select-none" role="group"
           aria-label={`Payoff: ${e.single_expiry ? "at expiry and " : ""}on the chosen date, against the price of the underlying`}
           onPointerMove={onMove} onPointerLeave={() => { setHover(null); setDrag(null); }} onPointerUp={() => setDrag(null)} onPointerCancel={() => setDrag(null)}>
        {/* the probability cone: two and one expected moves */}
        <rect x={clampX(sx(c.outer[0]))} y={M.top} width={Math.max(0, clampX(sx(c.outer[1])) - clampX(sx(c.outer[0])))} height={H - M.top - M.bottom}
              className="fill-brand/[0.05]" />
        <rect x={clampX(sx(c.inner[0]))} y={M.top} width={Math.max(0, clampX(sx(c.inner[1])) - clampX(sx(c.inner[0])))} height={H - M.top - M.bottom}
              className="fill-brand/[0.08]" />
        {yTicks.map((v) => (
          <g key={v}>
            <line x1={M.left} x2={W - M.right} y1={sy(v)} y2={sy(v)} style={{ stroke: "rgb(var(--chart-grid))" }} />
            <text x={M.left - 8} y={sy(v)} dy="0.32em" textAnchor="end" className="font-mono text-[11px]" style={{ fill: "rgb(var(--chart-text))" }}>{money(v)}</text>
          </g>
        ))}
        {xTicks.map((v, i) => (
          <text key={v} x={sx(v)} y={H - M.bottom + 30} textAnchor={i === 0 ? "start" : i === xTicks.length - 1 ? "end" : "middle"} className="font-mono text-[11px]" style={{ fill: "rgb(var(--chart-text))" }}>{Math.round(v).toLocaleString("en-IN")}</text>
        ))}
        {areas.profit.map((d, i) => <path key={`p${i}`} d={d} className="fill-up/15" />)}
        {areas.loss.map((d, i) => <path key={`l${i}`} d={d} className="fill-down/15" />)}
        <line x1={M.left} x2={W - M.right} y1={sy(0)} y2={sy(0)} className="stroke-fg-muted/60" strokeDasharray="2 3" />
        {e.at_expiry && <path d={linePath(xs, e.at_expiry, sx, sy)} className="fill-none stroke-fg" strokeWidth={2} data-testid="curve-expiry" />}
        <path d={linePath(xs, e.on_date, sx, sy)} className="fill-none stroke-brand" strokeWidth={2} strokeDasharray={e.days_forward > 0 ? "6 4" : undefined} data-testid="curve-date" />
        {/* spot */}
        <line x1={sx(spot)} x2={sx(spot)} y1={M.top} y2={H - M.bottom} className="stroke-info" strokeWidth={1} />
        <text x={sx(spot)} y={M.top + 10} dx={4} className="fill-info font-mono text-[11px]">spot</text>
        {(e.breakevens ?? []).filter((b) => b >= xs[0] && b <= xs[xs.length - 1]).map((b) => (
          <g key={b} data-testid="breakeven">
            <circle cx={sx(b)} cy={sy(0)} r={3.5} className="fill-surface stroke-fg" strokeWidth={1.5} />
            <text x={sx(b)} y={sy(0) - 8} textAnchor="middle" className="fill-fg font-mono text-[11px]">{Math.round(b).toLocaleString("en-IN")}</text>
          </g>
        ))}
        {/* strikes: drag on the axis, or focus and use the arrow keys */}
        {optionLegs.map(({ l, n }) => {
          const x = clampX(sx(l.strike));
          return (
            <g key={l.id} transform={`translate(${x},${H - M.bottom + 10})`} role="slider" tabIndex={0}
               aria-label={`Strike of leg ${n} (${l.direction} ${l.option_type})`} aria-valuenow={l.strike} aria-valuemin={lo} aria-valuemax={hi}
               aria-valuetext={`${l.strike}`} onKeyDown={keyMove(l)}
               onPointerDown={(ev) => { ev.preventDefault(); (ev.currentTarget.ownerSVGElement as SVGSVGElement | null)?.setPointerCapture?.(ev.pointerId); setDrag(l.id); }}
               className="cursor-ew-resize outline-none [&:focus-visible>circle]:stroke-brand" data-testid="strike-handle">
              <line y1={-10 - (H - M.bottom - M.top)} y2={-10} className={cx(l.direction === "BUY" ? "stroke-up/40" : "stroke-down/40")} strokeDasharray="1 3" />
              <circle r={7} className={cx("stroke-2", l.direction === "BUY" ? "fill-up/80 stroke-up" : "fill-down/80 stroke-down", drag === l.id && "stroke-fg")} />
              <text dy="0.32em" textAnchor="middle" className="pointer-events-none fill-surface font-mono text-[9px] font-semibold">{n}</text>
            </g>
          );
        })}
        {hover != null && hover >= xs[0] && hover <= xs[xs.length - 1] && (
          <g pointerEvents="none">
            <line x1={sx(hover)} x2={sx(hover)} y1={M.top} y2={H - M.bottom} className="stroke-fg-muted/50" />
          </g>
        )}
      </svg>
      {hover != null && hover >= xs[0] && hover <= xs[xs.length - 1] && (
        <figcaption className="pointer-events-none absolute right-2 top-2 rounded-md border border-border bg-surface-1/95 px-2 py-1 font-mono text-xs text-fg shadow">
          <div>at {Math.round(hover).toLocaleString("en-IN")}</div>
          {e.at_expiry && <div>expiry {money(at(e.at_expiry, hover))}</div>}
          <div className="text-brand">{e.days_forward > 0 ? `+${e.days_forward}d` : "today"} {money(at(e.on_date, hover))}</div>
        </figcaption>
      )}
      <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-fg-muted">
        {e.at_expiry && <span><span className="mr-1 inline-block h-0.5 w-4 bg-fg align-middle" />At expiry</span>}
        <span><span className="mr-1 inline-block h-0.5 w-4 bg-brand align-middle" />{e.days_forward > 0 ? `In ${e.days_forward} days` : "Today"}</span>
        <span><span className="mr-1 inline-block h-2.5 w-4 bg-brand/20 align-middle" />1 and 2 expected moves</span>
        <span>Drag a strike, or focus it and use ← →</span>
      </div>
    </figure>
  );
}
