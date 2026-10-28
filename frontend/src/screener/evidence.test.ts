import { describe, expect, it } from "vitest";
import sample from "./evidenceSample.json";
import {
  barTimeText, chartSummary, drawableColor, evidenceGeometry, replaySteps, stepReplay, trendTone, verifyRows, visibleDrawables,
  type EvidenceDrawable, type EvidencePack,
} from "./evidence";

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
    const oneFailed = { ...pack, rows: pack.rows.map((r, i) => (i === 0 ? { ...r, passed: false } : r)) };
    expect(verifyRows(oneFailed, true).some((r) => r.passed === false)).toBe(true);
    expect(verifyRows(oneFailed, false).some((r) => r.passed === false)).toBe(false);
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

describe("evidence context helpers (U8 EV3)", () => {
  it("replay steps are the window's bar closes up to the pack's own bar, oldest first; the last is the stored pack", () => {
    const steps = replaySteps(pack);
    expect(steps).toHaveLength(pack.bars.t.length);
    expect(steps[0]).toBe(new Date(Date.parse(pack.bars.t[0]) + 86400e3).toISOString());
    expect(Date.parse(steps[steps.length - 1])).toBe(Date.parse(pack.as_of.bar_close));
    expect(steps.every((t, i) => i === 0 || Date.parse(t) > Date.parse(steps[i - 1]))).toBe(true);
    expect(replaySteps({ ...pack, base_tf: "1w" })).toEqual([]);                          // weekly screens are not replayed
    const fiveMin = { ...pack, base_tf: "5m", bars: { ...pack.bars, t: ["2026-10-09T03:45:00+00:00", "2026-10-09T03:50:00+00:00"] },
                      as_of: { bar_start: "2026-10-09T03:50:00+00:00", bar_close: "2026-10-09T03:55:00+00:00" } };
    expect(replaySteps(fiveMin)).toEqual(["2026-10-09T03:50:00.000Z", "2026-10-09T03:55:00.000Z"]);
  });

  it("steps back and forward inside the window, and returns to the stored pack at the match", () => {
    expect(stepReplay(null, -1, 40)).toBe(38);
    expect(stepReplay(38, -1, 40)).toBe(37);
    expect(stepReplay(0, -1, 40)).toBe(0);
    expect(stepReplay(38, 1, 40)).toBeNull();
    expect(stepReplay(null, 1, 40)).toBeNull();
    expect(stepReplay(null, -1, 1)).toBeNull();
  });

  it("names trend tones in words-first order and labels option-chain levels on the price pane", () => {
    expect([trendTone("UP"), trendTone("DOWN"), trendTone("SIDEWAYS"), trendTone(null)]).toEqual(["up", "down", "flat", "flat"]);
    const wall: EvidenceDrawable = { id: "dc", row: pack.rows[0].id, kind: "overlay", pane: "price", label: "PCR(): option chain 2026-11-26", failed: false,
      series: [], levels: [{ value: 101, label: "call OI wall", value_text: "101.00" }, { value: 99, label: "put OI wall", value_text: "99.00" }],
      chain: { underlying: "BRK", expiry: "2026-11-26" } };
    const g = evidenceGeometry(pack, [wall]);
    expect(g.shapes.filter((s) => s.kind === "line")).toHaveLength(2);
    expect(g.shapes.filter((s) => s.kind === "text").map((s) => s.text)).toEqual(["call OI wall 101.00", "put OI wall 99.00"]);
    const plain = evidenceGeometry(pack, [{ ...wall, chain: undefined }]);
    expect(plain.shapes.filter((s) => s.kind === "text")).toHaveLength(0);              // EV1 / EV2 levels unchanged
  });
});
