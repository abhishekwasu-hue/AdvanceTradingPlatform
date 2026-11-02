/**
 * M77 SX-4: the study chart renderer (backend app/study/chart.py `place_band_labels`) is a port of the Charts tab's
 * `placeBandLabels`. Both run the same cases (bandLabelsFixture.json) and must give the same chips - so a label in a
 * saved study's chart sits where the Charts tab would put it: never on the price axis, never over another label.
 */
import { describe, expect, it } from "vitest";
import fixture from "./bandLabelsFixture.json";
import { overlaps, placeBandLabels, type Converters, type Rect, type Shape } from "./geometry";

type Chip = Extract<Shape, { type: "chip" }>;

describe("M77 SX-4 band labels: one placement for the Charts tab and the study charts", () => {
  for (const c of fixture.cases) {
    it(c.name, () => {
      const conv: Converters = { x: (t) => t, y: (p) => p, width: c.width, height: c.height, avoid: c.avoid as Rect[], labelStyle: { fontPx: c.fontPx, chip: null, ink: null } };
      const items = c.items.map((it) => ({ ...it, color: "#000000" }));
      const chips = placeBandLabels(items, conv).filter((s): s is Chip => s.type === "chip");
      const got = chips.map((ch) => ({ index: items.findIndex((it) => it.text === ch.band.label), x: ch.x, y: ch.y, w: ch.w, h: ch.h }));
      expect(got).toEqual(c.expected);
      for (const [i, a] of chips.entries()) {
        expect(a.x + a.w).toBeLessThanOrEqual(c.width);                     // never on the price axis (outside the pane)
        for (const b of chips.slice(i + 1)) expect(overlaps(a, b)).toBe(false);
      }
    });
  }
});
