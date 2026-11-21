import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ANCHOR_RULES, type DrawingKind, type DrawingV1, type StoredDrawing } from "../charting/drawings";
import { DrawingController, type DrawingsStore, type ToolView } from "../charting/tools";
import { readChartPalette } from "./chartTheme";
import { DrawingSelectionBar } from "./DrawingSelectionBar";
import { drawingSettings, shownColour, swatchColours, textProblem, toHex } from "./drawingSelection";

/** Owner 21 Nov ("make it like tradingview"): what the selected drawing's bar offers, and the controller calls it uses. */

const T1 = "2026-10-19T04:00:00Z";
const T2 = "2026-10-19T05:00:00Z";
const trend = (style?: DrawingV1["style"]): DrawingV1 => ({ kind: "trendline", anchors: [{ t: T1, p: 100 }, { t: T2, p: 110 }], ...(style ? { style } : {}) });

class Store implements DrawingsStore {
  rows = new Map<number, StoredDrawing>();
  next = 1;
  updates: DrawingV1[] = [];
  async list() { return [...this.rows.values()]; }
  async create(symbol: string, drawing: DrawingV1) {
    const r: StoredDrawing = { id: this.next++, symbol, exchange: "NSE", kind: drawing.kind, drawing, version: 1, locked: false, updated_at: null };
    this.rows.set(r.id, r);
    return r;
  }
  async update(stored: StoredDrawing, drawing: DrawingV1) {
    this.updates.push(drawing);
    const n = { ...stored, drawing, version: stored.version + 1 };
    this.rows.set(n.id, n);
    return n;
  }
  async lock(stored: StoredDrawing, locked: boolean) { const n = { ...stored, locked }; this.rows.set(n.id, n); return n; }
  async remove(stored: StoredDrawing) { this.rows.delete(stored.id); return { deleted: 1 }; }
}

async function withDrawing(d: DrawingV1, locked = false) {
  const store = new Store();
  const row = await store.create("NIFTY 50", d);
  row.locked = locked;
  const ctl = new DrawingController(store, "NIFTY 50");
  await ctl.load();
  ctl.select(String(row.id));
  return { store, ctl, key: String(row.id) };
}

const html = (ctl: DrawingController, view: ToolView = ctl.view(), showMessage = true) =>
  renderToStaticMarkup(<DrawingSelectionBar view={view} controller={ctl} insets={{ right: 64, bottom: 28 }} root={null} showMessage={showMessage} />);
const labels = (markup: string) => [...markup.matchAll(/<button[^>]*aria-label="([^"]+)"/g)].map((m) => m[1]);

