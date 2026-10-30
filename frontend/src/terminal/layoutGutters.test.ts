import { describe, expect, it } from "vitest";
import {
  COLUMNS, MAX_HEIGHT, PANELS, chainHiddenColumns, equalWidths, fitToScreen, gutterLabel, gutters, layoutProblem, maximiseView, placeLimit,
  presetLayout, resetEdge, resizeAcrossEdge, sizePanel, type Gutter, type PanelId, type PanelPlace,
} from "./layoutModel";

/** M58 (T4): the lines between panels, the Panels dialog's sizes, maximise - the layout model's pure functions. */
const seller = () => presetLayout("option_seller").panels;
const at = (panels: readonly PanelPlace[], id: PanelId) => panels.find((p) => p.id === id)!;
const line = (panels: readonly PanelPlace[], ids: string): Gutter => {
  const g = gutters(panels).find((x) => [...x.before, ...x.after].join("|") === ids);
  if (!g) throw new Error(`no gutter ${ids}`);
  return g;
};

describe("M58 T4-1: the gutters of a layout", () => {
  it("every shared edge of the Option seller layout, full length", () => {
    const list = gutters(seller());
    expect(list.map((g) => `${g.axis}@${g.at}:${[...g.before, ...g.after].join("|")}`)).toEqual([
      "x@4:chart-1|chain", "x@9:chain|basket", "y@12:chart-1|chain|basket|positions",
    ]);
    expect(line(seller(), "chart-1|chain")).toMatchObject({ from: 0, to: 12 });
    expect(line(seller(), "chart-1|chain|basket|positions")).toMatchObject({ from: 0, to: 12 });
    expect(gutterLabel(line(seller(), "chart-1|chain"))).toBe("Resize Chart and Option chain");
    expect(gutterLabel(line(seller(), "chart-1|chain|basket|positions"))).toBe("Resize Chart, Option chain, Basket and Positions & orders");
  });

  it("closed panels have no gutter; panels that only touch at a corner have none", () => {
    expect(gutters(seller().map((p) => (p.id === "basket" ? { ...p, open: false } : p))).some((g) => g.after.includes("basket"))).toBe(false);
    const corner: PanelPlace[] = [
      { id: "chart-1", x: 0, y: 0, w: 6, h: 6, open: true }, { id: "chain", x: 6, y: 6, w: 6, h: 6, open: true },
    ];
    expect(gutters(corner)).toEqual([]);
  });
});

describe("M58 T4-1 / T4-2: dragging a gutter grows one panel and shrinks its neighbour", () => {
  it("both neighbours change by the same amount; the layout stays valid", () => {
    const r = resizeAcrossEdge(seller(), line(seller(), "chart-1|chain"), 1);
    expect(r.limit).toBeNull();
    expect(r.moved).toBe(1);
    expect(at(r.panels, "chart-1")).toMatchObject({ x: 0, w: 5 });
    expect(at(r.panels, "chain")).toMatchObject({ x: 5, w: 4 });
    expect(at(r.panels, "basket")).toMatchObject({ x: 9, w: 3 });                  // not on this line: unchanged
    expect(layoutProblem(r.panels)).toBeNull();
    const back = resizeAcrossEdge(r.panels, line(r.panels, "chart-1|chain"), -2);
    expect(at(back.panels, "chart-1").w).toBe(3);
    expect(at(back.panels, "chain")).toMatchObject({ x: 3, w: 6 });
  });

  it("the horizontal line moves every panel above it and the one below it", () => {
    const r = resizeAcrossEdge(seller(), line(seller(), "chart-1|chain|basket|positions"), -2);
    for (const id of ["chart-1", "chain", "basket"] as const) expect(at(r.panels, id).h).toBe(10);
    expect(at(r.panels, "positions")).toMatchObject({ y: 10, h: 10 });
  });

  it("stops at the neighbour's minimum width and says why (never a silent refusal)", () => {
    const r = resizeAcrossEdge(seller(), line(seller(), "chart-1|chain"), 4);    // the chain is 5 wide, min 3
    expect(r.moved).toBe(2);
    expect(at(r.panels, "chain").w).toBe(PANELS.chain.minW);
    expect(at(r.panels, "chart-1").w).toBe(6);
    expect(r.limit).toEqual({ kind: "min", panel: "chain", text: "Option chain cannot be narrower than 3 columns." });
    const none = resizeAcrossEdge(r.panels, line(r.panels, "chart-1|chain"), 1);
    expect(none.moved).toBe(0);
    expect(none.panels).toEqual(r.panels);
    expect(none.limit?.kind).toBe("min");
  });

  it("stops at a minimum height and at the tallest height, with the reason", () => {
    const g = line(seller(), "chart-1|chain|basket|positions");
    const down = resizeAcrossEdge(seller(), g, 6);                                   // positions 8 high, min 5
    expect(down.moved).toBe(3);
    expect(down.limit).toMatchObject({ kind: "min", panel: "positions", text: "Positions & orders cannot be shorter than 5 rows." });
    const up = resizeAcrossEdge(seller(), g, -10);                                   // the chart's min height is 6
    expect(up.moved).toBe(-6);
    expect(up.limit).toMatchObject({ kind: "min", panel: "chart-1" });
    const tall: PanelPlace[] = [{ id: "chart-1", x: 0, y: 0, w: 12, h: MAX_HEIGHT - 1, open: true }, { id: "positions", x: 0, y: MAX_HEIGHT - 1, w: 12, h: 12, open: true }];
    const r = resizeAcrossEdge(tall, line(tall, "chart-1|positions"), 3);
    expect(r.moved).toBe(1);
    expect(r.limit).toMatchObject({ kind: "max", panel: "chart-1", text: `Chart cannot be taller than ${MAX_HEIGHT} rows.` });
  });

  it("a move or a corner resize that cannot be applied says why", () => {
    expect(placeLimit(seller(), "positions", "move", 0, 11)?.text).toBe("Another panel is in the way.");
    expect(placeLimit(seller(), "positions", "move", 1, 12)?.text).toBe("This is the edge of the grid.");
    expect(placeLimit(seller(), "positions", "move", 0, 13)).toBeNull();
    expect(placeLimit(seller(), "chart-1", "resize", 5, 12)?.text).toMatch(/^Another panel is in the way\. Drag the line/);
    expect(placeLimit(seller(), "chart-1", "resize", 2, 12)?.text).toBe("Chart cannot be narrower than 3 columns.");
  });
});

