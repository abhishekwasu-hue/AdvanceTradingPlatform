/**
 * CH2c-1 (ADR-0023): the interactive drawing tools, without a chart. Everything a pointer does on a chart becomes a
 * data point (time in epoch seconds, price) before it reaches this file, so placing anchors, hit-testing, dragging,
 * undo/redo and lock are plain functions and one controller, unit-tested without a browser. CH2c-2 wires the chart's
 * pointer events and a toolbar to it.
 *
 * Storage is the CH1 drawings API: every finished edit is saved with its version, a stale edit (another tab) comes
 * back as `DrawingConflict` and the latest version is shown instead. Undo/redo are client-side over those saves.
 * Nothing here touches an order.
 */
import { ANCHOR_RULES, DrawingConflict, drawingProblem, type Anchor, type DrawingKind, type DrawingV1, type StoredDrawing } from "./drawings";
import { shapes, timeToLogical, type Converters, type Shape } from "./geometry";

/** A pointer position in data space: time in epoch seconds (UTC), price. */
export interface DataPoint { t: number; p: number }

const iso = (sec: number) => new Date(Math.round(sec) * 1000).toISOString();
const toSec = (value: string | undefined) => (value ? Date.parse(value) / 1000 : NaN);

/** The same drawing by meaning: kind, text, levels, style and anchors (times compared as instants, so
 * "…:00Z" from the server equals "…:00.000Z" from here). */
export function sameDrawing(a: DrawingV1, b: DrawingV1): boolean {
  if (a.kind !== b.kind || a.anchors.length !== b.anchors.length || (a.text ?? "") !== (b.text ?? "")) return false;
  if (JSON.stringify(a.levels ?? null) !== JSON.stringify(b.levels ?? null)) return false;
  return a.anchors.every((x, i) => {
    const y = b.anchors[i];
    const tx = x.t === undefined ? null : toSec(x.t);
    const ty = y.t === undefined ? null : toSec(y.t);
    return tx === ty && (x.p ?? null) === (y.p ?? null);
  });
}

/** The anchor a point makes for one anchor rule: time and price, price only, or time only. */
export function anchorFor(rule: "tp" | "p" | "t", pt: DataPoint): Anchor {
  if (rule === "p") return { p: pt.p };
  if (rule === "t") return { t: iso(pt.t) };
  return { t: iso(pt.t), p: pt.p };
}

/** The drawing while it is being placed: the anchors so far, and the pointer standing in for every missing one (so a
 * trendline follows the pointer after its first click). Null before the first anchor without a pointer. */
export function placementPreview(kind: DrawingKind, placed: readonly Anchor[], pointer: DataPoint | null, text?: string): DrawingV1 | null {
  const rules = ANCHOR_RULES[kind];
  const anchors = [...placed];
  if (pointer) for (let i = anchors.length; i < rules.length; i++) anchors.push(anchorFor(rules[i], pointer));
  if (anchors.length < rules.length) return null;
  return { kind, anchors, ...(kind === "text" ? { text: text ?? "" } : {}) };
}

/** The inverse of `timeToLogical`: the time (epoch seconds) at a fractional bar index, interpolated between bars and
 * extrapolated with the nearest bar spacing beyond either end. */
export function logicalToTime(times: readonly number[], logical: number): number | null {
  const n = times.length;
  if (!Number.isFinite(logical) || n < 2) return n === 1 && logical === 0 ? times[0] : null;
  if (logical <= 0) return times[0] + logical * (times[1] - times[0]);
  if (logical >= n - 1) return times[n - 1] + (logical - (n - 1)) * (times[n - 1] - times[n - 2]);
  const i = Math.floor(logical);
  return times[i] + (logical - i) * (times[i + 1] - times[i]);
}

/** One anchor moved to a point; the anchor keeps its rule (a stop stays price-only). */
export function moveAnchor(d: DrawingV1, index: number, pt: DataPoint): DrawingV1 {
  const rule = ANCHOR_RULES[d.kind][index];
  if (rule === undefined) return d;
  return { ...d, anchors: d.anchors.map((a, i) => (i === index ? anchorFor(rule, pt) : a)) };
}

