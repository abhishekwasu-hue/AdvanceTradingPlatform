import { describe, expect, it } from "vitest";
import { DrawingConflict, type DrawingV1, type StoredDrawing } from "./drawings";
import { timeToLogical, type Converters } from "./geometry";
import {
  DrawingController, HISTORY_LIMIT, anchorFor, sameDrawing, distanceToShape, handles, hitTest, logicalToTime, moveAnchor, placementPreview,
  translate, type DrawingsStore, type ToolView,
} from "./tools";

// 15-minute bars from T0 with an overnight gap after bar 24; 10 px per bar; price 100 at y=500, 10 px per rupee.
const T0 = Date.parse("2026-03-02T03:45:00Z") / 1000;
const BAR = 900;
const times = Array.from({ length: 40 }, (_, i) => T0 + i * BAR + (i >= 25 ? 17.5 * 3600 : 0));
const at = (bar: number) => logicalToTime(times, bar) as number;
const iso = (bar: number) => new Date(at(bar) * 1000).toISOString();
const conv: Converters = {
  x: (t) => { const l = timeToLogical(times, t); return l == null ? null : l * 10; },
  y: (p) => 500 - (p - 100) * 10,
  width: 400, height: 600,
};

/** An in-memory drawings API with the backend's version, lock and conflict rules. */
/** Like the server's JSON: times as "…:00Z" (no milliseconds) and the schema filled in. */
const normalize = (d: DrawingV1): DrawingV1 => ({
  schema: "drawing/1", ...d,
  anchors: d.anchors.map((a) => ({ ...a, ...(a.t ? { t: new Date(a.t).toISOString().replace(".000Z", "Z") } : {}) })),
});

class FakeStore implements DrawingsStore {
  rows = new Map<number, StoredDrawing>();
  next = 1;
  calls: string[] = [];
  fail: Error | null = null;

  private row(id: number) {
    const r = this.rows.get(id);
    if (!r) throw new DrawingConflict(null);
    return r;
  }

  async list() { this.calls.push("list"); return [...this.rows.values()]; }

  async create(symbol: string, drawing: DrawingV1, exchange = "NSE") {
    this.calls.push("create");
    if (this.fail) throw this.fail;
    const r: StoredDrawing = { id: this.next++, symbol, exchange, kind: drawing.kind, drawing: normalize(drawing), version: 1, locked: false, updated_at: null };
    this.rows.set(r.id, r);
    return r;
  }

  async update(stored: StoredDrawing, drawing: DrawingV1) {
    this.calls.push(`update ${stored.id}@${stored.version}`);
    const r = this.row(stored.id);
    if (r.locked) throw new Error("locked");
    if (r.version !== stored.version) throw new DrawingConflict(r);
    const next = { ...r, drawing: normalize(drawing), version: r.version + 1 };
    this.rows.set(r.id, next);
    return next;
  }

  async lock(stored: StoredDrawing, locked: boolean) {
    this.calls.push(`lock ${stored.id} ${locked}`);
    const next = { ...this.row(stored.id), locked };
    this.rows.set(stored.id, next);
    return next;
  }

  async remove(stored: StoredDrawing) {
    this.calls.push(`remove ${stored.id}@${stored.version}`);
    const r = this.row(stored.id);
    if (r.version !== stored.version) throw new DrawingConflict(r);
    this.rows.delete(stored.id);
    return { deleted: stored.id };
  }
}

const pt = (bar: number, p: number) => ({ t: at(bar), p });

function setup() {
  const store = new FakeStore();
  const ctl = new DrawingController(store, "TCS");
  let view!: ToolView;
  ctl.subscribe((v) => { view = v; });
  return { store, ctl, view: () => view };
}

