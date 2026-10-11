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
/** A platform overlay (price-action levels, option walls, trade markers...), computed server-side with an as-of time. */
export interface LayerData { asOf?: string; lines?: LayerLine[]; markers?: LayerMarker[] }

export interface EngineLayout { engine: string; symbol: string | null; timeframe: string | null; studies: StudySpec[]; drawings: Record<string, DrawingV1> }

export type EngineEvent =
  | { type: "crosshair"; time: number | null; price: number | null }
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
  serialize(): EngineLayout;
  deserialize(layout: EngineLayout): void;
  on(listener: (event: EngineEvent) => void): () => void;
  dispose(): void;
}
