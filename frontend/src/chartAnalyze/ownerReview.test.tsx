import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { fixtureResult } from "./fixtures";
import { areaNumber, distancePct, labelSize, legendFor, shapesFor } from "./shapes";
import { wordsUsed } from "./wordsUsed";
import ResultViewV2 from "./ResultViewV2";

/** Owner review of the GLENMARK read (3 Nov) + CA-D3. */
describe("Analyze a chart - owner review 3 Nov", () => {
  it("CA-D3: the method words of the read are explained, from the one glossary", () => {
    const r = fixtureResult("formed");
    const words = wordsUsed(r).map((w) => w.term);
    expect(words).toContain("Invalidation");
    expect(words.length).toBeGreaterThan(1);
    const html = renderToStaticMarkup(<ResultViewV2 result={r} imageSrc={null} />);
    expect(html).toContain("Words used");
    expect(html).toContain("What these words mean");
  });

  it("the legend tells a seller area (box) from the invalidation (dashed line)", () => {
    const rows = legendFor(fixtureResult("formed").annotations);
    const seller = rows.find((r) => r.text === "Seller area"), inv = rows.find((r) => r.text === "Invalidation");
    if (seller && inv) expect(seller.swatch).not.toBe(inv.swatch);
    expect(rows.filter((r) => r.text === "Invalidation").every((r) => r.swatch === "dashed")).toBe(true);
  });

  it("labels grow with a wide screenshot; setup-line labels sit at the price-axis end", () => {
    expect(labelSize(800)).toBe(15);
    expect(labelSize(1600)).toBe(29);
    expect(labelSize(300)).toBe(12);
    const lines = shapesFor(fixtureResult("formed").annotations, 1000).filter((s) => s.kind === "line" && s.dashed);
    expect(lines.length).toBeGreaterThan(0);
    for (const l of lines) expect(l.kind === "line" && l.labelAt).toBe("end");
  });

  it("the state card reads 'Price now', never a second trend word", () => {
    const html = renderToStaticMarkup(<ResultViewV2 result={fixtureResult("formed")} imageSrc={null} />);
    expect(html).toContain("Price now");
    expect(html).not.toMatch(/>Trending</);
  });

  it("CA-Q3: CHoCH line and setup labels carry their price; areas are numbered and the list matches", () => {
    const r = fixtureResult("formed");
    const labels = shapesFor(r.annotations, 640).map((s) => ("label" in s ? s.label : undefined)).filter(Boolean);
    expect(labels).toContain("CHoCH ≈ 126.00");
    expect(labels).toContain("Invalidation ≈ 101.00");
    expect(labels).toContain("1 · Buyer area · tested");
    expect(legendFor(r.annotations).map((x) => x.text)).toContain("CHoCH (change of character)");
    expect(areaNumber(r.annotations, 98, 101)).toBe(1);
    expect(areaNumber(r.annotations, 120.5, 120.5)).toBeNull();
    const html = renderToStaticMarkup(<ResultViewV2 result={r} imageSrc={null} />);
    expect(html).toContain("data-level-number");
    expect(html).toContain("14.4% below");                     // 101 vs the last price 118
  });

  it("CA-Q3: distance in % of the last price", () => {
    expect(distancePct(100, 102, 104)).toBe("2.0% above");
    expect(distancePct(100, 95, 98)).toBe("2.0% below");
    expect(distancePct(100, 99, 101)).toBe("at the price");
    expect(distancePct(null, 99, 101)).toBeNull();
  });
});

import { readingOverlay } from "./HybridResultV2";
import { fixtureHybrid } from "./fixtures";

describe("CA-Q1: ATP's reading on ATP's own chart", () => {
  it("the reading's protected level, break, swings and areas become chart lines and bands (same values as the picture)", () => {
    const r = fixtureHybrid(false).reading;
    const { levels, areas } = readingOverlay(r);
    if (r.protected != null) expect(levels).toContainEqual({ price: r.protected, title: "Protected", tone: "neutral" });
    expect(levels.length).toBe((r.protected != null ? 1 : 0) + (r.last_break ? 1 : 0) + r.levels.length);
    expect(areas.map((a) => [a.low, a.high])).toEqual(r.areas.map((a) => [a.low, a.high]));
    for (const [i, a] of areas.entries()) expect(a.role).toBe(r.areas[i].where === "above" ? "supply" : r.areas[i].where === "below" ? "demand" : "neutral");
  });
});