describe("placing anchors", () => {
  it("each anchor keeps its rule; the pointer stands in for the missing ones", () => {
    expect(anchorFor("p", pt(3, 101))).toEqual({ p: 101 });
    expect(anchorFor("t", pt(3, 101))).toEqual({ t: iso(3) });
    expect(placementPreview("trendline", [], null)).toBeNull();
    expect(placementPreview("trendline", [{ t: iso(1), p: 100 }], pt(5, 102))?.anchors).toEqual([{ t: iso(1), p: 100 }, { t: iso(5), p: 102 }]);
    expect(placementPreview("long_position", [{ t: iso(1), p: 100 }], pt(5, 97))?.anchors).toEqual([{ t: iso(1), p: 100 }, { p: 97 }, { p: 97 }]);
    expect(placementPreview("text", [], pt(2, 100), "note")).toMatchObject({ kind: "text", text: "note" });
  });

  it("a click per anchor; the last click saves one drawing, selected, and the tool is done", async () => {
    const { store, ctl, view } = setup();
    ctl.setTool("channel");
    await ctl.click(pt(0, 100));
    ctl.pointerMove(pt(6, 103));
    expect(view().preview?.anchors).toHaveLength(3);
    await ctl.click(pt(10, 105));
    expect(store.calls).toEqual([]);
    await ctl.click(pt(5, 99.5));
    expect(store.calls).toEqual(["create"]);
    expect(view()).toMatchObject({ tool: null, preview: null, selected: "1", canUndo: true });
    expect(sameDrawing(view().drawings[0].drawing, { kind: "channel", anchors: [{ t: iso(0), p: 100 }, { t: iso(10), p: 105 }, { t: iso(5), p: 99.5 }] })).toBe(true);
  });

  it("a drawing the backend would refuse is not sent; escape drops a half-placed drawing", async () => {
    const { store, ctl, view } = setup();
    ctl.setTool("text", "   ");
    await ctl.click(pt(1, 100));
    expect(store.calls).toEqual([]);
    expect(view().message).toBe("a text drawing needs text");
    ctl.setTool("trendline");
    await ctl.click(pt(1, 100));
    ctl.cancel();
    await ctl.click(pt(2, 101));
    expect(view()).toMatchObject({ tool: null, preview: null });
    expect(store.calls).toEqual([]);
  });
});

describe("hit-testing", () => {
  const line: DrawingV1 = { kind: "trendline", anchors: [{ t: iso(0), p: 100 }, { t: iso(10), p: 101 }] };
  const box: DrawingV1 = { kind: "rectangle", anchors: [{ t: iso(4), p: 99 }, { t: iso(8), p: 102 }] };
  const items = [{ key: "a", drawing: line }, { key: "b", drawing: box }];

  it("handles win over bodies, the top drawing wins, and a miss is null", () => {
    expect(hitTest(items, conv, 0, 500)).toEqual({ key: "a", handle: 0 });          // line's first anchor
    expect(hitTest(items, conv, 50, 497)).toEqual({ key: "b", handle: null });      // inside the box (drawn on top)
    expect(hitTest(items, conv, 20, 497)).toEqual({ key: "a", handle: null });      // on the line, outside the box
    expect(hitTest(items, conv, 200, 100)).toBeNull();
    expect(hitTest(items, conv, 20, 490, 6)).toBeNull();                            // 8 px from the line
  });

  it("price-only and time-only handles have a place; distances are geometric", () => {
    const pos: DrawingV1 = { kind: "long_position", anchors: [{ t: iso(20), p: 100 }, { p: 95 }, { p: 112 }] };
    expect(handles(pos, conv)).toEqual([{ index: 0, x: 200, y: 500 }, { index: 1, x: 200, y: 550 }, { index: 2, x: 200, y: 380 }]);
    expect(handles({ kind: "hline", anchors: [{ p: 101 }] }, conv)).toEqual([{ index: 0, x: 200, y: 490 }]);
    expect(handles({ kind: "vline", anchors: [{ t: iso(7) }] }, conv)).toEqual([{ index: 0, x: 70, y: 300 }]);
    expect(distanceToShape({ type: "segment", x1: 0, y1: 0, x2: 10, y2: 0, color: "", width: 1, dash: "solid" }, 13, 4)).toBe(5);
    expect(distanceToShape({ type: "polygon", points: [[0, 0], [10, 0], [10, 10], [0, 10]], fill: "" }, 5, 5)).toBe(0);
    expect(distanceToShape({ type: "polygon", points: [[0, 0], [10, 0], [10, 10], [0, 10]], fill: "" }, 13, 5)).toBe(3);
    expect(distanceToShape({ type: "text", x: 0, y: 0, text: "ab", color: "", align: "left", baseline: "top" }, 5, 5)).toBe(0);
  });
});

