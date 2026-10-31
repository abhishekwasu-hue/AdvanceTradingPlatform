/**
 * P1-c2 / Z5: the payoff canvas - the builder's one memorable element (spec §3.3). The expiry payoff and the T+0
 * curve (and the curve on a chosen later date) on one chart; profit tinted above zero, loss hatched below it with the
 * max-loss line; the probability cone (one and two expected moves) behind them; the underlying's price marker and the
 * breakevens; and the strikes as handles on the x-axis: drag one (or focus it and use the arrow keys, Page Up / Down,
 * Home / End) and the strike moves on its grid and the curves follow. Every number drawn comes from the engine; this
 * file only places it. Colour is never the only cue: buy handles are circles and sell handles diamonds, the loss area
 * is hatched, and each curve has its own dash.
 */
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { cx } from "../components/primitives";
import { formatNumber } from "../ui/format";
import {
  canvasSize, cone, hitRadius, linear, linePath, maxLossY, money, niceTicks, seriesAt, signAreas, strikeAtPointer, strikeBounds, strikeForKey, yDomain,
  type Evaluation, type Leg,
} from "./model";

const M = { top: 16, right: 16, bottom: 44, left: 64 };

export interface PayoffCanvasProps {
  evaluation: Evaluation;
  legs: Leg[];
  spot: number;
  step: number;
  onStrikeChange: (legId: string, strike: number) => void;
  /** a strike drag started (true) or ended (false): the page holds the chart's range still meanwhile */
  onDragging?: (dragging: boolean) => void;
}

function Handle({ buy, active }: { buy: boolean; active: boolean }) {
  const tone = buy ? "fill-up/85 stroke-up" : "fill-down/85 stroke-down";
  return buy
    ? <circle r={7.5} className={cx("stroke-2", tone, active && "stroke-fg")} />
    : <rect x={-6.5} y={-6.5} width={13} height={13} transform="rotate(45)" className={cx("stroke-2", tone, active && "stroke-fg")} />;
}