describe("M58 T4-3: the Panels dialog's sizes", () => {
  it("a width stepper takes the column from the neighbour; a height stepper uses free space first", () => {
    const w = sizePanel(seller(), "chart-1", "w", 5);
    expect(w.limit).toBeNull();
    expect(at(w.panels, "chart-1").w).toBe(5);
    expect(at(w.panels, "chain")).toMatchObject({ x: 5, w: 4 });
    const basket = sizePanel(seller(), "basket", "w", 4);                           // no line on its right: its left line moves
    expect(at(basket.panels, "basket")).toMatchObject({ x: 8, w: 4 });
    expect(at(basket.panels, "chain").w).toBe(4);
    const h = sizePanel(seller(), "positions", "h", 10);                             // nothing below: it grows down
    expect(at(h.panels, "positions")).toMatchObject({ y: 12, h: 10 });
    expect(at(h.panels, "chart-1").h).toBe(12);
  });

  it("below the minimum or past the neighbour's minimum: the reason", () => {
    expect(sizePanel(seller(), "chain", "w", 2).limit?.text).toBe("Option chain cannot be narrower than 3 columns.");
    const far = sizePanel(seller(), "chart-1", "w", 12);
    expect(at(far.panels, "chart-1").w).toBe(6);
    expect(far.limit?.kind).toBe("min");
    expect(layoutProblem(far.panels)).toBeNull();
  });

  it("Equal widths: a row of side-by-side panels shares the 12 columns", () => {
    const r = equalWidths(seller());
    expect(["chart-1", "chain", "basket"].map((id) => at(r.panels, id as PanelId))).toMatchObject([{ x: 0, w: 4 }, { x: 4, w: 4 }, { x: 8, w: 4 }]);
    expect(at(r.panels, "positions")).toMatchObject({ x: 0, w: 12 });
    expect(r.limit).toBeNull();
    expect(layoutProblem(r.panels)).toBeNull();
  });

  it("Fit to screen: the panels fill the width and the rows the screen shows, each kept at its minimum", () => {
    const narrow = seller().map((p) => (p.id === "basket" ? { ...p, open: false } : p.id === "positions" ? { ...p, w: 9 } : p));
    const r = fitToScreen(narrow, 16);
    expect(r.limit).toBeNull();
    expect(Math.max(...r.panels.filter((p) => p.open).map((p) => p.x + p.w))).toBe(COLUMNS);
    expect(Math.max(...r.panels.filter((p) => p.open).map((p) => p.y + p.h))).toBe(16);
    expect(layoutProblem(r.panels)).toBeNull();
    const short = fitToScreen(seller(), 6);                                          // too short for 6 + 5 rows
    expect(short.limit?.text).toBe("The screen is too short for every open panel at its smallest height.");
    expect(layoutProblem(short.panels)).toBeNull();
  });
});

describe("M58 T4-4: reset a line, maximise and restore", () => {
  it("a double-click on a line puts the two panels back to the ready sizes", () => {
    const moved = resizeAcrossEdge(seller(), line(seller(), "chart-1|chain"), 2).panels;
    const r = resetEdge(moved, line(moved, "chart-1|chain"), "option_seller");
    expect(at(r.panels, "chart-1")).toMatchObject({ x: 0, w: 4 });
    expect(at(r.panels, "chain")).toMatchObject({ x: 4, w: 5 });
    expect(r.moved).toBe(-2);
    expect(r.limit).toBeNull();
  });

  it("maximise: the panel takes the full width, the others collapse to their titles; the layout itself is unchanged", () => {
    const base = seller();
    const view = maximiseView(base, "chain");
    expect(at(view, "chain")).toMatchObject({ x: 0, y: 1, w: COLUMNS });
    expect(at(view, "chain").h).toBeGreaterThanOrEqual(19);
    for (const id of ["chart-1", "basket", "positions"] as const) expect(at(view, id)).toMatchObject({ y: 0, h: 1 });
    expect(["chart-1", "basket", "positions"].map((id) => at(view, id as PanelId).w).reduce((a, b) => a + b)).toBe(COLUMNS);
    expect(layoutProblem(view)).toBeNull();
    expect(base).toEqual(seller());                                                 // restore = the layout as it was
    expect(maximiseView(base, "order")).toEqual(base);                               // a closed panel: nothing to maximise
  });
});

describe("M58 T4-5: the chain's column priority", () => {
  it("IV goes first, then OI change; never at the ready width", () => {
    expect(chainHiddenColumns(5)).toEqual([]);
    expect(chainHiddenColumns(12)).toEqual([]);
    expect(chainHiddenColumns(4)).toEqual(["iv"]);
    expect(chainHiddenColumns(PANELS.chain.minW)).toEqual(["iv", "oi_change"]);
  });
});