/** The whole drawing moved by the pointer's move from `from` to `to`. Time moves in bars (on the chart's bar times),
 * not in seconds, so dragging across a night or a weekend keeps the drawing's shape. */
export function translate(d: DrawingV1, from: DataPoint, to: DataPoint, times: readonly number[]): DrawingV1 {
  const l0 = timeToLogical(times, from.t);
  const l1 = timeToLogical(times, to.t);
  const dl = l0 == null || l1 == null ? 0 : l1 - l0;
  const dp = to.p - from.p;
  return {
    ...d,
    anchors: d.anchors.map((a) => {
      const out: Anchor = {};
      if (a.t !== undefined) {
        const l = timeToLogical(times, toSec(a.t));
        const t = l == null ? null : logicalToTime(times, l + dl);
        out.t = t == null ? a.t : iso(t);
      }
      if (a.p !== undefined) out.p = a.p + dp;
      return out;
    }),
  };
}

/** Where each anchor's drag handle sits in pane pixels. A price-only anchor sits at the time of the drawing's first
 * timed anchor (a position's stop and target), else mid-pane; a time-only anchor sits mid-height. */
export function handles(d: DrawingV1, c: Converters): { index: number; x: number; y: number }[] {
  const timed = d.anchors.find((a) => a.t !== undefined);
  const baseX = timed ? c.x(toSec(timed.t)) : null;
  const out: { index: number; x: number; y: number }[] = [];
  d.anchors.forEach((a, index) => {
    const x = a.t !== undefined ? c.x(toSec(a.t)) : baseX ?? c.width / 2;
    const y = a.p !== undefined ? c.y(a.p) : c.height / 2;
    if (x != null && y != null && Number.isFinite(x) && Number.isFinite(y)) out.push({ index, x, y });
  });
  return out;
}

function segmentDistance(px: number, py: number, x1: number, y1: number, x2: number, y2: number): number {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const len2 = dx * dx + dy * dy;
  const u = len2 === 0 ? 0 : Math.max(0, Math.min(1, ((px - x1) * dx + (py - y1) * dy) / len2));
  return Math.hypot(px - (x1 + u * dx), py - (y1 + u * dy));
}

