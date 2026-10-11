import { describe, expect, it } from "vitest";
import sample from "./evidenceSample.json";
import { barTimeText, chartSummary, drawableColor, evidenceGeometry, verifyRows, visibleDrawables, type EvidencePack } from "./evidence";

const pack = sample as unknown as EvidencePack;

describe("evidence pack helpers (U8 EV1)", () => {
  it("draws only the pack's drawables: failed ones only with Show failed conditions, hidden blocks never", () => {
    const passing = visibleDrawables(pack, new Set(), false);
    expect(passing.every((d) => !d.failed)).toBe(true);
    expect(passing.length).toBe(pack.drawables.filter((d) => !d.failed).length);
    const all = visibleDrawables(pack, new Set(), true);
    expect(all.length).toBe(pack.drawables.length);
    const firstBlock = pack.blocks[0].id;
    const withoutFirst = visibleDrawables(pack, new Set([firstBlock]), true);
    const blockOf = new Map(pack.rows.map((r) => [r.id, r.block]));
    expect(withoutFirst.some((d) => blockOf.get(d.row) === firstBlock)).toBe(false);
    expect(withoutFirst.length).toBeLessThan(all.length);
  });

  it("lists failed conditions in the verify strip only when asked", () => {
    expect(verifyRows(pack, false).every((r) => r.passed !== false)).toBe(true);
    expect(verifyRows(pack, true).length).toBe(pack.rows.length);
    expect(verifyRows(pack, true).some((r) => r.passed === false)).toBe(true);
  });

  it("builds geometry from the pack alone: candles, one shape set per drawable, one lower pane, the matched bar", () => {
    const shown = visibleDrawables(pack, new Set(), false);
    const g = evidenceGeometry(pack, shown, 720, 360);
    expect(g.candles.length).toBe(pack.bars.c.length);
    const ids = new Set(shown.map((d) => d.id));
    for (const s of [...g.shapes, ...g.subShapes]) expect(ids.has(s.drawable ?? "")).toBe(true);   // nothing outside the pack
    expect(g.sub).not.toBeNull();
    expect(g.matchedX).toBeCloseTo(g.candles[g.candles.length - 1].x, 5);
    // the box spans its base and ends before the matched bar; the band reaches the right edge
    const box = shown.find((d) => d.kind === "box")!;
    const boxRect = g.shapes.find((s) => s.key === `${box.id}-box`)!;
    expect((boxRect.x ?? 0) + (boxRect.w ?? 0)).toBeLessThan(g.matchedX!);
    const band = shown.find((d) => d.kind === "band")!;
    const bandRect = g.shapes.find((s) => s.key === `${band.id}-band`)!;
    expect((bandRect.x ?? 0) + (bandRect.w ?? 0)).toBeCloseTo(g.right, 5);
    // prices map inside the pane
    for (const c of g.candles) {
      expect(c.high).toBeGreaterThanOrEqual(g.priceTop);
      expect(c.low).toBeLessThanOrEqual(g.priceBottom);
    }
  });

  it("is deterministic and colours zone bands by role (colour-blind safe palette)", () => {
    const shown = visibleDrawables(pack, new Set(), false);
    expect(JSON.stringify(evidenceGeometry(pack, shown))).toBe(JSON.stringify(evidenceGeometry(pack, shown)));
    const band = shown.find((d) => d.kind === "band")!;
    expect(drawableColor(band, shown)).toBe("#009E73");
    const others = shown.filter((d) => d.kind !== "band");
    const colours = others.map((d) => drawableColor(d, shown));
    expect(new Set(colours).size).toBe(others.length);
    expect(colours).not.toContain(drawableColor(band, shown));                              // a band never shares a colour
  });

  it("reads bar times in IST and summarises the chart for screen readers", () => {
    expect(barTimeText({ base_tf: "1d", as_of: { bar_start: "2026-07-19T00:00:00+00:00", bar_close: "2026-07-20T00:00:00+00:00" } })).toBe("bar of 19 Jul 2026");
    expect(barTimeText({ base_tf: "5m", as_of: { bar_start: "2026-03-10T07:00:00+00:00", bar_close: "2026-03-10T07:05:00+00:00" } }))
      .toBe("bar closed 10 Mar 2026, 12:35 IST");
    const text = chartSummary(pack, visibleDrawables(pack, new Set(), false));
    expect(text).toContain("BRK 1d candles up to the matched bar of");
    expect(text).toContain("range high");
  });
});