export function PayoffCanvas({ evaluation: e, legs, spot, step, onStrikeChange, onDragging }: PayoffCanvasProps) {
  const svg = useRef<SVGSVGElement>(null);
  const uid = useId().replace(/:/g, "");
  const [hover, setHover] = useState<number | null>(null);
  const [drag, setDrag] = useState<string | null>(null);
  const [drawnWidth, setDrawnWidth] = useState(800);
  const { W, H, xTickCount } = canvasSize(drawnWidth);
  const xs = e.prices;
  const main = e.at_expiry ?? e.on_date;                       // shading follows the expiry payoff when there is one
  const later = e.days_forward > 0;
  const sx = useMemo(() => linear([xs[0], xs[xs.length - 1]], [M.left, W - M.right]), [xs, W]);
  const sy = useMemo(() => linear(yDomain([e.at_expiry, e.on_date, e.today]), [H - M.bottom, M.top]), [e.at_expiry, e.on_date, e.today, H]);
  const areas = useMemo(() => signAreas(xs, main, sx, sy), [xs, main, sx, sy]);
  const c = cone(spot, e.summary.expected_move);
  const clampX = (v: number) => Math.min(W - M.right, Math.max(M.left, v));
  const yTicks = useMemo(() => niceTicks(sy.domain[0], sy.domain[1], H < 360 ? 5 : 6), [sy]);
  const xTicks = useMemo(() => Array.from({ length: xTickCount }, (_, i) => xs[0] + ((xs[xs.length - 1] - xs[0]) * i) / (xTickCount - 1)), [xs, xTickCount]);
  const bounds = strikeBounds(xs, step);
  const lossY = maxLossY(e.extremes, sy);
  const hit = hitRadius(drawnWidth, W);

  // the chart's drawn width, so a strike handle's hit area stays 44 px across on a phone
  useEffect(() => {
    const el = svg.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(([entry]) => setDrawnWidth(entry.contentRect.width || 800));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const toPrice = (clientX: number): number | null => {
    const r = svg.current?.getBoundingClientRect();
    return r && r.width > 0 ? sx.invert(((clientX - r.left) / r.width) * W) : null;
  };
  const endDrag = () => { if (drag) onDragging?.(false); setDrag(null); };
  const onMove = (ev: PointerEvent<SVGSVGElement>) => {
    setHover(toPrice(ev.clientX));
    if (!drag) return;
    const r = svg.current?.getBoundingClientRect();
    const leg = legs.find((l) => l.id === drag);
    const next = r ? strikeAtPointer(ev.clientX, r.left, r.width, W, sx, step, bounds) : null;
    if (leg && next != null && next !== leg.strike) onStrikeChange(drag, next);
  };
  const keyMove = (leg: Leg) => (ev: KeyboardEvent) => {
    const next = strikeForKey(ev.key, leg.strike, step, bounds);
    if (next == null) return;
    ev.preventDefault();
    if (next !== leg.strike) onStrikeChange(leg.id, next);
  };
  // numbered as in the leg table (futures have no strike handle but keep their number)
  const optionLegs = legs.map((l, i) => ({ l, n: i + 1 })).filter(({ l }) => l.option_type !== "FUT");
  const inChart = hover != null && hover >= xs[0] && hover <= xs[xs.length - 1];
  const dateLabel = later ? `In ${e.days_forward} days` : "Today (T+0)";

  return (
    <figure className="relative">
      <svg ref={svg} viewBox={`0 0 ${W} ${H}`} className="block h-auto w-full select-none [touch-action:pan-y]" role="group"
           aria-label={`Payoff: ${e.single_expiry ? "at expiry, " : ""}today${later ? ` and in ${e.days_forward} days` : ""}, against the price of the underlying`}
           onPointerMove={onMove} onPointerLeave={() => { setHover(null); endDrag(); }} onPointerUp={endDrag} onPointerCancel={endDrag}>
        <defs>
          <pattern id={`${uid}-loss`} width={6} height={6} patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <line x1={0} y1={0} x2={0} y2={6} className="stroke-down/45" strokeWidth={2} />
          </pattern>
        </defs>
        {/* the probability cone: two and one expected moves */}
        <rect x={clampX(sx(c.outer[0]))} y={M.top} width={Math.max(0, clampX(sx(c.outer[1])) - clampX(sx(c.outer[0])))} height={H - M.top - M.bottom}
              className="fill-brand/[0.05]" data-testid="cone-outer" />
        <rect x={clampX(sx(c.inner[0]))} y={M.top} width={Math.max(0, clampX(sx(c.inner[1])) - clampX(sx(c.inner[0])))} height={H - M.top - M.bottom}
              className="fill-brand/[0.08]" data-testid="cone-inner" />
        {yTicks.map((v) => (
          <g key={v}>
            <line x1={M.left} x2={W - M.right} y1={sy(v)} y2={sy(v)} style={{ stroke: "rgb(var(--chart-grid))" }} />
            <text x={M.left - 8} y={sy(v)} dy="0.32em" textAnchor="end" className="font-mono text-xs" style={{ fill: "rgb(var(--chart-text))" }}>{money(v)}</text>
          </g>
        ))}
        {xTicks.map((v, i) => (
          <text key={v} x={sx(v)} y={H - M.bottom + 30} textAnchor={i === 0 ? "start" : i === xTicks.length - 1 ? "end" : "middle"} className="font-mono text-xs" style={{ fill: "rgb(var(--chart-text))" }}>{formatNumber(v, 0)}</text>
        ))}
        {areas.profit.map((d, i) => <path key={`p${i}`} d={d} className="fill-up/15" />)}
        {areas.loss.map((d, i) => (
          <g key={`l${i}`} data-testid="loss-area">
            <path d={d} className="fill-down/10" />
            <path d={d} fill={`url(#${uid}-loss)`} />
          </g>
        ))}
        <line x1={M.left} x2={W - M.right} y1={sy(0)} y2={sy(0)} className="stroke-fg-muted/60" strokeDasharray="2 3" />
        {lossY != null && e.extremes?.max_loss != null && (
          <g data-testid="max-loss-line" pointerEvents="none">
            <line x1={M.left} x2={W - M.right} y1={lossY} y2={lossY} className="stroke-down" strokeWidth={1.25} strokeDasharray="8 4" />
            <text x={M.left + 6} y={lossY - 6} className="fill-down font-mono text-xs font-semibold">Max loss {money(e.extremes.max_loss)}</text>
          </g>
        )}
        {e.at_expiry && <path d={linePath(xs, e.at_expiry, sx, sy)} className="fill-none stroke-fg" strokeWidth={2} data-testid="curve-expiry" />}
        {later && <path d={linePath(xs, e.today, sx, sy)} className="fill-none stroke-signal/70" strokeWidth={1.5} strokeDasharray="1 3" data-testid="curve-today" />}
        <path d={linePath(xs, e.on_date, sx, sy)} className="fill-none stroke-signal" strokeWidth={2.25} strokeDasharray={later ? "6 4" : undefined} data-testid="curve-date" />
        {/* the underlying's price */}
        <line x1={sx(spot)} x2={sx(spot)} y1={M.top} y2={H - M.bottom} className="stroke-info" strokeWidth={1.25} />
        <text x={sx(spot)} y={M.top + 10} dx={4} className="fill-info font-mono text-xs">spot {formatNumber(spot, 0)}</text>
        {(e.breakevens ?? []).filter((b) => b >= xs[0] && b <= xs[xs.length - 1]).map((b, i) => (
          <g key={b} data-testid="breakeven">{/* alternate labels above / below zero so close breakevens stay readable */}
            <circle cx={sx(b)} cy={sy(0)} r={3.5} className="fill-surface stroke-fg" strokeWidth={1.5} />
            <text x={sx(b)} y={i % 2 ? sy(0) + 16 : sy(0) - 8} textAnchor="middle" className="fill-fg font-mono text-xs">{formatNumber(b, 0)}</text>
          </g>
        ))}
        {/* strikes: drag on the axis, or focus and use the keys */}
        {optionLegs.map(({ l, n }) => {
          const x = clampX(sx(l.strike));
          const buy = l.direction === "BUY";
          return (
            <g key={l.id} transform={`translate(${x},${H - M.bottom + 10})`} role="slider" tabIndex={0}
               aria-label={`Strike of leg ${n} (${l.direction} ${l.option_type})`} aria-valuenow={l.strike} aria-valuemin={bounds[0]} aria-valuemax={bounds[1]}
               aria-valuetext={`${l.strike}`} onKeyDown={keyMove(l)}
               onPointerDown={(ev) => { ev.preventDefault(); svg.current?.setPointerCapture?.(ev.pointerId); setDrag(l.id); onDragging?.(true); }}
               className="cursor-ew-resize outline-none [touch-action:none] [&:focus-visible>.ring]:opacity-100" data-testid="strike-handle">
              <line y1={-10 - (H - M.bottom - M.top)} y2={-10} className={buy ? "stroke-up/40" : "stroke-down/40"} strokeDasharray="1 3" pointerEvents="none" />
              <circle r={hit} fill="transparent" pointerEvents="all" data-testid="strike-hit" />
              <circle r={12} className="ring fill-none stroke-brand opacity-0" strokeWidth={2} />
              <Handle buy={buy} active={drag === l.id} />
              <text dy="0.32em" textAnchor="middle" className="pointer-events-none fill-surface font-mono text-xs font-semibold">{n}</text>
            </g>
          );
        })}
        {inChart && hover != null && (
          <g pointerEvents="none">
            <line x1={sx(hover)} x2={sx(hover)} y1={M.top} y2={H - M.bottom} className="stroke-fg-muted/50" />
          </g>
        )}
      </svg>
      {inChart && hover != null && (
        <figcaption className="pointer-events-none absolute right-2 top-2 rounded-md border border-border bg-surface-1/95 px-2 py-1 font-mono text-xs text-fg shadow">
          <div>at {formatNumber(hover, 0)}</div>
          {e.at_expiry && <div>expiry {money(seriesAt(xs, e.at_expiry, hover))}</div>}
          {later && <div className="text-signal">today {money(seriesAt(xs, e.today, hover))}</div>}
          <div className="text-signal">{later ? `+${e.days_forward}d` : "today"} {money(seriesAt(xs, e.on_date, hover))}</div>
        </figcaption>
      )}
      <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-fg-muted">
        {e.at_expiry && <span><span className="mr-1 inline-block h-0.5 w-4 bg-fg align-middle" />At expiry</span>}
        {later && <span><span className="mr-1 inline-block w-4 border-t-2 border-dotted border-signal/70 align-middle" />Today (T+0)</span>}
        <span><span className={cx("mr-1 inline-block w-4 border-t-2 border-signal align-middle", later && "border-dashed")} />{dateLabel}</span>
        <span><span className="mr-1 inline-block h-2.5 w-4 bg-brand/20 align-middle" />1 and 2 expected moves</span>
        {lossY != null && <span><span className="mr-1 inline-block w-4 border-t-2 border-dashed border-down align-middle" />Max loss</span>}
        <span className="inline-flex items-center gap-1">
          <svg width="12" height="12" viewBox="-7 -7 14 14" aria-hidden><circle r={5.5} className="fill-up/85 stroke-up" /></svg>Buy
          <svg width="12" height="12" viewBox="-7 -7 14 14" aria-hidden className="ml-2"><rect x={-4.5} y={-4.5} width={9} height={9} transform="rotate(45)" className="fill-down/85 stroke-down" /></svg>Sell
        </span>
        <span>Drag a strike, or focus it and use ← →</span>
      </div>
    </figure>
  );
}
