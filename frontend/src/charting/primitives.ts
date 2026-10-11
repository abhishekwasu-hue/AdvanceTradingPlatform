/**
 * CH2b (ADR-0023): a `drawing/1` as a lightweight-charts v5 series primitive. The primitive owns no geometry: at each
 * paint it asks the engine for converters (time -> x, price -> y, pane size), turns the drawing into shapes
 * (`geometry.ts`) and paints them. One primitive per drawing, attached to the candle series.
 */
import type { CanvasRenderingTarget2D } from "fancy-canvas";
import type { IPrimitivePaneRenderer, IPrimitivePaneView, ISeriesPrimitive, SeriesAttachedParameter, Time } from "lightweight-charts";
import type { DrawingV1 } from "./drawings";
import { shapes, type Converters, type Shape } from "./geometry";

/** The slice of CanvasRenderingContext2D the painter uses (structural, so tests can record calls). */
export type PaintContext = Pick<CanvasRenderingContext2D,
  "save" | "restore" | "beginPath" | "moveTo" | "lineTo" | "closePath" | "stroke" | "fill" | "fillRect" | "strokeRect" | "fillText" | "setLineDash"
> & { strokeStyle: unknown; fillStyle: unknown; lineWidth: number; font: string; textAlign: CanvasTextAlign; textBaseline: CanvasTextBaseline };

const DASH: Record<string, number[]> = { solid: [], dashed: [6, 4], dotted: [2, 3] };

export function paint(ctx: PaintContext, list: readonly Shape[]): void {
  ctx.save();
  try {
    for (const s of list) {
      if (s.type === "segment") {
        ctx.beginPath();
        ctx.strokeStyle = s.color;
        ctx.lineWidth = s.width;
        ctx.setLineDash(DASH[s.dash] ?? []);
        ctx.moveTo(s.x1, s.y1);
        ctx.lineTo(s.x2, s.y2);
        ctx.stroke();
      } else if (s.type === "rect") {
        ctx.fillStyle = s.fill;
        ctx.fillRect(s.x, s.y, s.w, s.h);
        if (s.stroke) {
          ctx.setLineDash([]);
          ctx.lineWidth = 1;
          ctx.strokeStyle = s.stroke;
          ctx.strokeRect(s.x, s.y, s.w, s.h);
        }
      } else if (s.type === "polygon") {
        if (s.points.length < 3) continue;
        ctx.beginPath();
        ctx.moveTo(s.points[0][0], s.points[0][1]);
        for (const [x, y] of s.points.slice(1)) ctx.lineTo(x, y);
        ctx.closePath();
        ctx.fillStyle = s.fill;
        ctx.fill();
      } else if (s.text) {
        ctx.font = "11px sans-serif";
        ctx.fillStyle = s.color;
        ctx.textAlign = s.align;
        ctx.textBaseline = s.baseline;
        ctx.fillText(s.text, s.x, s.y);
      }
    }
  } finally {
    ctx.restore();
  }
}

/** Builds the converters for one paint of a pane `width` x `height` (media pixels); null when nothing can be placed. */
export type ConvertersFor = (width: number, height: number) => Converters | null;

class Renderer implements IPrimitivePaneRenderer {
  constructor(private readonly owner: DrawingPrimitive) {}

  draw(target: CanvasRenderingTarget2D) {
    target.useMediaCoordinateSpace(({ context, mediaSize }) => {
      const c = this.owner.converters(mediaSize.width, mediaSize.height);
      if (c) paint(context, shapes(this.owner.drawing, c));
    });
  }
}

export class DrawingPrimitive implements ISeriesPrimitive<Time> {
  private requestUpdate: (() => void) | null = null;
  private readonly views: readonly IPrimitivePaneView[];

  constructor(public drawing: DrawingV1, readonly converters: ConvertersFor) {
    const renderer = new Renderer(this);
    this.views = [{ zOrder: () => "top", renderer: () => renderer }];
  }

  attached(param: SeriesAttachedParameter<Time>) { this.requestUpdate = param.requestUpdate; }
  detached() { this.requestUpdate = null; }
  paneViews() { return this.views; }

  setDrawing(drawing: DrawingV1) {
    this.drawing = drawing;
    this.requestUpdate?.();
  }
}
