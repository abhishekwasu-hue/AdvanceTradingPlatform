/**
 * CH5-b: session breaks on the chart - a thin dotted vertical line at the first bar of each session (the times come
 * from `chartTypes.sessionBreaks`, in the venue's own zone: IST for Indian venues, UTC for 24x7 crypto). One series
 * primitive under everything else; it places each line by the bar's time on the chart's own time scale.
 */
import type { CanvasRenderingTarget2D } from "fancy-canvas";
import type { IPrimitivePaneRenderer, IPrimitivePaneView, ISeriesPrimitive, SeriesAttachedParameter, Time } from "lightweight-charts";
import type { Shape } from "./geometry";
import { paint } from "./primitives";

/** The lines for one paint: x for each break time the scale can place (null = off screen or unknown). */
export function breakShapes(times: readonly number[], xOf: (t: number) => number | null, height: number, color: string): Shape[] {
  const out: Shape[] = [];
  for (const t of times) {
    const x = xOf(t);
    if (x == null || !Number.isFinite(x)) continue;
    out.push({ type: "segment", x1: x, y1: 0, x2: x, y2: height, color, width: 1, dash: "dotted" });
  }
  return out;
}

export class SessionBreaksPrimitive implements ISeriesPrimitive<Time> {
  private requestUpdate: (() => void) | null = null;
  private xOf: ((t: number) => number | null) | null = null;
  private readonly views: readonly IPrimitivePaneView[];

  constructor(private times: readonly number[], private color = "rgba(148,163,184,0.35)") {
    const renderer: IPrimitivePaneRenderer = {
      draw: (target: CanvasRenderingTarget2D) => {
        const xOf = this.xOf;
        if (!xOf || this.times.length === 0) return;
        target.useMediaCoordinateSpace(({ context, mediaSize }) => paint(context, breakShapes(this.times, xOf, mediaSize.height, this.color)));
      },
    };
    this.views = [{ zOrder: () => "bottom", renderer: () => renderer }];
  }

  attached(param: SeriesAttachedParameter<Time>) {
    this.requestUpdate = param.requestUpdate;
    const scale = param.chart.timeScale();
    this.xOf = (t) => scale.timeToCoordinate(t as Time);
  }

  detached() { this.requestUpdate = null; this.xOf = null; }
  paneViews() { return this.views; }

  setTimes(times: readonly number[]) {
    this.times = times;
    this.requestUpdate?.();
  }
}