describe("dragging", () => {
  it("a handle moves one anchor and keeps its rule; the body moves in bars, across the night gap", () => {
    const pos: DrawingV1 = { kind: "short_position", anchors: [{ t: iso(20), p: 100 }, { p: 104 }, { p: 92 }] };
    expect(moveAnchor(pos, 1, pt(30, 106)).anchors[1]).toEqual({ p: 106 });
    const line: DrawingV1 = { kind: "trendline", anchors: [{ t: iso(20), p: 100 }, { t: iso(23), p: 102 }] };
    const moved = translate(line, pt(21, 100), pt(25, 99), times);            // 4 bars right, across the gap
    expect(moved.anchors).toEqual([{ t: iso(24), p: 99 }, { t: iso(27), p: 101 }]);
    expect(logicalToTime(times, timeToLogical(times, at(31.5)) as number)).toBeCloseTo(at(31.5));
    expect(logicalToTime(times, 41)).toBe(times[39] + 2 * BAR);
  });

  it("a drag saves once at the end with its version; a locked drawing does not move", async () => {
    const { store, ctl, view } = setup();
    ctl.setTool("trendline");
    await ctl.click(pt(0, 100));
    await ctl.click(pt(10, 101));
    expect(ctl.beginDrag("1", 1, pt(10, 101))).toBe(true);
    ctl.dragTo(pt(12, 103), times);
    ctl.dragTo(pt(14, 104), times);
    expect(view().drawings[0].drawing.anchors[1]).toEqual({ t: iso(14), p: 104 });   // shown while dragging
    expect(store.calls).toEqual(["create"]);
    await ctl.endDrag();
    expect(store.calls).toEqual(["create", "update 1@1"]);
    expect(ctl.beginDrag("1", null, pt(5, 100))).toBe(true);
    await ctl.endDrag();                                                          // nothing moved: nothing saved
    expect(store.calls).toHaveLength(2);
    await ctl.toggleLock();
    expect(ctl.beginDrag("1", null, pt(5, 100))).toBe(false);
    expect(view().message).toMatch(/locked/);
    await ctl.deleteSelected();
    expect(store.rows.size).toBe(1);
  });
});

