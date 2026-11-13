// Owner 13 Nov ("look at wrong area marking its a seller area"; "timeframe nivadlya nusar analysis"): the area labels of
// the chart answer - a line level is one price (never "x – x"), and an area carries its timeframe (a higher one marked
// "context") and its state. Synthetic numbers only.
import { describe, expect, it } from "vitest";

import { areaNote, areaRangeText, areaText, cardAreaText, type CardArea } from "./model";

const num = (v: number) => v.toFixed(2);

describe("area labels (owner 13 Nov)", () => {
  it("shows a line level as one price, never a zero-width range", () => {
    expect(areaRangeText({ from: 1050, to: 1050, kind: "line" }, num)).toBe("Level 1050.00");
    expect(areaRangeText({ from: 1050, to: 1050 }, num)).toBe("Level 1050.00");          // an older answer without `kind`
    expect(areaRangeText({ from: 1000, to: 1040, kind: "band" }, num)).toBe("1000.00 – 1040.00");
    expect(areaText({ side: "supply", from: 1050, to: 1050, where: "above", distance: 60, distance_pct: 6, kind: "line" }, num))
      .toBe("Supply Level 1050.00");
  });

  it("labels an area with its timeframe, a higher one as context, and its state", () => {
    const seller: CardArea = { side: "supply", from: 1000, to: 1040, where: "above", distance: 10, kind: "band", timeframe: "1d",
      state: "tested", context: true };
    expect(areaNote(seller, num)).toBe("Supply · 1d context · tested · 10.00 pts");
    expect(cardAreaText(seller, num)).toBe("Supply 1000.00 – 1040.00 · 1d context · tested · 10.00 pts");
    const own: CardArea = { ...seller, side: "demand", where: "inside", distance: 0, timeframe: "15m", context: false, state: "fresh" };
    expect(areaNote(own, num)).toBe("Demand · 15m · fresh · price inside");
  });

  it("keeps an older answer's text as it was", () => {
    expect(areaNote({ side: "supply", from: 1, to: 2, where: "above", distance: 3 }, String)).toBe("Supply · 3 pts");
    expect(cardAreaText({ side: null, from: 1, to: 2, where: "inside", distance: 0 }, String)).toBe("1 – 2 · price inside");
  });
});
