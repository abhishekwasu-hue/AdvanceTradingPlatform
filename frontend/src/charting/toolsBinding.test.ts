import { describe, expect, it } from "vitest";
import type { DrawingV1, StoredDrawing } from "./drawings";
import { timeToLogical, type Converters } from "./geometry";
import { DrawingController, logicalToTime, type DrawingsStore } from "./tools";
import { PREVIEW_ID, bindTools, shown, type KeyLike, type PointerLike, type Surface, type ToolEngine } from "./toolsBinding";

// One bar every 15 minutes, 10 px per bar; price 100 at y=500, 10 px per rupee. The pane starts 40 px into the
// element (a left price scale) and the element sits at (100, 50) on the page.
const T0 = Date.parse("2026-03-02T03:45:00Z") / 1000;
const times = Array.from({ length: 40 }, (_, i) => T0 + i * 900);
const conv: Converters = {
  x: (t) => { const l = timeToLogical(times, t); return l == null ? null : l * 10; },
  y: (p) => 500 - (p - 100) * 10,
  width: 400, height: 600,
};

class Store implements DrawingsStore {
  rows = new Map<number, StoredDrawing>();
  next = 1;
  async list() { return [...this.rows.values()]; }
  async create(symbol: string, drawing: DrawingV1) {
    const r: StoredDrawing = { id: this.next++, symbol, exchange: "NSE", kind: drawing.kind, drawing, version: 1, locked: false, updated_at: null };
    this.rows.set(r.id, r);
    return r;
  }
  async update(stored: StoredDrawing, drawing: DrawingV1) {
    const r = { ...this.rows.get(stored.id)!, drawing, version: stored.version + 1 };
    this.rows.set(r.id, r);
    return r;
  }
  async lock(stored: StoredDrawing, locked: boolean) { const r = { ...this.rows.get(stored.id)!, locked }; this.rows.set(r.id, r); return r; }
  async remove(stored: StoredDrawing) { this.rows.delete(stored.id); return { deleted: stored.id }; }
}

function harness() {
  const drawn = new Map<string, DrawingV1>();
  const panning: boolean[] = [];
  const engine: ToolEngine = {
    toData: (x, y) => ({ t: logicalToTime(times, x / 10) as number, p: 100 + (500 - y) / 10 }),
    paneConverters: () => conv,
    paneOffsetX: () => 40,
    candleTimes: () => times,
    setPanning: (on) => { panning.push(on); },
    addDrawing: (id, d) => { drawn.set(id, d); },
    removeDrawing: (id) => { drawn.delete(id); },
  };
  const listeners = new Map<string, ((e: never) => void)[]>();
  const add = (type: string, fn: (e: never) => void) => listeners.set(type, [...(listeners.get(type) ?? []), fn]);
  const remove = (type: string, fn: (e: never) => void) => listeners.set(type, (listeners.get(type) ?? []).filter((f) => f !== fn));
  const captured: number[] = [];
  const surface: Surface = {
    addEventListener: add as Surface["addEventListener"], removeEventListener: remove as Surface["removeEventListener"],
    getBoundingClientRect: () => ({ left: 100, top: 50 }),
    setPointerCapture: (id) => { captured.push(id); },
    releasePointerCapture: (id) => { captured.splice(captured.indexOf(id), 1); },
  };
  const keys = { addEventListener: add, removeEventListener: remove } as unknown as Parameters<typeof bindTools>[3];
  let stopped = 0;
  const fire = (type: string, paneX: number, y: number, extra: Partial<PointerLike> = {}) => {
    const e: PointerLike = { clientX: paneX + 140, clientY: y + 50, button: 0, pointerId: 1, preventDefault() {}, stopPropagation() { stopped++; }, ...extra };
    for (const fn of listeners.get(type) ?? []) (fn as (e: PointerLike) => void)(e);
  };
  const key = (k: string, extra: Partial<KeyLike> = {}) => {
    const e: KeyLike = { key: k, preventDefault() {}, ...extra };
    for (const fn of listeners.get("keydown") ?? []) (fn as (e: KeyLike) => void)(e);
  };
  const store = new Store();
  const ctl = new DrawingController(store, "TCS");
  const unbind = bindTools(engine, ctl, surface, keys);
  const settle = () => new Promise((r) => setTimeout(r, 0));
  return { drawn, panning, listeners, captured, fire, key, store, ctl, unbind, settle, stopped: () => stopped };
}

