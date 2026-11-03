import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { fixtureResult } from "./fixtures";
import { labelSize, legendFor, shapesFor } from "./shapes";
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
});
