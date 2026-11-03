import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { FIXTURE_HEIGHT, fixtureResult } from "./fixtures";
import { boxOverlap, labelSize, layoutLabels, shapesFor, type PlacedLabel } from "./shapes";
import { drawShapes } from "./exportImage";
import { Overlay } from "./AnnotatedChartV2";

/** Owner CA-Q3: on a crowded chart no label is drawn over another, and the screen and the download agree. */

function labelsAt(width: number): PlacedLabel[] {
  return layoutLabels(shapesFor(fixtureResult("formed").annotations, width), FIXTURE_HEIGHT, labelSize(width));
}

/** A canvas context that records every text drawn (and accepts every other call). */
function recordingCtx(height: number) {
  const texts: { text: string; x: number; y: number }[] = [];
  const ctx = new Proxy({ canvas: { width: 640, height }, textAlign: "left" } as Record<string, unknown>, {
    get(target, key: string) {
      if (key === "fillText") return (text: string, x: number, y: number) => texts.push({ text, x, y });
      if (key in target) return target[key];
      return () => undefined;
    },
    set(target, key: string, value) { target[key] = value; return true; },
  });
  return { ctx: ctx as unknown as CanvasRenderingContext2D, texts };
}

describe("Analyze a chart - labels never cover each other (CA-Q3)", () => {
  for (const width of [640, 1600]) {
    it(`no two label boxes overlap and every label stays inside the image (width ${width})`, () => {
      const labels = labelsAt(width);
      expect(labels.map((l) => l.text)).toEqual(expect.arrayContaining(["HL", "HH", "LH", "2 · Seller area · fresh", "Trigger ≈ 120.50", "Commitment", "CHoCH ≈ 126.00"]));
      for (let i = 0; i < labels.length; i++) {
        expect(labels[i].box.y).toBeGreaterThanOrEqual(0);
        expect(labels[i].box.y + labels[i].box.h).toBeLessThanOrEqual(FIXTURE_HEIGHT);
        for (let j = i + 1; j < labels.length; j++) expect(boxOverlap(labels[i].box, labels[j].box), `${labels[i].text} / ${labels[j].text}`).toBe(0);
      }
    });
  }

  it("the crowded pair is pulled apart: the seller area's label no longer sits on the swing 'HH'", () => {
    const r = fixtureResult("formed");
    const shapes = shapesFor(r.annotations, r.image.width);
    const fs = labelSize(r.image.width);
    const usual = layoutLabels(shapes.filter((s) => s.kind === "rect"), FIXTURE_HEIGHT, fs).find((l) => l.text.includes("Seller area"));
    const hh = layoutLabels(shapes.filter((s) => s.kind === "text"), FIXTURE_HEIGHT, fs).find((l) => l.text === "HH");
    expect(usual && hh && boxOverlap(usual.box, hh.box)).toBeGreaterThan(0);          // where they were both drawn before
    const all = labelsAt(r.image.width);
    const a = all.find((l) => l.text.includes("Seller area")), b = all.find((l) => l.text === "HH");
    expect(a && b && boxOverlap(a.box, b.box)).toBe(0);
  });

  it("is deterministic, and the on-screen SVG and the exported PNG draw every label at the same spot", () => {
    for (const width of [640, 1600]) {
      const r = fixtureResult("formed");
      expect(labelsAt(width)).toEqual(labelsAt(width));
      const html = renderToStaticMarkup(<Overlay src="x.png" width={width} height={FIXTURE_HEIGHT} annotations={r.annotations} alt="chart" />);
      const svg = [...html.matchAll(/<text x="([^"]+)" y="([^"]+)"[^>]*>([^<]*)<\/text>/g)]
        .map((m) => ({ text: m[3].replace(/&amp;/g, "&"), x: Number(m[1]), y: Number(m[2]) }));
      const { ctx, texts } = recordingCtx(FIXTURE_HEIGHT + 200);       // a canvas taller than the image (the caption band)
      drawShapes(ctx, shapesFor(r.annotations, width), () => "rgb(0 0 0)", labelSize(width), FIXTURE_HEIGHT);
      expect(svg.length).toBe(labelsAt(width).length);
      expect(texts).toEqual(svg);
    }
  });
});