describe("bindTools", () => {
  it("places a trendline: presses become anchors (offset by the element and the left scale), the preview follows", async () => {
    const h = harness();
    h.ctl.setTool("trendline");
    h.fire("pointerdown", 20, 500);                                     // bar 2, price 100
    h.fire("pointermove", 60, 480);
    expect(h.drawn.get(PREVIEW_ID)?.anchors.map((a) => a.p)).toEqual([100, 102]);
    expect(h.stopped()).toBe(1);                                        // the press did not reach the chart (no pan)
    h.fire("pointerdown", 80, 470);
    await h.settle();
    expect(h.drawn.has(PREVIEW_ID)).toBe(false);
    const saved = [...h.store.rows.values()][0];
    expect(saved.drawing.anchors).toEqual([{ t: new Date(times[2] * 1000).toISOString(), p: 100 }, { t: new Date(times[8] * 1000).toISOString(), p: 103 }]);
    expect(h.drawn.get("d:1")?.style?.width).toBe(2);                    // selected: shown a little bolder
  });

  it("drags a handle with panning off until release, then saves once", async () => {
    const h = harness();
    h.ctl.setTool("hline");
    h.fire("pointerdown", 100, 490);                                    // hline at 101
    await h.settle();
    h.ctl.select(null);
    h.fire("pointerdown", 200, 490);                                    // the hline's handle (mid-pane, y of 101)
    expect(h.panning).toEqual([false]);
    expect(h.captured).toEqual([1]);
    h.fire("pointermove", 200, 450);
    expect(h.drawn.get("d:1")?.anchors[0].p).toBe(105);                 // shown while dragging
    h.fire("pointerup", 200, 450);
    await h.settle();
    expect(h.panning).toEqual([false, true]);
    expect(h.captured).toEqual([]);
    expect(h.store.rows.get(1)?.drawing.anchors[0].p).toBe(105);
    expect(h.store.rows.get(1)?.version).toBe(2);
  });

  it("a press on empty chart clears the selection and pans as usual; a right press is ignored", async () => {
    const h = harness();
    h.ctl.setTool("hline");
    h.fire("pointerdown", 100, 490);
    await h.settle();
    expect(h.ctl.view().selected).toBe("1");
    h.fire("pointerdown", 100, 100, { button: 2 });
    expect(h.ctl.view().selected).toBe("1");
    h.fire("pointerdown", 100, 100);
    expect(h.ctl.view().selected).toBeNull();
    expect(h.panning).toEqual([]);
    expect(h.stopped()).toBe(1);                                        // only the placing press was stopped
  });

  it("keys: Delete removes the selection, Ctrl+Z / Ctrl+Shift+Z undo and redo, Escape cancels; typing is ignored", async () => {
    const h = harness();
    h.ctl.setTool("hline");
    h.fire("pointerdown", 100, 490);
    await h.settle();
    h.key("Delete", { target: { tagName: "INPUT" } });
    await h.settle();
    expect(h.store.rows.size).toBe(1);
    h.key("Delete");
    await h.settle();
    expect(h.store.rows.size).toBe(0);
    expect(h.drawn.size).toBe(0);
    h.key("z", { ctrlKey: true });
    await h.settle();
    expect(h.store.rows.size).toBe(1);
    h.key("Z", { metaKey: true, shiftKey: true });
    await h.settle();
    expect(h.store.rows.size).toBe(0);
    h.ctl.setTool("trendline");
    h.fire("pointerdown", 20, 500);
    h.key("Escape");
    expect(h.ctl.view().tool).toBeNull();
    expect(h.drawn.has(PREVIEW_ID)).toBe(false);
  });

  it("unbind removes every listener and every drawing it drew", async () => {
    const h = harness();
    h.ctl.setTool("hline");
    h.fire("pointerdown", 100, 490);
    await h.settle();
    h.unbind();
    expect([...h.listeners.values()].every((l) => l.length === 0)).toBe(true);
    expect(h.drawn.size).toBe(0);
  });

  it("the selected look never changes the drawing that is saved", () => {
    const d: DrawingV1 = { kind: "hline", anchors: [{ p: 1 }], style: { width: 6 } };
    expect(shown(d, true).style?.width).toBe(6);
    expect(shown(d, false)).toBe(d);
    expect(d.style?.width).toBe(6);
  });
});
