/**
 * CH2b (ADR-0023): a `drawing/1` as a lightweight-charts v5 series primitive. The primitive owns no geometry: at each
 * paint it asks the engine for converters (time -> x, price -> y, pane size), turns the drawing into shapes
 * (`geometry.ts`) and paints them. One primitive per drawing, attached to the candle series.
 */
import type { CanvasRenderingTarget2D } from "fancy-canvas";
import type { IPrimitivePaneRenderer, IPrimitivePaneView, ISeriesPrimitive, SeriesAttachedParameter, Time } from "lightweight-charts";
import type { DrawingV1 } from "./drawings";
import { BAND_LABEL_FONT_PX, LABEL_FONT_PX, shapes, type BandRef, type Converters, type Rect, type Shape } from "./geometry";

/** The slice of CanvasRenderingContext2D the painter uses (structural, so tests can record calls). */
export type PaintContext = Pick<CanvasRenderingContext2D,
  "save" | "restore" | "beginPath" | "moveTo" | "lineTo" | "arcTo" | "closePath" | "stroke" | "fill" | "fillRect" | "strokeRect" | "fillText" | "setLineDash"
> & { strokeStyle: unknown; fillStyle: unknown; lineWidth: number; font: string; textAlign: CanvasTextAlign; textBaseline: CanvasTextBaseline };

const labelFont = (px: number = LABEL_FONT_PX) => `${px}px sans-serif`;
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
      } else if (s.type === "chip") {
        if (!s.fill) continue;
        // M78 (F1): a rounded box under a band label's text
        const r = Math.max(0, Math.min(s.r, s.w / 2, s.h / 2));
        ctx.beginPath();
        ctx.moveTo(s.x + r, s.y);
        ctx.arcTo(s.x + s.w, s.y, s.x + s.w, s.y + s.h, r);
        ctx.arcTo(s.x + s.w, s.y + s.h, s.x, s.y + s.h, r);
        ctx.arcTo(s.x, s.y + s.h, s.x, s.y, r);
        ctx.arcTo(s.x, s.y, s.x + s.w, s.y, r);
        ctx.closePath();
        ctx.fillStyle = s.fill;
        ctx.fill();
      } else if (s.text) {
        ctx.font = labelFont(s.size);
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

/** M78 (F1): a shape painted above the series - a band label's chip and text - so a candle never covers it. */
export const onTop = (s: Shape) => (s.type === "chip" || s.type === "text") && s.top === true;

/** Z7a-3: a layer's shapes (built from the converters at each paint, e.g. `bandShapes`) as one series primitive. The
 * bands, legs and swing texts are drawn under the series; M78 (F1): the band labels (chip + text) in a second view
 * drawn above it. The chips of the last paint are kept for the band tooltip (F5). */
export class LayerPrimitive implements ISeriesPrimitive<Time> {
  private requestUpdate: (() => void) | null = null;
  private readonly views: readonly IPrimitivePaneView[];
  /** M78 (F5): the label chips of the last paint and the pane size it was painted at. */
  private chips: { rect: Rect; band: BandRef }[] = [];
  private size: { width: number; height: number } | null = null;

  constructor(public build: (c: Converters) => Shape[], readonly converters: ConvertersFor) {
    const renderer = (top: boolean): IPrimitivePaneRenderer => ({
      draw: (target: CanvasRenderingTarget2D) => {
        target.useMediaCoordinateSpace(({ context, mediaSize }) => {
          const c = this.converters(mediaSize.width, mediaSize.height);
          if (!c) {
            if (top) { this.chips = []; this.size = null; }
            return;
          }
          // M75 W-1: labels are placed by their real width, in the font they are painted with
          const font = labelFont(c.labelStyle?.fontPx ?? BAND_LABEL_FONT_PX);
          const measure = (text: string) => {
            context.save();
            context.font = font;
            const w = context.measureText(text).width;
            context.restore();
            return w;
          };
          const list = this.build({ ...c, measure });
          if (top) {
            this.chips = list.flatMap((s) => (s.type === "chip" ? [{ rect: { x: s.x, y: s.y, w: s.w, h: s.h }, band: s.band }] : []));
            this.size = { width: mediaSize.width, height: mediaSize.height };
          }
          paint(context, list.filter((s) => onTop(s) === top));
        });
      },
    });
    const under = renderer(false), over = renderer(true);
    this.views = [{ zOrder: () => "bottom", renderer: () => under }, { zOrder: () => "top", renderer: () => over }];
  }

  /** M75 W-1: paint at once - the library does not repaint on attach, so a layer set while the chart is idle (a
   * reading that answers after the bars, the market closed: no ticks) stayed invisible until the next pan or tick. */
  attached(param: SeriesAttachedParameter<Time>) {
    this.requestUpdate = param.requestUpdate;
    param.requestUpdate();
  }
  detached() { this.requestUpdate = null; }
  paneViews() { return this.views; }

  setBuild(build: (c: Converters) => Shape[]) {
    this.build = build;
    this.requestUpdate?.();
  }

  /** M75 W-1: repaint with the same build (its inputs - theme colours, reserved rectangles - changed). */
  refresh() { this.requestUpdate?.(); }

  /** M78 (F5): the band whose label chip holds this pane point (as last painted), or null. */
  labelAt(x: number, y: number): BandRef | null {
    const hit = this.chips.find(({ rect: r }) => x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h);
    return hit ? hit.band : null;
  }

  /** M78 (F5): the pane size of the last paint (null before the first one). */
  paneSize(): { width: number; height: number } | null { return this.size; }
}