describe("undo / redo", () => {
  it("undo walks back create, move and delete through the API; redo walks forward; a new edit clears redo", async () => {
    const { store, ctl, view } = setup();
    ctl.setTool("hline");
    await ctl.click(pt(3, 101));
    ctl.beginDrag("1", 0, pt(3, 101));
    ctl.dragTo(pt(3, 105), times);
    await ctl.endDrag();
    ctl.select("1");
    await ctl.deleteSelected();
    expect(store.rows.size).toBe(0);
    await ctl.undo();                                                             // the delete: re-created, new id
    expect([...store.rows.values()].map((r) => [r.id, r.drawing.anchors[0].p])).toEqual([[2, 105]]);
    await ctl.undo();                                                             // the move, on the new id
    expect(store.rows.get(2)?.drawing.anchors[0].p).toBe(101);
    await ctl.undo();                                                             // the create
    expect(store.rows.size).toBe(0);
    expect(view()).toMatchObject({ canUndo: false, canRedo: true });
    await ctl.redo();
    await ctl.redo();
    expect([...store.rows.values()].map((r) => r.drawing.anchors[0].p)).toEqual([105]);
    ctl.setTool("vline");
    await ctl.click(pt(9, 100));
    expect(view().canRedo).toBe(false);
  });

  it("a conflict shows the latest version and drops that drawing's undo steps", async () => {
    const { store, ctl, view } = setup();
    ctl.setTool("hline");
    await ctl.click(pt(3, 101));
    const other = { ...store.rows.get(1)!, version: 2, drawing: { kind: "hline" as const, anchors: [{ p: 120 }] } };
    store.rows.set(1, other);                                                     // edited in another tab
    ctl.beginDrag("1", 0, pt(3, 101));
    ctl.dragTo(pt(3, 99), times);
    await ctl.endDrag();
    expect(view().drawings[0].drawing.anchors[0].p).toBe(120);
    expect(view().message).toMatch(/another window/);
    expect(view().canUndo).toBe(false);
    await ctl.undo();
    expect(store.rows.get(1)?.drawing.anchors[0].p).toBe(120);                   // never overwritten
  });

  it("other errors keep the drawing as it was; history is capped", async () => {
    const { store, ctl, view } = setup();
    store.fail = new Error("network down");
    ctl.setTool("hline");
    await ctl.click(pt(3, 101));
    expect(view()).toMatchObject({ message: "network down", canUndo: false });
    expect(view().drawings).toHaveLength(0);
    store.fail = null;
    ctl.setTool("hline");
    await ctl.click(pt(3, 101));
    for (let i = 0; i < HISTORY_LIMIT + 5; i++) {
      ctl.beginDrag("1", 0, pt(3, 101 + i));
      ctl.dragTo(pt(3, 102 + i), times);
      await ctl.endDrag();
    }
    let steps = 0;
    while (ctl.view().canUndo) { await ctl.undo(); steps++; }
    expect(steps).toBe(HISTORY_LIMIT);
  });

  it("edits run one at a time, in order", async () => {
    const { store, ctl } = setup();
    ctl.setTool("hline");
    await ctl.click(pt(3, 101));
    ctl.beginDrag("1", 0, pt(3, 101));
    ctl.dragTo(pt(3, 102), times);
    const a = ctl.endDrag();
    const b = ctl.undo();
    const c = ctl.redo();
    await Promise.all([a, b, c]);
    expect(store.calls).toEqual(["create", "update 1@1", "update 1@2", "update 1@3"]);
    expect(store.rows.get(1)?.drawing.anchors[0].p).toBe(102);
  });

  it("load replaces the drawings and starts a fresh history", async () => {
    const { store, ctl, view } = setup();
    await store.create("TCS", { kind: "hline", anchors: [{ p: 1 }] });
    await ctl.load();
    expect(view().drawings.map((d) => d.key)).toEqual(["1"]);
    expect(view().canUndo).toBe(false);
  });
});


