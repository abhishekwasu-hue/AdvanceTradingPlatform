import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ANCHOR_RULES, type DeleteAllResult, type DrawingKind, type DrawingV1, type StoredDrawing } from "../charting/drawings";
import type { useDrawingTools } from "../charting/DrawingToolbar";
import { DrawingController, type DrawingsStore, type ToolView } from "../charting/tools";
import { ConfirmBox } from "./ConfirmBox";
import { DrawingMenu, DrawingRail } from "./DrawingRail";
import { MORE_TOOLS, RAIL_TOOLS, deleteAllText, isMoreTool, placeHint } from "./drawingRail";

/** UI2-4c: the drawing rail's tools and "More" grouping (CHP-8), the Delete all question (UI-11), and the controller's
 * timeframe on a new drawing and its Delete all (UI2-4a1). */

const T = "2026-10-19T04:00:00Z";
const hline = (p: number): DrawingV1 => ({ kind: "hline", anchors: [{ p }] });

class Store implements DrawingsStore {
  rows = new Map<number, StoredDrawing>();
  next = 1;
  created: { symbol: string; exchange?: string; timeframe?: string; args: number }[] = [];
  removeAllCalls: string[] = [];
  async list() { return [...this.rows.values()]; }
  async create(symbol: string, drawing: DrawingV1, exchange?: string, timeframe?: string) {
    this.created.push({ symbol, exchange, timeframe, args: arguments.length });
    const r: StoredDrawing = { id: this.next++, symbol, exchange: exchange ?? "NSE", kind: drawing.kind, drawing, version: 1, locked: false, updated_at: null, timeframe: timeframe ?? null };
    this.rows.set(r.id, r);
    return r;
  }
  async update(stored: StoredDrawing, drawing: DrawingV1) { const n = { ...stored, drawing, version: stored.version + 1 }; this.rows.set(n.id, n); return n; }
  async lock(stored: StoredDrawing, locked: boolean) { const n = { ...stored, locked }; this.rows.set(n.id, n); return n; }
  async remove(stored: StoredDrawing) { this.rows.delete(stored.id); return { deleted: 1 }; }
  async removeAll(symbol: string, exchange?: string): Promise<DeleteAllResult> {
    this.removeAllCalls.push(`${exchange}:${symbol}`);
    const ids = [...this.rows.values()].filter((r) => !r.locked).map((r) => r.id);
    ids.forEach((id) => this.rows.delete(id));
    return { deleted: ids.length, kept_locked: this.rows.size, ids };
  }
}

describe("UI2-4c rail tools (CHP-8)", () => {
  it("the rail holds the brief's six tools in order; every other kind is under More; no kind is lost", () => {
    expect(RAIL_TOOLS.map((t) => t.kind)).toEqual(["trendline", "hline", "ray", "rectangle", "text", "measure"]);
    expect(MORE_TOOLS.map((t) => t.kind)).toEqual(["fib_retracement", "fib_extension", "channel", "vline", "long_position", "short_position"]);
    const all = [...RAIL_TOOLS, ...MORE_TOOLS].map((t) => t.kind).sort();
    expect(all).toEqual((Object.keys(ANCHOR_RULES) as DrawingKind[]).sort());
    expect(isMoreTool("channel")).toBe(true);
    expect(isMoreTool("hline")).toBe(false);
    expect(isMoreTool(null)).toBe(false);
  });

  it("the placing hint says how many points and how to stop", () => {
    expect(placeHint("hline")).toBe("Click the chart to place the horizontal line. Press Esc to cancel.");
    expect(placeHint("trendline")).toBe("Click 2 points on the chart for the trend line. Press Esc to cancel.");
    expect(placeHint("long_position", true)).toBe("Tap 3 points on the chart for the long position.");
    expect(placeHint(null)).toBeNull();
  });

  it("the rail renders the tools, More, Hide all and Delete all; with no drawings Delete all rests", () => {
    const view: ToolView = { drawings: [], preview: null, selected: null, tool: "hline", canUndo: false, canRedo: false, message: null };
    const tools = { view, controller: {} as DrawingController } as ReturnType<typeof useDrawingTools>;
    const html = renderToStaticMarkup(<DrawingRail tools={tools} symbol="NIFTY 50" hidden={false} onHidden={() => {}} />);
    const labels = [...html.matchAll(/aria-label="([^"]+)"/g)].map((m) => m[1]);
    expect(labels).toEqual(["Drawing tools", "Trend line", "Horizontal line", "Ray", "Rectangle", "Text", "Measure", "More drawing tools",
      "Hide all drawings", "Delete all drawings"]);
    expect(html).toMatch(/aria-label="Horizontal line"[^>]*aria-pressed="true"/);
    expect(html).toMatch(/aria-label="Delete all drawings"[^>]*disabled=""/);
    // Hidden: the tools rest, the eye shows pressed (Show again).
    const hidden = renderToStaticMarkup(<DrawingRail tools={tools} symbol="NIFTY 50" hidden onHidden={() => {}} />);
    expect(hidden).toMatch(/aria-label="Trend line"[^>]*disabled=""/);
    expect(hidden).toMatch(/aria-label="Hide all drawings"[^>]*aria-pressed="true"/);
  });

  it("the phone menu is one 44 px button until opened", () => {
    const html = renderToStaticMarkup(<DrawingMenu tools={null} symbol="NIFTY 50" hidden={false} onHidden={() => {}} />);
    expect(html).toContain('aria-label="Drawing tools"');
    expect(html).toContain("min-h-11 min-w-11");
    expect(html).toContain("md:hidden");
    expect(html).not.toContain('role="dialog"');
  });
});

