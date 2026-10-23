import { describe, expect, it } from "vitest";
import { solidBrandLabels, tokenToRgb } from "../e2e/solidBrand";

/** Chart + colour order Part 3 (CC-8): the one-solid-brand-button heuristic's pure part. */
describe("one solid brand button per screen", () => {
  it("turns a token value into the computed background form", () => {
    expect(tokenToRgb(" 29 78 216")).toBe("rgb(29, 78, 216)");
    expect(tokenToRgb("37 99 235\n")).toBe("rgb(37, 99, 235)");
    expect(tokenToRgb("")).toBeNull();
    expect(tokenToRgb("29 78")).toBeNull();
    expect(tokenToRgb("29 78 300")).toBeNull();
  });

  it("counts only visible elements painted with the brand fill", () => {
    const fill = "rgb(29, 78, 216)";
    const labels = solidBrandLabels([
      { label: "Place order", background: fill, visible: true },
      { label: "Cancel", background: "rgba(0, 0, 0, 0)", visible: true },
      { label: "Hidden start", background: fill, visible: false },
      { label: "Hovered", background: "rgb(30, 64, 175)", visible: true },          // brand-fill-strong: not counted
      { label: "Connect", background: fill, visible: true },
    ], fill);
    expect(labels).toEqual(["Place order", "Connect"]);
    expect(solidBrandLabels([{ label: "x", background: fill, visible: true }], null)).toEqual([]);
  });
});