describe("drawing settings per kind", () => {
  it("lines get colour, width and style; extension only where the picture uses it; a label its colour and text", () => {
    expect(drawingSettings("trendline")).toEqual({ colour: true, line: true, extend: true, text: false });
    expect(drawingSettings("channel")).toEqual({ colour: true, line: true, extend: true, text: false });
    expect(drawingSettings("ray")).toEqual({ colour: true, line: true, extend: false, text: false });
    expect(drawingSettings("hline")).toEqual({ colour: true, line: true, extend: false, text: false });
    expect(drawingSettings("rectangle")).toEqual({ colour: true, line: true, extend: false, text: false });
    expect(drawingSettings("fib_retracement")).toEqual({ colour: true, line: true, extend: false, text: false });
    expect(drawingSettings("text")).toEqual({ colour: true, line: false, extend: false, text: true });
    expect(drawingSettings("long_position")).toEqual({ colour: false, line: false, extend: false, text: false });
    for (const kind of Object.keys(ANCHOR_RULES) as DrawingKind[]) expect(drawingSettings(kind)).toBeTruthy();
  });

  it("colours become the stored #rrggbb form; the swatches come from the chart theme, each once", () => {
    expect(toHex("rgb(41, 98, 255)")).toBe("#2962ff");
    expect(toHex("rgba(0, 0, 0, 0.5)")).toBe("#000000");
    expect(toHex("#ABCDEF")).toBe("#abcdef");
    expect(toHex("blue")).toBeNull();
    const p = readChartPalette(null);
    const s = swatchColours(p);
    expect(s[0]).toBe(toHex(p.drawing));
    expect(new Set(s).size).toBe(s.length);
    expect(s.every((c) => /^#[0-9a-f]{6}$/.test(c))).toBe(true);
  });

  it("the shown colour is the drawing's own, or the chart's drawing colour for the default", () => {
    const p = readChartPalette(null);
    expect(shownColour(trend({ color: "#ABCDEF" }), p)).toBe("#abcdef");
    expect(shownColour(trend(), p)).toBe(toHex(p.drawing));
    expect(shownColour(trend({ color: "#2962ff" }), p)).toBe(toHex(p.drawing));
  });

  it("a label's text must have something and no < or >", () => {
    expect(textProblem("  ")).toBe("Type the label's text.");
    expect(textProblem("a<b")).toBe("The text may not contain < or >.");
    expect(textProblem("Seller area")).toBeNull();
  });
});

describe("the selection bar", () => {
  it("a selected trend line: colour, line, extension, then lock, duplicate, delete, close", async () => {
    const { ctl } = await withDrawing(trend());
    const markup = html(ctl);
    expect(markup).toContain('aria-label="Trend line settings"');
    expect(labels(markup)).toEqual(["Colour", "Line width and style", "Extend the line", "Lock", "Duplicate", "Delete", "Close"]);
    expect(markup).not.toMatch(/<button[^>]*disabled=""/);
  });

  it("only the settings a kind honours: a horizontal line has no extension, a label edits its text, a position only lock / copy / delete", async () => {
    expect(labels(html((await withDrawing({ kind: "hline", anchors: [{ p: 100 }] })).ctl))).toEqual(["Colour", "Line width and style", "Lock", "Duplicate", "Delete", "Close"]);
    expect(labels(html((await withDrawing({ kind: "text", anchors: [{ t: T1, p: 100 }], text: "Seller area" })).ctl))).toEqual(["Colour", "Edit the text", "Lock", "Duplicate", "Delete", "Close"]);
    expect(labels(html((await withDrawing({ kind: "long_position", anchors: [{ t: T1, p: 100 }, { p: 95 }, { p: 110 }] })).ctl))).toEqual(["Lock", "Duplicate", "Delete", "Close"]);
  });

  it("locked: Unlock pressed, the settings and Delete rest, Duplicate stays", async () => {
    const markup = html((await withDrawing(trend(), true)).ctl);
    expect(markup).toMatch(/aria-label="Unlock"[^>]*aria-pressed="true"/);
    for (const name of ["Colour", "Line width and style", "Extend the line", "Delete"]) expect(markup).toMatch(new RegExp(`aria-label="${name}"[^>]*disabled=""`));
    expect(markup).not.toMatch(/aria-label="Duplicate"[^>]*disabled=""/);
  });

  it("nothing selected, or a tool being placed: no bar", async () => {
    const { ctl } = await withDrawing(trend());
    ctl.select(null);
    expect(html(ctl)).toBe("");
    ctl.setTool("hline");
    expect(html(ctl)).toBe("");
  });

  it("the controller's message shows as one short line, unless the page shows it itself", async () => {
    const { ctl, key } = await withDrawing(trend(), true);
    await ctl.edit(key, { style: { width: 3 } });
    expect(html(ctl)).toContain("This drawing is locked. Unlock it to change it.");
    expect(html(ctl, ctl.view(), false)).not.toContain("data-drawing-bar-message");
  });
});

describe("the controller calls the bar uses (contract)", () => {
  it("edit merges the style, saves once and is one undo step", async () => {
    const { store, ctl, key } = await withDrawing(trend({ color: "#112233" }));
    await ctl.edit(key, { style: { width: 3 } });
    expect(store.updates).toHaveLength(1);
    expect(store.updates[0].style).toEqual({ color: "#112233", width: 3 });
    expect(ctl.selectedDrawing()).toMatchObject({ key, locked: false, drawing: { style: { color: "#112233", width: 3 } } });
    await ctl.undo();
    expect(store.updates[1].style).toEqual({ color: "#112233" });
  });

  it("edit refuses a locked drawing and a bad text; duplicate saves a selected copy", async () => {
    const locked = await withDrawing(trend(), true);
    await locked.ctl.edit(locked.key, { style: { width: 2 } });
    expect(locked.store.updates).toHaveLength(0);
    const label = await withDrawing({ kind: "text", anchors: [{ t: T1, p: 100 }], text: "a" });
    await label.ctl.edit(label.key, { text: "x<y" });
    expect(label.store.updates).toHaveLength(0);
    await label.ctl.duplicate(label.key);
    expect(label.store.rows.size).toBe(2);
    expect(label.ctl.view().selected).toBe("2");
  });
});
