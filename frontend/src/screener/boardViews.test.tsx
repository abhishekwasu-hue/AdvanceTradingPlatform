import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { gridSymbols, heatmapLayout, heatmapResults, nextTab, sparkPath, squarify, tileFill, BOARD_VIEWS, CONTEXT_MAX, GRID_MAX, type ContextRow } from "./boardViews";
import { ChartGridView, HeatmapView } from "./ResultViews";

const ctx = (symbol: string, sector: string, cap: number | null, change: number | null): ContextRow => ({
  symbol, sector, industry: null, market_cap: cap, cap_category: null, change_pct: change,
  closes: [{ t: 1, c: 100 }, { t: 2, c: 100 + (change ?? 0) }],
});
const results = [
  { symbol: "AAA", matched: true, reason: null }, { symbol: "BBB", matched: false, reason: null },
  { symbol: "CCC", matched: false, reason: "not enough history on 1d" }, { symbol: "DDD", matched: true, reason: null, evidence_id: 7 },
];
const context = [ctx("AAA", "Energy", 400, 1.5), ctx("BBB", "Energy", 100, -0.5), ctx("CCC", "IT", null, null), ctx("DDD", "IT", 300, -2)];

describe("result board views (U5 D3a)", () => {
  it("squarifies: areas proportional to the values and the box exactly filled", () => {
    const box = { x: 0, y: 0, w: 300, h: 200 };
    const out = squarify([{ item: "a", value: 6 }, { item: "b", value: 6 }, { item: "c", value: 4 }, { item: "d", value: 3 }, { item: "e", value: 2 },
                          { item: "f", value: 2 }, { item: "g", value: 1 }, { item: "z", value: 0 }], box);
    expect(out.map((o) => o.item)).toEqual(["a", "b", "c", "d", "e", "f", "g"]);          // zero-valued items get no tile
    const total = 24, area = 300 * 200;
    for (const o of out) {
      const v = { a: 6, b: 6, c: 4, d: 3, e: 2, f: 2, g: 1 }[o.item as "a"];
      expect(o.rect.w * o.rect.h).toBeCloseTo((v / total) * area, 6);
      expect(o.rect.x).toBeGreaterThanOrEqual(-1e-9);
      expect(o.rect.x + o.rect.w).toBeLessThanOrEqual(300 + 1e-6);
      expect(o.rect.y + o.rect.h).toBeLessThanOrEqual(200 + 1e-6);
    }
  });

  it("groups by sector sized by market cap; a symbol without one counts at the median; nothing invented", () => {
    const groups = heatmapLayout(results, context, { x: 0, y: 0, w: 400, h: 300 });
    expect(groups.map((g) => g.sector).sort()).toEqual(["Energy", "IT"]);
    const it = groups.find((g) => g.sector === "IT")!;
    expect(it.tiles.find((t) => t.symbol === "CCC")).toMatchObject({ unread: true, change: null, cap: null });
    expect(groups.find((g) => g.sector === "Energy")!.matched).toBe(1);
    const energy = groups.find((g) => g.sector === "Energy")!.rect, itr = it.rect;
    expect((energy.w * energy.h) / (itr.w * itr.h)).toBeCloseTo(500 / (300 + 300), 6);    // CCC at the median cap (300)
    expect(tileFill(null).color).toBe("rgb(var(--surface-2))");
    expect(tileFill(5)).toEqual({ color: "rgb(var(--up))", opacity: 0.8 });
    expect(tileFill(-1.5).color).toBe("rgb(var(--down))");
  });

  it("renders an accessible heatmap and a grid of the matched symbols only", () => {
    const heat = renderToStaticMarkup(<HeatmapView results={results} context={context} />);
    expect(heat).toContain('aria-label="AAA: matched, day change +1.50 %"');
    expect(heat).toContain('aria-label="CCC: could not be read, day change n/a"');
    expect(heat).toContain("Sized by market cap");
    const grid = renderToStaticMarkup(<ChartGridView results={results} context={context} packs={{}} />);
    expect((grid.match(/<li /g) ?? []).length).toBe(2);
    expect(grid).toContain("the last 2 daily closes");
    expect(gridSymbols(Array.from({ length: 30 }, (_, i) => ({ symbol: `S${i}`, matched: true, reason: null })))).toHaveLength(GRID_MAX);
    expect(sparkPath([{ c: 1 }], 10, 10)).toBeNull();
    expect(sparkPath([{ c: 1 }, { c: 3 }], 10, 10)).toBe("M0.0 10.0L10.0 0.0");
    expect(heat + grid).not.toMatch(/\b(buy|sell|order)\b/i);
  });

  it("lays out only the symbols with context (no fake Unclassified tiles past the context cap) and says so", () => {
    const many = Array.from({ length: 520 }, (_, i) => ({ symbol: `S${i}`, matched: i % 7 === 0, reason: null }));
    const some = many.slice(0, CONTEXT_MAX).map((r, i) => ctx(r.symbol, i % 2 ? "Energy" : "IT", 100 + i, 0.5));
    expect(heatmapResults(many, some)).toHaveLength(CONTEXT_MAX);
    const groups = heatmapLayout(many, some, { x: 0, y: 0, w: 400, h: 300 });
    expect(groups.map((g) => g.sector).sort()).toEqual(["Energy", "IT"]);                 // no "Unclassified" group invented
    expect(groups.reduce((a, g) => a + g.tiles.length, 0)).toBe(CONTEXT_MAX);
    const heat = renderToStaticMarkup(<HeatmapView results={many} context={some} />);
    expect(heat).toContain(`First ${CONTEXT_MAX} of 520 shown`);
    expect(renderToStaticMarkup(<HeatmapView results={results} context={context} />)).not.toContain("shown ·");
  });

  it("moves the roving tab stop with the arrow, Home and End keys", () => {
    const ids = BOARD_VIEWS.map((v) => v.id);
    expect(nextTab(ids, "table", "ArrowRight")).toBe(ids[1]);
    expect(nextTab(ids, "table", "ArrowLeft")).toBe(ids[ids.length - 1]);
    expect(nextTab(ids, ids[ids.length - 1], "ArrowRight")).toBe("table");
    expect(nextTab(ids, "heatmap", "Home")).toBe("table");
    expect(nextTab(ids, "table", "End")).toBe(ids[ids.length - 1]);
    expect(nextTab(ids, "table", "Enter")).toBeNull();
  });
});