describe("UI2-4c Delete all asks first (UI-11)", () => {
  it("the question names what goes, where, that Undo cannot bring it back, and what stays", () => {
    expect(deleteAllText("NIFTY 50", [{ locked: false }, { locked: false }, { locked: true }])).toEqual({
      title: "Delete 2 drawings on NIFTY 50?",
      body: "They are removed from every timeframe of NIFTY 50, and Undo cannot bring them back. 1 locked drawing stays.",
    });
    expect(deleteAllText("TCS", [{ locked: false }]).title).toBe("Delete 1 drawing on TCS?");
    expect(deleteAllText("TCS", [{ locked: false }]).body).not.toContain("locked");
  });

  it("the confirm box: an alert dialog with both texts, Cancel first, neutral buttons (no red)", () => {
    const html = renderToStaticMarkup(<ConfirmBox title="Delete 2 drawings on NIFTY 50?" body="They are removed." confirm="Delete all" onConfirm={() => {}} onCancel={() => {}} />);
    expect(html).toMatch(/role="alertdialog" aria-labelledby="[^"]+" aria-describedby="[^"]+"/);
    expect([...html.matchAll(/<button[^>]*>([^<]+)<\/button>/g)].map((m) => m[1])).toEqual(["Cancel", "Delete all"]);
    expect(html).not.toMatch(/text-down|bg-down|border-down/);
  });

  it("the controller deletes all unlocked drawings in one request, keeps the locked ones, forgets their undo steps", async () => {
    const store = new Store();
    const ctl = new DrawingController(store, "NIFTY 50", "NSE", "15min");
    let view!: ToolView;
    ctl.subscribe((v) => { view = v; });
    for (const p of [100, 101, 102]) { ctl.setTool("hline"); await ctl.click({ t: Date.parse(T) / 1000, p }); }
    ctl.select(view.drawings[0].key);
    await ctl.toggleLock();
    expect(view.canUndo).toBe(true);
    await ctl.deleteAll();
    expect(store.removeAllCalls).toEqual(["NSE:NIFTY 50"]);
    expect(view.drawings.map((d) => d.drawing.anchors[0].p)).toEqual([100]);
    expect(view.drawings[0].locked).toBe(true);
    await ctl.undo();                                                         // nothing to bring back
    expect(store.created).toHaveLength(3);
  });

  it("a store without Delete all does nothing", async () => {
    const store = new Store();
    const plain: DrawingsStore = { list: store.list.bind(store), create: store.create.bind(store), update: store.update.bind(store),
      lock: store.lock.bind(store), remove: store.remove.bind(store) };
    const ctl = new DrawingController(plain, "TCS");
    const spy = vi.spyOn(store, "removeAll");
    await ctl.deleteAll();
    expect(spy).not.toHaveBeenCalled();
  });
});

describe("UI2-4a1 timeframe on a new drawing", () => {
  it("a new drawing records the chart's timeframe; a timeframe change applies to the next one without a reload", async () => {
    const store = new Store();
    const ctl = new DrawingController(store, "NIFTY 50", "NSE", "15min");
    ctl.setTool("hline");
    await ctl.click({ t: Date.parse(T) / 1000, p: 100 });
    ctl.setTimeframe("day");
    ctl.setTool("hline");
    await ctl.click({ t: Date.parse(T) / 1000, p: 101 });
    expect(store.created.map((c) => c.timeframe)).toEqual(["15min", "day"]);
  });

  it("without a timeframe (the old pages) the store is called as before, with three arguments", async () => {
    const store = new Store();
    const ctl = new DrawingController(store, "TCS");
    ctl.setTool("hline");
    await ctl.click({ t: Date.parse(T) / 1000, p: 100 });
    expect(store.created).toEqual([{ symbol: "TCS", exchange: "NSE", timeframe: undefined, args: 3 }]);
  });
});
