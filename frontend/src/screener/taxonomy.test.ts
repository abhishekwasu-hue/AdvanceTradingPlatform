import { describe, expect, it } from "vitest";
import { STARTER_BUILDS, STARTERS } from "./FunnelCanvas";
import starterRows from "./starters.json";
import sample from "./taxonomySample.json";
import { cellCount, chipCount, emptyText, filterScreens, isTaxonomy, NO_FILTER, placeText, railCount, tagLabel, type Taxonomy } from "./taxonomy";

/** The server's real taxonomy (backend tests/test_z8_tx1_taxonomy.py fails when this sample drifts from the data file). */
const tax = sample as unknown as Taxonomy;
/** What the server sends while the Legends flag (U7 LG1, `screener_legends`) is off: the starters only. */
const starters = tax.screens.filter((s) => s.source === "starter");
const withScreens = (extra: Partial<Taxonomy["screens"][number]>[]): Taxonomy => {
  const screens = [...starters, ...extra.map((e, i) => ({ ...tax.screens[0], id: `test.s${i}`, source: "test", ...e }))];
  const matrix = Object.fromEntries(tax.methods.map((m) => [m.id, Object.fromEntries(tax.setups.map((s) => [s.id, 0]))]));
  for (const s of screens) matrix[s.method][s.setup] += 1;
  return { ...tax, screens, matrix, total: screens.length };
};

describe("the taxonomy sample (U9 TX1)", () => {
  it("has six methods and eight setups, a full matrix and every screen filed on both axes", () => {
    expect(tax.methods.map((m) => m.code)).toEqual(["A", "B", "C", "D", "E", "F"]);
    expect(tax.setups.map((s) => s.code)).toEqual(["1", "2", "3", "4", "5", "6", "7", "8"]);
    for (const m of tax.methods) expect(Object.keys(tax.matrix[m.id]).sort()).toEqual(tax.setups.map((s) => s.id).sort());
    const methods = new Set(tax.methods.map((m) => m.id));
    const setups = new Set(tax.setups.map((s) => s.id));
    for (const s of tax.screens) expect(methods.has(s.method) && setups.has(s.setup)).toBe(true);
    expect(cellCount(tax, null, null)).toBe(tax.total);
    expect(isTaxonomy(tax)).toBe(true);
    for (const bad of [null, {}, { detail: "Not found" }, { methods: [], setups: [] }]) expect(isTaxonomy(bad)).toBe(false);
  });

  it("files every starter template the empty canvas offers, by id and name", () => {
    const filed = new Map(tax.screens.filter((s) => s.source === "starter").map((s) => [s.id, s.name]));
    expect(STARTERS.map((t) => t.id).sort()).toEqual([...filed.keys()].sort());
    for (const t of STARTERS) expect(filed.get(t.id)).toBe(t.name);
  });
});

describe("the starter templates (starters.json)", () => {
  it("every row has a build and every build has a row; ids are unique", () => {
    const ids = starterRows.starters.map((t) => t.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(Object.keys(STARTER_BUILDS).sort()).toEqual([...ids].sort());
    expect(STARTERS).toHaveLength(ids.length);
    for (const t of STARTERS) expect(t.id.startsWith("starter.") && t.name.length > 0 && t.summary.length > 0).toBe(true);
  });
});

describe("gallery filtering", () => {
  const t = withScreens([{ method: "price_action", setup: "breakout", name: "Range high", definition: "Close above the 20-bar high" }]);

  it("narrows by method (rail) and setup (chips), and both together are one cell", () => {
    expect(filterScreens(t, NO_FILTER)).toHaveLength(t.total);
    expect(filterScreens(t, { ...NO_FILTER, method: "price_action" }).map((s) => s.name)).toEqual(["Range high"]);
    expect(filterScreens(t, { ...NO_FILTER, setup: "breakout" }).map((s) => s.name)).toEqual(["Range high"]);
    expect(filterScreens(t, { ...NO_FILTER, method: "indicator", setup: "breakout" })).toEqual([]);
    expect(filterScreens(t, { ...NO_FILTER, method: "indicator", setup: "momentum" }).map((s) => s.id)).toEqual(["starter.momentum"]);
  });

  it("searches names and definitions, every word, ignoring case", () => {
    expect(filterScreens(t, { ...NO_FILTER, query: "rsi" }).map((s) => s.id)).toEqual(["starter.momentum", "starter.oversold"]);
    expect(filterScreens(t, { ...NO_FILTER, query: "RSI   30" }).map((s) => s.id)).toEqual(["starter.oversold"]);
    expect(filterScreens(t, { ...NO_FILTER, query: "20-bar high" }).map((s) => s.name)).toEqual(["Range high"]);
    expect(filterScreens(t, { ...NO_FILTER, query: "nothing like this" })).toEqual([]);
  });

  it("counts: the rail within the picked setup, the chips within the picked method", () => {
    const f = { ...NO_FILTER, method: "indicator", setup: "momentum" };
    expect(railCount(t, "indicator", f)).toBe(1);
    expect(railCount(t, "price_action", f)).toBe(0);
    expect(railCount(t, null, f)).toBe(1);
    expect(chipCount(t, "breakout", f)).toBe(0);
    expect(chipCount(t, null, f)).toBe(3);
    expect(chipCount(t, "breakout", NO_FILTER)).toBe(1);
  });

  it("says which cell is empty, and why a user-built folder is", () => {
    expect(placeText(t, { ...NO_FILTER, method: "indicator", setup: "breakout" })).toBe("Indicator → Breakout / breakdown");
    expect(emptyText(t, { ...NO_FILTER, method: "indicator", setup: "breakout" })).toBe("No screens are filed under Indicator → Breakout / breakdown yet.");
    expect(emptyText(t, { ...NO_FILTER, method: "my_screens" })).toContain("screens you build and save");
    expect(emptyText(t, { ...NO_FILTER, method: "indicator", query: "zzz" })).toBe('No screen in Indicator → All setups matches "zzz".');
  });

  it("labels tags from the vocabulary", () => {
    expect(tagLabel(t, "data_tier", "live_per_user")).toBe("Your broker");
    expect(tagLabel(t, "direction", "unknown")).toBe("unknown");
  });
});
