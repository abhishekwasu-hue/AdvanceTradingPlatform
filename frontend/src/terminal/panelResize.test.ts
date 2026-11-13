import { describe, expect, it } from "vitest";
import {
  COLUMNS, EMPTY_PREFS, PANELS, activeKey, gutters, layoutFor, layoutProblem, presetLayout, readPrefs, resetToPreset, resizeAcrossEdge,
  resizeFromCorner, saveAs, withoutWatchlist, type Gutter, type PanelId, type PanelPlace,
} from "./layoutModel";

/**
 * Owner bug 13 Nov: "chart and the other panels do not become small or big by dragging". The size model behind the
 * Terminal's lines and corners: a corner drag moves the shared line (it was refused whenever a neighbour touched);
 * minimums hold; the sizes are kept with "Save as mine", read back on load, and "Reset" brings the ready sizes back.
 * The layout as the owner sees it: Option seller with the watchlist off (Chart 8 · Chain 4 over Positions 9 · Basket 3).
 */
const seen = () => withoutWatchlist(presetLayout("option_seller").panels);
const at = (panels: readonly PanelPlace[], id: PanelId) => panels.find((p) => p.id === id)!;
const line = (panels: readonly PanelPlace[], ids: string): Gutter => gutters(panels).find((g) => [...g.before, ...g.after].join("|") === ids)!;

describe("the corner moves the shared line (owner bug 13 Nov)", () => {
  it("wider: the chart grows, the chain shrinks by the same columns; nothing overlaps", () => {
    const r = resizeFromCorner(seen(), "chart-1", 9, at(seen(), "chart-1").h);
    expect(at(r.panels, "chart-1")).toMatchObject({ x: 0, w: 9 });
    expect(at(r.panels, "chain")).toMatchObject({ x: 9, w: 3 });
    expect(r.limit).toBeNull();
    expect(layoutProblem(r.panels)).toBeNull();
  });

  it("taller: the whole top row grows, Positions & orders and the Basket give the rows", () => {
    const r = resizeFromCorner(seen(), "chart-1", 7, 14);
    expect(at(r.panels, "chart-1").h).toBe(14);
    expect(at(r.panels, "chain").h).toBe(14);
    expect(at(r.panels, "positions")).toMatchObject({ y: 14, h: 6 });
    expect(at(r.panels, "basket")).toMatchObject({ y: 14, h: 6 });
    expect(layoutProblem(r.panels)).toBeNull();
  });

  it("before the fix this was refused outright: a corner that is free on one side still grows there", () => {
    // Positions & orders has no panel below it: its corner grows it into the free rows.
    const r = resizeFromCorner(seen(), "positions", 9, 12);
    expect(at(r.panels, "positions").h).toBe(12);
    expect(at(r.panels, "basket").h).toBe(8);
  });

  it("minimums hold, and the stop says why", () => {
    const r = resizeFromCorner(seen(), "chart-1", 12, 12);
    expect(at(r.panels, "chain").w).toBe(PANELS.chain.minW);
    expect(r.limit?.text).toBe("Option chain cannot be narrower than 3 columns.");
    const small = resizeFromCorner(seen(), "chart-1", 1, 12);
    expect(at(small.panels, "chart-1").w).toBe(PANELS["chart-1"].minW);
    expect(small.limit?.kind).toBe("min");
    const short = resizeFromCorner(seen(), "chart-1", 7, 2);
    expect(at(short.panels, "chart-1").h).toBe(PANELS["chart-1"].minH);
  });

  it("the grid edge stops the last panel on the right", () => {
    const r = resizeFromCorner(seen(), "chain", 9, 12);
    expect(at(r.panels, "chain").x + at(r.panels, "chain").w).toBe(COLUMNS);
    expect(r.limit?.kind).toBe("edge");
  });
});

describe("sizes: saved with Save as mine, restored on load, Reset", () => {
  const dragged = () => {
    const a = resizeAcrossEdge(seen(), line(seen(), "chart-1|chain"), -1).panels;
    return resizeAcrossEdge(a, line(a, "chart-1|chain|positions|basket"), -3).panels;
  };

  it("a dragged layout saved as his own comes back with the same sizes after a reload", () => {
    const working = { ...presetLayout("option_seller"), panels: dragged() };
    const r = saveAs(EMPTY_PREFS, working, "Wide chart", "own1");
    if ("error" in r) throw new Error(r.error);
    const loaded = readPrefs(JSON.parse(JSON.stringify(r.prefs)));            // the server's copy, read back on load
    const back = layoutFor(loaded, activeKey(loaded)).panels;
    expect(at(back, "chart-1")).toMatchObject({ w: 7, h: 9 });
    expect(at(back, "chain")).toMatchObject({ x: 7, w: 5, h: 9 });
    expect(at(back, "positions")).toMatchObject({ y: 9, h: 11 });
  });

  it("Reset gives the ready layout's sizes back", () => {
    const working = { ...presetLayout("option_seller"), panels: dragged() };
    const reset = withoutWatchlist(resetToPreset(working).panels);
    expect(reset).toEqual(seen());
  });

  it("a dragged layout keeps every panel at or above its minimum", () => {
    expect(gutters(dragged()).length).toBeGreaterThan(0);
    for (const q of dragged().filter((x) => x.open)) {
      expect(q.w).toBeGreaterThanOrEqual(PANELS[q.id].minW);
      expect(q.h).toBeGreaterThanOrEqual(PANELS[q.id].minH);
    }
  });
});
