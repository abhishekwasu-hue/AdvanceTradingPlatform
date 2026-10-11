/**
 * CH2c-2 (ADR-0023): wires the drawing tools (`tools.ts`) to a chart. Pointer events on the chart element become data
 * points through the engine, the controller decides, and its view is drawn back through the engine's drawings (the
 * same primitives as stored drawings, plus one preview while a drawing is being placed).
 *
 * Only the chart element's own pointer events and the window's keys are used; nothing here touches an order.
 */
import type { DrawingV1 } from "./drawings";
import type { Converters } from "./geometry";
import { hitTest, type DataPoint, type DrawingController, type ToolView } from "./tools";

/** The engine methods the binding needs (LightweightEngine has them; tests pass a fake). */
export interface ToolEngine {
  toData(x: number, y: number): DataPoint | null;
  paneConverters(): Converters | null;
  paneOffsetX(): number;
  candleTimes(): readonly number[];
  setPanning(enabled: boolean): void;
  addDrawing(id: string, drawing: DrawingV1): void;
  removeDrawing(id: string): void;
}

export interface PointerLike { clientX: number; clientY: number; button?: number; pointerId?: number; preventDefault(): void; stopPropagation?(): void }
export interface KeyLike { key: string; ctrlKey?: boolean; metaKey?: boolean; shiftKey?: boolean; target?: unknown; preventDefault(): void }

type Listener<E> = (event: E) => void;
export interface Surface {
  addEventListener(type: "pointerdown" | "pointermove" | "pointerup" | "pointercancel" | "pointerleave", fn: Listener<PointerLike>, capture?: boolean): void;
  removeEventListener(type: "pointerdown" | "pointermove" | "pointerup" | "pointercancel" | "pointerleave", fn: Listener<PointerLike>, capture?: boolean): void;
  getBoundingClientRect(): { left: number; top: number };
  setPointerCapture?(id: number): void;
  releasePointerCapture?(id: number): void;
}
export interface KeySource {
  addEventListener(type: "keydown", fn: Listener<KeyLike>): void;
  removeEventListener(type: "keydown", fn: Listener<KeyLike>): void;
}

export const PREVIEW_ID = "__preview";
const SELECTED_EXTRA_WIDTH = 1;
const drawingId = (key: string) => `d:${key}`;

/** A key typed into a text box is the user typing, not a chart shortcut. */
function typing(target: unknown): boolean {
  const t = target as { tagName?: string; isContentEditable?: boolean } | null;
  return !!t && (t.isContentEditable === true || ["INPUT", "TEXTAREA", "SELECT"].includes(String(t.tagName ?? "").toUpperCase()));
}

/** The drawing as shown: the selected one a little bolder (never saved that way). */
export function shown(d: DrawingV1, selected: boolean): DrawingV1 {
  if (!selected) return d;
  return { ...d, style: { ...d.style, width: Math.min(6, (d.style?.width ?? 1) + SELECTED_EXTRA_WIDTH) } };
}

/**
 * Binds one controller to one chart. Returns the unbind function (removes every listener and every drawing it drew,
 * and turns panning back on).
 *
 * - A tool is active: each press places the next anchor (the press is not passed on, so the chart does not pan).
 * - No tool: a press on a drawing selects it and starts a drag (handle = that anchor, body = the whole drawing, with
 *   panning off until release); a press on empty chart clears the selection and pans as usual.
 * - Keys: Escape cancels, Delete / Backspace deletes the selection, Ctrl/Cmd+Z undo, Ctrl/Cmd+Shift+Z or Ctrl+Y redo.
 */
export function bindTools(engine: ToolEngine, ctl: DrawingController, surface: Surface, keys: KeySource): () => void {
  let dragging = false;
  let drawn = new Set<string>();

  const local = (e: PointerLike) => {
    const r = surface.getBoundingClientRect();
    return { x: e.clientX - r.left - engine.paneOffsetX(), y: e.clientY - r.top };
  };
  const point = (e: PointerLike) => {
    const { x, y } = local(e);
    return engine.toData(x, y);
  };

  const render = (v: ToolView) => {
    const next = new Set<string>();
    for (const d of v.drawings) {
      const id = drawingId(d.key);
      engine.addDrawing(id, shown(d.drawing, d.key === v.selected));
      next.add(id);
    }
    if (v.preview) {
      engine.addDrawing(PREVIEW_ID, v.preview);
      next.add(PREVIEW_ID);
    }
    for (const id of drawn) if (!next.has(id)) engine.removeDrawing(id);
    drawn = next;
  };
  const unsubscribe = ctl.subscribe(render);

  const stopDrag = (e?: PointerLike) => {
    if (!dragging) return;
    dragging = false;
    engine.setPanning(true);
    if (e?.pointerId != null) surface.releasePointerCapture?.(e.pointerId);
    void ctl.endDrag();
  };

  const down = (e: PointerLike) => {
    if (e.button != null && e.button !== 0) return;
    const pt = point(e);
    if (ctl.view().tool) {
      e.preventDefault();
      e.stopPropagation?.();
      if (pt) void ctl.click(pt);
      return;
    }
    const conv = engine.paneConverters();
    const { x, y } = local(e);
    const hit = conv ? hitTest(ctl.view().drawings, conv, x, y) : null;
    if (!hit) {
      ctl.select(null);
      return;
    }
    e.preventDefault();
    e.stopPropagation?.();
    if (pt && ctl.beginDrag(hit.key, hit.handle, pt)) {
      dragging = true;
      engine.setPanning(false);
      if (e.pointerId != null) surface.setPointerCapture?.(e.pointerId);
    }
  };
  const move = (e: PointerLike) => {
    const pt = point(e);
    if (dragging) {
      if (pt) ctl.dragTo(pt, engine.candleTimes());
      return;
    }
    ctl.pointerMove(pt);
  };
  const up = (e: PointerLike) => stopDrag(e);
  const leave = () => { if (!dragging) ctl.pointerMove(null); };

  const key = (e: KeyLike) => {
    if (typing(e.target)) return;
    const mod = e.ctrlKey || e.metaKey;
    const k = e.key.toLowerCase();
    if (e.key === "Escape") { stopDrag(); ctl.cancel(); return; }
    if ((e.key === "Delete" || e.key === "Backspace") && ctl.view().selected) { e.preventDefault(); void ctl.deleteSelected(); return; }
    if (mod && k === "z" && !e.shiftKey) { e.preventDefault(); void ctl.undo(); return; }
    if (mod && ((k === "z" && e.shiftKey) || k === "y")) { e.preventDefault(); void ctl.redo(); }
  };

  // Capture phase: a press that places an anchor or grabs a drawing is stopped before the chart pans on it.
  surface.addEventListener("pointerdown", down, true);
  surface.addEventListener("pointermove", move);
  surface.addEventListener("pointerup", up);
  surface.addEventListener("pointercancel", up);
  surface.addEventListener("pointerleave", leave);
  keys.addEventListener("keydown", key);

  return () => {
    unsubscribe();
    surface.removeEventListener("pointerdown", down, true);
    surface.removeEventListener("pointermove", move);
    surface.removeEventListener("pointerup", up);
    surface.removeEventListener("pointercancel", up);
    surface.removeEventListener("pointerleave", leave);
    keys.removeEventListener("keydown", key);
    for (const id of drawn) engine.removeDrawing(id);
    drawn = new Set();
    if (dragging) engine.setPanning(true);
  };
}