describe("review follow-up: queued edits read the state they run on", () => {
  /** A store whose answers wait until released, so edits pile up in the queue as on a slow network. */
  function slow() {
    const { store, ctl, view } = setup();
    const gates: (() => void)[] = [];
    const wrap = <K extends "update" | "remove" | "lock">(k: K) => {
      const real = store[k].bind(store) as (...a: unknown[]) => Promise<unknown>;
      (store as unknown as Record<string, unknown>)[k] = (...a: unknown[]) => new Promise((res, rej) => gates.push(() => { real(...a).then(res, rej); }));
    };
    return { store, ctl, view, gates, hold: () => { wrap("update"); wrap("remove"); wrap("lock"); },
             // the queue sends the next request only after the previous answer: release over several turns
             release: async () => { for (let i = 0; i < 20; i++) { while (gates.length) gates.shift()!(); await new Promise((r) => setTimeout(r, 0)); } } };
  }

  it("a second drag before the first save returns builds on the first", async () => {
    const t = slow();
    t.ctl.setTool("trendline");
    await t.ctl.click(pt(0, 100));
    await t.ctl.click(pt(10, 101));
    t.hold();
    t.ctl.beginDrag("1", 0, pt(0, 100));
    t.ctl.dragTo(pt(0, 90), times);
    const a = t.ctl.endDrag();
    expect(t.view().drawings[0].drawing.anchors[0].p).toBe(90);                      // shown at once, not jumping back
    t.ctl.beginDrag("1", 1, pt(10, 101));
    t.ctl.dragTo(pt(10, 110), times);
    const b = t.ctl.endDrag();
    await t.release();
    await Promise.all([a, b]);
    expect(t.store.rows.get(1)?.drawing.anchors.map((x) => x.p)).toEqual([90, 110]);
    const u = t.ctl.undo();
    await t.release();
    await u;
    expect(t.store.rows.get(1)?.drawing.anchors.map((x) => x.p)).toEqual([90, 101]);
  });

  it("delete right after a move deletes (and undo restores) the moved drawing; two lock toggles unlock", async () => {
    const t = slow();
    t.ctl.setTool("hline");
    await t.ctl.click(pt(3, 100));
    t.hold();
    t.ctl.beginDrag("1", 0, pt(3, 100));
    t.ctl.dragTo(pt(3, 120), times);
    const a = t.ctl.endDrag();
    const b = t.ctl.deleteSelected();
    await t.release();
    await Promise.all([a, b]);
    expect(t.store.rows.size).toBe(0);
    await t.ctl.undo();
    await t.release();
    expect([...t.store.rows.values()][0].drawing.anchors[0].p).toBe(120);
    t.ctl.select(String([...t.store.rows.keys()][0]));
    const c = t.ctl.toggleLock();
    const d = t.ctl.toggleLock();
    await t.release();
    await Promise.all([c, d]);
    expect([...t.store.rows.values()][0].locked).toBe(false);
  });

  it("a drawing deleted elsewhere leaves the chart and does not jam undo", async () => {
    const { store, ctl, view } = setup();
    ctl.setTool("hline");
    await ctl.click(pt(3, 101));
    ctl.beginDrag("1", 0, pt(3, 101));
    ctl.dragTo(pt(3, 105), times);
    await ctl.endDrag();
    store.rows.delete(1);                                                         // deleted in another tab (404)
    await ctl.undo();
    expect(view()).toMatchObject({ canUndo: false, selected: null });
    expect(view().drawings).toHaveLength(0);
  });

  it("a drag that wanders back to where it started saves nothing, even after the server reformatted the times", async () => {
    const { store, ctl } = setup();
    ctl.setTool("trendline");
    await ctl.click(pt(0, 100));
    await ctl.click(pt(10, 101));
    ctl.beginDrag("1", null, pt(5, 100));
    ctl.dragTo(pt(8, 102), times);
    ctl.dragTo(pt(5, 100), times);
    await ctl.endDrag();
    expect(store.calls).toEqual(["create"]);
    expect(ctl.view().canUndo).toBe(true);                                      // only the create
  });

  it("after a conflict, a drag queued behind the failed one is not saved over the other tab's version", async () => {
    const t = slow();
    t.ctl.setTool("hline");
    await t.ctl.click(pt(3, 100));
    t.store.rows.set(1, { ...t.store.rows.get(1)!, version: 5, drawing: { kind: "hline", anchors: [{ p: 130 }] } });
    t.hold();
    t.ctl.beginDrag("1", 0, pt(3, 100));
    t.ctl.dragTo(pt(3, 90), times);
    const a = t.ctl.endDrag();
    t.ctl.beginDrag("1", 0, pt(3, 90));
    t.ctl.dragTo(pt(3, 80), times);
    const b = t.ctl.endDrag();
    await t.release();
    await Promise.all([a, b]);
    expect(t.store.rows.get(1)?.drawing.anchors[0].p).toBe(130);
    expect(t.view().drawings[0].drawing.anchors[0].p).toBe(130);
  });
});