function insidePolygon(px: number, py: number, pts: readonly [number, number][]): boolean {
  let inside = false;
  for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
    const [xi, yi] = pts[i];
    const [xj, yj] = pts[j];
    if (yi > py !== yj > py && px < ((xj - xi) * (py - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

/** Pixels from a point to a shape: 0 inside a filled area. A label counts as a box around its text. */
export function distanceToShape(s: Shape, x: number, y: number): number {
  if (s.type === "segment") return segmentDistance(x, y, s.x1, s.y1, s.x2, s.y2);
  if (s.type === "rect") {
    const dx = Math.max(s.x - x, 0, x - (s.x + s.w));
    const dy = Math.max(s.y - y, 0, y - (s.y + s.h));
    return Math.hypot(dx, dy);
  }
  if (s.type === "polygon") {
    if (s.points.length < 3) return Infinity;
    if (insidePolygon(x, y, s.points)) return 0;
    return Math.min(...s.points.map(([x1, y1], i) => {
      const [x2, y2] = s.points[(i + 1) % s.points.length];
      return segmentDistance(x, y, x1, y1, x2, y2);
    }));
  }
  const w = s.text.length * 6.5;
  const left = s.align === "left" ? s.x : s.align === "right" ? s.x - w : s.x - w / 2;
  const top = s.baseline === "top" ? s.y : s.baseline === "bottom" ? s.y - 12 : s.y - 6;
  return Math.hypot(Math.max(left - x, 0, x - (left + w)), Math.max(top - y, 0, y - (top + 12)));
}

export interface Hit { key: string; handle: number | null }

/** The drawing under a pane point. Handles win over bodies, and the drawing painted last (on top) wins a tie. */
export function hitTest(items: readonly { key: string; drawing: DrawingV1 }[], c: Converters, x: number, y: number, tolerance = 6): Hit | null {
  for (let i = items.length - 1; i >= 0; i--) {
    const h = handles(items[i].drawing, c).find((p) => Math.hypot(p.x - x, p.y - y) <= tolerance);
    if (h) return { key: items[i].key, handle: h.index };
  }
  for (let i = items.length - 1; i >= 0; i--) {
    if (shapes(items[i].drawing, c).some((s) => distanceToShape(s, x, y) <= tolerance)) return { key: items[i].key, handle: null };
  }
  return null;
}

/** The slice of `drawingsApi` the controller uses (a fake in tests). */
export interface DrawingsStore {
  list(symbol: string, exchange?: string): Promise<StoredDrawing[]>;
  create(symbol: string, drawing: DrawingV1, exchange?: string): Promise<StoredDrawing>;
  update(stored: StoredDrawing, drawing: DrawingV1): Promise<StoredDrawing>;
  lock(stored: StoredDrawing, locked: boolean): Promise<StoredDrawing>;
  remove(stored: StoredDrawing): Promise<{ deleted: number }>;
}

/** One undoable edit: `before` null = it was created, `after` null = it was deleted. */
interface Edit { key: string; before: DrawingV1 | null; after: DrawingV1 | null }

export const HISTORY_LIMIT = 100;

export interface ToolView {
  drawings: { key: string; drawing: DrawingV1; locked: boolean }[];
  preview: DrawingV1 | null;
  selected: string | null;
  tool: DrawingKind | null;
  canUndo: boolean;
  canRedo: boolean;
  message: string | null;
}

interface Drag { key: string; handle: number | null; from: DataPoint; start: DrawingV1; current: DrawingV1; epoch: number }

/** The drawing tools for one symbol: place, select, drag, delete, lock, undo/redo; every finished edit is saved. */
export class DrawingController {
  private items = new Map<string, StoredDrawing>();
  private pending = new Map<string, DrawingV1>();        // a finished drag whose save has not come back yet
  private epochs = new Map<string, number>();            // bumped on a conflict: edits begun before it are stale
  private undoStack: Edit[] = [];
  private redoStack: Edit[] = [];
  private tool: DrawingKind | null = null;
  private toolText: string | undefined;
  private placed: Anchor[] = [];
  private pointer: DataPoint | null = null;
  private selected: string | null = null;
  private drag: Drag | null = null;
  private message: string | null = null;
  private listeners = new Set<(view: ToolView) => void>();
  private queue: Promise<unknown> = Promise.resolve();

  constructor(private readonly api: DrawingsStore, readonly symbol: string, readonly exchange = "NSE") {}

  subscribe(listener: (view: ToolView) => void): () => void {
    this.listeners.add(listener);
    listener(this.view());
    return () => this.listeners.delete(listener);
  }

  view(): ToolView {
    const drawings = [...this.items.entries()].map(([key, s]) => ({
      key, locked: s.locked, drawing: this.drag?.key === key ? this.drag.current : this.current(key) ?? s.drawing,
    }));
    return {
      drawings,
      preview: this.tool ? placementPreview(this.tool, this.placed, this.pointer, this.toolText) : null,
      selected: this.selected,
      tool: this.tool,
      canUndo: this.undoStack.length > 0,
      canRedo: this.redoStack.length > 0,
      message: this.message,
    };
  }

  /** The drawing as the user last left it: a drag not yet saved, else the stored one. */
  private current(key: string): DrawingV1 | null {
    return this.pending.get(key) ?? this.items.get(key)?.drawing ?? null;
  }

  private emit() {
    const v = this.view();
    this.listeners.forEach((l) => l(v));
  }

  /** Edits run one at a time, in order (a double click never saves twice in parallel). */
  private run<T>(fn: () => Promise<T>): Promise<T> {
    const next = this.queue.then(fn, fn);
    this.queue = next.catch(() => undefined);
    return next;
  }

  /** Through the edit queue, so an edit still in flight never writes into the reloaded state. */
  load(): Promise<void> {
    return this.run(async () => {
      const rows = await this.api.list(this.symbol, this.exchange);
      this.items = new Map(rows.map((r) => [String(r.id), r]));
      this.pending.clear();
      this.undoStack = [];
      this.redoStack = [];
      this.emit();
    });
  }

  /** Pick a tool (null = the pointer). A text drawing takes its text up front. */
  setTool(kind: DrawingKind | null, text?: string) {
    this.tool = kind;
    this.toolText = text;
    this.placed = [];
    this.selected = null;
    this.message = null;
    this.emit();
  }

  /** Escape: drop the tool, the anchors placed so far and an unfinished drag. */
  cancel() {
    this.tool = null;
    this.placed = [];
    this.drag = null;
    this.emit();
  }

  pointerMove(pt: DataPoint | null) {
    this.pointer = pt;
    if (this.tool) this.emit();
  }

  /** A click while a tool is active places the next anchor; the last one saves the drawing. */
  click(pt: DataPoint): Promise<void> {
    if (!this.tool) return Promise.resolve();
    const kind = this.tool;
    const rules = ANCHOR_RULES[kind];
    this.placed = [...this.placed, anchorFor(rules[this.placed.length], pt)];
    if (this.placed.length < rules.length) {
      this.emit();
      return Promise.resolve();
    }
    const drawing = placementPreview(kind, this.placed, null, this.toolText) as DrawingV1;
    this.tool = null;
    this.placed = [];
    const problem = drawingProblem(drawing);
    if (problem) {
      this.message = problem;
      this.emit();
      return Promise.resolve();
    }
    return this.run(async () => {
      const stored = await this.guard(() => this.api.create(this.symbol, drawing, this.exchange));
      if (stored) {
        const key = String(stored.id);
        this.items.set(key, stored);
        this.selected = key;
        this.record({ key, before: null, after: stored.drawing });
      }
      this.emit();
    });
  }

  select(key: string | null) {
    this.selected = key && this.items.has(key) ? key : null;
    this.emit();
  }

  /** Start dragging a handle (an anchor) or, with handle null, the whole drawing. A locked drawing does not move. */
  beginDrag(key: string, handle: number | null, from: DataPoint): boolean {
    const stored = this.items.get(key);
    if (!stored) return false;
    this.selected = key;
    if (stored.locked) {
      this.message = "This drawing is locked. Unlock it to move it.";
      this.emit();
      return false;
    }
    const start = this.current(key) ?? stored.drawing;      // includes a previous drag still being saved
    this.drag = { key, handle, from, start, current: start, epoch: this.epochs.get(key) ?? 0 };
    this.emit();
    return true;
  }

  dragTo(pt: DataPoint, times: readonly number[]) {
    const d = this.drag;
    if (!d) return;
    d.current = d.handle == null ? translate(d.start, d.from, pt, times) : moveAnchor(d.start, d.handle, pt);
    this.emit();
  }

  /** Save the drag (one undo step). A move that changed nothing saves nothing. */
  endDrag(): Promise<void> {
    const d = this.drag;
    this.drag = null;
    if (!d || sameDrawing(d.current, d.start)) {
      this.emit();
      return Promise.resolve();
    }
    this.pending.set(d.key, d.current);                    // shown (and dragged again) at once, saved in order
    this.emit();
    return this.run(async () => {
      if ((this.epochs.get(d.key) ?? 0) !== d.epoch) {        // built on a version another tab replaced: not saved
        if (this.pending.get(d.key) === d.current) this.pending.delete(d.key);
        this.emit();
        return;
      }
      const ok = await this.save(d.key, d.current);
      if (this.pending.get(d.key) === d.current) this.pending.delete(d.key);   // a later drag keeps its own
      if (ok) this.record({ key: d.key, before: d.start, after: d.current });
      this.emit();
    });
  }

  /** The state is read when the queued work runs, not when the key is pressed, so an edit still being saved is
   * what gets deleted (and what undo brings back). */
  deleteSelected(): Promise<void> {
    const key = this.selected;
    if (!key || !this.items.has(key)) return Promise.resolve();
    return this.run(async () => {
      const stored = this.items.get(key);
      if (!stored) return;
      if (stored.locked) {
        this.message = "This drawing is locked. Unlock it to delete it.";
        this.emit();
        return;
      }
      const before = stored.drawing;
      if (await this.erase(key)) {
        if (this.selected === key) this.selected = null;
        this.record({ key, before, after: null });
      }
      this.emit();
    });
  }

  toggleLock(): Promise<void> {
    const key = this.selected;
    if (!key || !this.items.has(key)) return Promise.resolve();
    return this.run(async () => {
      const stored = this.items.get(key);
      if (!stored) return;
      const next = await this.guard(() => this.api.lock(stored, !stored.locked), key);
      if (next) this.items.set(key, next);
      this.emit();
    });
  }

  undo(): Promise<void> {
    return this.run(() => this.step(this.undoStack, this.redoStack, "before"));
  }

  redo(): Promise<void> {
    return this.run(() => this.step(this.redoStack, this.undoStack, "after"));
  }

  private async step(from: Edit[], to: Edit[], side: "before" | "after") {
    const edit = from[from.length - 1];
    if (!edit) return;
    const stored = this.items.get(edit.key);
    if (stored?.locked) {
      this.message = "This drawing is locked. Unlock it to undo or redo its edits.";
      this.emit();
      return;
    }
    const target = edit[side];
    let ok: boolean;
    if (target === null) {
      ok = await this.erase(edit.key);
    } else if (!stored) {
      const created = await this.guard(() => this.api.create(this.symbol, target, this.exchange));
      ok = created != null;
      if (created) {
        const key = String(created.id);
        this.items.set(key, created);
        this.rekey(edit.key, key);                     // a re-created drawing has a new id: later steps follow it
      }
    } else {
      ok = await this.save(edit.key, target);
    }
    if (ok) {
      from.pop();
      to.push(edit);
      this.message = null;
    }
    this.emit();
  }

  private record(edit: Edit) {
    this.undoStack.push(edit);
    if (this.undoStack.length > HISTORY_LIMIT) this.undoStack.shift();
    this.redoStack = [];
  }

  private rekey(oldKey: string, newKey: string) {
    for (const e of [...this.undoStack, ...this.redoStack]) if (e.key === oldKey) e.key = newKey;
    if (this.selected === oldKey) this.selected = newKey;
  }

  private forget(key: string) {
    this.undoStack = this.undoStack.filter((e) => e.key !== key);
    this.redoStack = this.redoStack.filter((e) => e.key !== key);
  }

  private async save(key: string, drawing: DrawingV1): Promise<boolean> {
    const stored = this.items.get(key);
    if (!stored) return false;
    const next = await this.guard(() => this.api.update(stored, drawing), key);
    if (next) this.items.set(key, next);
    return next != null;
  }

  private async erase(key: string): Promise<boolean> {
    const stored = this.items.get(key);
    if (!stored) return false;
    const done = await this.guard(() => this.api.remove(stored), key);
    if (done) this.items.delete(key);
    return done != null;
  }

  /** Runs one request. A conflict shows the latest version (or drops a drawing deleted elsewhere) and forgets this
   * drawing's undo steps, which were made against a version that no longer exists. Any other error is shown. */
  private async guard<T>(fn: () => Promise<T>, key?: string): Promise<T | null> {
    try {
      const out = await fn();
      this.message = null;
      return out;
    } catch (error) {
      if (error instanceof DrawingConflict && key) {
        if (error.current) this.items.set(key, error.current); else this.items.delete(key);
        this.pending.delete(key);
        this.epochs.set(key, (this.epochs.get(key) ?? 0) + 1);
        if (!error.current && this.selected === key) this.selected = null;
        this.forget(key);
        this.message = error.message;
      } else {
        this.message = error instanceof Error ? error.message : String(error);
      }
      return null;
    }
  }
}
