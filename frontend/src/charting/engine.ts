/**
 * CH1 (ADR-0023): the one chart-engine interface. Pages and the platform layers (drawings, price-action, options,
 * trades, screener hits) talk to `ChartEngine`, never to a chart library, so the engine can change - ProChart on
 * lightweight-charts today (B-lite), lightweight-charts v5 primitives (CH2), TradingView Advanced Charts (CH7) -
 * without losing user data or rewriting pages.
 *
 * Rules every implementation keeps:
 * - every number shown comes from data (bars, layers from the backend with an `as_of`), never from pixels;
 * - drawings are `drawing/1` (time/price anchors); an engine that cannot show a kind says so via `supports`;
 * - no method places, changes or proposes an order.
 */
import type { DrawingKind, DrawingV1 } from "./drawings";

export interface EngineBar { time: number; open: number; high: number; low: number; close: number; volume?: number }

export interface StudySpec { id: string; name: string; params?: Record<string, number | string | boolean> }

export interface LayerLine { price: number; color?: string; title?: string; style?: "solid" | "dashed" | "dotted" }
export interface LayerMarker { time: number; position: "aboveBar" | "belowBar" | "inBar"; shape: "arrowUp" | "arrowDown" | "circle" | "square"; color?: string; text?: string }
/** Z7a-3: a horizontal price band across the pane (low == high: a line), with its label (M75 W-1: placed inside or
 * beside the band, clear of the controls; geometry.ts `placeBandLabels`). `role`: a buyer (demand) / seller (supply)
 * area - an engine that knows the chart theme paints it in the theme's up / down colours (`color` is the fallback). */
export type BandRole = "demand" | "supply" | "neutral";
export interface LayerBand {
  low: number; high: number; color?: string; opacity?: number; style?: "solid" | "dashed" | "dotted"; label?: string; role?: BandRole;
  /** M97 PR-4: a time-anchored band - it starts at this time (epoch seconds) and runs to the right edge; left out, the
   * band spans the whole pane as before. */
  start?: number;
  /** M97 PR-4: the band's own id (a tap names it to the page) and the rows its tooltip shows instead of Top / Bottom. */
  id?: string;
  tip?: readonly (readonly [string, string])[];
}
/** CH3: a straight line between two data points (a price-action leg, a broken level's run), placed by time and price.
 * M97 PR-4: `extend: "right"` makes it a horizontal line at `p1` from `t1` on to the pane's right edge (`t2` / `p2`
 * unused); `role` paints it in the theme's buyer / seller colour (as a band's). */
export interface LayerSegment {
  t1: number; p1: number; t2: number; p2: number; color?: string; width?: number; style?: "solid" | "dashed" | "dotted"; label?: string;
  extend?: "right"; role?: BandRole;
}
/** CH3: a text at a data point (a swing's "HH 512.30", a break), above or below it. M97 PR-4: `inside` - drawn only
 * when its point is on the pane (a tapped zone's "Leg-out" of a candle left of the bars shown is not cut in half). */
export interface LayerLabel { time: number; price: number; text: string; color?: string; position?: "above" | "below"; inside?: boolean }
/** M98 F1: a candle mark (a tapped zone's leg-in / base / leg-out): an outlined box around exactly the chart bars of the
 * candle [time, end) - `time` its start as the chart stamps bars, `end` the start of the next one of its timeframe -
 * from its low to its high, and an optional text above or below the box. `role` paints it in the buyer / seller colour. */
export interface LayerMark { time: number; end?: number; low: number; high: number; text?: string; position?: "above" | "below"; role?: BandRole; color?: string }
/** A platform overlay (price-action levels, market-zone bands, option walls, trade markers...), computed server-side with an as-of time. */
export interface LayerData { asOf?: string; lines?: LayerLine[]; markers?: LayerMarker[]; bands?: LayerBand[]; segments?: LayerSegment[]; labels?: LayerLabel[]; marks?: LayerMark[] }

/** N4 CH6: a study line from the server (ScreenQL via the screener runtime): on the price pane or in its own pane. */
export interface StudyLine { data: { time: number; value: number }[]; color?: string; pane: "price" | "own"; title?: string }

export interface EngineLayout { engine: string; symbol: string | null; timeframe: string | null; studies: StudySpec[]; drawings: Record<string, DrawingV1> }

export type EngineEvent =
  /** `user`: false when the move did not come from the user's pointer (CH5: such moves are never passed on by a link). */
  | { type: "crosshair"; time: number | null; price: number | null; user?: boolean }
  | { type: "click"; time: number | null; price: number | null }
  | { type: "drawingChanged"; id: string; drawing: DrawingV1 }
  | { type: "visibleRangeChanged"; from: number; to: number };

export interface ChartEngine {
  readonly name: string;
  setSymbol(symbol: string): void;
  setTimeframe(timeframe: string): void;
  setBars(bars: EngineBar[]): void;
  /** A live update: the forming bar changes or a new bar starts. */
  appendBar(bar: EngineBar): void;
  addStudy(spec: StudySpec): void;
  removeStudy(id: string): void;
  supports(kind: DrawingKind): boolean;
  addDrawing(id: string, drawing: DrawingV1): void;
  updateDrawing(id: string, drawing: DrawingV1): void;
  removeDrawing(id: string): void;
  setLayer(id: string, data: LayerData | null): void;
  /** N4 CH6: draw (or with null remove) one study line; an engine without study series ignores it (optional). */
  setStudy?(id: string, line: StudyLine | null): void;
  serialize(): EngineLayout;
  deserialize(layout: EngineLayout): void;
  /** CH5: shows the crosshair at this time (its own bar at or before it), or hides it with null - for the workspace's
   * crosshair link. Optional: an engine without it is simply not followed. */
  setCrosshair?(time: number | null): void;
  /** CEPE-1: shows this time range (seconds) - a linked chart's scroll / zoom. Optional, as setCrosshair. */
  setVisibleTimeRange?(from: number, to: number): void;
  on(listener: (event: EngineEvent) => void): () => void;
  dispose(): void;
}
