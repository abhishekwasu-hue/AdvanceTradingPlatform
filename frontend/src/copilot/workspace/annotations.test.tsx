import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ZONE_COLORS } from "../../charting/marketZones";
import type { ReadChart } from "../../chart/readChart";
import { annotationAreaLabel, annotationLayer, annotationRows, workspaceLayer, type Annotations } from "./annotations";
import { AnnotationsV2 } from "./AnnotationsV2";
import workspaceSource from "./ChartWorkspaceV2.tsx?raw";

/**
 * M75 W-3 (owner order M75 CW-2): the workspace draws ONE reading from ONE source - areas as filled bands with side /
 * timeframe / state / tests, swings HH / HL / LH / LL, BOS / CHoCH, an RSI divergence, the structure level - and writes it
 * as a short table. Every number is the server's.
 */

const area = (role: "demand" | "supply", low: number, high: number, extra: Partial<Annotations["areas"][number]> = {}) => ({
  role, side: role === "demand" ? "buyer" as const : "seller" as const, low, high, low_text: low.toFixed(2), high_text: high.toFixed(2),
  timeframe: "15m", state: "fresh", state_words: "untouched since it formed", tests: 2, words: "Area.", ...extra,
});

const A: Annotations = {
  source: "atp", source_words: "Drawn from ATP's chart readers on this chart's closed bars.", timeframe: "15m", as_of: "2026-10-20T10:00:00+00:00",
  last_close: 100,
  areas: [area("demand", 95, 97), area("supply", 104, 106, { state: "flipped", tests: 0 })],
  swings: [{ label: "HL", kind: "L", time: 10, price: 96, price_text: "96.00", timeframe: "15m" },
           { label: "HH", kind: "H", time: 20, price: 103, price_text: "103.00", timeframe: "15m" }],
  events: [{ type: "BOS", name: "BOS", direction: "up", time: 30, swing_time: 20, price: 103, price_text: "103.00", timeframe: "15m", words: "break of structure up" }],
  trend: [{ timeframe: "1d", direction: "down (weak)", words: "daily: down, weakening" }, { timeframe: "15m", direction: "up", words: "15-minute: up" }],
  next_areas: { above: null, below: null, inside: null },
  divergence: { side: "bullish", role: "demand", timeframe: "15m", first: { time: 5, price: 96.5 }, second: { time: 25, price: 95.5 }, words: "RSI divergence." },
  level: { price: 95.5, price_text: "95.50", side: "below", words: "The up structure changes beyond 95.50." },
  summary: ["Trend - daily: down, weakening; 15-minute: up."], not_given: [],
};
A.next_areas = { above: A.areas[1], below: A.areas[0], inside: null };

describe("W-3: the annotations as chart layers", () => {
  const layer = annotationLayer(A);
  it("areas are filled bands with side, timeframe, state and tests; the structure level a thin labelled band", () => {
    expect(layer.bands?.slice(0, 2).map((b) => [b.low, b.high, b.role, b.color, b.label])).toEqual([
      [95, 97, "demand", ZONE_COLORS.demand, "Buyer area · 15m · fresh · 2 tests"],
      [104, 106, "supply", ZONE_COLORS.supply, "Seller area · 15m · flipped"],
    ]);
    expect(layer.bands?.slice(0, 2).every((b) => (b.opacity ?? 0) > 0 && b.style === "solid")).toBe(true);   // filled, not dashed lines
    expect(layer.bands?.[2]).toMatchObject({ low: 95.5, high: 95.5, role: "neutral", label: "Structure changes beyond 95.50" });
    expect(layer.lines ?? []).toEqual([]);                      // no price-line titles on the axis (W-1)
  });
  it("swings HH / HL above / below, BOS / CHoCH with their broken level, the divergence as a segment", () => {
    expect(layer.labels?.map((l) => [l.text, l.position])).toEqual([["HL", "below"], ["HH", "above"], ["BOS ↑", "below"]]);
    expect(layer.segments).toEqual([
      expect.objectContaining({ t1: 20, t2: 30, p1: 103, p2: 103, style: "dotted" }),
      expect.objectContaining({ t1: 5, p1: 96.5, t2: 25, p2: 95.5, label: "RSI divergence" }),
    ]);
  });
  it("a reading without annotations keeps its own layer", () => {
    const read = { areas: [], structure: null, commitment: null, as_of: null } as unknown as ReadChart;
    expect(workspaceLayer(read).bands).toEqual([]);
    expect(workspaceLayer({ ...read, annotations: A }).labels?.length).toBe(3);
    expect(workspaceSource).toContain("workspaceLayer");
  });
  it("area label drops what the data lacks", () => {
    expect(annotationAreaLabel(area("supply", 1, 2, { state: null, tests: null, timeframe: "" }))).toBe("Seller area");
  });
});

describe("W-3: the analysis table", () => {
  it("rows in reading order, toned by meaning", () => {
    const rows = annotationRows(A);
    expect(rows.map((r) => r.key)).toEqual(["trend-1d", "trend-15m", "event", "above", "below", "divergence", "level", "swings"]);
    expect(rows[0]).toMatchObject({ value: "down, weakening", tone: "down" });
    expect(rows.find((r) => r.key === "above")).toMatchObject({ value: "seller area 104.00 - 106.00 (15m, flipped)", tone: "down" });
    expect(rows.find((r) => r.key === "divergence")).toMatchObject({ value: "bullish (15m)", tone: "up" });
  });
  it("the engine's missing parts say so, never filled from ATP", () => {
    const rows = annotationRows({ ...A, source: "engine", events: [], divergence: null, level: null, not_given: ["events", "divergence", "level"] });
    expect(rows.find((r) => r.key === "event")?.value).toBe("Not given by this source");
    expect(rows.find((r) => r.key === "divergence")?.value).toBe("Not given by this source");
    expect(rows.find((r) => r.key === "level")?.value).toBe("Not shown for this reading");
  });
  it("renders the table, the source line and no order words", () => {
    const html = renderToStaticMarkup(<AnnotationsV2 annotations={A} />);
    expect(html).toContain('data-workspace-annotations="atp"');
    expect(html).toContain("text-up");
    expect(html).toContain(A.source_words.split("'").join("&#x27;"));
    expect(html.toLowerCase()).not.toMatch(/\b(buy|sell|target|stop[- ]loss|entry)\b/);
  });
});
